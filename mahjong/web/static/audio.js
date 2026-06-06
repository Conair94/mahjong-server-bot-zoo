// FB-06: lightweight Web Audio cues. Synthesized tones (no binary assets, in
// keeping with the build-free client) so a tile draw gives a soft blip and a
// claim opportunity an escalating tone — chi < peng < gang < hu — to build the
// moment. Respects a mute toggle (Settings). The cue-selection functions are pure
// (and unit-tested); `AudioCues.play` is the only side-effecting part and no-ops
// silently when muted or when Web Audio is unavailable.

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

// Frequency (Hz) + duration (s) per cue — ascending pitch/intensity; `hu` is a
// two-note flourish.
const VOICES = {
  draw: [[440, 0.06]],
  chi: [[523, 0.1]],
  peng: [[659, 0.12]],
  gang: [[784, 0.14]],
  hu: [[784, 0.1], [1047, 0.2]],
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
      let t = this._ctx.currentTime;
      for (const [freq, dur] of VOICES[cue]) {
        this._beep(freq, t, dur);
        t += dur;
      }
    } catch {
      // Web Audio blocked (e.g. no user gesture yet) — non-fatal, stay silent.
    }
  }

  _beep(freq, start, dur) {
    const osc = this._ctx.createOscillator();
    const gain = this._ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.15, start + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + dur);
    osc.connect(gain).connect(this._ctx.destination);
    osc.start(start);
    osc.stop(start + dur);
  }
}

// One shared instance for the app; exported so tests can inspect `lastCue`.
export const audioCues = new AudioCues();
