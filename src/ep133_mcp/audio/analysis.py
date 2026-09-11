"""analyze_reference: tempo, key, downbeat and per-stem onsets for a clip.

Separation is demucs (htdemucs, CPU) when the model can be loaded, else an
HPSS split (percussive -> drums, harmonic below BASS_SPLIT_HZ -> bass, rest ->
other, no vocals). The method used is always reported; demucs needs its
weights downloaded once, which tests never do.

Beats and downbeats: Beat This! (CPJKU, ISMIR 2024) when its checkpoint can
be loaded, else librosa beat tracking with the downbeat guessed as the beat
phase (of four) carrying the most low-band drum energy. Like demucs, Beat This!
downloads its weights once; tests pass beat_tracker="librosa". The reported
BPM is refined from the mean inter-beat interval so it is not quantized to
either tracker's frame grid; confidence is the fraction of librosa's local
tempo estimates agreeing with it. Key: Krumhansl-Schmuckler profile
correlation on mean CQT chroma of the harmonic content. All of these are
marked probable; the agent should say so.

Results are cached as analysis.json next to the clip, stems as stems/*.wav
(mono, clip rate) so extract_kit can cut from them. The cache is reused
unless force is set or a specific separation/beat_tracker was asked for and
the cached record used another.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import deps
from .errors import FetchFailed, InvalidReference

log = logging.getLogger(__name__)

HOP = 512
STEM_NAMES = ("drums", "bass", "other", "vocals")
BASS_SPLIT_HZ = 200.0
KICK_BAND_HZ = 150.0
MIN_BPM, MAX_BPM = 40.0, 250.0
ONSET_LEAD_FRAMES = 8      # silence prepended before onset picking so t=0 can be an onset
ONSET_GATE = 0.05          # onsets weaker than this fraction of the stem's peak strength are dropped
SILENT_DBFS = -50.0
DEMUCS_MODEL = "htdemucs"
SEPARATIONS = ("auto", "demucs", "hpss")
BEAT_TRACKERS = ("auto", "beat_this", "librosa")
BEAT_THIS_CHECKPOINT = "final0"
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
# Krumhansl-Kessler key profiles.
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)


def load_clip(path: str | Path) -> tuple[Any, int]:
    """Mono float32 at the clip's own rate."""
    deps.require_modules("numpy", "soundfile", "librosa")
    import numpy as np
    import soundfile as sf

    path = Path(path).expanduser()
    if not path.is_file():
        raise InvalidReference("Clip not found", observed=str(path), expected="clip.wav from fetch_reference",
                               next_step="Call fetch_reference first and pass its clip path.")
    try:
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    except RuntimeError as e:
        raise InvalidReference("Clip is not readable audio", observed=str(e)[:300],
                               next_step="Pass the clip path returned by fetch_reference.") from e
    if data.shape[0] == 0:
        raise InvalidReference("Clip is empty", observed=str(path), next_step="Fetch a non-empty range.")
    return np.ascontiguousarray(data.mean(axis=1, dtype="float32")), int(sr)


# --- separation ------------------------------------------------------------------

def separate_hpss(y, sr: int) -> dict[str, Any]:
    import librosa
    import numpy as np
    from scipy.signal import butter, sosfiltfilt

    spectrum = librosa.stft(y, hop_length=HOP)
    harmonic, percussive = librosa.decompose.hpss(spectrum, margin=3.0)
    drums = librosa.istft(percussive, hop_length=HOP, length=len(y))
    tonal = librosa.istft(harmonic, hop_length=HOP, length=len(y))
    low = butter(4, BASS_SPLIT_HZ, btype="lowpass", fs=sr, output="sos")
    bass = sosfiltfilt(low, tonal).astype(np.float32)
    return {"drums": drums.astype(np.float32), "bass": bass, "other": (tonal - bass).astype(np.float32),
            "vocals": None}


def separate_demucs(y, sr: int) -> dict[str, Any]:
    import numpy as np
    import torch
    from demucs.api import Separator

    separator = Separator(model=DEMUCS_MODEL, device="cpu", progress=False)
    wav = torch.from_numpy(np.stack([y, y]))
    _, stems = separator.separate_tensor(wav, sr)
    out: dict[str, Any] = {}
    for name in STEM_NAMES:
        tensor = stems.get(name)
        if tensor is None:
            out[name] = None
            continue
        mono = tensor.mean(dim=0).cpu().numpy().astype(np.float32)
        if separator.samplerate != sr:
            import librosa

            mono = librosa.resample(mono, orig_sr=separator.samplerate, target_sr=sr)
        out[name] = mono[: len(y)]
    return out


def check_methods(separation: str, beat_tracker: str) -> None:
    if separation not in SEPARATIONS:
        raise InvalidReference("Unknown separation method", observed=separation,
                               expected=", ".join(SEPARATIONS), next_step="Omit separation to let the server choose.")
    if beat_tracker not in BEAT_TRACKERS:
        raise InvalidReference("Unknown beat tracker", observed=beat_tracker,
                               expected=", ".join(BEAT_TRACKERS), next_step="Omit beat_tracker to let the server choose.")


def separate(y, sr: int, method: str = "auto") -> tuple[str, dict[str, Any]]:
    check_methods(method, "auto")
    if method in ("auto", "demucs"):
        try:
            deps.require_modules("demucs", "torch")
            return "demucs", separate_demucs(y, sr)
        except Exception as e:  # model download, torch, or memory failures: fall back or report
            if method == "demucs":
                raise FetchFailed("demucs separation failed", observed=str(e)[:300],
                                  next_step="Retry with separation='hpss', or check network access for the "
                                            "first-time model download.") from e
            log.warning("demucs unavailable (%s); using HPSS separation", str(e)[:120])
    return "hpss", separate_hpss(y, sr)


# --- features ----------------------------------------------------------------------

def _dbfs(y) -> float:
    import numpy as np

    rms = float(np.sqrt(np.mean(np.square(y)))) if len(y) else 0.0
    return 20 * np.log10(rms) if rms > 0 else -120.0


def track_beats_beat_this(y, sr: int) -> tuple[list[float], list[float]]:
    from beat_this.inference import Audio2Beats

    beats, downbeats = Audio2Beats(checkpoint_path=BEAT_THIS_CHECKPOINT, device="cpu")(y, sr)
    return [float(b) for b in beats], [float(d) for d in downbeats]


def track_beats_librosa(y, sr: int) -> tuple[list[float], list[float]]:
    """Beats only; librosa has no downbeat tracker."""
    import librosa

    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    _, beats = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=HOP, units="time", trim=False)
    return [float(b) for b in beats], []


def track_beats(y, sr: int, method: str = "auto") -> tuple[str, list[float], list[float]]:
    check_methods("auto", method)
    if method in ("auto", "beat_this"):
        try:
            deps.require_modules("beat_this", "torch")
            return ("beat_this", *track_beats_beat_this(y, sr))
        except Exception as e:  # checkpoint download, torch, or memory failures: fall back or report
            if method == "beat_this":
                raise FetchFailed("Beat This! tracking failed", observed=str(e)[:300],
                                  next_step="Retry with beat_tracker='librosa', or check network access for the "
                                            "first-time checkpoint download.") from e
            log.warning("beat_this unavailable (%s); using librosa beat tracking", str(e)[:120])
    return ("librosa", *track_beats_librosa(y, sr))


def estimate_tempo(y, sr: int, beats: list[float]) -> dict:
    """BPM from the steady inter-beat intervals, with librosa's local tempo as the confidence check."""
    import librosa
    import numpy as np

    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    local = np.atleast_1d(librosa.feature.tempo(onset_envelope=env, sr=sr, hop_length=HOP, aggregate=None)).ravel()
    bpm = float(np.median(local)) if len(local) else 0.0
    if len(beats) >= 3:
        intervals = np.diff(beats)
        median = float(np.median(intervals))
        steady = intervals[np.abs(intervals - median) <= 0.15 * median]
        if len(steady) and steady.mean() > 0:
            bpm = 60.0 / float(steady.mean())
    agree = np.abs(local - bpm) <= 0.05 * bpm if bpm > 0 else np.zeros(0, dtype=bool)
    confidence = float(agree.mean()) if len(agree) else 0.0
    alternates = [round(alt, 2) for alt in (bpm / 2, bpm * 2) if MIN_BPM <= alt <= MAX_BPM]
    return {"value": round(bpm, 2), "confidence": round(confidence, 3), "alternates": alternates,
            "beats_s": [round(b, 4) for b in beats], "probable": True}


def estimate_key(y, sr: int) -> dict:
    import librosa
    import numpy as np

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=HOP).mean(axis=1)
    if not np.any(chroma):
        return {"tonic": None, "mode": None, "name": None, "confidence": 0.0, "probable": False}
    scores = []
    for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
        for tonic in range(12):
            template = np.roll(np.array(profile), tonic)
            scores.append((float(np.corrcoef(chroma, template)[0, 1]), tonic, mode))
    scores.sort(reverse=True)
    (best, tonic, mode), (second, _, _) = scores[0], scores[1]
    return {"tonic": NOTE_NAMES[tonic], "mode": mode, "name": f"{NOTE_NAMES[tonic]} {mode}",
            "confidence": round(max(0.0, best - second), 3), "correlation": round(best, 3), "probable": True}


def detect_onsets(y, sr: int) -> dict:
    """Onset times with strengths and backtracked starts; empty for silent stems."""
    import librosa
    import numpy as np

    if y is None or _dbfs(y) < SILENT_DBFS:
        return {"onsets_s": [], "strength": [], "starts_s": []}
    # Leading silence so a hit on the very first sample (a clip cut on a downbeat) can be peak-picked.
    env = librosa.onset.onset_strength(y=np.concatenate([np.zeros(ONSET_LEAD_FRAMES * HOP, dtype=y.dtype), y]),
                                       sr=sr, hop_length=HOP)
    # The silence-to-signal edge itself can dwarf every real hit and onset_detect normalises by the
    # maximum, so the boundary frames are capped at the peak of the rest of the envelope.
    peak = float(env[ONSET_LEAD_FRAMES + 2:].max()) if len(env) > ONSET_LEAD_FRAMES + 2 else 0.0
    if peak <= 0:
        return {"onsets_s": [], "strength": [], "starts_s": []}
    env[:ONSET_LEAD_FRAMES + 2] = np.minimum(env[:ONSET_LEAD_FRAMES + 2], peak)
    frames = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=HOP, backtrack=False, units="frames")
    frames = np.asarray([f for f in frames if env[f] >= ONSET_GATE * peak], dtype=int)
    starts = librosa.onset.onset_backtrack(frames, env) if len(frames) else frames
    times = librosa.frames_to_time(np.maximum(frames - ONSET_LEAD_FRAMES, 0), sr=sr, hop_length=HOP)
    start_times = librosa.frames_to_time(np.maximum(starts - ONSET_LEAD_FRAMES, 0), sr=sr, hop_length=HOP)
    return {"onsets_s": [round(float(t), 4) for t in times],
            "strength": [round(float(env[f] / peak), 4) for f in frames],
            "starts_s": [round(float(t), 4) for t in start_times]}


def estimate_downbeat(drums, sr: int, beats: list[float], downbeats: list[float]) -> dict:
    """Tracked downbeats when available, else the beat phase with the most kick-band energy."""
    import numpy as np

    if not beats:
        return {"value_s": 0.0, "phase": 0, "beats_per_bar": 4, "downbeats_s": [], "probable": False}
    if downbeats:
        beat_index = [int(np.argmin(np.abs(np.asarray(beats) - d))) for d in downbeats]
        spacing = np.diff(beat_index)
        beats_per_bar = int(np.median(spacing)) if len(spacing) else 4
        return {"value_s": round(downbeats[0], 4), "phase": beat_index[0], "beats_per_bar": max(1, beats_per_bar),
                "downbeats_s": [round(d, 4) for d in downbeats], "probable": True}
    if drums is None or len(beats) < 4 or _dbfs(drums) < SILENT_DBFS:
        return {"value_s": round(beats[0], 4), "phase": 0, "beats_per_bar": 4, "downbeats_s": [], "probable": False}
    from scipy.signal import butter, sosfiltfilt

    low = butter(4, KICK_BAND_HZ, btype="lowpass", fs=sr, output="sos")
    band = sosfiltfilt(low, drums)
    window = int(sr * 0.05)
    energy = np.asarray([float(np.sum(np.square(band[int(b * sr):int(b * sr) + window]))) for b in beats])
    phase = int(np.argmax([energy[p::4].mean() for p in range(4)]))
    return {"value_s": round(beats[phase], 4), "phase": phase, "beats_per_bar": 4,
            "downbeats_s": [round(b, 4) for b in beats[phase::4]], "probable": True}


# --- entry point ---------------------------------------------------------------

def analysis_paths(clip: str | Path) -> tuple[Path, Path]:
    clip = Path(clip).expanduser()
    return clip.parent / "analysis.json", clip.parent / "stems"


def analyze_reference(clip: str | Path, separation: str = "auto", beat_tracker: str = "auto",
                      force: bool = False) -> dict:
    check_methods(separation, beat_tracker)
    cache, stems_dir = analysis_paths(clip)
    if not force and cache.is_file():
        try:
            record = json.loads(cache.read_text())
            wanted = ((separation in ("auto", record["separation"]["method"]))
                      and (beat_tracker in ("auto", record["beat_tracker"]["method"])))
            if wanted and all(Path(s["path"]).is_file() for s in record["stems"].values() if s):
                return {**record, "status": "cached"}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    y, sr = load_clip(clip)
    import numpy as np
    import soundfile as sf

    method, stems = separate(y, sr, separation)
    tracker, beats, downbeats = track_beats(y, sr, beat_tracker)
    tempo = estimate_tempo(y, sr, beats)
    tonal = stems["other"] if stems.get("bass") is None else stems["other"] + stems["bass"]
    key = estimate_key(tonal, sr)
    downbeat = estimate_downbeat(stems.get("drums"), sr, beats, downbeats)
    stems_dir.mkdir(parents=True, exist_ok=True)
    stem_records: dict[str, Any] = {}
    for name in STEM_NAMES:
        audio = stems.get(name)
        if audio is None:
            stem_records[name] = None
            continue
        path = stems_dir / f"{name}.wav"
        sf.write(str(path), np.clip(audio, -1.0, 1.0), sr, subtype="PCM_16")
        stem_records[name] = {"path": str(path), "level_dbfs": round(_dbfs(audio), 1), **detect_onsets(audio, sr)}
    record = {
        "clip": str(Path(clip).expanduser()), "sample_rate": sr, "duration_s": round(len(y) / sr, 3),
        "bpm": tempo, "key": key, "downbeat": downbeat,
        "separation": {"method": method, "model": DEMUCS_MODEL if method == "demucs" else None},
        "beat_tracker": {"method": tracker, "checkpoint": BEAT_THIS_CHECKPOINT if tracker == "beat_this" else None},
        "stems": stem_records,
    }
    cache.write_text(json.dumps(record, indent=2) + "\n")
    return {**record, "status": "analyzed"}
