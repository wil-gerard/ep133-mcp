"""set_pad / set_slot: per-pad and per-slot sound parameters over FILE_METADATA_SET.

The write is the one this server already makes to assign a pad ({'sym': slot}); what changes
is the field set. Upstream PROTOCOL.md sections 5-6 and 10 record what the device honours and
the three rules that bite:

- enums are strings; an int is rejected;
- `sound.playmode` only gates playback when written with its paired `envelope.release`
  (oneshot -> 255, key -> 15), so the pair is completed here unless the caller sets release;
- writes are partial-merge, so only the fields the caller named are sent.

None of that has been observed on this hardware, so the result is built from a read-back: every
requested field is reported as applied (the device stored the value), changed (it stored a
different one - clamped or coerced), or dropped (the key is not in the record afterwards), and
any other key whose value moved is listed as a side effect. An ACK proves nothing. Whether a
value persists across a power-cycle is a separate claim the result leaves unmade.

Every write is backup-gated and confirmed like an install, and journalled with the previous
values so undo_last_pad_change can write them back.
"""

from __future__ import annotations

import hashlib
import json
import math
import secrets
import time

from ..device import DeviceError
from ..protocol.payloads import PAD_LABELS
from .errors import InvalidDestination, VerificationFailed
from .install import device_id
from .preflight import validate_destination

CONFIRM_SECONDS = 300
PLAYMODES = ("oneshot", "key", "legato")
TIME_MODES = ("off", "bar", "bpm")
PAIRED_RELEASE = {"oneshot": 255, "key": 15}
MAX_NAME = 20
LIBRARY_SLOT_RANGE = (1, 999)


def _int(low, high):
    def check(value):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"must be an integer {low}..{high}")
        return value
    return check


def _number(low, high):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or not low <= value <= high:
            raise ValueError(f"must be a number {low}..{high}")
        return float(value)
    return check


def _enum(values):
    def check(value):
        if not isinstance(value, str) or value not in values:
            raise ValueError(f"must be one of {', '.join(values)} (a string: the device rejects integers)")
        return value
    return check


def _bool(value):
    if type(value) is not bool:
        raise ValueError("must be true or false")
    return value


def _name(value):
    if not isinstance(value, str) or not value or len(value) > MAX_NAME or not value.isascii() \
            or "\0" in value or not value.isprintable():
        raise ValueError(f"must be 1..{MAX_NAME} printable ASCII characters")
    return value


SOUND_FIELDS = {
    "sound.playmode": _enum(PLAYMODES),
    "envelope.attack": _int(0, 255),
    "envelope.release": _int(0, 255),
    "sound.pitch": _number(-12.0, 12.0),
    "sound.amplitude": _int(0, 200),        # 200 observed on P03 A01/A02 (docs/research/pad-metadata.md)
    "sound.pan": _int(-16, 16),
    "time.mode": _enum(TIME_MODES),
}
# Written to a pad record on OS 2.5.1 the device drops these three and leaves the slot alone
# (docs/research/pad-params-proof.md), so they are slot-only here.
SLOT_ONLY_FIELDS = {
    "sound.bpm": _number(1.0, 200.0),       # 240 is rejected upstream; bars clamp to powers of two
    "sound.bars": _number(0.0625, 64.0),
    "sound.rootnote": _int(0, 127),
}
PAD_FIELDS = SOUND_FIELDS | {
    "sample.start": _int(0, 2**31 - 1),
    "sample.end": _int(1, 2**31 - 1),
    "sound.mutegroup": _bool,
    "midi.channel": _int(0, 15),
}
SLOT_FIELDS = SOUND_FIELDS | SLOT_ONLY_FIELDS | {
    "name": _name,
    "sound.loopstart": _int(-1, 2**31 - 1),   # trim-only on this device: never an auto-loop
    "sound.loopend": _int(-1, 2**31 - 1),
}


def validate_params(params, allowed: dict, what: str) -> dict:
    """The fields to send, in the caller's order, or InvalidDestination naming the first bad one."""
    if not isinstance(params, dict) or not params:
        raise InvalidDestination(f"{what} needs a non-empty params object",
                                 expected=sorted(allowed), next_step="Pass the fields to change.")
    out = {}
    for key, value in params.items():
        if key not in allowed:
            if key in SLOT_ONLY_FIELDS:
                raise InvalidDestination(f"{key} lives on the slot, not the pad", observed=key,
                                         expected=sorted(allowed),
                                         next_step="Write it with set_slot; a pad SET drops it.")
            raise InvalidDestination(f"Unknown or read-only field for {what}", observed=key,
                                     expected=sorted(allowed), next_step="Remove the field.")
        try:
            out[key] = allowed[key](value)
        except ValueError as e:
            raise InvalidDestination(f"Invalid value for {key}", observed=value, expected=str(e),
                                     next_step="Correct the value.") from e
    start, end = out.get("sample.start"), out.get("sample.end")
    if start is not None and end is not None and end <= start:
        raise InvalidDestination("sample.end must be greater than sample.start",
                                 observed={"sample.start": start, "sample.end": end},
                                 next_step="Correct the trim.")
    if "sound.playmode" in out and "envelope.release" not in out and out["sound.playmode"] in PAIRED_RELEASE:
        out["envelope.release"] = PAIRED_RELEASE[out["sound.playmode"]]
    return out


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)
    return a == b


def compare(requested: dict, before: dict, after: dict) -> dict:
    """What the device did with each requested field, plus every other key that moved."""
    applied, changed, dropped = {}, {}, []
    for key, value in requested.items():
        if key not in after:
            dropped.append(key)
        elif _same(after[key], value):
            applied[key] = after[key]
        else:
            changed[key] = {"requested": value, "stored": after[key]}
    side_effects = {key: {"before": before.get(key), "after": after.get(key)}
                    for key in sorted(set(before) | set(after))
                    if key not in requested and not _same(before.get(key), after.get(key))}
    return {"applied": applied, "changed": changed, "dropped": dropped, "side_effects": side_effects}


class ParamWriter:
    def __init__(self, backups, journal):
        self.backups = backups
        self.journal = journal
        self._confirmations = {}

    def _confirm(self, binding: str, confirm, impact: dict, undo: str):
        previous = self._confirmations.pop(confirm, None) if confirm else None
        if previous is not None and previous[0] == binding and previous[1] >= time.monotonic():
            return None
        self._confirmations = {k: v for k, v in self._confirmations.items() if v[1] >= time.monotonic()}
        token = secrets.token_urlsafe(32)
        self._confirmations[token] = (binding, time.monotonic() + CONFIRM_SECONDS)
        return {"status": "needs_confirmation", "impact": impact, "confirm": token, "undo": undo,
                "instruction": "Show this exact impact to the owner; repeat only after approval."}

    # ---- pads ----------------------------------------------------------------

    def set_pad(self, project, group, pad, params, backup_id, device, confirm=None):
        node = validate_destination(project, group, pad)
        fields = validate_params(params, PAD_FIELDS, "set_pad")
        live = self.backups.require_current(backup_id, device)
        current = device.read_pad(project, group, pad)
        before, slot = current["pad_metadata"], current["slot_metadata"]
        if not current["sym"]:
            raise InvalidDestination("Pad has no sample; parameters need a sound to act on",
                                     observed={"project": project, "group": group, "pad": pad, "sym": 0},
                                     next_step="Install or assign a sample on this pad first.")
        frames = (slot or {}).get("sample.end")
        if type(frames) is int and any(fields.get(k, 0) > frames for k in ("sample.start", "sample.end")):
            raise InvalidDestination("Trim exceeds the sample's length",
                                     observed={k: fields[k] for k in ("sample.start", "sample.end") if k in fields},
                                     expected=f"at most {frames} frames (slot {current['sym']})",
                                     next_step="Trim within the sample.")
        target = {"project": project, "group": group, "pad": pad, "label": PAD_LABELS[pad], "node": node,
                  "sym": current["sym"]}
        impact = target | {"before": {k: before.get(k) for k in fields}, "after": fields,
                           "auto_paired": {"envelope.release": fields["envelope.release"]}
                           if "sound.playmode" in fields and "envelope.release" not in (params or {})
                           and "envelope.release" in fields else {}}
        binding = hashlib.sha256(json.dumps({"backup_id": backup_id, "device_id": device_id(live),
                                             "impact": impact}, sort_keys=True).encode()).hexdigest()
        pending = self._confirm(binding, confirm, impact,
                                "undo_last_pad_change writes the before values back")
        if pending:
            return pending
        entry = target | {"file_id": node, "kind": "pad", "requested": fields,
                          "before": {k: before[k] for k in fields if k in before},
                          "before_record": before, "status": "pending"}
        record = self.journal.create(device_id(live), backup_id, [entry], operation="set_pad")
        return self._write(record, entry, device, lambda: device.read_pad(project, group, pad)["pad_metadata"])

    # ---- slots ---------------------------------------------------------------

    def set_slot(self, slot, params, backup_id, device, confirm=None):
        if type(slot) is not int or not LIBRARY_SLOT_RANGE[0] <= slot <= LIBRARY_SLOT_RANGE[1]:
            raise InvalidDestination(f"Slot must be {LIBRARY_SLOT_RANGE[0]}..{LIBRARY_SLOT_RANGE[1]}",
                                     observed=slot, next_step="Pass a library slot number.")
        fields = validate_params(params, SLOT_FIELDS, "set_slot")
        live = self.backups.require_current(backup_id, device)
        device.greet()
        device.begin_read()
        before = device.metadata(slot)
        if not before or "crc" not in before:
            raise InvalidDestination("Slot is empty or unreadable", observed={"slot": slot, "metadata": before},
                                     next_step="Pick an occupied slot (list_pads shows which are stored).")
        frames = before.get("sample.end")
        if type(frames) is int and any(fields.get(k, -1) > frames for k in ("sound.loopstart", "sound.loopend")):
            raise InvalidDestination("Loop point exceeds the sample's length",
                                     observed={k: fields[k] for k in ("sound.loopstart", "sound.loopend") if k in fields},
                                     expected=f"at most {frames} frames", next_step="Trim within the sample.")
        target = {"slot": slot, "name": before.get("name"), "crc": before.get("crc")}
        impact = target | {"before": {k: before.get(k) for k in fields}, "after": fields,
                           "auto_paired": {"envelope.release": fields["envelope.release"]}
                           if "sound.playmode" in fields and "envelope.release" not in (params or {})
                           and "envelope.release" in fields else {}}
        binding = hashlib.sha256(json.dumps({"backup_id": backup_id, "device_id": device_id(live),
                                             "impact": impact}, sort_keys=True).encode()).hexdigest()
        pending = self._confirm(binding, confirm, impact,
                                "undo_last_pad_change writes the before values back")
        if pending:
            return pending
        entry = target | {"file_id": slot, "kind": "slot", "requested": fields,
                          "before": {k: before[k] for k in fields if k in before},
                          "before_record": before, "status": "pending"}
        record = self.journal.create(device_id(live), backup_id, [entry], operation="set_pad")

        def read_back():
            device.greet()
            device.begin_read()
            return device.metadata(slot)
        return self._write(record, entry, device, read_back)

    # ---- shared write / undo -------------------------------------------------

    def _write(self, record, entry, device, read_back):
        self.backups.invalidate()
        entry["status"] = "write_attempted"
        self.journal.save(record)
        try:
            device.set_metadata(entry["file_id"], entry["requested"])
            after = read_back()
            if not isinstance(after, dict):
                raise VerificationFailed("Record unreadable after write", observed=after,
                                         next_step="Read the pad or slot again before retrying.")
        except (DeviceError, ValueError, OSError) as e:
            entry["status"] = "failed"
            entry["failure"] = {"error": type(e).__name__, "message": str(e)}
            record["status"] = "partial"
            self.journal.save(record)
            return self._result(record, entry)
        outcome = compare(entry["requested"], entry["before_record"], after)
        entry.update(outcome, after_record=after)
        entry["status"] = "written" if not outcome["changed"] and not outcome["dropped"] else "written_with_differences"
        record["status"] = "written"
        self.journal.save(record)
        return self._result(record, entry)

    @staticmethod
    def _result(record, entry):
        out = {k: v for k, v in entry.items() if k not in ("before_record", "after_record")}
        out.update(status=entry["status"], journal_id=record["id"], power_cycle_verified=False,
                   verified="every requested field re-read from the device after the write",
                   next_step="Play the pad to check the result; take a fresh backup before another write.")
        return out

    def undo(self, device):
        greeting = device.greet()
        identity = device_id({"sku": greeting.sku, "serial": greeting.serial})
        record = self.journal.latest(identity, operation="set_pad")
        if record is None:
            return {"status": "nothing_to_undo"}
        if record["status"] in ("undone", "partial"):
            return {"status": record["status"], "journal_id": record["id"],
                    "note": "nothing was written" if record["status"] == "partial" else "already undone"}
        self.backups.invalidate()
        entry = record["entries"][0]
        prior = entry["before"]
        missing = [k for k in entry["requested"] if k not in prior]
        if not prior:
            entry["undo_failure"] = f"the record had none of {sorted(entry['requested'])} before; nothing to write back"
            record["status"] = "undo_partial"
            self.journal.save(record)
            return {"status": record["status"], "journal_id": record["id"], "undo_failure": entry["undo_failure"]}
        entry["status"] = "undo_attempted"
        self.journal.save(record)
        try:
            device.set_metadata(entry["file_id"], prior)
            device.greet()
            device.begin_read()
            after = device.metadata(entry["file_id"])
            if not isinstance(after, dict):
                raise VerificationFailed("Record unreadable after undo", observed=after,
                                         next_step="Read the pad or slot again.")
        except (DeviceError, ValueError, OSError) as e:
            entry["undo_failure"] = str(e)
            record["status"] = "undo_partial"
            self.journal.save(record)
            return {"status": record["status"], "journal_id": record["id"], "undo_failure": entry["undo_failure"]}
        outcome = compare(prior, entry.get("after_record", {}), after)
        entry.update(undo=outcome)
        restored = not outcome["changed"] and not outcome["dropped"]
        entry["status"] = "undone" if restored else "undo_partial"
        record["status"] = entry["status"]
        self.journal.save(record)
        return {"status": record["status"], "journal_id": record["id"], "file_id": entry["file_id"],
                "kind": entry["kind"], "restored": prior, **outcome,
                "not_restorable": missing, "power_cycle_verified": False}
