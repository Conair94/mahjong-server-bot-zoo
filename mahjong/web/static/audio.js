// FB-06: Web Audio cues. Synthesized (no binary assets, in keeping with the
// build-free client) but voiced richly — detuned-oscillator chorus + ADSR
// envelopes, brightening timbre, and a triad flourish for `hu` — so a tile draw
// gives a soft blip and a claim opportunity an escalating, satisfying tone:
// chi < peng < gang < hu. Respects a mute toggle (Settings). The cue-selection
// functions are pure (and unit-tested); `AudioCues.play` is the only
// side-effecting part and no-ops silently when muted or when Web Audio is
// unavailable.

const CLAIM_PRIORITY = ["HU", "GANG", "PENG", "CHI"];

// Which cue (if any) an inbound record EVENT should play for the local seat.
// Only the local human's own draw blips — opponents' draws would be noise.
export function cueForEvent(event, ownSeat) {
  if (!event) return null;
  if (event.event === "DRAW" && event.seat === ownSeat && event.tile) return "draw";
  return null;
}

// The highest-intensity claim a CLAIM_WINDOW prompt offers the local human, or
// null (PASS-only / non-claim prompts make no sound).
export function cueForPrompt(prompt) {
  if (!prompt || prompt.phase !== "CLAIM_WINDOW") return null;
  const types = new Set((prompt.legal_actions ?? []).map((a) => a.type));
  for (const t of CLAIM_PRIORITY) {
    if (types.has(t)) return t.toLowerCase();
  }
  return null;
}

// Per-cue "score": each cue is a waveform + a list of notes (freq Hz, start
// offset s, duration s). Richer than a single sine — every note is voiced by a
// detuned oscillator pair (chorus) under an ADSR envelope, the timbre brightens
// with intensity (triangle → sawtooth), and `hu` is a rising arpeggio into a
// triad-with-octave-shimmer flourish. Still synthesized: no binary assets.
const G5 = 783.99, B5 = 987.77, D6 = 1174.66, G6 = 1567.98;
const VOICES = {
  draw: { wave: "triangle", notes: [{ f: 440, t: 0, d: 0.09 }] },
  chi: { wave: "triangle", notes: [{ f: 523.25, t: 0, d: 0.13 }] },
  peng: {
    wave: "sawtooth",
    notes: [{ f: 587.33, t: 0, d: 0.07 }, { f: 659.25, t: 0.06, d: 0.16 }],
  },
  gang: {
    wave: "sawtooth",
    notes: [
      { f: 659.25, t: 0, d: 0.07 },
      { f: 783.99, t: 0.06, d: 0.07 },
      { f: 880.0, t: 0.12, d: 0.2 },
    ],
  },
  hu: {
    wave: "sawtooth",
    notes: [
      // rising arpeggio …
      { f: G5, t: 0.0, d: 0.1 },
      { f: B5, t: 0.08, d: 0.1 },
      { f: D6, t: 0.16, d: 0.1 },
      // … resolving into a held triad + an octave shimmer on top.
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

  // Play `cue` unless muted/unknown. Records `lastCue` only when it actually
  // plays, so a muted session leaves `lastCue` null (asserted in tests).
  play(cue) {
    if (this.muted || !cue || !(cue in VOICES)) return;
    this.lastCue = cue;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      this._ctx = this._ctx || new Ctx();
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
