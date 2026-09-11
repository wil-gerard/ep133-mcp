"""extract_kit: cut up to 12 one-shots from an analyzed clip's stems.

Drums: every onset on the drums stem gets three features over its first
FEATURE_WINDOW_S (spectral centroid, spectral flatness, low-band energy
ratio), z-scored and clustered with k-means (k=3). Clusters are named by
centroid order: lowest = kick, middle = snare, highest = hat. The exemplar per
class is the onset with the highest onset strength weighted by its gap to the
next onset, so the slice is as clean as the clip allows. `perc` slots take the
member of any cluster that is farthest in feature space from its exemplar,
when that distance is large enough to be a different sound (open vs closed
hat, rim vs snare); otherwise they stay unfilled and say so.

Bass and melodic: pyin over each onset on the bass / other stem, up to
PITCH_WINDOW_S or the next onset, so a coincident drum hit at the attack does
not own the whole window. An onset counts as a note only when pyin is reasonably sure
it is voiced; frames are weighted by voiced probability so a kick tail leaking
into the bass stem does not become a note. Distinct MIDI notes are ranked by
their total support and the strongest onset of each is the exemplar.

Every slice: trimmed to its backtracked start, ended at the next onset on the
same stem or MAX_SLICE_S, peak-normalised, 5 ms fade-out, resampled to
46875 Hz mono 16-bit with soxr through librosa, written as
kit/<pad>_<class>.wav plus kit.json. Each written file is then read back with
the install preflight's own reader, so the bytes reported here are exactly the
bytes install_kit will count against free memory.

All class labels and notes are probable, not verified; the result says so.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from ..safety.preflight import RATE as DEVICE_RATE, read_sample
from .analysis import analyze_reference
from .errors import InvalidReference

log = logging.getLogger(__name__)

CLASSES = ("kick", "snare", "hat", "perc", "bass", "melodic")
DRUM_CLASSES = ("kick", "snare", "hat")
DEFAULT_WANT = ("kick", "snare", "hat", "perc", "perc", "perc",
                "bass", "bass", "bass", "melodic", "melodic", "melodic")
MAX_PADS = 12
MAX_SLICE_S = 1.0
MIN_SLICE_S = 0.03
FADE_S = 0.005
FEATURE_WINDOW_S = 0.05
PITCH_WINDOW_S = 0.5          # pyin sees this much of a note, cut at the next onset but never under PITCH_MIN_S
PITCH_MIN_S = 0.2
LOW_BAND_HZ = 150.0
PERC_MIN_DISTANCE = 1.5       # z-scored feature distance before a cluster member counts as a different sound
MIN_VOICED_PROBABILITY = 0.3  # pyin's best frame must be at least this sure before an onset yields a note
PEAK = 0.9
PITCH_RANGES = {"bass": ("C1", "C4"), "melodic": ("C2", "C7")}
STEM_FOR = {"kick": "drums", "snare": "drums", "hat": "drums", "perc": "drums", "bass": "bass", "melodic": "other"}
KMEANS_RESTARTS = 5


def kit_paths(clip: str | Path) -> tuple[Path, Path]:
    folder = Path(clip).expanduser().parent / "kit"
    return folder, folder / "kit.json"


def _validate_want(want) -> list[str]:
    if want is None:
        return list(DEFAULT_WANT)
    if isinstance(want, str):
        want = [w.strip() for w in want.split(",") if w.strip()]
    if not isinstance(want, (list, tuple)) or not 1 <= len(want) <= MAX_PADS:
        raise InvalidReference("want must list 1..12 classes", observed=want, expected=", ".join(CLASSES),
                               next_step="Omit want for the default kick, snare, hat, perc×3, bass×3, melodic×3.")
    unknown = [w for w in want if w not in CLASSES]
    if unknown:
        raise InvalidReference("Unknown kit class", observed=unknown, expected=", ".join(CLASSES),
                               next_step="Use only the listed class names.")
    return list(want)


# --- drum features and clustering ------------------------------------------------

def drum_features(y, sr: int, starts: list[float]):
    """(n, 3) array: spectral centroid Hz, spectral flatness, low-band energy ratio per onset."""
    import numpy as np

    window = int(sr * FEATURE_WINDOW_S)
    taper = np.hanning(window)
    freqs = np.fft.rfftfreq(window, 1 / sr)
    rows = []
    for start in starts:
        begin = int(start * sr)
        segment = y[begin:begin + window]
        segment = np.pad(segment, (0, window - len(segment)))
        magnitude = np.abs(np.fft.rfft(segment * taper))
        power = np.square(magnitude)
        total = float(power.sum()) + 1e-12
        centroid = float((freqs * power).sum() / total)
        flatness = float(np.exp(np.mean(np.log(magnitude + 1e-12))) / (magnitude.mean() + 1e-12))
        low = float(power[freqs < LOW_BAND_HZ].sum() / total)
        rows.append((centroid, flatness, low))
    return np.asarray(rows, dtype=np.float64).reshape(-1, 3)


def _zscore(features):
    import numpy as np

    scale = features.std(axis=0)
    scale[scale == 0] = 1.0
    return (features - features.mean(axis=0)) / scale


def cluster_drums(features) -> dict[str, list[int]]:
    """Onset indices per drum class, named by ascending mean spectral centroid."""
    import numpy as np
    from scipy.cluster.vq import kmeans2

    n = len(features)
    if n == 0:
        return {}
    z = _zscore(features)
    k = min(len(DRUM_CLASSES), len(np.unique(z.round(6), axis=0)))
    if k == 1:
        labels = np.zeros(n, dtype=int)
    else:
        best = None
        for restart in range(KMEANS_RESTARTS):
            centroids, candidate = kmeans2(z, k, minit="++", seed=restart)
            distortion = float(np.sum(np.square(z - centroids[candidate])))
            if best is None or distortion < best[0]:
                best = (distortion, candidate)
        labels = best[1]
    present = [label for label in range(k) if np.any(labels == label)]
    order = sorted(present, key=lambda label: float(features[labels == label, 0].mean()))
    names = DRUM_CLASSES if len(order) == 3 else ("kick", "hat")[:len(order)] if len(order) == 2 else ("kick",)
    return {name: [int(i) for i in np.flatnonzero(labels == label)] for name, label in zip(names, order)}


def _gaps(onsets: list[float], duration: float):
    import numpy as np

    times = np.asarray(onsets + [duration], dtype=np.float64)
    return np.diff(times)


def pick_exemplar(members: list[int], strength: list[float], gaps) -> int:
    """Highest onset strength, discounted when the next onset would cut the slice short."""
    return max(members, key=lambda i: (strength[i] * min(float(gaps[i]), MAX_SLICE_S) / MAX_SLICE_S, strength[i]))


def perc_candidates(features, clusters: dict[str, list[int]], exemplars: dict[str, int]) -> list[tuple[float, int, str]]:
    """Cluster members that sound different from their class exemplar, most different first."""
    import numpy as np

    z = _zscore(features)
    out = []
    for name, members in clusters.items():
        if name not in exemplars:
            continue
        reference = z[exemplars[name]]
        for i in members:
            distance = float(np.linalg.norm(z[i] - reference))
            if distance >= PERC_MIN_DISTANCE:
                out.append((distance, i, name))
    out.sort(key=lambda item: -item[0])
    return out


# --- pitched onsets ------------------------------------------------------------------

def pitched_onsets(y, sr: int, starts: list[float], gaps, note_range: tuple[str, str]) -> list[dict]:
    """Per onset: {index, midi, confidence} for onsets pyin is reasonably sure are voiced."""
    import librosa
    import numpy as np

    fmin, fmax = (float(librosa.note_to_hz(n)) for n in note_range)
    frame_length = int(2 ** math.ceil(math.log2(2 * sr / fmin)))
    out = []
    for i, start in enumerate(starts):
        begin = int(start * sr)
        window = int(sr * min(PITCH_WINDOW_S, max(PITCH_MIN_S, float(gaps[i]))))
        segment = y[begin:begin + window]
        if len(segment) < frame_length:
            segment = np.pad(segment, (0, frame_length - len(segment)))
        f0, _, probability = librosa.pyin(segment, fmin=fmin, fmax=fmax, sr=sr, frame_length=frame_length)
        sure = probability >= MIN_VOICED_PROBABILITY
        if not np.any(sure & ~np.isnan(f0)):
            continue
        midi = np.rint(librosa.hz_to_midi(f0[sure & ~np.isnan(f0)])).astype(int)
        weights = probability[sure & ~np.isnan(f0)]
        # Weighted mode, not mean: a window that runs into the next note must not average the two.
        support = {int(n): float(weights[midi == n].sum()) for n in np.unique(midi)}
        note = max(support, key=support.get)
        out.append({"index": i, "midi": note,
                    "confidence": round(float(probability[sure & ~np.isnan(f0)][midi == note].max()), 3)})
    return out


def pick_notes(notes: list[dict], strength: list[float], count: int) -> list[dict]:
    """Distinct MIDI notes ranked by total support; each with its strongest onset."""
    support: dict[int, float] = {}
    best: dict[int, dict] = {}
    for note in notes:
        weight = strength[note["index"]] * note["confidence"]
        support[note["midi"]] = support.get(note["midi"], 0.0) + weight
        if note["midi"] not in best or weight > best[note["midi"]]["weight"]:
            best[note["midi"]] = {**note, "weight": weight}
    ranked = sorted(support, key=lambda midi: -support[midi])[:count]
    return [{"index": best[m]["index"], "midi": m, "confidence": best[m]["confidence"],
             "support": round(support[m], 3)} for m in ranked]


# --- slicing --------------------------------------------------------------------------

def cut_slice(y, sr: int, start: float, end: float):
    import librosa
    import numpy as np

    begin = int(start * sr)
    stop = max(int(end * sr), begin + int(MIN_SLICE_S * sr))
    segment = np.asarray(y[begin:stop], dtype=np.float32)
    if len(segment) == 0:
        raise InvalidReference("Onset lies past the end of the stem", observed=start,
                               next_step="Re-run analyze_reference with force=True.")
    resampled = librosa.resample(segment, orig_sr=sr, target_sr=DEVICE_RATE, res_type="soxr_hq")
    peak = float(np.max(np.abs(resampled)))
    gain = PEAK / peak if peak > 0 else 1.0
    resampled = resampled * gain
    fade = min(len(resampled), int(DEVICE_RATE * FADE_S))
    if fade:
        resampled[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return np.clip(resampled, -1.0, 1.0), 20 * math.log10(gain) if gain > 0 else 0.0


def write_slice(y, sr: int, start: float, end: float, path: Path) -> dict:
    import soundfile as sf

    audio, gain_db = cut_slice(y, sr, start, end)
    sf.write(str(path), audio, DEVICE_RATE, subtype="PCM_16")
    sample = read_sample(path)   # the preflight's own reader: format and byte count exactly as install_kit sees them
    return {"path": str(path), "pcm_bytes": len(sample.pcm), "duration_ms": round(1000 * sample.duration_seconds, 1),
            "gain_db": round(gain_db, 1), "source_s": [round(start, 4), round(end, 4)]}


# --- entry point ------------------------------------------------------------------------

def extract_kit(clip: str | Path, want=None, separation: str = "auto", beat_tracker: str = "auto") -> dict:
    want = _validate_want(want)
    analysis = analyze_reference(clip, separation, beat_tracker)
    import numpy as np
    import soundfile as sf

    duration = float(analysis["duration_s"])
    stems: dict[str, Any] = {}
    for name in ("drums", "bass", "other"):
        record = analysis["stems"].get(name)
        if record and record["onsets_s"]:
            audio, sr = sf.read(record["path"], dtype="float32")
            stems[name] = {"audio": np.ascontiguousarray(audio), "sr": int(sr), **record,
                           "gaps": _gaps(record["starts_s"], duration)}

    # Drums: features, clusters, exemplars, perc variants.
    chosen: dict[str, list[tuple[int, dict]]] = {name: [] for name in CLASSES}
    clusters: dict[str, list[int]] = {}
    exemplars: dict[str, int] = {}
    features = None
    drums = stems.get("drums")
    if drums:
        features = drum_features(drums["audio"], drums["sr"], drums["starts_s"])
        clusters = cluster_drums(features)
        for name, members in clusters.items():
            exemplars[name] = pick_exemplar(members, drums["strength"], drums["gaps"])
            chosen[name].append((exemplars[name], {"cluster": name}))
        for distance, i, name in perc_candidates(features, clusters, exemplars):
            chosen["perc"].append((i, {"cluster": name, "distance_from_exemplar": round(distance, 2)}))

    # Pitched stems.
    for cls in ("bass", "melodic"):
        stem = stems.get(STEM_FOR[cls])
        if not stem:
            continue
        notes = pitched_onsets(stem["audio"], stem["sr"], stem["starts_s"], stem["gaps"], PITCH_RANGES[cls])
        for note in pick_notes(notes, stem["strength"], want.count(cls)):
            chosen[cls].append((note["index"], {"note": note["midi"], "pitch_confidence": note["confidence"],
                                                "support": note["support"]}))

    folder, kit_file = kit_paths(clip)
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("*.wav"):
        stale.unlink()
    slices: list[dict] = []
    unfilled: list[dict] = []
    queues = {name: list(items) for name, items in chosen.items()}
    for pad, cls in enumerate(want, start=1):
        if not queues[cls]:
            reason = ("no onsets on the drums stem" if cls in DRUM_CLASSES + ("perc",) and not drums
                      else "no distinct variant within the drum clusters" if cls == "perc"
                      else f"no confident {cls} note" if cls in PITCH_RANGES
                      else f"fewer than {len(DRUM_CLASSES)} drum clusters")
            unfilled.append({"pad": pad, "class": cls, "reason": reason})
            continue
        index, extra = queues[cls].pop(0)
        stem = stems[STEM_FOR[cls]]
        start = stem["starts_s"][index]
        end = min(start + MAX_SLICE_S, start + float(stem["gaps"][index]), duration)
        written = write_slice(stem["audio"], stem["sr"], start, end, folder / f"{pad:02d}_{cls}.wav")
        slices.append({"pad": pad, "class": cls, "note": extra.get("note"), "stem": STEM_FOR[cls],
                       "onset_s": stem["onsets_s"][index], "strength": stem["strength"][index], **written,
                       **{k: v for k, v in extra.items() if k != "note"}})

    required = sum(s["pcm_bytes"] for s in slices)
    record = {
        "clip": analysis["clip"], "kit_dir": str(folder), "kit": str(kit_file), "sample_rate": DEVICE_RATE,
        "want": want, "slices": slices, "unfilled": unfilled,
        "install_mapping": [{"pad": s["pad"], "path": s["path"]} for s in slices],
        "required_pcm_bytes": required, "required_seconds": round(required / (2 * DEVICE_RATE), 3),
        "drum_clusters": {name: {"onsets": len(members),
                                 "centroid_hz": round(float(features[members, 0].mean()), 1)}
                          for name, members in clusters.items()},
        "separation": analysis["separation"], "bpm": analysis["bpm"]["value"], "key": analysis["key"]["name"],
        "probable": True,
    }
    kit_file.write_text(json.dumps(record, indent=2) + "\n")
    return {**record, "status": "extracted"}
