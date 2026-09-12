"""Name every byte that differs between two copies of one project.

This is the tool for the diff method in docs/research/fx-and-settings-map.md: the owner changes
one control, the agent diffs the project before and after, and the changed offset gets a name.
`settings` and `fx_settings` are float tables, so a change is reported as the field it sits in
(`settings.params[17]`, `fx_settings.selector`) with before/after values and the n/256 step the
device quantises knobs to; every other member is reported as byte ranges, so a stray pad or
pattern edit during a mapping session is visible rather than mistaken for a knob.

Pure functions of two TARs; the MCP tool diff_project feeds them backups or a live read.
"""

from __future__ import annotations

import struct

from . import decode as D
from . import patterns as enc
from .generate import _ranges
from .projects import unpack_project

FADER_SLOTS_PER_GROUP = 12   # settings params index = 12 * group + fader function id (hypothesis)


def _n256(value: float):
    """The knob step a float sits on, when it is one; None for -1 (unset) or off-grid values."""
    if value < 0:
        return None
    step = value * 256
    return int(round(step)) if abs(step - round(step)) < 1e-4 else None


def _float_fields(member: str, old: bytes, new: bytes, offset: int, count: int, label: str) -> list[dict]:
    out = []
    for i in range(count):
        at = offset + 4 * i
        if len(old) < at + 4 or len(new) < at + 4:
            break
        a = struct.unpack_from("<f", old, at)[0]
        b = struct.unpack_from("<f", new, at)[0]
        if a != b:
            entry = {"member": member, "field": f"{label}[{i}]", "index": i, "offset": at,
                     "before": a, "after": b, "before_n256": _n256(a), "after_n256": _n256(b)}
            if member == "settings":
                entry["group"] = "ABCD"[i // FADER_SLOTS_PER_GROUP] if i < 4 * FADER_SLOTS_PER_GROUP else None
                entry["function"] = i % FADER_SLOTS_PER_GROUP
            out.append(entry)
    return out


def _byte_fields(member: str, old: bytes, new: bytes, offsets: dict[int, str]) -> list[dict]:
    out = []
    for at, name in offsets.items():
        if at < len(old) and at < len(new) and old[at] != new[at]:
            out.append({"member": member, "field": name, "offset": at, "before": old[at], "after": new[at]})
    return out


def diff_settings(old: bytes, new: bytes) -> list[dict]:
    if old == new:
        return []
    out = []
    a, b = struct.unpack_from("<f", old, enc.BPM_OFFSET)[0], struct.unpack_from("<f", new, enc.BPM_OFFSET)[0]
    if a != b:
        out.append({"member": "settings", "field": "bpm", "offset": enc.BPM_OFFSET, "before": a, "after": b})
    out += _float_fields("settings", old, new, D.SETTINGS_PARAMS_OFFSET, D.SETTINGS_PARAMS, "params")
    out += _byte_fields("settings", old, new,
                        {D.SETTINGS_GROUP_BYTES + g: f"group_bytes[{g}] (group {'ABCD'[g]})" for g in range(4)})
    covered = set(range(enc.BPM_OFFSET, enc.BPM_OFFSET + 4)) | set(range(D.SETTINGS_PARAMS_OFFSET, D.SETTINGS_PARAMS_OFFSET + 4 * D.SETTINGS_PARAMS)) \
        | set(range(D.SETTINGS_GROUP_BYTES, D.SETTINGS_GROUP_BYTES + 4))
    out += _unnamed("settings", old, new, covered)
    return out


def diff_fx_settings(old: bytes, new: bytes) -> list[dict]:
    if old == new:
        return []
    out = _byte_fields("fx_settings", old, new, {D.FX_SELECTOR: "selector"})
    out += _float_fields("fx_settings", old, new, D.FX_PARAMS_OFFSET, D.FX_PARAMS, "params")
    covered = {D.FX_SELECTOR} | set(range(D.FX_PARAMS_OFFSET, D.FX_PARAMS_OFFSET + 4 * D.FX_PARAMS))
    out += _unnamed("fx_settings", old, new, covered)
    return out


def _unnamed(member: str, old: bytes, new: bytes, covered: set[int]) -> list[dict]:
    """Byte ranges outside the named fields, so nothing that moved goes unreported."""
    if len(old) != len(new):
        return [{"member": member, "field": "size", "before": len(old), "after": len(new)}]
    out = []
    for r in _ranges(old, new):
        span = set(range(r["offset"], r["offset"] + r["length"]))
        if span - covered:
            out.append({"member": member, "field": "unnamed", **r})
    return out


def diff_project_floats(old_tar: bytes, new_tar: bytes) -> dict:
    """{settings: [...], fx_settings: [...], other: [...], identical: bool}."""
    old, new = unpack_project(old_tar), unpack_project(new_tar)
    out = {"settings": [], "fx_settings": [], "other": [], "identical": old == new}
    for member in sorted(set(old) | set(new)):
        a, b = old.get(member), new.get(member)
        if a == b:
            continue
        if a is None or b is None:
            out["other"].append({"member": member, "change": "added" if a is None else "removed",
                                 "bytes": len(b if a is None else a)})
        elif member == "settings":
            out["settings"] = diff_settings(a, b)
        elif member == "fx_settings":
            out["fx_settings"] = diff_fx_settings(a, b)
        elif len(a) != len(b):
            out["other"].append({"member": member, "change": "resized", "before": len(a), "after": len(b)})
        else:
            out["other"].append({"member": member, "change": "patched", "ranges": _ranges(a, b)})
    return out
