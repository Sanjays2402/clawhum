"""Generate synthetic library fixtures for local dogfood."""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import soundfile as sf

NOTES = {
    "twinkle":   [262, 262, 392, 392, 440, 440, 392],
    "mary":      [330, 294, 262, 294, 330, 330, 330],
    "ode":       [330, 330, 349, 392, 392, 349, 330, 294],
    "happybday": [262, 262, 294, 262, 349, 330],
    "joyworld":  [523, 494, 440, 392, 349, 330, 294, 262],
}

def synth(freqs, sr=48000, note_s=0.4):
    t = np.linspace(0, note_s, int(note_s * sr), endpoint=False)
    out = []
    for f in freqs:
        out.append((0.4 * np.sin(2 * np.pi * f * t)).astype(np.float32))
    return np.concatenate(out)

def main(out: str = "./data/audio"):
    root = Path(out)
    root.mkdir(parents=True, exist_ok=True)
    for name, freqs in NOTES.items():
        sf.write(str(root / f"{name}.wav"), synth(freqs), 48000)
    print(f"wrote {len(NOTES)} files to {root}")

if __name__ == "__main__":
    main(*sys.argv[1:])
