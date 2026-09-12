"""Decode a whole project TAR into JSON: bpm, pad records, patterns, scenes, settings, fx_settings.

Field meanings and their verification status are in docs/research/pattern-encoding.md.
Everything here is a pure function of bytes; the MCP tool read_project feeds it either a live
project_tar() read or a project taken from a .pak/.ppak on disk. Type-1 (fader automation)
events are kept as a separate `automation` list and never decoded as notes; any other event
type is reported raw under `other` rather than guessed at.
"""

from __future__ import annotations

import struct

from . import patterns as enc
from .payloads import PAD_LABELS
from .projects import read_pak, unpack_project

EVENT_TYPE_PARAM = 1
SETTINGS_PARAMS = 48
SETTINGS_PARAMS_OFFSET = 24
SETTINGS_GROUP_BYTES = 216
FX_SELECTOR = 4
FX_PARAMS = 34
FX_PARAMS_OFFSET = 8
SCENES_TRAILER = enc.SCENES_HEAD + enc.SCENE_CHUNK * enc.SCENE_SLOTS   # 601
SONG_LENGTH_OFFSET = 11                                                # within the trailer


def _floats(data: bytes, start: int, count: int) -> list[float]:
    return [struct.unpack_from("<f", data, start + 4 * i)[0] for i in range(count)]


def decode_pattern_file(data: bytes) -> dict:
    """{bars, events, automation, other} with note and parameter events told apart by type."""
    decoded = enc.decode_pattern(data)
    events, automation, other = [], [], []
    for off in range(enc.PATTERN_HEADER, len(data), enc.EVENT_SIZE):
        raw = data[off:off + enc.EVENT_SIZE]
        pos, b2, b3, b4, value, b7 = struct.unpack("<HBBBHB", raw)
        kind = b2 & 7
        if kind == enc.EVENT_TYPE_NOTE:
            events.append({"pad": (b2 >> 3) + 1, "tick": pos, "duration": value, "note": b3,
                           "byte4": b4, "byte7": b7})
        elif kind == EVENT_TYPE_PARAM:
            automation.append({"tick": pos, "param": b3, "value": value, "byte4": b4,
                               "padbits": b2 >> 3, "byte7": b7})
        else:
            other.append({"tick": pos, "type": kind, "raw": raw.hex()})
    return {"bars": decoded["bars"], "events": events, "automation": automation, "other": other}


def decode_scenes(data: bytes) -> dict:
    if len(data) not in enc.SCENES_SIZES:
        raise ValueError(f"scenes file must be one of {enc.SCENES_SIZES} bytes, not {len(data)}")
    head = data[:enc.SCENES_HEAD]
    scenes = []
    for k in range(1, enc.SCENE_SLOTS + 1):
        chunk = enc.decode_scene(data, k)
        if chunk["A"] or chunk["B"] or chunk["C"] or chunk["D"]:
            scenes.append({"scene": k, **chunk})
    trailer = data[SCENES_TRAILER:]
    out = {
        "size": len(data),
        "live": {"A": head[1], "B": head[2], "C": head[3], "D": head[4], "num": head[5], "den": head[6]},
        "scenes": scenes,
        # Upstream calls this the scene count; three observations on this device say it is the
        # currently selected scene, 1-based (docs/handoff/session-2026-09-11-handoff.md).
        "selected_scene": struct.unpack_from(">I", trailer, 0)[0],
        "trailer": trailer.hex(),
    }
    if len(trailer) > SONG_LENGTH_OFFSET:
        length = trailer[SONG_LENGTH_OFFSET]
        out["song"] = list(trailer[SONG_LENGTH_OFFSET + 1:SONG_LENGTH_OFFSET + 1 + length])
    return out


def decode_settings(data: bytes) -> dict:
    if len(data) not in enc.SETTINGS_SIZES:
        raise ValueError(f"settings file must be one of {enc.SETTINGS_SIZES} bytes, not {len(data)}")
    return {
        "size": len(data),
        "bpm": enc.decode_bpm(data),
        "head": data[:SETTINGS_PARAMS_OFFSET].hex(),
        "params": _floats(data, SETTINGS_PARAMS_OFFSET, SETTINGS_PARAMS),
        "group_bytes": list(data[SETTINGS_GROUP_BYTES:SETTINGS_GROUP_BYTES + 4]),
        "tail": data[SETTINGS_GROUP_BYTES + 4:].hex(),
    }


def decode_fx_settings(data: bytes) -> dict:
    if len(data) < FX_PARAMS_OFFSET + 4 * FX_PARAMS:
        raise ValueError(f"fx_settings file is too short: {len(data)} bytes")
    return {
        "size": len(data),
        "selector": data[FX_SELECTOR],
        "head": data[:FX_PARAMS_OFFSET].hex(),
        "params": _floats(data, FX_PARAMS_OFFSET, FX_PARAMS),
        "tail": data[FX_PARAMS_OFFSET + 4 * FX_PARAMS:].hex(),
    }


def decode_pad_record(record: bytes) -> dict:
    if len(record) not in enc.PAD_RECORD_SIZES:
        raise ValueError(f"pad record must be one of {enc.PAD_RECORD_SIZES} bytes, not {len(record)}")
    return {"stored_slot": struct.unpack_from("<H", record, 1)[0],
            "stored_length": struct.unpack_from("<I", record, 8)[0],
            "raw": record.hex()}


def decode_project(tar: bytes) -> dict:
    """Every member of a project TAR, decoded. Raises ValueError on a malformed archive."""
    files = unpack_project(tar)
    pads = []
    for group in enc.GROUPS:
        for pad in range(1, 13):
            name = f"pads/{group.lower()}/p{pad:02d}"
            if name not in files:
                raise ValueError(f"project has no {name}")
            pads.append({"group": group, "pad": pad, "label": PAD_LABELS[pad], **decode_pad_record(files[name])})
    patterns = []
    for name in sorted(files):
        if not name.startswith("patterns/"):
            continue
        stem = name[len("patterns/"):]
        if len(stem) != 3 or stem[0] not in "abcd" or not stem[1:].isdigit():
            raise ValueError(f"unrecognised pattern member {name}")
        patterns.append({"group": stem[0].upper(), "index": int(stem[1:]), **decode_pattern_file(files[name])})
    settings = decode_settings(files["settings"]) if "settings" in files else None
    return {
        "bpm": settings["bpm"] if settings else None,
        "pads": pads,
        "patterns": patterns,
        "scenes": decode_scenes(files["scenes"]) if "scenes" in files else None,
        "settings": settings,
        "fx_settings": decode_fx_settings(files["fx_settings"]) if "fx_settings" in files else None,
        "members": {name: len(data) for name, data in files.items()},
    }


def project_from_pak(data: bytes, project: int) -> bytes:
    """The TAR of one project inside a .pak/.ppak."""
    _, projects, _ = read_pak(data)
    if project not in projects:
        raise ValueError(f"pak holds no project P{project:02d}")
    return projects[project]
