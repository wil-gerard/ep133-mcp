"""Clear every stored pad in whole projects, so the slots they held can be deleted.

`delete_samples` refuses a slot any pad record stores, so emptying a library means emptying
the projects that point into it first. This is that step: one confirmation, one journal, every
stored pad of the named projects written to `sym 0` and read back. A pad that stores a slot the
library no longer has (a stale reference, already resolving empty) is cleared too, so the
project records come out clean rather than merely quiet.

The active project is refused: its records are the ones the device is playing from, and
nothing here has been written to it (docs/research/chop-proof.md and pad-params-proof.md were
all non-active projects). Switch the device to the project to keep and clear the rest.

`undo_last_clear` re-points each pad at its prior slot with the trim and playmode the record
held, when that slot still exists; a prior slot that has since been deleted stays cleared, with
`undo_note` saying so - the same rule the chop and install undos follow.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import tarfile
import time

from ..device import DeviceError
from ..protocol.payloads import PAD_LABELS, pad_node
from .errors import InvalidDestination, VerificationFailed
from .install import device_id, pad_record, verify_prior

CONFIRM_SECONDS = 300
RESTORE_KEYS = ("sample.start", "sample.end", "sound.playmode", "envelope.release")


class Clearer:
    REVERTABLE = ("cleared", "clear_attempted", "undo_attempted")

    def __init__(self, backups, journal):
        self.backups = backups
        self.journal = journal
        self._confirmations = {}

    def clear(self, projects, backup_id, device, confirm=None):
        if (not isinstance(projects, (list, tuple)) or not projects
                or any(type(p) is not int or not 1 <= p <= 9 for p in projects)
                or len(set(projects)) != len(projects)):
            raise InvalidDestination("Expected a list of distinct projects 1..9", observed=projects,
                                     next_step="Pass the projects whose pads to clear.")
        live = self.backups.require_current(backup_id, device)
        device.begin_read()
        active = device.active_project()
        if active in projects:
            raise InvalidDestination("Refusing to clear the active project", observed={"active_project": active},
                                     next_step="Switch the device to the project you are keeping, then retry.")
        targets = [(p, g, n, slot, length) for (p, g, n), (slot, length) in sorted(live["pads"].items())
                   if p in projects and slot]
        if not targets:
            return {"status": "nothing_to_clear", "projects": sorted(projects)}
        names = {}
        for _, _, _, slot, _ in targets:
            if slot in live["slots"] and slot not in names:
                names[slot] = (device.metadata(slot) or {}).get("name")
        entries = [{"project": p, "group": g, "pad": n, "label": PAD_LABELS[n], "node": pad_node(p, g, n),
                    "prior_slot": slot, "prior_length": length, "stale": slot not in live["slots"],
                    "name": names.get(slot)} for p, g, n, slot, length in targets]
        impact = {"projects": sorted(projects), "pads": len(entries),
                  "live": sum(not e["stale"] for e in entries), "stale": sum(e["stale"] for e in entries),
                  "slots_freed_for_delete": sorted({e["prior_slot"] for e in entries if not e["stale"]}),
                  "entries": [{k: e[k] for k in ("project", "group", "pad", "prior_slot", "name", "stale")}
                              for e in entries]}
        binding = hashlib.sha256(json.dumps({"backup_id": backup_id, "device_id": device_id(live),
                                             "impact": impact}, sort_keys=True).encode()).hexdigest()
        previous = self._confirmations.pop(confirm, None) if confirm else None
        if previous is None or previous[0] != binding or previous[1] < time.monotonic():
            self._confirmations = {k: v for k, v in self._confirmations.items() if v[1] >= time.monotonic()}
            token = secrets.token_urlsafe(32)
            self._confirmations[token] = (binding, time.monotonic() + CONFIRM_SECONDS)
            return {"status": "needs_confirmation", "impact": impact, "confirm": token,
                    "undo": "undo_last_clear re-points each pad at its prior slot while that slot exists",
                    "instruction": "Show this exact impact to the owner; repeat only after approval."}
        for e in entries:
            e["kind"], e["status"] = "pad", "pending"
        record = self.journal.create(device_id(live), backup_id, entries, operation="clear_pads")
        self.backups.invalidate()
        for entry in record["entries"]:
            project, group, pad = entry["project"], entry["group"], entry["pad"]
            try:
                device.begin_read()
                entry["before_record"] = device.pad_metadata(project, group, pad)
                entry["status"] = "clear_attempted"
                self.journal.save(record)
                device.set_metadata(entry["node"], {"sym": 0})
                note = verify_prior(device, project, group, pad, (0, 0))
                if note:
                    entry["note"] = note
                entry["status"] = "cleared"
                self.journal.save(record)
            except (DeviceError, ValueError, OSError, tarfile.TarError) as e:
                entry["status"] = "failed"
                entry["failure"] = {"error": type(e).__name__, "message": str(e), **getattr(e, "detail", {})}
                record["status"] = "partial"
                self.journal.save(record)
                break
        else:
            record["status"] = "cleared"
            self.journal.save(record)
        return self._result(record)

    def _result(self, record):
        entries = [{k: v for k, v in e.items() if k != "before_record"} for e in record["entries"]]
        return {"status": record["status"], "journal_id": record["id"],
                "projects": sorted({e["project"] for e in entries}),
                "cleared": sum(e["status"] == "cleared" for e in entries),
                "undone": sum(e["status"] == "undone" for e in entries), "entries": entries,
                "transactional": False, "power_cycle_verified": False,
                "verified": "each pad's stored record and JSON re-read after its write",
                "next_step": "Take a fresh backup; delete_samples now accepts the slots these pads stored."}

    def undo(self, device):
        greeting = device.greet()
        identity = device_id({"sku": greeting.sku, "serial": greeting.serial})
        records = self.journal.records(identity, operation="clear_pads")
        if not records:
            return {"status": "nothing_to_undo"}
        record = next((r for r in records
                       if any(e["status"] in self.REVERTABLE for e in r["entries"])), None)
        if record is None:
            return self._result(records[0])
        self.backups.invalidate()
        for entry in reversed(record["entries"]):
            if entry["status"] not in self.REVERTABLE:
                continue
            project, group, pad = entry["project"], entry["group"], entry["pad"]
            prior = (entry["prior_slot"], entry["prior_length"])
            current = pad_record(device, project, group, pad)
            if entry["status"] == "undo_attempted" and self._settle(device, entry, prior, record):
                continue
            if current[0] != 0:
                entry["undo_failure"] = "Pad changed since the clear; no overwrite attempted."
                self.journal.save(record)
                continue
            device.begin_read()
            if not device.slot_exists(entry["prior_slot"]):
                entry["undo_note"] = (f"prior slot {entry['prior_slot']} is absent from the library; "
                                      "pad left cleared")
                entry["status"] = "undone"
                self.journal.save(record)
                continue
            entry["status"] = "undo_attempted"
            self.journal.save(record)
            try:
                before = entry.get("before_record", {})
                restore = {k: before[k] for k in RESTORE_KEYS if k in before}
                device.set_metadata(entry["node"], {"sym": prior[0]} | restore)
                note = verify_prior(device, project, group, pad, prior)
                if note:
                    entry["undo_note"] = note
                entry["status"] = "undone"
                entry.pop("undo_failure", None)
                self.journal.save(record)
            except (DeviceError, ValueError, OSError, tarfile.TarError) as e:
                entry["undo_failure"] = str(e)
                self.journal.save(record)
                break
        record["status"] = ("undone" if all(e["status"] in ("undone", "pending") for e in record["entries"])
                            else "undo_partial")
        self.journal.save(record)
        return self._result(record)

    def _settle(self, device, entry, prior, record):
        """An interrupted undo whose write did land: mark it undone without writing again."""
        try:
            note = verify_prior(device, entry["project"], entry["group"], entry["pad"], prior)
        except VerificationFailed:
            return False
        if note:
            entry["undo_note"] = note
        entry["status"] = "undone"
        entry.pop("undo_failure", None)
        self.journal.save(record)
        return True
