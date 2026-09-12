"""Delete library slots that nothing references.

Emptying a project frees no sample space: a pad record only points at a slot. Removing the
sound itself is this operation, and it is the only destructive thing in the server that cannot
be undone by `undo_last_install` - a deleted slot's PCM is gone from the device, and the only
way back is a Sample Tool restore of a backup that still holds it.

Three guards stand in for that missing undo:

- a verified current backup, as `install_kit` requires;
- a refusal to delete any slot that a pad record in ANY of the nine projects stores, because a
  stored slot that no longer exists resolves to 0 and silently reads as an empty pad - deleting
  a referenced slot is how you manufacture the stale references this device is already full of;
- a confirmation round trip carrying the exact slots, sizes and the projects that would be
  affected, so the owner sees what goes.

FILE_DELETE itself is documented upstream and UNVERIFIED on this hardware, so every result
reports what the device did per slot, re-read from the device rather than taken from the
command's status. A device that ignores the command answers ok and changes nothing; that shows
up here as `status: 'not_deleted'`, never as success.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time

from .backup import snapshot
from .errors import InvalidDestination
from .install import device_id

LIBRARY_SLOT_RANGE = (1, 999)
MAX_DELETE = 128
CONFIRM_SECONDS = 300


def referencing_pads(live: dict) -> dict[int, list[dict]]:
    """{slot: [{project, group, pad, stored_length}]} for every stored slot in every project."""
    out: dict[int, list[dict]] = {}
    for (project, group, pad), (slot, length) in live["pads"].items():
        if slot:
            out.setdefault(slot, []).append(
                {"project": project, "group": group, "pad": pad, "stored_length": length})
    return out


class Deleter:
    def __init__(self, backups, journal):
        self.backups = backups
        self.journal = journal
        self._confirmations = {}

    def plan(self, slots, live):
        if not isinstance(slots, (list, tuple)) or not slots or len(slots) > MAX_DELETE:
            raise InvalidDestination(f"Expected 1..{MAX_DELETE} library slots",
                                     next_step="Pass the slots to delete.")
        wanted = []
        for slot in slots:
            if type(slot) is not int or not LIBRARY_SLOT_RANGE[0] <= slot <= LIBRARY_SLOT_RANGE[1]:
                raise InvalidDestination(f"Slot must be {LIBRARY_SLOT_RANGE[0]}..{LIBRARY_SLOT_RANGE[1]}",
                                         observed=slot, next_step="Correct the slot list.")
            if slot in wanted:
                raise InvalidDestination("Slot given twice", observed=slot,
                                         next_step="Correct the slot list.")
            wanted.append(slot)
        references = referencing_pads(live)
        referenced = {slot: references[slot] for slot in wanted if slot in references}
        if referenced:
            raise InvalidDestination(
                "Refusing to delete a slot a project still stores",
                observed={str(slot): pads for slot, pads in referenced.items()},
                next_step="Clear those pads first, or leave the slot in place. A stored slot that "
                          "no longer exists reads as an empty pad and cannot be told from one.")
        missing = [slot for slot in wanted if slot not in live["slots"]]
        if missing:
            raise InvalidDestination("Slot is not in the library", observed=missing,
                                     next_step="Re-read the library; it may already be gone.")
        return wanted

    def delete(self, slots, backup_id, device, confirm=None):
        live = self.backups.require_current(backup_id, device)
        wanted = self.plan(slots, live)
        # The owner needs to recognise what goes, so the impact carries the device's own
        # metadata for each slot, not just its number.
        device.begin_read()
        impact = []
        for slot in wanted:
            meta = device.metadata(slot) or {}
            impact.append({"slot": slot, "name": meta.get("name"), "frames": meta.get("sample.end"),
                           "crc": meta.get("crc"), "referenced_by": []})
        binding = hashlib.sha256(json.dumps({"backup_id": backup_id, "device_id": device_id(live),
                                             "slots": wanted}, sort_keys=True).encode()).hexdigest()
        previous = self._confirmations.pop(confirm, None) if confirm else None
        if previous is None or previous[0] != binding or previous[1] < time.monotonic():
            self._confirmations = {k: v for k, v in self._confirmations.items()
                                   if v[1] >= time.monotonic()}
            token = secrets.token_urlsafe(32)
            self._confirmations[token] = (binding, time.monotonic() + CONFIRM_SECONDS)
            return {"status": "needs_confirmation", "impact": impact, "confirm": token,
                    "undo": "none: a deleted slot is gone from the device and only a Sample Tool "
                            "restore of a backup that still holds it brings the audio back",
                    "instruction": "Show this exact impact to the owner; repeat only after approval."}
        record = self.journal.create(device_id(live), backup_id,
                                     [{"slot": slot, "status": "pending"} for slot in wanted],
                                     operation="delete_samples")
        self.backups.invalidate()
        results = []
        for entry in record["entries"]:
            slot = entry["slot"]
            entry["status"] = "delete_attempted"
            self.journal.save(record)
            try:
                gone = device.delete_slot(slot)
            except Exception as e:                       # noqa: BLE001 - reported, never swallowed
                entry["status"] = "failed"
                entry["failure"] = {"error": type(e).__name__, "message": str(e),
                                    **getattr(e, "detail", {})}   # status + the device's reason string
                self.journal.save(record)
                results.append(dict(entry))
                record["status"] = "partial"
                self.journal.save(record)
                break
            entry["status"] = "deleted" if gone else "not_deleted"
            self.journal.save(record)
            results.append(dict(entry))
        else:
            record["status"] = "deleted"
            self.journal.save(record)
        after = snapshot(device)
        next_step = "Take a fresh backup: the verified one is now stale."
        if any(e.get("failure", {}).get("reason") == "failed to delete" for e in results):
            # Observed on OS 2.5.1 for a Sample Tool slot (704) and an MCP-uploaded, unreferenced
            # slot (30) alike, 2026-09-11/12 (docs/research/delete-proof.md): the firmware answers
            # FILE_DELETE and refuses it. Nothing was removed and the backup is still current.
            next_step = ("The device refused the delete ('failed to delete'): FILE_DELETE as sent "
                         "here does not remove slots on this OS. Delete with Sample Tool instead; "
                         "nothing changed on the device.")
        return {"status": record["status"], "journal_id": record["id"], "entries": results,
                "library_slots_before": len(live["slots"]), "library_slots_after": len(after["slots"]),
                "free_bytes": device.sample_root().free_space_in_bytes,
                "verified": "each slot re-read from the device after its delete",
                "next_step": next_step}
