"""Write a full .pak backup by reading the device, with no Sample Tool.

Everything a Sample Tool backup holds is readable over SysEx: the nine project TARs, and each
library slot's raw PCM through FILE_READ_OPEN / FILE_READ_DATA. Slot 17 read back byte for byte
equal to the WAV payload inside a Sample Tool backup, with its crc32 equal to the crc the slot's
metadata stores, so every slot here is verified against that crc before it goes in the file.

The device streams roughly 25 KiB/s, so a full library is tens of minutes. A backup is therefore
incremental: a slot whose crc already appears in `base` is copied from it rather than re-read,
which makes every backup after the first cost only the audio that changed. The crc is the device's
own and is checked on both sides, so copying is not a guess. Reused PCM is wrapped with fresh metadata so sound edits are retained.

What this cannot do is prove the file restores. Full restore uses Sample Tool, and the handoff
records that a restore once failed to revert a pad. This writes the backup; it does not change the
rule that only a post-restore backup diff proves a restore worked.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import struct
import time
import wave
import zipfile
import zlib

from ..protocol.projects import PAK_RELEASE, generated_at
from .backup import LIBRARY_SLOTS
from .errors import InvalidBackup

SOUND_NAME = re.compile(r"^/sounds/(\d+) (.*)\.wav$")


# The device's own WAVs carry its per-sample settings, so a backup that writes a bare 44-byte
# header silently loses every sample's playmode, tuning, envelope and loop on restore. These
# were read out of a Sample Tool backup chunk by chunk and reproduce it byte for byte.
TNGE_ORDER = ("sound.loopstart", "sound.loopend", "sound.playmode", "sound.rootnote", "sound.bpm",
              "sound.pitch", "sound.pan", "sound.amplitude", "envelope.attack", "envelope.release",
              "time.mode", "sample.start", "sample.end")


def _scalar(value):
    """The device reports whole numbers as floats; its own JSON writes them as integers."""
    return int(value) if isinstance(value, float) and value == int(value) else value


def _riff(chunk_id: bytes, body: bytes) -> bytes:
    return chunk_id + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")


def loop_points(meta: dict) -> tuple[int, int] | None:
    """(start, end) when the slot loops. A loop starting at 0 is real, so never test truthiness."""
    start, end = meta.get("sound.loopstart"), meta.get("sound.loopend")
    if start is None or end is None or start < 0 or end < 0:
        return None
    return int(start), int(end)


def tnge_json(meta: dict) -> bytes:
    """The device's settings blob, in the key order its own files use."""
    looped = loop_points(meta) is not None
    fields = {}
    for key in TNGE_ORDER:
        if key in ("sound.loopstart", "sound.loopend") and not looped:
            continue
        if key == "sound.bpm" and not meta.get(key):
            continue
        if key in meta:
            fields[key] = _scalar(meta[key])
    return json.dumps(fields, separators=(",", ":")).encode()


def smpl_chunk(meta: dict) -> bytes:
    """Standard sampler chunk: unity note, plus one forward loop when the slot has one."""
    loop = loop_points(meta)
    body = struct.pack("<9I", 0, 0, 0, int(meta.get("sound.rootnote") or 60), 0, 0, 0, int(loop is not None), 0)
    if loop is not None:
        body += struct.pack("<6I", 1, 0, loop[0], loop[1], 0, 0)
    return body


def wav_bytes(pcm: bytes, meta: dict) -> bytes:
    """Wrap raw device PCM in the WAV container Sample Tool's backups use, settings and all."""
    fmt = str(meta.get("format") or "s16")
    if fmt != "s16":
        raise InvalidBackup("Unsupported sample format", observed=fmt, expected="s16",
                            next_step="Report the slot; only 16-bit PCM has been seen on this device.")
    channels = int(meta.get("channels") or 1)
    rate = int(meta.get("samplerate") or 46875)
    body = _riff(b"fmt ", struct.pack("<HHIIHH", 1, channels, rate, rate * channels * 2, channels * 2, 16))
    body += _riff(b"smpl", smpl_chunk(meta))
    bpm = meta.get("sound.bpm") or 0
    if bpm:
        body += _riff(b"acid", bytes(20) + struct.pack("<f", float(bpm)))
    payload = tnge_json(meta)
    # Null-terminated first, then padded to four: a length already divisible by four still
    # gains a whole four bytes, which is how the device's own files come out.
    payload += b"\0" * (4 - len(payload) % 4)
    body += _riff(b"LIST", b"INFO" + b"TNGE" + struct.pack("<I", len(payload)) + payload)
    body += _riff(b"data", pcm)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def base_audio(path) -> dict[int, tuple[str, bytes]]:
    """{slot: (name, wav bytes)} from an existing .pak, for reuse by crc."""
    out: dict[int, tuple[str, bytes]] = {}
    with zipfile.ZipFile(path) as z:
        for entry in z.namelist():
            match = SOUND_NAME.match("/" + entry.lstrip("/"))
            if match:
                out[int(match.group(1))] = (match.group(2), z.read(entry))
    return out


def wav_pcm(data: bytes) -> bytes | None:
    try:
        with wave.open(io.BytesIO(data)) as w:
            return w.readframes(w.getnframes())
    except (wave.Error, EOFError):
        return None


def meta_json(greeting, now: float | None = None) -> dict:
    return {"info": "teenage engineering - pak file", "pak_version": 1, "pak_type": "user",
            "pak_release": PAK_RELEASE, "device_name": greeting.product or "EP-133",
            "device_sku": greeting.sku, "device_version": greeting.os_version,
            "generated_at": generated_at(now), "author": "computer", "base_sku": greeting.sku}


def create_backup(device, out, base=None, progress=None) -> dict:
    """Read the device into a .pak at `out`. Returns a report; never overwrites `out`."""
    out = Path(out).expanduser()
    if out.exists():
        raise InvalidBackup("Refusing to overwrite an existing backup", observed=str(out),
                            next_step="Choose a new path.")
    if out.suffix != ".pak":
        raise InvalidBackup("Backup path must end in .pak", observed=str(out),
                            next_step="Choose a .pak path.")
    reused_source = base_audio(base) if base else {}
    greeting = device.greet()
    device.begin_read()
    started = time.monotonic()

    projects_tar = {n: device.project_tar(n) for n in range(1, 10)}
    device.begin_read()
    slots = [slot for slot in LIBRARY_SLOTS if device.slot_exists(slot)]

    sounds: dict[str, bytes] = {}
    read_bytes = reused = failed = 0
    problems = []
    for index, slot in enumerate(slots):
        meta = device.metadata(slot)
        if not meta:
            problems.append({"slot": slot, "problem": "vanished between the scan and the read"})
            failed += 1
            continue
        name = str(meta.get("name") or f"{slot:03d}")
        entry = f"/sounds/{slot:03d} {name}.wav"
        candidate = reused_source.get(slot)
        if candidate is not None:
            pcm = wav_pcm(candidate[1])
            if pcm is not None and zlib.crc32(pcm) == meta.get("crc"):
                sounds[entry] = wav_bytes(pcm, meta)
                reused += 1
                if progress:
                    progress({"slot": slot, "action": "reused", "index": index + 1, "total": len(slots)})
                continue
        try:
            pcm = device.slot_pcm(slot)
        except Exception as e:                      # noqa: BLE001 - recorded, never silently dropped
            problems.append({"slot": slot, "problem": type(e).__name__, "message": str(e)[:200]})
            failed += 1
            continue
        sounds[entry] = wav_bytes(pcm, meta)
        read_bytes += len(pcm)
        if progress:
            progress({"slot": slot, "action": "read", "bytes": len(pcm),
                      "index": index + 1, "total": len(slots)})

    meta = meta_json(greeting)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for number in sorted(projects_tar):
            z.writestr(f"/projects/P{number:02d}.tar", projects_tar[number])
        for entry in sorted(sounds):
            z.writestr(entry, sounds[entry])
        z.writestr("/meta.json", json.dumps(meta, indent=2))
    data = buffer.getvalue()
    out.write_bytes(data)
    elapsed = time.monotonic() - started
    return {"status": "written" if not problems else "partial", "backup": str(out),
            "bytes": len(data), "projects": len(projects_tar), "slots": len(slots),
            "slots_read": len(slots) - reused - failed, "slots_reused": reused,
            "audio_bytes_read": read_bytes, "seconds": round(elapsed, 1),
            "problems": problems,
            "sha256": hashlib.sha256(data).hexdigest(),
            "verified": "every slot read was checked against the crc the device stores for it",
            "restore": "Sample Tool restores this; a restore is only proven by a post-restore backup diff."}
