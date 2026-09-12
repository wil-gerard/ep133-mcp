"""MCP server over stdio.

Stdout is the MCP stream. Nothing in this package writes to it directly; all
logging is configured to stderr before anything else runs.

Writes require current backups and complete preflight (docs/design/tool-contracts.md).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import tarfile
import threading
import zipfile
from typing import Any

from mcp.server.mcpserver import MCPServer

from . import __version__
from .audio import deps as audio_deps
from .audio.analysis import analyze_reference as _analyze_reference
from .audio.groove import transcribe_groove as _transcribe_groove
from .audio.kit import extract_kit as _extract_kit
from .audio.reference import MAX_CLIP_SECONDS, fetch_reference as _fetch_reference
from .device import DeviceError, DeviceSession, DeviceUnavailable
from .protocol import decode as _decode
from .protocol import generate as _generate
from .safety.backup import BackupRegistry, RESTORE_PROCEDURE
from .safety.capture import create_backup as _create_backup
from .safety.delete import Deleter
from .safety.install import Installer
from .safety.journal import Journal
from .safety.preflight import validate_destination

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("ep133_mcp")

server = MCPServer(
    name="ep133-mcp",
    version=__version__,
    instructions=(
        "Installs samples onto a Teenage Engineering EP-133 K.O. II. "
        "Read-only tools never modify the device. Writes require a verified current backup. "
        "Show needs_confirmation impact to the owner and obtain approval before echoing its token. "
        "Kit installs are sequential and may partially succeed."
    ),
)

_session: DeviceSession | None = None
_session_lock = threading.Lock()
_operation_lock = threading.RLock()
_backups = BackupRegistry(float(os.environ.get("EP133_BACKUP_MAX_AGE_SECONDS", "86400")))
_journal = Journal(os.environ.get(
    "EP133_JOURNAL_DIR", str(Path.home() / ".local/state/ep133-mcp/journal")))
_installer = Installer(_backups, _journal)
_deleter = Deleter(_backups, _journal)


def _device() -> DeviceSession:
    """The process-wide session, opened on first use. One owner per port."""
    global _session
    with _session_lock:
        if _session is None:
            _session = DeviceSession().open()
        return _session


def _error(e: DeviceError) -> dict[str, Any]:
    return {"error": type(e).__name__, "message": str(e), **e.detail}


@server.tool(
    name="device_info",
    description=(
        "Identify the connected EP-133 and report memory. Read-only. "
        "Returns SKU, OS version, sample capacity and free bytes (re-read from the "
        "device on every call), the native sample rate, and the active project number. "
        "The device serial is omitted unless include_serial is true."
    ),
)
def device_info(include_serial: bool = False) -> dict[str, Any]:
    try:
        with _operation_lock:
            d = _device()
            g = d.greet()
            d.begin_read()
            root = d.sample_root()
            active = d.active_project()
    except DeviceUnavailable as e:
        return _error(e)
    except DeviceError as e:
        log.warning("device_info failed: %s", e)
        return _error(e)

    info: dict[str, Any] = {
        "product": g.product,
        "mode": g.mode,
        "sku": g.sku,
        "base_sku": g.base_sku,
        "os_version": g.os_version,
        "sw_version": g.sw_version,
        "bootloader_version": g.bl_version,
        "sample_capacity_bytes": root.max_capacity,
        "sample_free_bytes": root.free_space_in_bytes,
        "native_sample_rate_hz": root.native_rate,
        "active_project": active,
        "write_tools_available": True,
    }
    if include_serial:
        info["serial"] = g.serial
    return info


@server.tool(
    name="list_pads",
    description=(
        "Read all 48 pads in a project (1–9, default active). Returns resolved sym, "
        "stored slot and length, and stale_reference for nonzero stored slots "
        "absent from the library. With fields=true each pad also carries metadata: the whole "
        "JSON record the device holds for the pad node (playmode, trim, envelope, pitch, level, "
        "pan, mute group, time mode, MIDI channel - whatever this OS returns, unfiltered). "
        "Reads fresh project TAR and metadata."
    ),
)
def list_pads(project: int | None = None, fields: bool = False) -> dict[str, Any]:
    if project is not None and not 1 <= project <= 9:
        return {"error": "InvalidProject", "message": "project must be 1..9"}
    try:
        with _operation_lock:
            return _device().list_pads(project, fields=bool(fields))
    except DeviceError as e:
        log.warning("list_pads failed: %s", e)
        return _error(e)


@server.tool(
    name="read_pad",
    description=(
        "Read one pad's complete JSON metadata (project 1..9, group A..D, pad index 1..12 in "
        "the list_pads numbering) and, when its sym resolves to a library slot, that slot's "
        "complete JSON too (name, channels, samplerate, crc, sample.start/end, loop points, "
        "playmode, envelope, pitch, level, pan, root note, time mode, bpm, bars). Read-only; "
        "every key the device returns is passed through unfiltered so a write can be proven "
        "by reading back. The device rewrites its own state while running, so nothing is cached."
    ),
)
def read_pad(project: int, group: str, pad: int) -> dict[str, Any]:
    try:
        validate_destination(project, group, pad)
    except DeviceError as e:
        return _error(e)
    try:
        with _operation_lock:
            return _device().read_pad(project, group, pad)
    except DeviceError as e:
        log.warning("read_pad failed: %s", e)
        return _error(e)


@server.tool(
    name="read_project",
    description=(
        "Decode a whole project (1..9): bpm, all 48 stored pad records, every pattern file as "
        "{group, index, bars, events: [{pad, tick, duration, note, byte4, byte7}], automation: "
        "[{tick, param, value}] for recorded fader moves (type-1 events, never decoded as notes)}, "
        "the scenes file (populated scenes, live pattern per group, selected scene, song), and the "
        "raw settings / fx_settings floats. Ticks are 384 per bar; pads use the list_pads index. "
        "source=<.pak/.ppak path> decodes that file's copy offline instead of reading the device; "
        "otherwise the project TAR is read live (read-only, no backup gate). Field meanings and "
        "their verification status are in docs/research/pattern-encoding.md."
    ),
)
def read_project(project: int, source: str | None = None) -> dict[str, Any]:
    if type(project) is not int or not 1 <= project <= 9:
        return {"error": "InvalidProject", "message": "project must be 1..9"}
    try:
        if source is not None:
            path = Path(source).expanduser()
            if not path.is_file():
                return {"error": "InvalidInput", "message": f"source not found: {path}"}
            tar = _decode.project_from_pak(path.read_bytes(), project)
            origin = {"source": str(path)}
        else:
            with _operation_lock:
                d = _device()
                d.greet()
                tar = d.project_tar(project)
            origin = {"source": "device"}
        return {"project": project, **origin, "tar_bytes": len(tar), **_decode.decode_project(tar)}
    except (ValueError, OSError, tarfile.TarError, zipfile.BadZipFile) as e:   # not a project
        return {"error": "InvalidInput", "message": str(e)}
    except DeviceError as e:
        log.warning("read_project failed: %s", e)
        return _error(e)


@server.tool(
    name="verify_backup",
    description=(
        "Validate a full Sample Tool .pak against device SKU/OS, library occupancy "
        "and all 432 stored pad slot/length fields. Read-only; returns current/stale "
        "and a SHA-256 backup_id. Does not compare audio content or prove full restore."
    ),
)
def verify_backup(path: str) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _backups.verify(path, _device())
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="restore_procedure",
    description="Return manual Sample Tool restore and post-restore backup comparison guidance. No device I/O.",
)
def restore_procedure() -> dict[str, Any]:
    return RESTORE_PROCEDURE


@server.tool(
    name="install_sample",
    description=(
        "Install one mono 16-bit 46875 Hz WAV onto a pad index (1..12). Requires "
        "a current verify_backup backup_id. Shows exact destructive impact for owner "
        "confirmation before writing. Verifies CRC and stored assignment, journals "
        "partial failures. Power-cycle persistence needs a separate hardware check."
    ),
)
def install_sample(path: str, project: int, group: str, pad: int,
                   backup_id: str, confirm: str | None = None) -> dict[str, Any]:
    return install_kit([{"pad": pad, "path": path}], project, group, backup_id, confirm)


@server.tool(
    name="install_kit",
    description=(
        "Install 1..12 distinct {pad, path} entries into a project/group. NOT a "
        "transaction: preflights all entries, then installs sequentially and stops "
        "on failure, reporting each outcome and a shared undo journal. Requires "
        "a verified current backup_id and owner approval for destructive impact."
    ),
)
def install_kit(mapping: list[dict[str, Any]], project: int, group: str,
                backup_id: str, confirm: str | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _installer.install(mapping, project, group, backup_id, _device(), confirm)
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="undo_last_install",
    description=(
        "Revert only assignments from the latest journal for this device, provided "
        "pads still match the installed values. Reports anything it cannot restore. "
        "Uploaded library slots remain in place; delete_samples removes those."
    ),
)
def undo_last_install() -> dict[str, Any]:
    try:
        with _operation_lock:
            return _installer.undo(_device())
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="create_backup",
    description=(
        "Write a full .pak backup by reading the device - the nine project TARs and every "
        "library slot's audio - with no Sample Tool. Each slot is verified against the CRC the "
        "device stores for it, and the result is the same shape Sample Tool writes, so "
        "verify_backup accepts it and Sample Tool can restore it. The device streams about "
        "25 KiB/s, so a full library takes tens of minutes; pass base=<an earlier .pak> to copy "
        "any slot whose CRC still matches and read only what changed. Never overwrites out. "
        "Writing a backup does not prove it restores: only a post-restore backup diff does."
    ),
)
def create_backup(out: str, base: str | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _create_backup(_device(), out, base)
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="delete_samples",
    description=(
        "Permanently delete library slots, freeing sample space. THIS CANNOT BE UNDONE by "
        "undo_last_install: the audio is gone from the device and only a Sample Tool restore "
        "of a backup that still holds it brings it back. Refuses any slot a pad record in any "
        "of the nine projects stores, because a stored slot that no longer exists reads as an "
        "empty pad and cannot be told from one - clear those pads first. Requires a verified "
        "current backup_id and returns needs_confirmation with each slot's name, frames and "
        "CRC; show that to the owner and repeat with confirm. The device command is documented "
        "upstream but unverified here, so each slot is re-read after its delete and reported as "
        "deleted or not_deleted - never assumed. Not a transaction: read every entry."
    ),
)
def delete_samples(slots: list[int], backup_id: str, confirm: str | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _deleter.delete(slots, backup_id, _device(), confirm)
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="fetch_reference",
    description=(
        "Fetch a section of a song as a local 44.1 kHz 16-bit PCM WAV clip for "
        f"analysis (max {MAX_CLIP_SECONDS:.0f} s). Accepts a YouTube URL, a Spotify track "
        "link (title via Spotify oEmbed, then a YouTube search; the chosen video is "
        "reported — pass a YouTube URL to override), or a local file path/URL. No device "
        "I/O. Needs the audio extra plus ffmpeg and yt-dlp; returns AudioToolsUnavailable "
        "with the install command otherwise. Clips are cached under the state directory "
        "with a source.json naming the URL and time range. Clips and kits cut from them "
        "are for the owner's own device and personal use; nothing is published."
    ),
)
def fetch_reference(url: str, start_s: float = 0.0, end_s: float | None = None) -> dict[str, Any]:
    try:
        return _fetch_reference(url, start_s, end_s)
    except DeviceError as e:
        log.warning("fetch_reference failed: %s", e)
        return _error(e)


@server.tool(
    name="analyze_reference",
    description=(
        "Analyze a clip from fetch_reference: tempo (BPM with confidence and half/double "
        "alternates), key, downbeat, and per-stem onset times, writing stems/*.wav next "
        "to the clip for extract_kit. Every estimate is probable, not verified; treat bpm, "
        "key and downbeat as hypotheses and say so. The result names which separator ran "
        "(separation: 'demucs' htdemucs on CPU, or 'hpss' harmonic/percussive split with "
        "no vocals stem) and which beat tracker ran (beat_tracker: 'beat_this' with its "
        "checkpoint, or 'librosa' with the downbeat guessed from kick energy). 'auto' "
        "prefers demucs and Beat This! and falls back when their weights cannot be loaded; "
        "the first run of each downloads weights once. Results are cached as analysis.json "
        "unless force is true. No device I/O. Needs the audio extra; returns "
        "AudioToolsUnavailable with the install command otherwise."
    ),
)
def analyze_reference(clip: str, separation: str = "auto", beat_tracker: str = "auto",
                      force: bool = False) -> dict[str, Any]:
    try:
        return _analyze_reference(clip, separation, beat_tracker, force)
    except DeviceError as e:
        log.warning("analyze_reference failed: %s", e)
        return _error(e)


@server.tool(
    name="extract_kit",
    description=(
        "Cut up to 12 one-shots from an analyzed clip's stems (runs analyze_reference "
        "first if needed). want lists pad classes in pad order, default kick, snare, hat, "
        "perc×3, bass×3, melodic×3. Drum hits are clustered by spectral features into "
        "kick/snare/hat; bass and melodic slices are distinct pyin notes. Every class label "
        "and note is probable: the owner should audition the slices (afplay) before "
        "installing. Slices are 46875 Hz mono 16-bit, at most 1 s, written to kit/ beside "
        "the clip with kit.json; install_mapping is ready for install_kit and "
        "required_pcm_bytes is exactly what its preflight will count. Unfilled pads are "
        "listed with a reason. No device I/O. Needs the audio extra."
    ),
)
def extract_kit(clip: str, want: list[str] | None = None, separation: str = "auto",
                beat_tracker: str = "auto") -> dict[str, Any]:
    try:
        return _extract_kit(clip, want, separation, beat_tracker)
    except DeviceError as e:
        log.warning("extract_kit failed: %s", e)
        return _error(e)


@server.tool(
    name="transcribe_groove",
    description=(
        "Transcribe the drums of an analyzed clip as x/. strings per kit pad, in the "
        "pattern shape generate_ppak takes (16 steps of 24 ticks per bar). Each drum "
        "onset goes to the nearest kick/snare/hat pad of the kit from extract_kit; perc, "
        "bass and melodic pads are not transcribed. The grid follows the tracked beats "
        "from the analyzed downbeat; pass downbeat_s to correct the phase when the groove "
        "comes out rotated by a beat (likely with the librosa fallback). bars is 1, 2 or 4, "
        "fitted to the clip unless given; onsets outside the bars fold back. Reports the "
        "quantization error in ms, and a tick_pattern holding the same hits at the tick each "
        "onset fell on, in the events shape generate_ppak takes - prefer it over pattern when "
        "the feel matters. detector 'classify' (default) gives each onset of the "
        "drums stem one pad; 'bands' detects kick, snare and hat independently in their own "
        "frequency bands, so a kick under a hat is both - much better on real material where "
        "hits coincide, and the only way to recover a four-on-the-floor kick, but not yet exact "
        "on the synthetic fixture. min_strength (0..1) drops onsets weaker than that, "
        "which keeps the accents when every hit would otherwise play at one velocity. "
        "Every hit is probable. Writes kit/groove.json. No device I/O. Needs the audio extra."
    ),
)
def transcribe_groove(clip: str, kit: str | None = None, bars: int | None = None, group: str = "A",
                      index: int = 1, downbeat_s: float | None = None, separation: str = "auto",
                      beat_tracker: str = "auto", min_strength: float = 0.0,
                      detector: str = "classify") -> dict[str, Any]:
    try:
        return _transcribe_groove(clip, kit, bars, group, index, downbeat_s, separation, beat_tracker,
                                  min_strength, detector)
    except DeviceError as e:
        log.warning("transcribe_groove failed: %s", e)
        return _error(e)


@server.tool(
    name="generate_ppak",
    description=(
        "Write a .ppak that patches an existing project for import with Sample Tool. The "
        "template is project N as the device wrote it: from template_pak (a .pak/.ppak "
        "path) or, when omitted, read live from the device (read-only). Only proven fields "
        "change: bpm (settings), pads [{group, pad, slot, frames}] from install_kit results, "
        "patterns, either [{group, index 1..99, bars, steps: {pad: 'x...'}}] on a 16th grid "
        "of 24 ticks or [{group, index, bars, events: [{pad, tick, duration?}]}] which places "
        "each hit at an exact tick - the device stores 24 ticks per 16th and its own recordings "
        "use them, so the events form is what keeps a transcription's micro-timing and per-class "
        "note lengths (whole pattern files are replaced), and scenes [{scene, A, B, C, D}] with a "
        "pattern index 1..99 per group (1 for a silent group). 'o' encodes like 'x' until "
        "velocity is proven. Unknown fields are rejected; song mode is unsupported. Returns "
        "a manifest of every byte range that differs from the template and the decoded "
        "patterns. Whether the device accepts the file is unverified until the import "
        "ladder passes; import only into a non-active project after a fresh backup. Never "
        "overwrites out."
    ),
)
def generate_ppak(out: str, project: int, template_pak: str | None = None, bpm: float | None = None,
                  pads: list[dict[str, Any]] | None = None, patterns: list[dict[str, Any]] | None = None,
                  scenes: list[dict[str, Any]] | None = None, include_sounds: bool = False) -> dict[str, Any]:
    if type(project) is not int or not 1 <= project <= 9:
        return {"error": "InvalidProject", "message": "project must be 1..9"}
    try:
        if template_pak is not None:
            template, meta, sounds = _generate.template_from_pak(template_pak, project)
        else:
            with _operation_lock:
                d = _device()
                g = d.greet()
                template = d.project_tar(project)
            meta = {"device_sku": g.sku, "base_sku": g.base_sku, "device_version": g.os_version}
            sounds = {}
        return _generate.generate_ppak(template, project, out, meta, bpm=bpm, pads=pads, patterns=patterns,
                                       scenes=scenes, sounds=sounds if include_sounds else None)
    except _generate.GenerateError as e:
        return {"error": "InvalidInput", "message": str(e)}
    except DeviceError as e:
        log.warning("generate_ppak failed: %s", e)
        return _error(e)


@server.tool(
    name="server_status",
    description=(
        "Report this server's version, whether an EP-133 MIDI port is visible "
        "(without opening it), and whether the optional audio extra, ffmpeg and "
        "yt-dlp are available. Safe to call when no device is attached."
    ),
)
def server_status() -> dict[str, Any]:
    import mido

    outs = mido.get_output_names()
    ins = mido.get_input_names()
    return {
        "version": __version__,
        "device_output_visible": any("EP-133" in n for n in outs),
        "device_input_visible": any("EP-133" in n for n in ins),
        "session_open": _session is not None,
        "write_tools_available": True,
        "audio": audio_deps.probe(),
    }


def main() -> None:
    log.info("ep133-mcp %s starting over stdio", __version__)
    try:
        server.run(transport="stdio")
    finally:
        if _session is not None:
            _session.close()


if __name__ == "__main__":
    main()
