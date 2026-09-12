"""transcribe_groove: the drums-stem onsets as x/. strings per kit pad.

Kick, snare and hat are detected independently, each in its own frequency
band of the drums stem, because drums coincide: a four-on-the-floor kick
under an offbeat hat is two sounds at one instant, and classifying a single
onset stream into one class per onset can only keep one of them. Measured on
a real breakbeat, the single-stream classifier found a kick on 1 of 16 beats
that all carried one. Bands are BANDS; a mid-band onset swamped by the low
band is the kick's body, not a snare, and is dropped (LOW_DOMINANCE). perc
pads are variants the owner places by hand; bass and melodic pads are not
transcribed.

The kit is only read for the pad numbers and slice files, so a kit extracted
from a different section of the same track is fine. Each band goes to the pad
whose slice sounds like it (pad_roles), not to the pad extract_kit named for
it; the result reports both `class` and `role` per pad.

Grid: 16 steps per bar of 24 ticks, anchored on the tracked beats. Each
onset's position is interpolated between the two beats around it (mean
interval beyond the ends), measured in beats from the downbeat, so tempo
drift across a long clip does not smear the 16ths; a clip with fewer than
two beats falls back to a fixed grid at the reported BPM. beats_per_bar from
the tracker is only reported as a hint - bars are always 4 beats, which is
the only bar the device's 384-tick pattern holds.

Bars = clip length / bar length, rounded to 1, 2 or 4 on a log scale unless
given. Onsets past the last bar, or before the downbeat, fold modulo the
pattern, where the clip bars covering a cell vote on it (CONSENSUS): each
bar votes with the power of the class's loudest hit in it, so a bar where
the class is silent abstains, a bar of faint intro ticks barely counts, and
a fill in one bar of four is dropped while the loop is kept (a 30 s clip
folded to 4 bars: 254 hits became 68, against the owner's 77). Onsets
within MIN_INTERVAL_STEPS of each other are the same hit, placed where the
loudest of them fell (a 16th grid cannot hold two, and separation
double-triggers land there).
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
from ..protocol.patterns import MAX_DURATION as enc_max_duration, MAX_VELOCITY
from .kit import DRUM_CLASSES, LOW_BAND_HZ, drum_features, kit_paths

STEPS_PER_BEAT = 4
BEATS_PER_BAR = 4
STEPS_PER_BAR = STEPS_PER_BEAT * BEATS_PER_BAR
TICKS_PER_STEP = 24
BAR_CHOICES = (1, 2, 4)
MIN_INTERVAL_STEPS = 0.6
GROUPS = ("A", "B", "C", "D")
DETECTORS = ("classify", "bands")
# Detection bands per class, and the onset-picking parameters for each.
BANDS = {"kick": (20.0, 140.0), "snare": (180.0, 2800.0), "hat": (5500.0, 16000.0)}
# Thresholds apply to each band's onset envelope after scaling to its own peak, so they mean
# the same thing whatever the band's absolute level is.
ONSET_DELTA = {"kick": 0.15, "snare": 0.15, "hat": 0.15}
ONSET_FLOOR = {"kick": 0.25, "snare": 0.15, "hat": 0.25}
ONSET_WAIT = {"kick": 3, "snare": 3, "hat": 3}
LOW_DOMINANCE = 0.10          # a mid-band onset with less than this share of the low band is the kick
HIGH_DOMINANCE = 0.15         # ... and with less than this share of the high band it is the hat
CONSENSUS = 0.5               # share of the covering bars' vote (power of their loudest hit) that keeps a cell
LEAD_IN_S = 0.05              # silence prepended so a hit at t=0 has a rise to detect
# The device stores velocity per event (1..127; a pressure recording gave 127 hard, 54..71 soft).
# Strength is 0..1 relative to the loudest onset of the class, so the loudest hit of each class
# lands at 127 and a ghost note stays a ghost rather than being flattened to accent level.
VELOCITY_FLOOR = 40
N_FFT = 2048
HOP = 256


def velocity_for(strength: float) -> int:
    """Onset strength 0..1 to a device velocity VELOCITY_FLOOR..127."""
    return VELOCITY_FLOOR + int(round((MAX_VELOCITY - VELOCITY_FLOOR) * max(0.0, min(1.0, float(strength)))))


def slice_ms(pads: list[dict], cls: str) -> float:
    """How long the kit's slice for this class is; the note length that plays it whole."""
    for pad in pads:
        if pad["class"] == cls:
            return float(pad.get("duration_ms") or 0.0)
    return 0.0


def pad_roles(pads: list[dict]) -> dict[int, str]:
    """Which drum pad plays which band, from the slice as it sounds, not from its class name.

    extract_kit names a slice by the first FEATURE_WINDOW_S of its onset; the slice it writes runs
    to the next onset and can be owned by a different drum for most of its length. Measured on
    the kit the owner played over (fixtures/p03-hand-played-2026-09-12.json): the 'kick' slice is
    a 50 ms thump under a 200 ms hat tail and the 'snare' slice is a crack over a 250 ms kick body,
    and the owner used them as hat and kick. The whole slice is what plays, so the pad with the
    most low-band energy over the whole slice takes the kick stream, the brightest of the rest
    the hat stream, the last the snare stream."""
    import numpy as np
    import soundfile as sf

    measured = []
    for pad in pads:
        audio, sr = sf.read(pad["path"], dtype="float32")
        mono = audio.mean(axis=1) if audio.ndim > 1 else audio
        power = np.square(np.abs(np.fft.rfft(mono * np.hanning(len(mono)))))
        freqs = np.fft.rfftfreq(len(mono), 1 / sr)
        total = float(power.sum()) + 1e-12
        measured.append((pad["pad"], float(power[freqs < LOW_BAND_HZ].sum() / total), float((freqs * power).sum() / total)))
    roles: dict[int, str] = {}
    for role, key in (("kick", lambda m: m[1]), ("hat", lambda m: m[2]), ("snare", lambda m: -m[2])):
        left = [m for m in measured if m[0] not in roles]
        if left:
            roles[max(left, key=key)[0]] = role
    return roles


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
    """Nearest drum pad class per onset, by z-scored centroid/flatness/low-band features.

    One onset gets one class, so coincident hits keep only the loudest character; `band_onsets`
    is the detector that can hear a kick and a hat at the same instant."""
    import numpy as np

    features = drum_features(drums_audio, sr, onsets["starts_s"])
    targets = drum_features(drums_audio, sr, [p["source_s"][0] for p in pads])
    mean, scale = features.mean(axis=0), features.std(axis=0)
    scale[scale == 0] = 1.0
    z, t = (features - mean) / scale, (targets - mean) / scale
    return [pads[int(np.argmin(np.linalg.norm(t - row, axis=1)))]["class"] for row in z]


def band_energy(audio, sr: int, at: float, low: float, high: float, window: float = 0.045) -> float:
    import numpy as np

    begin = int(at * sr)
    segment = audio[begin:begin + int(window * sr)]
    if len(segment) < 8:
        return 0.0
    magnitude = np.abs(np.fft.rfft(segment * np.hanning(len(segment))))
    freqs = np.fft.rfftfreq(len(segment), 1 / sr)
    return float(np.square(magnitude[(freqs >= low) & (freqs < high)]).sum())


def band_onsets(audio, sr: int) -> dict[str, list[tuple[float, float]]]:
    """(time, strength 0..1) per drum class, detected in that class's own band.

    Detecting per band is what lets a kick and a hat at the same instant both be heard. The
    strength is the onset envelope at the peak, scaled by the loudest peak in that band, so
    min_strength means the same thing for every class."""
    import librosa
    import numpy as np

    padded = np.concatenate([np.zeros(int(LEAD_IN_S * sr), dtype=np.float32), np.asarray(audio, dtype=np.float32)])
    spectrum = np.abs(librosa.stft(padded, n_fft=N_FFT, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=N_FFT)
    out: dict[str, list[tuple[float, float]]] = {}
    for name, (low, high) in BANDS.items():
        rows = spectrum[(freqs >= low) & (freqs < high)]
        if not rows.size:
            out[name] = []
            continue
        envelope = librosa.onset.onset_strength(S=librosa.amplitude_to_db(rows, ref=np.max), sr=sr, hop_length=HOP)
        envelope = envelope / (float(envelope.max()) or 1.0)
        frames = [f for f in librosa.onset.onset_detect(onset_envelope=envelope, sr=sr, hop_length=HOP,
                                                        backtrack=False, delta=ONSET_DELTA[name],
                                                        wait=ONSET_WAIT[name])
                  if envelope[f] >= ONSET_FLOOR[name]]
        peak = 1.0
        hits = []
        for frame in frames:
            at = float(librosa.frames_to_time(frame, sr=sr, hop_length=HOP)) - LEAD_IN_S
            if at < -LEAD_IN_S:
                continue
            if name == "snare":
                # The snare band overlaps both neighbours: the kick's body reaches up into it and
                # the hat's noise reaches down. A real snare owns its band at that instant.
                mid = band_energy(audio, sr, max(at, 0.0), *BANDS["snare"])
                bottom = band_energy(audio, sr, max(at, 0.0), *BANDS["kick"])
                top = band_energy(audio, sr, max(at, 0.0), *BANDS["hat"])
                if mid < LOW_DOMINANCE * bottom or mid < HIGH_DOMINANCE * top:
                    continue
            hits.append((max(at, 0.0), min(1.0, float(envelope[frame]) / peak)))
        out[name] = hits
    return out


def transcribe_groove(clip: str | Path, kit: str | Path | None = None, bars: int | None = None,
                      group: str = "A", index: int = 1, downbeat_s: float | None = None,
                      separation: str = "auto", beat_tracker: str = "auto",
                      min_strength: float = 0.0, detector: str = "classify") -> dict:
    if detector not in DETECTORS:
        raise InvalidReference(f"detector must be one of {DETECTORS}", observed=detector,
                               next_step="'bands' detects each class in its own frequency band.")
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
    # "classify" matches each onset to a pad's own exemplar, so its class is that pad. "bands"
    # hears the kick, snare and hat of the music, which go to the pads that sound like them.
    roles = pad_roles(pads) if detector == "bands" else {p["pad"]: p["class"] for p in pads}
    pad_of = {roles[p["pad"]]: p["pad"] for p in pads}
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

    mono = audio.mean(axis=1) if audio.ndim > 1 else audio
    if detector == "bands":
        detected = band_onsets(mono, sr)
    else:
        # "classify" reads each pad's exemplar out of THIS clip at a time stored relative to the
        # kit's own clip, so a foreign kit collapses every onset onto whichever exemplar still
        # lands inside the audio. "bands" only needs the class -> pad numbers.
        outside = [p["class"] for p in pads if not 0.0 <= p["source_s"][0] < float(analysis["duration_s"])]
        if outside:
            raise InvalidReference("Kit was extracted from a different clip", observed=outside,
                                   expected=f"exemplar times inside 0..{float(analysis['duration_s']):.2f}s",
                                   next_step="Run extract_kit on this clip, or pass detector='bands'.")
        classes = assign_classes(audio, sr, drums, pads)
        strengths = drums.get("strength") or [1.0] * len(drums["onsets_s"])
        detected = {cls: [] for cls in pad_of}
        for t, cls, strength in zip(drums["onsets_s"], classes, strengths):
            detected.setdefault(cls, []).append((t, strength))
    rows = {cls: ["."] * total_steps for cls in pad_of}
    hits = {cls: 0 for cls in pad_of}
    errors: list[float] = []
    events: list[dict] = []
    # The device holds a note length per event; its own recordings vary it by class (a hand-played
    # bar here: kick median 18 ticks, snare 46, hat 94). One step for every hit chokes long sounds
    # and stretches short ones, so each class gets the length of the slice the kit cut for it.
    durations = {cls: max(1, min(enc_max_duration, int(round(slice_ms(pads, cls) / (60_000 / bpm / STEPS_PER_BEAT)
                                                            * TICKS_PER_STEP)))) for cls in pad_of}
    dropped = folded = before = weak = 0
    detected_total = sum(len(detected.get(cls, ())) for cls in pad_of)
    # "classify" produces one stream of onsets, so a retrigger is a retrigger whatever class it
    # was given; "bands" produces one stream per class, where a kick and a hat at the same
    # instant are two real hits and only a repeat within the same class is a retrigger.
    scopes = {cls: cls for cls in pad_of} if detector == "bands" else {cls: "" for cls in pad_of}
    placements = sorted(((t, cls, strength) for cls in pad_of for t, strength in detected.get(cls, ())),
                        key=lambda item: item[0])
    # Onsets within MIN_INTERVAL_STEPS of each other in one scope are one hit, and the loudest of
    # them is where it is: a faint pre-echo a third of a step before a backbeat must not be the
    # onset that stands for it.
    merged: list[tuple[float, float, str, float]] = []      # (steps, error, cls, strength)
    last: dict[str, int] = {}                                # scope -> index into merged
    for t, cls, strength in placements:
        # min_strength thins the transcription; the hits that stay keep their level below.
        if strength < min_strength:
            weak += 1
            continue
        steps, error = grid.step(t)
        scope = scopes[cls]
        if scope in last and steps - merged[last[scope]][0] < MIN_INTERVAL_STEPS:
            dropped += 1
            if strength > merged[last[scope]][3]:
                merged[last[scope]] = (steps, error, cls, strength)
            continue
        last[scope] = len(merged)
        merged.append((steps, error, cls, strength))
    # A clip longer than the pattern folds onto it. The union of every fold would keep each fill
    # and variation from every bar of the clip; the pattern keeps a cell only when the folds that
    # cover it mostly fire there (the loop, not the fills), at the median tick offset and
    # strength of those hits.
    cells: dict[tuple[str, int], list[tuple[float, float, int]]] = {}
    loudest: dict[str, dict[int, float]] = {cls: {} for cls in pad_of}   # per class, per clip bar
    for steps, error, cls, strength in merged:
        nearest = round(steps)
        if nearest < 0:
            before += 1
        if not 0 <= nearest < total_steps:
            folded += 1
        errors.append(abs(error) * 1000)
        bar = nearest // STEPS_PER_BAR
        cells.setdefault((cls, nearest % total_steps), []).append((steps - nearest, strength, bar))
        loudest[cls][bar] = max(loudest[cls].get(bar, 0.0), strength)
    minority = 0
    kept: list[tuple[int, str, float, float]] = []
    # The clip's extent in steps: a clip bar covers a cell only if that cell's step lies inside
    # the clip, so the beat before the downbeat and the tail past the last full bar vote only
    # on the cells they actually hold.
    first_step, last_step = grid.step(0.0)[0], grid.step(float(analysis["duration_s"]))[0]
    clip_bar_range = range(math.floor(first_step / STEPS_PER_BAR), math.ceil(last_step / STEPS_PER_BAR))
    for (cls, cell), found in cells.items():
        # Each covering clip bar votes with the power of the class's loudest hit in it: a bar of
        # faint intro ticks cannot outvote a backbeat, a bar where the class is silent does not
        # vote, and within a bar a soft offbeat hat counts as much as the accented one.
        covering = sum(loudest[cls].get(bar, 0.0) ** 2 for bar in clip_bar_range
                       if bar % bars == cell // STEPS_PER_BAR
                       and first_step <= bar * STEPS_PER_BAR + cell % STEPS_PER_BAR < last_step)
        votes = sum(loudest[cls][bar] ** 2 for bar in {bar for _, _, bar in found})
        if votes < CONSENSUS * covering - 1e-9:
            minority += 1
            continue
        offsets = sorted(offset for offset, _, _ in found)
        levels = sorted(level for _, level, _ in found)
        kept.append((cell, cls, offsets[len(offsets) // 2], levels[len(levels) // 2]))
    # "bands" scales strength to each band's own peak, "classify" to the whole stem's; velocity
    # is relative to the loudest cell of the class either way.
    peak = {cls: max((level for _, c, _, level in kept if c == cls), default=1.0) or 1.0 for cls in pad_of}
    for cell, cls, offset, level in sorted(kept):
        rows[cls][cell] = "x"
        tick = int(round((cell + offset) * TICKS_PER_STEP)) % (total_steps * TICKS_PER_STEP)
        events.append({"pad": pad_of[cls], "tick": tick, "duration": durations[cls],
                       "velocity": velocity_for(level / peak[cls])})
        hits[cls] += 1

    pattern = {"group": group, "index": index, "bars": bars,
               "steps": {str(pad_of[cls]): "".join(row) for cls, row in rows.items()}}
    # Same hits, placed at the tick each onset actually fell on: generate_ppak takes either.
    tick_pattern = {"group": group, "index": index, "bars": bars,
                    "events": sorted(events, key=lambda e: (e["tick"], e["pad"]))}
    record: dict[str, Any] = {
        "clip": analysis["clip"], "kit": kit_record["kit"], "pattern": pattern,
        "tick_pattern": tick_pattern,
        "pads": [{"pad": p["pad"], "class": p["class"], "role": roles[p["pad"]], "hits": hits[roles[p["pad"]]]}
                 for p in pads],
        "bpm": bpm, "downbeat_s": round(origin_s, 4), "bars": bars, "clip_bars": round(clip_bars, 2),
        "steps_per_bar": STEPS_PER_BAR, "ticks_per_step": TICKS_PER_STEP,
        "grid": grid.mode, "beats_per_bar_hint": analysis["downbeat"]["beats_per_bar"],
        "quantization": {"mean_ms": round(sum(errors) / len(errors), 1) if errors else None,
                         "max_ms": round(max(errors), 1) if errors else None,
                         "onsets": detected_total, "detector": detector, "placed": len(errors), "folded": folded,
                         "below_min_strength": weak,
                         "dropped_retriggers": dropped, "before_downbeat": before,
                         "folds": math.ceil(max(clip_bars, 1.0) / bars), "minority_cells": minority},
        "beat_tracker": analysis["beat_tracker"], "separation": analysis["separation"], "probable": True,
    }
    path = groove_path(clip)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n")
    return {**record, "groove": str(path), "status": "transcribed"}
