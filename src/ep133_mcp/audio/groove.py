"""transcribe_groove: the drums-stem onsets as x/. strings per kit pad.

Each onset on the drums stem is assigned to the nearest of the kit's kick,
snare and hat pads by the same three features extract_kit clustered on
(perc pads are variants the owner places by hand; bass and melodic pads are
not transcribed). A single onset gets a single pad: when hits coincide, the
slice extract_kit cut at that class is the coincident sound, so one pad is
what reproduces it.

Grid: 16 steps per bar of 24 ticks, anchored on the tracked beats. Each
onset's position is interpolated between the two beats around it (mean
interval beyond the ends), measured in beats from the downbeat, so tempo
drift across a long clip does not smear the 16ths; a clip with fewer than
two beats falls back to a fixed grid at the reported BPM. beats_per_bar from
the tracker is only reported as a hint - bars are always 4 beats, which is
the only bar the device's 384-tick pattern holds.

Bars = clip length / bar length, rounded to 1, 2 or 4 on a log scale unless
given. Onsets past the last bar, or before the downbeat, fold modulo the
pattern; an onset
within MIN_INTERVAL_STEPS of the previous accepted onset is the same hit
(a 16th grid cannot hold two, and separation double-triggers land there).
The quantization error is the mean and maximum |onset - grid| in ms over
the accepted onsets. Everything here is probable and the result says so.

The pattern is written as kit/groove.json in the shape generate_ppak takes:
{group, index, bars, steps: {pad: "x..."}}.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .analysis import analyze_reference
from .errors import InvalidReference
from .kit import DRUM_CLASSES, drum_features, kit_paths

STEPS_PER_BEAT = 4
BEATS_PER_BAR = 4
STEPS_PER_BAR = STEPS_PER_BEAT * BEATS_PER_BAR
TICKS_PER_STEP = 24
BAR_CHOICES = (1, 2, 4)
MIN_INTERVAL_STEPS = 0.6
GROUPS = ("A", "B", "C", "D")


def groove_path(clip: str | Path) -> Path:
    return kit_paths(clip)[0] / "groove.json"


def load_kit(clip: str | Path, kit: str | Path | None) -> dict:
    path = Path(kit).expanduser() if kit else kit_paths(clip)[1]
    if not path.is_file():
        raise InvalidReference("Kit not found", observed=str(path), expected="kit.json from extract_kit",
                               next_step="Run extract_kit on this clip first, or pass its kit.json path.")
    try:
        record = json.loads(path.read_text())
        slices = record["slices"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise InvalidReference("Kit is not readable", observed=str(e)[:200], expected="kit.json from extract_kit",
                               next_step="Re-run extract_kit.") from e
    if not any(s["class"] in DRUM_CLASSES for s in slices):
        raise InvalidReference("Kit has no kick, snare or hat pad", observed=[s["class"] for s in slices],
                               next_step="Re-run extract_kit with kick, snare or hat in want.")
    return record


def choose_bars(clip_bars: float) -> int:
    return min(BAR_CHOICES, key=lambda b: abs(math.log(max(clip_bars, 1e-6) / b)))


class BeatGrid:
    """Time <-> beat position, interpolated between tracked beats."""

    def __init__(self, beats: list[float], bpm: float, downbeat_s: float):
        import numpy as np

        if len(beats) >= 2:
            self.beats = np.asarray(beats, dtype=np.float64)
            self.interval = float(np.mean(np.diff(self.beats)))
            self.mode = "beats"
        else:
            if bpm <= 0:
                raise InvalidReference("No tempo to build a grid from", observed=bpm,
                                       next_step="Re-run analyze_reference; the clip may be too short or silent.")
            self.interval = 60.0 / bpm
            self.beats = np.asarray([0.0, self.interval])
            self.mode = "bpm"
        self.origin = self.position(downbeat_s)

    def position(self, t: float) -> float:
        """Beats from the first tracked beat (fractional, extrapolated outside the tracked span)."""
        import numpy as np

        b = self.beats
        if t < b[0]:
            return (t - b[0]) / self.interval
        if t >= b[-1]:
            return len(b) - 1 + (t - b[-1]) / self.interval
        i = int(np.searchsorted(b, t, side="right") - 1)
        return i + (t - b[i]) / (b[i + 1] - b[i])

    def time(self, position: float) -> float:
        b = self.beats
        if position < 0:
            return float(b[0] + position * self.interval)
        if position >= len(b) - 1:
            return float(b[-1] + (position - (len(b) - 1)) * self.interval)
        i = int(position)
        return float(b[i] + (position - i) * (b[i + 1] - b[i]))

    def step(self, t: float) -> tuple[float, float]:
        """(fractional 16th from the downbeat, error in seconds to the nearest 16th)."""
        steps = (self.position(t) - self.origin) * STEPS_PER_BEAT
        nearest = round(steps)
        return steps, t - self.time(self.origin + nearest / STEPS_PER_BEAT)


def assign_classes(drums_audio, sr: int, onsets: dict, pads: list[dict]) -> list[str]:
    """Nearest drum pad class per onset, by z-scored centroid/flatness/low-band features."""
    import numpy as np

    features = drum_features(drums_audio, sr, onsets["starts_s"])
    targets = drum_features(drums_audio, sr, [p["source_s"][0] for p in pads])
    mean, scale = features.mean(axis=0), features.std(axis=0)
    scale[scale == 0] = 1.0
    z, t = (features - mean) / scale, (targets - mean) / scale
    return [pads[int(np.argmin(np.linalg.norm(t - row, axis=1)))]["class"] for row in z]


def transcribe_groove(clip: str | Path, kit: str | Path | None = None, bars: int | None = None,
                      group: str = "A", index: int = 1, downbeat_s: float | None = None,
                      separation: str = "auto", beat_tracker: str = "auto",
                      min_strength: float = 0.0) -> dict:
    if not isinstance(min_strength, (int, float)) or isinstance(min_strength, bool) or not 0.0 <= min_strength <= 1.0:
        raise InvalidReference("min_strength must be a number from 0 to 1", observed=min_strength,
                               next_step="Omit it to keep every onset, or raise it to keep only accents.")
    if bars is not None and bars not in BAR_CHOICES:
        raise InvalidReference("bars must be 1, 2 or 4", observed=bars, next_step="Omit bars to fit the clip length.")
    if group not in GROUPS or not isinstance(index, int) or index < 1:
        raise InvalidReference("Pattern target must be group A..D and index >= 1", observed={"group": group, "index": index},
                               next_step="Pass the pattern slot generate_ppak should write.")
    analysis = analyze_reference(clip, separation, beat_tracker)
    kit_record = load_kit(clip, kit)
    pads = [s for s in kit_record["slices"] if s["class"] in DRUM_CLASSES]
    pad_of = {p["class"]: p["pad"] for p in pads}
    # Exemplars are timestamps into the kit's own clip; a kit from a different clip silently
    # collapses every onset onto whichever exemplar still lands inside this audio.
    outside = [p["class"] for p in pads if not 0.0 <= p["source_s"][0] < float(analysis["duration_s"])]
    if outside:
        raise InvalidReference("Kit was extracted from a different clip", observed=outside,
                               expected=f"exemplar times inside 0..{float(analysis['duration_s']):.2f}s",
                               next_step="Run extract_kit on this clip and pass that kit.json.")
    drums = analysis["stems"].get("drums")
    if not drums or not drums["onsets_s"]:
        raise InvalidReference("No drum onsets to transcribe", observed=drums and drums.get("level_dbfs"),
                               next_step="Pick a section with audible drums, or check the separation method.")
    import soundfile as sf

    audio, sr = sf.read(drums["path"], dtype="float32")
    beats = analysis["bpm"]["beats_s"]
    bpm = float(analysis["bpm"]["value"])
    origin_s = float(analysis["downbeat"]["value_s"] if downbeat_s is None else downbeat_s)
    grid = BeatGrid(beats, bpm, origin_s)
    bar_s = BEATS_PER_BAR * grid.interval
    clip_bars = (float(analysis["duration_s"]) - origin_s) / bar_s
    bars = bars or choose_bars(clip_bars)
    total_steps = bars * STEPS_PER_BAR

    classes = assign_classes(audio, sr, drums, pads)
    rows = {cls: ["."] * total_steps for cls in pad_of}
    hits = {cls: 0 for cls in pad_of}
    errors: list[float] = []
    dropped = folded = before = weak = 0
    last_step: float | None = None
    strengths = drums.get("strength") or [1.0] * len(drums["onsets_s"])
    for t, cls, strength in zip(drums["onsets_s"], classes, strengths):
        # Every hit plays at one velocity, so ghost notes land as loud as accents; dropping the
        # quiet ones keeps the skeleton of the groove instead of a uniform wall.
        if strength < min_strength:
            weak += 1
            continue
        steps, error = grid.step(t)
        if last_step is not None and steps - last_step < MIN_INTERVAL_STEPS:
            dropped += 1
            continue
        last_step = steps
        nearest = round(steps)
        if nearest < 0:
            before += 1
        if not 0 <= nearest < total_steps:
            folded += 1
        rows[cls][nearest % total_steps] = "x"
        hits[cls] += 1
        errors.append(abs(error) * 1000)

    pattern = {"group": group, "index": index, "bars": bars,
               "steps": {str(pad_of[cls]): "".join(row) for cls, row in rows.items()}}
    record: dict[str, Any] = {
        "clip": analysis["clip"], "kit": kit_record["kit"], "pattern": pattern,
        "pads": [{"pad": pad_of[cls], "class": cls, "hits": hits[cls]} for cls in pad_of],
        "bpm": bpm, "downbeat_s": round(origin_s, 4), "bars": bars, "clip_bars": round(clip_bars, 2),
        "steps_per_bar": STEPS_PER_BAR, "ticks_per_step": TICKS_PER_STEP,
        "grid": grid.mode, "beats_per_bar_hint": analysis["downbeat"]["beats_per_bar"],
        "quantization": {"mean_ms": round(sum(errors) / len(errors), 1) if errors else None,
                         "max_ms": round(max(errors), 1) if errors else None,
                         "onsets": len(drums["onsets_s"]), "placed": len(errors), "folded": folded,
                         "below_min_strength": weak,
                         "dropped_retriggers": dropped, "before_downbeat": before},
        "beat_tracker": analysis["beat_tracker"], "separation": analysis["separation"], "probable": True,
    }
    path = groove_path(clip)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n")
    return {**record, "groove": str(path), "status": "transcribed"}
