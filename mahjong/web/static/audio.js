// FB-06: Web Audio cues. Synthesized (no binary assets, in keeping with the
// build-free client) but voiced richly — detuned-oscillator chorus + ADSR
// envelopes, brightening timbre, and a triad flourish for `hu`. There are two
// distinct families of cue:
//
//   1. A private "your turn to decide" NOTIFICATION (`alert`) — fired only on
//      the local human's own CLAIM_WINDOW prompt, telling YOU to call or pass.
//   2. Public DECLARATION cues — fired for *everyone* the moment a claim
//      actually lands, escalating in importance chi < peng < gang < hu. A
//      physical table hears "Peng!"/"Hu!"; so does this one.
//
// Plus a soft private `draw` blip on your own draw.
//
// The cue-selection functions (`cueForEvent` / `cueForPrompt` / `cueForTerminal`)
// are pure and unit-tested; `AudioCues.play` is the only side-effecting part and
// no-ops silently when muted or when Web Audio is unavailable.
//
// "Not working" history: a browser starts an AudioContext created *outside* a
// user gesture in the `suspended` state, and ours was created lazily on the
// first inbound frame (a network event, not a gesture) — so it stayed suspended
// and every cue was silent. The fix is `unlock()` (called on the first user
// gesture, see app.js) plus a `resume()` on the suspended context inside
// `play()`. This is the standard Web Audio autoplay-policy unlock dance.

// Claim types in descending importance — also the set we treat as "a real
// claim" (a PASS-only window is not worth alerting on).
const CLAIM_PRIORITY = ["HU", "GANG", "PENG", "CHI"];

// Which cue (if any) an inbound record EVENT should play.
//   - DRAW for the local seat → a soft private blip (opponents' draws would be
//     noise, and you shouldn't hear when others draw).
//   - CLAIM_RESOLUTION (outcome CLAIMED) → the winning chi/peng/gang, heard by
//     EVERYONE. This is the authoritative single winner of a claim window, so
//     a losing contender never double-fires.
//   - A self-declared kong (CONCEALED / ADDED) carries no resolution event — it
//     lands on the CLAIM_DECISION — so catch the gang there. Also public.
export function cueForEvent(event, ownSeat) {
  if (!event) return null;
  const etype = event.event ?? event.kind;
  if (etype === "DRAW") {
    return event.seat === ownSeat && event.tile ? "draw" : null;
  }
  if (etype === "CLAIM_RESOLUTION" && event.outcome === "CLAIMED") {
    const claim = String(event.winning_claim ?? "").toLowerCase();
    return claim in VOICES ? claim : null;
  }
  if (
    etype === "CLAIM_DECISION" &&
    event.decision === "GANG" &&
    (event.kind === "CONCEALED" || event.kind === "ADDED")
  ) {
    return "gang";
  }
  return null;
}

// The local human's claim-window prompt → a single attention-grabbing
// NOTIFICATION ("you must call or pass"), regardless of which claims are on
// offer. The specific call escalation belongs to the public declaration, not
// to this private nudge. PASS-only / non-claim prompts make no sound. Only ever
// fired for the seat that owns the prompt, so it leaks nothing to opponents.
export function cueForPrompt(prompt) {
  if (!prompt || prompt.phase !== "CLAIM_WINDOW") return null;
  const types = new Set((prompt.legal_actions ?? []).map((a) => a.type));
  return CLAIM_PRIORITY.some((t) => types.has(t)) ? "alert" : null;
}

// HAND_END is its own wire frame (not EVENT-wrapped), so a winning HU surfaces
// here, not through cueForEvent. A win → the triumphant `hu`, heard by everyone;
// an exhaustive draw / abort (no winner) → silence.
export function cueForTerminal(terminal) {
  if (!terminal) return null;
  const w = terminal.winner;
  const winners = Array.isArray(w) ? w : w != null ? [w] : [];
  return winners.length > 0 ? "hu" : null;
}

// Per-cue "score": each cue is a waveform + a list of notes (freq Hz, start
// offset s, duration s). Every note is voiced by a detuned oscillator pair
// (chorus) under an ADSR envelope; the timbre brightens with intensity
// (sine → triangle → sawtooth) and `hu` is a rising arpeggio into a
// triad-with-octave-shimmer flourish. Still synthesized: no binary assets.
const C6 = 1046.5, E6 = 1318.51, G6 = 1567.98;
const G5 = 783.99, B5 = 987.77, D6 = 1174.66;
const VOICES = {
  // Soft private blip on your own draw.
  draw: { wave: "sine", notes: [{ f: 587.33, t: 0, d: 0.09 }] },
  // Bright rising "ding" — your turn to call or pass. Distinct from any call.
  alert: {
    wave: "triangle",
    notes: [{ f: 880.0, t: 0, d: 0.08 }, { f: E6, t: 0.07, d: 0.2 }],
  },
  // Declarations, escalating chi < peng < gang in pitch, note-count and edge.
  chi: { wave: "triangle", notes: [{ f: C6, t: 0, d: 0.14 }] },
  peng: {
    wave: "triangle",
    notes: [{ f: C6, t: 0, d: 0.08 }, { f: E6, t: 0.07, d: 0.18 }],
  },
  gang: {
    wave: "sawtooth",
    notes: [
      { f: C6, t: 0, d: 0.08 },
      { f: E6, t: 0.07, d: 0.08 },
      { f: G6, t: 0.14, d: 0.22 },
    ],
  },
  // The grand finale: a rising arpeggio resolving into a held G-major triad
  // with an octave shimmer on top.
  hu: {
    wave: "sawtooth",
    notes: [
      { f: G5, t: 0.0, d: 0.1 },
      { f: B5, t: 0.08, d: 0.1 },
      { f: D6, t: 0.16, d: 0.1 },
      { f: G5, t: 0.24, d: 0.5 },
      { f: B5, t: 0.24, d: 0.5 },
      { f: D6, t: 0.24, d: 0.5 },
      { f: G6, t: 0.3, d: 0.42 },
    ],
  },
};

export class AudioCues {
  constructor() {
    this.muted = false;
    this.lastCue = null; // last cue actually played (testability hook)
    this._ctx = null;
  }

  setMuted(muted) {
    this.muted = !!muted;
  }

  // Create (if needed) and resume the AudioContext from within a real user
  // gesture. Browsers refuse to start audio until then; calling this on the
  // first pointerdown/keydown is what makes every later cue audible. Idempotent
  // and safe to call when muted (we still want the context warm if unmuted).
  unlock() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      this._ctx = this._ctx || new Ctx();
      if (this._ctx.state === "suspended") this._ctx.resume();
    } catch {
      // No Web Audio / blocked — non-fatal, stay silent.
    }
  }

  // Play `cue` unless muted/unknown. Records `lastCue` only when it actually
  // plays, so a muted session leaves `lastCue` null (asserted in tests).
  play(cue) {
    if (this.muted || !cue || !(cue in VOICES)) return;
    this.lastCue = cue;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      this._ctx = this._ctx || new Ctx();
      // If we were created outside a gesture (suspended), try to resume now;
      // by the time gameplay frames arrive the user has almost always clicked
      // something, so the resume succeeds and the cue is heard.
      if (this._ctx.state === "suspended") this._ctx.resume();
      const voice = VOICES[cue];
      const t0 = this._ctx.currentTime;
      for (const note of voice.notes) {
        this._playNote(note.f, t0 + note.t, note.d, voice.wave);
      }
    } catch {
      // Web Audio blocked (e.g. no user gesture yet) — non-fatal, stay silent.
    }
  }

  // One note = a detuned oscillator pair (chorus) sharing an ADSR gain envelope.
  // Per-note peak is kept low so stacked notes (the `hu` triad) don't clip.
  _playNote(freq, start, dur, wave) {
    const peak = 0.09;
    const gain = this._ctx.createGain();
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(peak, start + 0.008); // attack
    gain.gain.exponentialRampToValueAtTime(peak * 0.45, start + dur * 0.5); // decay → sustain
    gain.gain.exponentialRampToValueAtTime(0.0001, start + dur); // release
    gain.connect(this._ctx.destination);
    for (const detune of [-5, 5]) {
      const osc = this._ctx.createOscillator();
      osc.type = wave;
      osc.frequency.value = freq;
      osc.detune.value = detune;
      osc.connect(gain);
      osc.start(start);
      osc.stop(start + dur + 0.02);
    }
  }
}

// One shared instance for the app; exported so tests can inspect `lastCue`.
export const audioCues = new AudioCues();
