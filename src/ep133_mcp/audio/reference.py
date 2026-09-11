"""fetch_reference: a section of a song as a local 44.1 kHz 16-bit PCM clip.

Sources:
- YouTube URL: yt-dlp downloads the best audio stream, ffmpeg (via yt-dlp -x)
  converts it to FLAC once per video under <references>/sources/<video id>/.
- Spotify track URL/URI: Spotify's public oEmbed endpoint gives the title, the
  track page's `music:musician_description` meta gives the artist (best effort),
  and `ytsearch1:"<artist> <title>"` picks the video. The chosen video is
  reported so the owner can override with an explicit YouTube URL.
- file:// URL or local path: anything libsndfile can read; no download.

Every clip is cached under <references>/<sha>/ keyed by (kind, media id,
start, end), with clip.wav and source.json naming the source URL and range.
Clips are capped at MAX_CLIP_SECONDS; ranges outside the source are refused
rather than silently clamped, so the agent picks a range deliberately.

Network and subprocess access go through _http_get and _run so tests can
replace them; tests never hit the network.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen, url2pathname

from . import deps
from .errors import FetchFailed, InvalidReference

CLIP_RATE = 44100
MAX_CLIP_SECONDS = 60.0
SPOTIFY_OEMBED = "https://open.spotify.com/oembed?url="
HTTP_TIMEOUT_S = 15
PROBE_TIMEOUT_S = 120
DOWNLOAD_TIMEOUT_S = 900
YOUTUBE_HOSTS = {"youtube.com", "youtu.be", "youtube-nocookie.com"}
SPOTIFY_HOSTS = {"open.spotify.com", "spotify.com", "play.spotify.com"}
MEDIA_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
BROWSER_UA = "Mozilla/5.0 (Macintosh) ep133-mcp"


@dataclass(frozen=True)
class Source:
    kind: str
    url: str
    media_id: str
    title: str
    webpage_url: str
    duration_s: float | None
    uploader: str | None = None
    resolved_from: dict | None = None


def references_dir() -> Path:
    return Path(os.environ.get("EP133_REFERENCES_DIR",
                               str(Path.home() / ".local/state/ep133-mcp/references"))).expanduser()


# --- seams -------------------------------------------------------------------

def _http_get(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": BROWSER_UA})
    with urlopen(request, timeout=HTTP_TIMEOUT_S) as response:
        return response.read()


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


# --- classification ----------------------------------------------------------

def _host(url: str) -> str:
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    for prefix in ("www.", "m.", "music."):
        host = host.removeprefix(prefix)
    return host


def classify(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise InvalidReference("url is empty", expected="YouTube URL, Spotify track URL, file:// URL or local path",
                               next_step="Pass the link the owner gave you.")
    url = url.strip()
    scheme = urlparse(url).scheme.lower()
    if scheme == "file" or scheme == "":
        return "file"
    if scheme == "spotify":
        return "spotify"
    if scheme in ("http", "https"):
        host = _host(url)
        if host in YOUTUBE_HOSTS:
            return "youtube"
        if host in SPOTIFY_HOSTS:
            return "spotify"
    raise InvalidReference("Unsupported reference URL", observed=url,
                           expected="YouTube URL, Spotify track URL, file:// URL or local path",
                           next_step="Give a YouTube link for this song, or a local audio file.")


def youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = _host(url)
    candidate = None
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
    elif parsed.path == "/watch":
        candidate = (parse_qs(parsed.query).get("v") or [None])[0]
    else:
        m = re.match(r"^/(?:shorts|embed|live|v)/([^/?]+)", parsed.path)
        candidate = m.group(1) if m else None
    return candidate if candidate and MEDIA_ID.fullmatch(candidate) else None


def _spotify_track_url(url: str) -> str:
    m = re.match(r"^spotify:track:([A-Za-z0-9]+)$", url)
    if m:
        return f"https://open.spotify.com/track/{m.group(1)}"
    parsed = urlparse(url)
    m = re.match(r"^(?:/intl-[a-z]{2})?/track/([A-Za-z0-9]+)", parsed.path)
    if not m:
        raise InvalidReference("Only Spotify track links are supported", observed=url,
                               expected="https://open.spotify.com/track/<id>",
                               next_step="Pass the link to the track itself, not an album or playlist.")
    return f"https://open.spotify.com/track/{m.group(1)}"


# --- resolution --------------------------------------------------------------

def _yt_dlp_info(yt_dlp: str, target: str) -> dict:
    cmd = [yt_dlp, "--no-playlist", "--no-warnings", "--skip-download", "--dump-single-json", target]
    try:
        proc = _run(cmd, PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise FetchFailed("yt-dlp timed out while resolving the video", observed=target,
                          next_step="Check the network and retry, or pass a different URL.") from e
    if proc.returncode != 0:
        raise FetchFailed("yt-dlp could not resolve the video", observed=proc.stderr.strip()[-500:],
                          next_step="Check the URL is public and playable, then retry.")
    try:
        info = json.loads(proc.stdout)
    except ValueError as e:
        raise FetchFailed("yt-dlp returned unreadable metadata", observed=str(e),
                          next_step="Update yt-dlp (`uv sync --extra audio --upgrade-package yt-dlp`) and retry.") from e
    if info.get("_type") == "playlist":
        entries = [e for e in info.get("entries") or [] if e]
        if not entries:
            raise FetchFailed("YouTube search returned no results", observed=target,
                              next_step="Pass an explicit YouTube URL for this song.")
        info = entries[0]
    if not info.get("id") or not MEDIA_ID.fullmatch(str(info["id"])):
        raise FetchFailed("yt-dlp metadata has no usable video id", observed=info.get("id"),
                          next_step="Pass an explicit YouTube URL for this song.")
    return info


def _source_from_info(url: str, info: dict, resolved_from: dict | None = None) -> Source:
    duration = info.get("duration")
    return Source(kind="youtube", url=url, media_id=str(info["id"]), title=str(info.get("title") or info["id"]),
                  webpage_url=str(info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}"),
                  duration_s=float(duration) if isinstance(duration, (int, float)) else None,
                  uploader=info.get("uploader") or info.get("channel"), resolved_from=resolved_from)


def resolve_spotify(url: str) -> tuple[str, dict]:
    """Title from oEmbed, artist from the track page when available. Returns (query, provenance)."""
    track_url = _spotify_track_url(url.strip())
    try:
        oembed = json.loads(_http_get(SPOTIFY_OEMBED + quote(track_url, safe="")))
        title = str(oembed["title"]).strip()
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise FetchFailed("Spotify oEmbed lookup failed", observed=str(e),
                          expected="JSON with a title for the track",
                          next_step="Pass a YouTube URL for this song instead.") from e
    if not title:
        raise FetchFailed("Spotify oEmbed returned an empty title", observed=track_url,
                          next_step="Pass a YouTube URL for this song instead.")
    artist = None
    try:
        page = _http_get(track_url).decode("utf-8", "replace")
        m = re.search(r'<meta\s+name="music:musician_description"\s+content="([^"]*)"', page)
        if m:
            artist = html.unescape(m.group(1)).strip() or None
    except (OSError, ValueError):
        artist = None
    query = f"{artist} {title}" if artist else title
    return query, {"spotify_url": track_url, "oembed_title": title, "artist": artist, "search_query": query}


def resolve_source(url: str) -> Source:
    """Identify the media without downloading it. Network for youtube/spotify only."""
    kind = classify(url)
    url = url.strip()
    if kind == "file":
        return _file_source(url)
    yt_dlp = deps.require_tool("yt-dlp")
    if kind == "spotify":
        query, provenance = resolve_spotify(url)
        info = _yt_dlp_info(yt_dlp, f"ytsearch1:{query}")
        return _source_from_info(url, info, provenance)
    return _source_from_info(url, _yt_dlp_info(yt_dlp, url))


def _file_path(url: str) -> Path:
    parsed = urlparse(url)
    raw = url2pathname(parsed.path) if parsed.scheme == "file" else url
    path = Path(raw).expanduser()
    if not path.is_file():
        raise InvalidReference("Local reference file not found", observed=str(path),
                               expected="An existing audio file", next_step="Check the path.")
    return path.resolve()


def _file_source(url: str) -> Source:
    path = _file_path(url)
    stat = path.stat()
    media_id = hashlib.sha256(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:16]
    return Source(kind="file", url=url, media_id=media_id, title=path.name, webpage_url=path.as_uri(),
                  duration_s=_file_duration(path))


def _file_duration(path: Path) -> float:
    deps.require_modules("numpy", "soundfile", "librosa")
    import soundfile as sf

    try:
        info = sf.info(str(path))
    except RuntimeError as e:
        raise InvalidReference("Local reference file is not readable audio", observed=str(e)[:300],
                               expected="WAV, FLAC, OGG, AIFF or MP3 readable by libsndfile",
                               next_step="Convert it with `ffmpeg -i <file> -ar 44100 <file>.wav` and pass that.") from e
    return info.frames / info.samplerate


# --- clip range and cache ------------------------------------------------------

def clip_bounds(start_s: Any, end_s: Any, duration_s: float | None) -> tuple[float, float]:
    def number(name, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise InvalidReference(f"{name} must be a finite number of seconds", observed=value,
                                   next_step="Pass seconds as numbers, e.g. start_s=42, end_s=70.")
        return float(value)

    start = number("start_s", start_s)
    if start < 0:
        raise InvalidReference("start_s must be >= 0", observed=start, next_step="Use a non-negative start.")
    if duration_s is not None and start >= duration_s:
        raise InvalidReference("start_s is past the end of the source", observed=start,
                               expected=f"< {duration_s:.2f} s", next_step="Pick a start inside the track.")
    if end_s is None:
        end = start + MAX_CLIP_SECONDS if duration_s is None else min(duration_s, start + MAX_CLIP_SECONDS)
    else:
        end = number("end_s", end_s)
    if end <= start:
        raise InvalidReference("end_s must be greater than start_s", observed={"start_s": start, "end_s": end},
                               next_step="Pass a range with end after start.")
    if duration_s is not None and end > duration_s + 0.01:
        raise InvalidReference("end_s is past the end of the source", observed=end,
                               expected=f"<= {duration_s:.2f} s", next_step="Pick an end inside the track.")
    if end - start > MAX_CLIP_SECONDS:
        raise InvalidReference("Clip is longer than the cap", observed=end - start,
                               expected=f"<= {MAX_CLIP_SECONDS:.0f} s",
                               next_step="Choose the section that matters; one or two phrases is enough for a kit.")
    return start, min(end, duration_s) if duration_s is not None else end


def cache_key(kind: str, media_id: str, start: float, end: float) -> str:
    return hashlib.sha256(f"{kind}|{media_id}|{round(start * 1000)}|{round(end * 1000)}".encode()).hexdigest()[:16]


def _cached(key: str) -> dict | None:
    folder = references_dir() / key
    clip, meta = folder / "clip.wav", folder / "source.json"
    if not (clip.is_file() and meta.is_file()):
        return None
    try:
        record = json.loads(meta.read_text())
    except (OSError, ValueError):
        return None
    if record.get("clip") != str(clip):
        record["clip"] = str(clip)
    return {**record, "status": "cached"}


# --- download and trim ---------------------------------------------------------

def _download_source(yt_dlp: str, source: Source) -> Path:
    folder = references_dir() / "sources" / source.media_id
    dest = folder / "audio.flac"
    if dest.is_file():
        return dest
    deps.require_tool("ffmpeg")
    folder.mkdir(parents=True, exist_ok=True)
    cmd = [yt_dlp, "--no-playlist", "--no-warnings", "-f", "bestaudio/best", "-x", "--audio-format", "flac",
           "-o", str(folder / "audio.%(ext)s"), source.webpage_url]
    try:
        proc = _run(cmd, DOWNLOAD_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise FetchFailed("yt-dlp timed out while downloading", observed=source.webpage_url,
                          next_step="Check the network and retry.") from e
    if proc.returncode != 0 or not dest.is_file():
        raise FetchFailed("yt-dlp did not produce audio.flac", observed=proc.stderr.strip()[-500:],
                          expected=str(dest), next_step="Check ffmpeg works (`ffmpeg -version`) and retry.")
    return dest


def _write_clip(src: Path, start: float, end: float, dest: Path) -> dict:
    deps.require_modules("numpy", "soundfile", "librosa")
    import numpy as np
    import soundfile as sf

    try:
        info = sf.info(str(src))
        rate = info.samplerate
        data, _ = sf.read(str(src), start=int(round(start * rate)), stop=int(round(end * rate)),
                          dtype="float32", always_2d=True)
    except RuntimeError as e:
        raise FetchFailed("Source audio could not be decoded", observed=str(e)[:300],
                          next_step="Delete the cached source under references/sources and retry.") from e
    if data.shape[0] == 0:
        raise FetchFailed("Selected range contains no audio", observed={"start_s": start, "end_s": end},
                          next_step="Pick a range inside the track.")
    if data.shape[1] > 2:
        data = data[:, :2]
    if rate != CLIP_RATE:
        import librosa

        data = np.ascontiguousarray(librosa.resample(data.T, orig_sr=rate, target_sr=CLIP_RATE).T)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dest), data, CLIP_RATE, subtype="PCM_16")
    return {"sample_rate": CLIP_RATE, "channels": int(data.shape[1]), "frames": int(data.shape[0]),
            "duration_s": round(data.shape[0] / CLIP_RATE, 3)}


def fetch_reference(url: str, start_s: float = 0.0, end_s: float | None = None) -> dict:
    kind = classify(url)
    url = url.strip()
    # A direct YouTube URL with an explicit range can hit the cache without any network.
    if kind == "youtube" and end_s is not None:
        vid = youtube_video_id(url)
        if vid:
            start, end = clip_bounds(start_s, end_s, None)
            hit = _cached(cache_key("youtube", vid, start, end))
            if hit:
                return hit
    source = resolve_source(url)
    start, end = clip_bounds(start_s, end_s, source.duration_s)
    key = cache_key(source.kind, source.media_id, start, end)
    hit = _cached(key)
    if hit:
        return hit
    if source.kind == "file":
        audio = _file_path(url)
    else:
        audio = _download_source(deps.require_tool("yt-dlp"), source)
    folder = references_dir() / key
    clip = folder / "clip.wav"
    written = _write_clip(audio, start, end, clip)
    record = {"clip": str(clip), "cache_dir": str(folder), "start_s": start, "end_s": end, **written,
              "source": asdict(source), "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    (folder / "source.json").write_text(json.dumps(record, indent=2) + "\n")
    return {**record, "status": "fetched"}
