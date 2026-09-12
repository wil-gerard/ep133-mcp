"""MCP server over stdio.

Stdout is the MCP stream. Nothing in this package writes to it directly; all
logging is configured to stderr before anything else runs.

Writes require current backups and complete preflight (docs/design/tool-contracts.md).
"""

from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path
import sys
import tarfile
import threading
import zipfile
import wave
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
from .protocol import diffmap as _diffmap
from .protocol import generate as _generate
from .protocol.payloads import SAMPLE_ROOT
from .safety.backup import BackupRegistry, RESTORE_PROCEDURE
from .safety.capture import create_backup as _create_backup
from .safety.chop import Chopper
from .safety.clear import Clearer
from .safety.import_project import Importer, check_ppak as _check_ppak
from .safety.delete import Deleter
from .safety.install import Installer
from .safety.library import export_project as _export_project, list_samples as _list_samples
from .midi.playback import Playback, compile_pattern
from .safety.selection import ProjectSelector
from .safety.recovery import Restorer
from .safety.journal import Journal
from .safety.params import ParamWriter
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
_params = ParamWriter(_backups, _journal)
_chopper = Chopper(_backups, _journal)
_clearer = Clearer(_backups, _journal)
_importer = Importer(_backups, _journal)
_restorer = Restorer(_backups, _journal)
_selector = ProjectSelector(_backups, _journal)
_playback = Playback()
atexit.register(_playback.stop)


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
        "stored slot and length (the trim, end - start, in frames), and stale_reference for nonzero stored slots "
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
        "{group, index, bars, events: [{pad, tick, duration, note, velocity, byte7}], automation: "
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
        "and all 432 stored pad slot/length fields. Read-only; returns current, superset (the "
        "device has only lost content since - slots deleted, pads cleared - so the backup still "
        "restores everything on it and stays valid for writes) or stale, with the differences, "
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
    name="set_pad",
    description=(
        "Write per-pad sound parameters to one pad (project 1..9, group A..D, pad 1..12 in the "
        "list_pads numbering) through the same metadata write that assigns a pad. params is any "
        "subset of: sample.start, sample.end (trim, in frames of the slot's sample), "
        "sound.playmode ('oneshot'|'key'|'legato' - strings, ints are rejected), envelope.attack, "
        "envelope.release (0..255), sound.pitch (semitones -12..12), sound.amplitude (0..200, 100 = unity), "
        "sound.pan (-16..16), sound.mutegroup (bool), time.mode ('off'|'bar'|'bpm'), midi.channel "
        "(0..15). sound.bpm, sound.bars and sound.rootnote live on the slot (a pad SET drops them, "
        "proven on OS 2.5.1) and are refused here - use set_slot. Only the fields given "
        "are sent (the device merges); sound.playmode is paired with envelope.release (oneshot 255, "
        "key 15) unless release is given. Requires a verified current backup_id and returns "
        "needs_confirmation with before/after values; repeat with confirm. The pad is read back "
        "after the write and every field reported as applied, changed (the device stored another "
        "value) or dropped, with side_effects for any other key that moved - the device's own "
        "coupling has only been documented upstream, never observed here. Journalled so "
        "undo_last_pad_change writes the previous values back. Power-cycle persistence is a "
        "separate check."
    ),
)
def set_pad(project: int, group: str, pad: int, params: dict[str, Any], backup_id: str,
            confirm: str | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _params.set_pad(project, group, pad, params, backup_id, _device(), confirm)
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="set_slot",
    description=(
        "Write slot-level fields to one library slot (1..999): name (<=20 ASCII), sound.loopstart / "
        "sound.loopend (frames; trim-only on this device, never an auto-loop; -1 clears), and the "
        "same sound.* / envelope.* / time.mode fields set_pad takes. Only the given fields are sent. "
        "Requires a verified current backup_id, returns needs_confirmation with before/after, "
        "reads the slot back and reports applied / changed / dropped / side_effects, and journals "
        "the previous values for undo_last_pad_change. A slot's parameters affect every pad in "
        "every project that plays it."
    ),
)
def set_slot(slot: int, params: dict[str, Any], backup_id: str, confirm: str | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _params.set_slot(slot, params, backup_id, _device(), confirm)
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="undo_last_pad_change",
    description=(
        "Write back the values the latest set_pad / set_slot journal recorded before its write, "
        "then read the record and report applied / changed / dropped. A field the record did not "
        "hold before the change cannot be unset and is listed under not_restorable. Does not touch "
        "install journals; undo_last_install handles those."
    ),
)
def undo_last_pad_change() -> dict[str, Any]:
    try:
        with _operation_lock:
            return _params.undo(_device())
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="chop_sample",
    description=(
        "Upload one mono 16-bit 46875 Hz WAV to one library slot and put it on up to 12 pads of a "
        "project/group, each trimmed (sample.start/end) to its own slice, so a chopped break costs "
        "one slot. slices is {mode: 'equal', count: N} (N = number of pads), {mode: 'onsets', pick: "
        "'first'|'strongest'|'spread'} (the detector extract_kit uses; needs the audio extra; N "
        "backtracked onsets start the slices - the earliest N by default, which front-loads a "
        "break, the N loudest, or every len/N-th so the slices span the clip; the detected times "
        "and strengths are returned so the owner can adjust), or explicit "
        "[{start_s, end_s}] in time order without overlap. playmode (oneshot default, key for "
        "gated chops, legato) is written to every pad with its paired release. Requires a verified "
        "current backup_id; returns needs_confirmation with the full impact when any pad is "
        "occupied. Not a transaction: upload is verified by CRC, then pads are assigned and "
        "trimmed one at a time, each read back (stored record, then JSON) and reported as chopped, "
        "chopped_with_differences (the device stored other values) or failed; stops at the first "
        "failure. One journal covers everything so undo_last_chop is one step. Power-cycle "
        "persistence is a separate check."
    ),
)
def chop_sample(path: str, project: int, group: str, pads: list[int], slices: Any, backup_id: str,
                confirm: str | None = None, playmode: str = "oneshot") -> dict[str, Any]:
    try:
        with _operation_lock:
            return _chopper.chop(path, project, group, pads, slices, backup_id, _device(), confirm, playmode)
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="undo_last_chop",
    description=(
        "Revert the newest chop_sample journal that still has pads to revert; calling it again "
        "reaches the next older one. Each pad that still stores the chop's slot gets its prior "
        "sym and, where the record held them, its prior trim and playmode written back, then its "
        "stored record is read to prove it. Reports anything it cannot restore. The uploaded slot "
        "stays in the library; delete_samples removes it."
    ),
)
def undo_last_chop() -> dict[str, Any]:
    try:
        with _operation_lock:
            return _chopper.undo(_device())
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="clear_pads",
    description=(
        "Write sym 0 to every stored pad in whole projects (1..9) so the slots they held can be "
        "deleted; stale references are cleared too. Refuses the active project - switch the "
        "device to the project you are keeping first. Requires a verified current backup_id and "
        "returns needs_confirmation listing every pad, its stored slot and name; show that to "
        "the owner and repeat with confirm. One journal for the whole call; each pad's stored "
        "record and JSON are re-read after its write. Not a transaction: stops at the first "
        "failure and reports every entry. undo_last_clear re-points pads at prior slots that "
        "still exist."
    ),
)
def clear_pads(projects: list[int], backup_id: str, confirm: str | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _clearer.clear(projects, backup_id, _device(), confirm)
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="undo_last_clear",
    description=(
        "Revert the newest clear_pads journal that still has pads to revert: each pad still at "
        "sym 0 gets its prior slot and the trim/playmode the record held, then is read back. A "
        "prior slot since deleted stays cleared and is reported in undo_note."
    ),
)
def undo_last_clear() -> dict[str, Any]:
    try:
        with _operation_lock:
            return _clearer.undo(_device())
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
        "CRC; show that to the owner and repeat with confirm. Sends the frame Sample Tool sends; "
        "a one-slot delete is proven on OS 2.5.1 by a backup diff (docs/research/delete-proof.md). "
        "Each slot is still re-read after its delete and reported as deleted or not_deleted - "
        "never assumed. Not a transaction: read every entry."
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
        "fitted to the clip unless given; a longer clip folds onto the pattern and its bars vote, "
        "so the loop is kept and one-off fills and intro ticks are not (each pad reports its "
        "class and the role it plays; with 'bands' each stream goes to the pad whose slice "
        "sounds like it, which is not always the pad extract_kit named for it). Reports the "
        "quantization error in ms, and a tick_pattern holding the same hits at the tick each "
        "onset fell on with a velocity 40..127 from its onset strength (loudest hit of each class "
        "at 127), in the events shape generate_ppak takes - prefer it over pattern when "
        "the feel matters. detector 'classify' (default) gives each onset of the "
        "drums stem one pad; 'bands' detects kick, snare and hat independently in their own "
        "frequency bands, so a kick under a hat is both - much better on real material where "
        "hits coincide, and the only way to recover a four-on-the-floor kick, but not yet exact "
        "on the synthetic fixture. min_strength (0..1) drops onsets weaker than that, relative to "
        "the loudest of the stem ('classify') or of each band ('bands', which already drops "
        "onsets under 0.15-0.25 per band, so smaller values change nothing). "
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
        "note lengths (whole pattern files are replaced); an event may carry note (0..127, "
        "default 60; which pitch a key-mode pad plays for it is unverified) and velocity (1..127, "
        "default 100; byte 4 is velocity, proven by a pressure recording) and an events-form "
        "pattern may carry automation [{tick, param, value 0..32767}], the recorded-fader event "
        "shape (param ids 1, 5, 6 seen on this device, meanings unmapped), and scenes [{scene, A, B, C, D}] with a "
        "pattern index 1..99 per group (1 for a silent group). 'o' steps write velocity 60, 'x' 100. fx {selector?, params?: {index 0..33: 0..1}} and settings {params?: "
        "{index 0..47: 0..1}, group_bytes?: {A..D: int}} patch the effect and fader tables by raw "
        "index (values snap to n/256, the device's knob step) - the meanings are only the "
        "hypotheses in docs/research/fx-and-settings-map.md until the mapping session names them. "
        "Unknown fields are rejected; song mode is unsupported. Returns "
        "a manifest of every byte range that differs from the template and the decoded "
        "patterns. Whether the device accepts the file is unverified until the import "
        "ladder passes; import only into a non-active project after a fresh backup. Never "
        "overwrites out."
    ),
)
def generate_ppak(out: str, project: int, template_pak: str | None = None, bpm: float | None = None,
                  pads: list[dict[str, Any]] | None = None, patterns: list[dict[str, Any]] | None = None,
                  scenes: list[dict[str, Any]] | None = None, include_sounds: bool = False,
                  fx: dict[str, Any] | None = None, settings: dict[str, Any] | None = None) -> dict[str, Any]:
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
                                       scenes=scenes, sounds=sounds if include_sounds else None, fx=fx,
                                       settings=settings)
    except _generate.GenerateError as e:
        return {"error": "InvalidInput", "message": str(e)}
    except DeviceError as e:
        log.warning("generate_ppak failed: %s", e)
        return _error(e)


@server.tool(
    name="list_files",
    description=(
        "Walk the device's file-id namespace with FILE_LIST from a node (default 0, the root), "
        "returning every entry's node id, kind (file/folder), size, name and path down to "
        "max_depth. The sounds root (1000, 999 slots) is skipped unless include_sounds is true. "
        "Read-only and documented safe in both upstream projects (ep133-ppak's GROUP_DUMP is page "
        "0 of it; phones24 walks from node 0 with it); no file is opened. This is how to find nodes "
        "outside the known map - the global settings node, for one - without guessing ids."
    ),
)
def list_files(node: int = 0, max_depth: int = 3, include_sounds: bool = False) -> dict[str, Any]:
    if type(node) is not int or not 0 <= node < 2**16 or type(max_depth) is not int or not 1 <= max_depth <= 8:
        return {"error": "InvalidInput", "message": "node must be 0..65535 and max_depth 1..8"}
    try:
        with _operation_lock:
            d = _device()
            d.greet()
            d.begin_read()
            entries = d.walk(node, max_depth=max_depth, skip=set() if include_sounds else {SAMPLE_ROOT})
        return {"node": node, "max_depth": max_depth, "entries": entries, "count": len(entries)}
    except DeviceError as e:
        log.warning("list_files failed: %s", e)
        return _error(e)


@server.tool(
    name="stat_file",
    description=(
        "STAT one file id (0..65535): node, parent, flags, size, name and kind, or exists=false when "
        "the device answers invalid id. Read-only; documented safe for any id upstream, but every "
        "new id on this device is treated as a possible wedge, so probe in small ranges and log "
        "each id first. Never opens the file."
    ),
)
def stat_file(file_id: int) -> dict[str, Any]:
    if type(file_id) is not int or not 0 <= file_id < 2**16:
        return {"error": "InvalidInput", "message": "file_id must be 0..65535"}
    try:
        with _operation_lock:
            d = _device()
            d.greet()
            d.begin_read()
            info = d.stat(file_id)
        return {"file_id": file_id, "exists": info is not None, **(info or {})}
    except DeviceError as e:
        log.warning("stat_file failed: %s", e)
        return _error(e)


@server.tool(
    name="play_note",
    description=(
        "Send one MIDI note (channel 1..16, note 0..127, velocity 1..127, held duration_s then "
        "released) on the EP-133's own port, so a pad can be auditioned without touching the "
        "device. Plain channel MIDI, not SysEx: nothing is written and no file is opened. Which "
        "channel reaches which group and which note reaches which pad are the device's MIDI "
        "settings (MIDI in must be on); the SysEx PLAY command is not used because its payload is "
        "undocumented and nobody has captured it. Owner present: if the sequencer is recording, "
        "the note lands in the active pattern. Observations go in docs/research/play-proof.md."
    ),
)
def play_note(channel: int, note: int, velocity: int = 100, duration_s: float = 0.25) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _device().send_note(channel, note, velocity, duration_s)
    except ValueError as e:
        return {"error": "InvalidInput", "message": str(e)}
    except DeviceError as e:
        return _error(e)


@server.tool(
    name="diff_project",
    description=(
        "Name every byte that differs in one project (1..9) between two copies: old is a .pak/.ppak "
        "path; new is another path or, when omitted, a live read of the device. settings and "
        "fx_settings changes come back as named fields - bpm, settings.params[i] (with the group "
        "and fader-function index the 4x12 layout hypothesis gives), settings group bytes, "
        "fx_settings.selector, fx_settings.params[i] - with before/after floats and the n/256 knob "
        "step; pads, patterns and scenes come back as byte ranges so a stray edit is visible. "
        "This is the loop for the diff method (docs/research/fx-and-settings-map.md): the owner "
        "changes one control, the agent calls this, the offset gets a name. Read-only."
    ),
)
def diff_project(project: int, old: str, new: str | None = None) -> dict[str, Any]:
    if type(project) is not int or not 1 <= project <= 9:
        return {"error": "InvalidProject", "message": "project must be 1..9"}
    try:
        old_tar = _decode.project_from_pak(Path(old).expanduser().read_bytes(), project)
        if new is not None:
            new_tar = _decode.project_from_pak(Path(new).expanduser().read_bytes(), project)
            source = {"old": old, "new": new}
        else:
            with _operation_lock:
                d = _device()
                d.greet()
                new_tar = d.project_tar(project)
            source = {"old": old, "new": "device"}
        return {"project": project, **source, **_diffmap.diff_project_floats(old_tar, new_tar)}
    except (ValueError, OSError, tarfile.TarError, zipfile.BadZipFile) as e:
        return {"error": "InvalidInput", "message": str(e)}
    except DeviceError as e:
        log.warning("diff_project failed: %s", e)
        return _error(e)


@server.tool(
    name="check_ppak",
    description=(
        "Preflight a .ppak for import into project N (1..9) before the owner runs Sample Tool - "
        "or, once the SysEx write path is proven, before import_ppak. Read-only. Reports whether "
        "the file is a single-project export in the device's own flavour and for that project "
        "number (Sample Tool asks for the number and does not read it from the file), whether N is "
        "the active project (never import there), the file's bpm, assigned pads, patterns and "
        "scenes, which library slots its pads reference and whether each exists on the device or "
        "is carried in the file, and what project N currently holds that an import would replace. "
        "status is ok or problems with each problem spelled out."
    ),
)
def check_ppak(path: str, project: int, slot_map: dict[str, int] | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _check_ppak(path, project, _device(), slot_map)
    except DeviceError as e:
        return _error(e)

    except (OSError, ValueError, EOFError, wave.Error, zipfile.BadZipFile, tarfile.TarError) as e:
        return {"error": type(e).__name__, "message": str(e)}


@server.tool(
    name="import_ppak",
    description=(
        "Write a .ppak's project TAR to project N (1..9) over SysEx, the way Sample Tool imports "
        "it (its uploadProjectArchive: FILE_PUT_META at the project node, data pages, terminator). "
        "Runs check_ppak first and refuses on any problem, including N being the active project; "
        "included samples are verified/reused or uploaded first; slot_map explicitly remaps collisions. Requires a verified "
        "current backup_id and returns needs_confirmation with the file's bpm/pads/patterns/scenes "
        "and what project N holds now; show that to the owner and repeat with confirm. The project "
        "is read back afterwards and compared byte-for-byte, then field-by-field if the bytes "
        "differ. undo_last_import restores the exact saved project preimage. "
        "Power-cycle persistence is a separate check."
    ),
)
def import_ppak(path: str, project: int, backup_id: str, confirm: str | None = None,
                slot_map: dict[str, int] | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _importer.import_ppak(path, project, backup_id, _device(), confirm, slot_map)
    except DeviceError as e:
        return _error(e)

    except (OSError, ValueError, EOFError, wave.Error, zipfile.BadZipFile, tarfile.TarError) as e:
        return {"error": type(e).__name__, "message": str(e)}


@server.tool(
    name="undo_last_import",
    description=(
        "Restore the exact project preimage saved before the newest import_ppak "
        "that still stands, then read it back and compare. Calling again reaches the next older "
        "import."
    ),
)
def undo_last_import() -> dict[str, Any]:
    try:
        with _operation_lock:
            return _importer.undo(_device())
    except DeviceError as e:
        return _error(e)

    except (OSError, ValueError, EOFError, wave.Error, zipfile.BadZipFile, tarfile.TarError) as e:
        return {"error": type(e).__name__, "message": str(e)}


@server.tool(description="Select the active project explicitly. Requires a current backup and confirmation, journals the previous project and verifies the new active value. Hardware acceptance pending.")
def set_active_project(project: int, backup_id: str, confirm: str | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _selector.select(project, backup_id, _device(), confirm)
    except DeviceError as e:
        return _error(e)


@server.tool(description="Audition simultaneous generate_ppak steps patterns over live MIDI, up to 120 seconds. Requires explicit routing entries {group,pad,channel,note}, since global MIDI routing is not readable. Checks actual active-project assignments. Device must not be recording. Returns immediately; use stop_playback or playback_status. Soft o hits use velocity 60, the same value generate_ppak writes for them.")
def play_pattern(project: int, patterns: list[dict], routing: list[dict], bpm: float,
                 repeats: int = 1) -> dict[str, Any]:
    try:
        validate_destination(project, "A", 1)
        events, duration, pads = compile_pattern(patterns, routing, bpm, repeats)
        with _operation_lock:
            d = _device()
            d.greet()
            d.begin_read()
            if d.active_project() != project:
                raise ValueError("Audition project must be active")
            assignments = []
            for group, pad in pads:
                meta = d.pad_metadata(project, group, pad)
                if not meta.get("sym"):
                    raise ValueError(f"{group}{pad} is empty or stale")
                assignments.append({"group": group, "pad": pad, "slot": meta["sym"]})
            def verify():
                d.begin_read()
                if d.active_project() != project or any(d.pad_metadata(project, a["group"], a["pad"]).get("sym") != a["slot"] for a in assignments):
                    raise ValueError("Project or assignments changed before playback")
            return _playback.start(d, _operation_lock, events, duration, assignments, verify)
    except DeviceError as e:
        return _error(e)
    except (ValueError, TypeError, KeyError) as e:
        return {"error": "InvalidInput", "message": str(e)}


@server.tool(description="Cancel the live audition and release its active notes. Available during playback without waiting for the device-operation lock.")
def stop_playback() -> dict[str, Any]:
    return _playback.stop()


@server.tool(description="Read live audition progress and any MIDI or note-off cleanup failure. No device I/O.")
def playback_status() -> dict[str, Any]:
    return _playback.status()


@server.tool(description="Restore selected samples from a backup to their original slots. Requires a current backup_id and confirmation of affected references. Refuses occupied slots unless audio and metadata match exactly. Reports partial failures; no automatic deletion.")
def restore_samples(source: str, slots: list[int], backup_id: str, confirm: str | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _restorer.restore(source, slots, backup_id, _device(), confirm)
    except DeviceError as e:
        return _error(e)
    except (OSError, ValueError, EOFError, wave.Error, zipfile.BadZipFile, tarfile.TarError) as e:
        return {"error": type(e).__name__, "message": str(e)}


@server.tool(description="Restore one non-active project from a local backup, optionally including its referenced samples. Uses import preflight, confirmation, journal and read-back; other projects remain intact. slot_map explicitly remaps included samples.")
def restore_project(source: str, project: int, backup_id: str, confirm: str | None = None,
                    include_samples: bool = True, slot_map: dict[str, int] | None = None) -> dict[str, Any]:
    import hashlib
    from .protocol.projects import read_pak, build_ppak, project_meta, referenced_sounds
    try:
        with _operation_lock:
            raw = Path(source).expanduser().read_bytes()
            meta, projects, sounds = read_pak(raw)
            if project not in projects or type(project) is not int:
                raise ValueError("Project absent from source")
            selected = referenced_sounds(projects[project], sounds) if include_samples else {}
            if include_samples:
                from .safety.library import references, sound_index
                missing = set(references({project: projects[project]})) - set(sound_index(selected))
                if missing:
                    raise ValueError(f"Referenced samples absent from restore source: {sorted(missing)}")
            data = build_ppak(project, projects[project], project_meta(meta, now=0), selected, now=315532800)
            cache = _journal.directory.parent / "restore-packages"
            cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            path = cache / (hashlib.sha256(data).hexdigest() + ".ppak")
            if path.exists():
                if path.read_bytes() != data:
                    raise ValueError("Cached restore package changed")
            else:
                with path.open("xb") as stream:
                    stream.write(data)
                path.chmod(0o600)
            return _importer.import_ppak(path, project, backup_id, _device(), confirm, slot_map)
    except DeviceError as e:
        return _error(e)
    except (OSError, ValueError, EOFError, wave.Error, zipfile.BadZipFile, tarfile.TarError) as e:
        return {"error": type(e).__name__, "message": str(e)}


@server.tool(description="Export one project from a local backup to a portable .ppak, including only referenced samples. Preserves original WAV metadata. No device writes; refuses missing dependencies and existing outputs.")
def export_project(source: str, project: int, out: str, include_samples: bool = True) -> dict[str, Any]:
    try:
        return _export_project(source, project, out, include_samples)
    except DeviceError as e:
        return _error(e)
    except (OSError, ValueError, EOFError, wave.Error, zipfile.BadZipFile, tarfile.TarError) as e:
        return {"error": type(e).__name__, "message": str(e)}


@server.tool(description="List sample metadata and every stored pad reference across nine projects, including stale references and unreferenced slots. Optional source is a full local backup; otherwise reads the device.")
def list_samples(source: str | None = None) -> dict[str, Any]:
    try:
        with _operation_lock:
            return _list_samples(None if source else _device(), source)
    except DeviceError as e:
        return _error(e)
    except (OSError, ValueError, EOFError, wave.Error, zipfile.BadZipFile, tarfile.TarError) as e:
        return {"error": type(e).__name__, "message": str(e)}


@server.tool(description="Report all stored uses of one sample across nine projects. source optionally reads a full local backup. Does not delete anything.")
def sample_usage(slot: int, source: str | None = None) -> dict[str, Any]:
    if type(slot) is not int or not 1 <= slot <= 999:
        return {"error": "InvalidDestination", "message": "slot must be 1..999"}
    report = list_samples(source)
    if "error" in report:
        return report
    found = next((s for s in report["samples"] if s["slot"] == slot), None)
    return {"slot": slot, "present": found is not None, "sample": found,
            "references": found["references"] if found else report["stale_references"].get(slot, [])}


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
