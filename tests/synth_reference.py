"""Deterministic synthesized reference: a few bars of kick/snare/hat/bass/melody.

Rendered at test time from a known BPM, pattern and note list so analysis and
kit extraction can be checked against ground truth. Nothing here is sampled
from any recording. Stems are rendered separately and summed into the mix, so
a test can stand in for source separation with the true stems.
"""

from __future__ import annotations

import numpy as np
import soundfile as sf

SR = 44100
BPM = 120.0
BARS = 2
STEPS_PER_BAR = 16
PATTERN = {
    "kick":  "x...x...x...x...",
    "snare": "....x.......x...",
    "hat":   "x.x.x.x.x.x.x.x.",
}
BASS_STEPS = [(0, 33), (4, 33), (8, 40), (12, 33)]          # A1 A1 E2 A1 per bar
MELODY_STEPS = [(0, 69), (6, 72), (8, 76), (14, 69)]        # A4 C5 E5 A4 per bar
KEY = ("A", "minor")


def _decay(seconds: float, tau: float) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return np.exp(-t / tau).astype(np.float32)


def kick() -> np.ndarray:
    n = int(SR * 0.30)
    t = np.arange(n) / SR
    freq = 50 + 110 * np.exp(-t / 0.03)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return (0.9 * np.sin(phase) * _decay(0.30, 0.09)).astype(np.float32)


def snare(rng: np.random.Generator) -> np.ndarray:
    n = int(SR * 0.20)
    t = np.arange(n) / SR
    tone = 0.4 * np.sin(2 * np.pi * 185 * t) * _decay(0.20, 0.04)
    noise = 0.5 * rng.standard_normal(n) * _decay(0.20, 0.06)
    return (tone + noise).astype(np.float32)


def hat(rng: np.random.Generator) -> np.ndarray:
    n = int(SR * 0.06)
    noise = rng.standard_normal(n)
    # Crude high-pass: first difference twice, then decay.
    hp = np.diff(np.diff(noise, prepend=0.0), prepend=0.0)
    hp /= np.max(np.abs(hp)) or 1.0
    return (0.35 * hp * _decay(0.06, 0.015)).astype(np.float32)


def note(midi: int, seconds: float, tau: float, harmonics=(1.0, 0.3, 0.1)) -> np.ndarray:
    f = 440.0 * 2 ** ((midi - 69) / 12)
    t = np.arange(int(SR * seconds)) / SR
    y = sum(a * np.sin(2 * np.pi * f * (k + 1) * t) for k, a in enumerate(harmonics))
    y = y / max(1.0, sum(harmonics))
    return (0.6 * y * _decay(seconds, tau)).astype(np.float32)


def _place(canvas: np.ndarray, start: int, sound: np.ndarray) -> None:
    end = min(len(canvas), start + len(sound))
    canvas[start:end] += sound[: end - start]


def render(bpm: float = BPM, bars: int = BARS, seed: int = 0) -> dict:
    rng = np.random.default_rng(seed)
    step = 60.0 / bpm / 4
    total = int(SR * step * STEPS_PER_BAR * bars) + int(SR * 0.5)
    drums, bass, other = (np.zeros(total, np.float32) for _ in range(3))
    sounds = {"kick": kick(), "snare": snare(rng), "hat": hat(rng)}
    events = []
    for bar in range(bars):
        for name, row in PATTERN.items():
            for i, ch in enumerate(row):
                if ch == "x":
                    s = (bar * STEPS_PER_BAR + i) * step
                    _place(drums, int(s * SR), sounds[name])
                    events.append((name, s))
        for i, midi in BASS_STEPS:
            s = (bar * STEPS_PER_BAR + i) * step
            _place(bass, int(s * SR), note(midi, step * 4, 0.25, harmonics=(1.0, 0.4)))
        for i, midi in MELODY_STEPS:
            s = (bar * STEPS_PER_BAR + i) * step
            _place(other, int(s * SR), note(midi, step * 2, 0.15))
    mix = drums + bass + other
    peak = float(np.max(np.abs(mix))) or 1.0
    scale = 0.9 / peak
    return {
        "sr": SR, "bpm": bpm, "bars": bars, "step_s": step, "pattern": PATTERN, "events": events,
        "bass_notes": sorted({m for _, m in BASS_STEPS}), "melody_notes": sorted({m for _, m in MELODY_STEPS}),
        "key": KEY,
        "mix": mix * scale, "stems": {"drums": drums * scale, "bass": bass * scale, "other": other * scale},
    }


def write(folder, bpm: float = BPM, bars: int = BARS, seed: int = 0) -> dict:
    """Write clip.wav (stereo, as fetch_reference does) and return the ground truth with paths."""
    truth = render(bpm, bars, seed)
    folder.mkdir(parents=True, exist_ok=True)
    clip = folder / "clip.wav"
    stereo = np.stack([truth["mix"], truth["mix"]], axis=1)
    sf.write(str(clip), stereo, SR, subtype="PCM_16")
    truth["clip"] = clip
    return truth
