"""chop_sample: one upload, N pads each trimmed to a slice of it.

Trim lives on the pad (`sample.start` / `sample.end` in frames of the slot's sample), so a break
chopped across twelve pads costs one library slot and one upload. The flow is install_sample's
for the first step (upload, CRC check against the slot's own metadata) and set_pad's for every
pad (assign `sym`, then write the trim and playmode, then read the pad back and report what the
device kept). One journal covers the lot so undo_last_chop is one step; the uploaded slot stays
in the library afterwards, as it does for undo_last_install.

Slices are planned before any I/O: equal parts, detected onsets (the same detector extract_kit
uses, which needs the audio extra), or explicit second ranges. Overlapping, empty or out-of-file
slices are refused. Frame math is at the device's 46875 Hz.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import tarfile

from ..device import DeviceError
from ..protocol.payloads import PAD_LABELS
from .backup import snapshot
from .errors import InvalidDestination, VerificationFailed
from .install import device_id, pad_record
from .params import PAIRED_RELEASE, PLAYMODES, compare
from .preflight import RATE, prepare, read_sample, validate_destination

CONFIRM_SECONDS = 300
MAX_SLICES = 12
MIN_SLICE_FRAMES = 2


def _frame(seconds, frames: int, what: str) -> int:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
        raise InvalidDestination(f"{what} must be a non-negative number of seconds", observed=seconds,
                                 next_step="Correct the slice.")
    return min(frames, int(round(seconds * RATE)))


def plan_slices(frames: int, slices, count: int, pcm: bytes | None = None) -> tuple[list[dict], dict]:
    """[{start, end}] in frames, one per pad, plus how they were found. Raises InvalidDestination."""
    if isinstance(slices, dict) and slices.get("mode") == "equal":
        if set(slices) - {"mode", "count"}:
            raise InvalidDestination("equal slices take only mode and count", observed=sorted(slices),
                                     next_step="Remove the extra keys.")
        wanted = slices.get("count", count)
        if type(wanted) is not int or wanted != count:
            raise InvalidDestination("count must equal the number of pads", observed=wanted, expected=count,
                                     next_step="Pass one pad per slice.")
        bounds = [round(i * frames / count) for i in range(count + 1)]
        ranges = [{"start": bounds[i], "end": bounds[i + 1]} for i in range(count)]
        detail = {"mode": "equal", "count": count}
    elif isinstance(slices, dict) and slices.get("mode") == "onsets":
        if set(slices) - {"mode"}:
            raise InvalidDestination("onset slices take only mode", observed=sorted(slices),
                                     next_step="Remove the extra keys.")
        onsets = detect_slice_onsets(pcm)
        if len(onsets) < count:
            raise InvalidDestination("Fewer onsets than pads", observed=len(onsets), expected=count,
                                     next_step="Pass fewer pads, use equal slices, or give explicit ranges.")
        chosen = onsets[:count]
        starts = [_frame(t, frames, "onset") for t in chosen]
        ranges = [{"start": starts[i], "end": starts[i + 1] if i + 1 < count else frames} for i in range(count)]
        detail = {"mode": "onsets", "detected_s": onsets, "used_s": chosen,
                  "unused_onsets": len(onsets) - count}
    elif isinstance(slices, list):
        if len(slices) != count:
            raise InvalidDestination("One slice per pad", observed=len(slices), expected=count,
                                     next_step="Match slices to pads.")
        ranges = []
        for item in slices:
            if not isinstance(item, dict) or set(item) != {"start_s", "end_s"}:
                raise InvalidDestination("Each explicit slice needs start_s and end_s", observed=item,
                                         next_step="Correct the slice.")
            ranges.append({"start": _frame(item["start_s"], frames, "start_s"),
                           "end": _frame(item["end_s"], frames, "end_s")})
        detail = {"mode": "explicit"}
    else:
        raise InvalidDestination("slices must be {mode: equal, count}, {mode: onsets} or [{start_s, end_s}]",
                                 observed=slices, next_step="Correct the slices argument.")
    previous_end = 0
    for r in ranges:
        if r["end"] - r["start"] < MIN_SLICE_FRAMES:
            raise InvalidDestination("Empty slice", observed=r, next_step="Every slice needs at least two frames.")
        if r["start"] < previous_end:
            raise InvalidDestination("Slices overlap or are out of order", observed=r,
                                     next_step="Give non-overlapping slices in time order.")
        previous_end = r["end"]
    for r in ranges:
        r["start_s"] = round(r["start"] / RATE, 4)
        r["end_s"] = round(r["end"] / RATE, 4)
    return ranges, detail


def detect_slice_onsets(pcm: bytes) -> list[float]:
    """Backtracked onset times in seconds, from the detector extract_kit uses."""
    from ..audio import deps
    from ..audio.analysis import detect_onsets
    deps.require_modules("librosa", "numpy")
    import numpy as np
    y = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    found = detect_onsets(y, RATE)
    return sorted(set(found["starts_s"]))


class Chopper:
    def __init__(self, backups, journal):
        self.backups = backups
        self.journal = journal
        self._confirmations = {}

    def chop(self, path, project, group, pads, slices, backup_id, device, confirm=None, playmode="oneshot"):
        if not isinstance(pads, list) or not 1 <= len(pads) <= MAX_SLICES or len(set(pads)) != len(pads):
            raise InvalidDestination(f"pads must be 1..{MAX_SLICES} distinct pad indexes",
                                     observed=pads, next_step="Correct the pad list.")
        nodes = [validate_destination(project, group, pad) for pad in pads]
        if playmode not in PLAYMODES:
            raise InvalidDestination("playmode must be oneshot, key or legato", observed=playmode,
                                     next_step="Correct the playmode.")
        if not isinstance(path, str):
            raise InvalidDestination("Sample path must be a string", next_step="Provide a local WAV path.")
        sample = read_sample(path)
        ranges, detail = plan_slices(sample.frames, slices, len(pads), sample.pcm)
        live = self.backups.require_current(backup_id, device)
        device.greet()
        device.begin_read()
        slot = prepare([(pads[0], sample)], project, group, live, device.sample_root().free_space_in_bytes)[0]["slot"]
        params = {"sound.playmode": playmode} | ({"envelope.release": PAIRED_RELEASE[playmode]}
                                                 if playmode in PAIRED_RELEASE else {})
        targets = []
        for pad, node, r in zip(pads, nodes, ranges):
            prior_slot, prior_length = live["pads"][project, group, pad]
            targets.append({"pad": pad, "label": PAD_LABELS[pad], "node": node, "start": r["start"], "end": r["end"],
                            "start_s": r["start_s"], "end_s": r["end_s"], "prior_slot": prior_slot,
                            "prior_length": prior_length,
                            "destructive": prior_length != 0 or prior_slot in live["slots"]})
        impact = {"project": project, "group": group, "slot": slot, "frames": sample.frames, "crc": sample.crc,
                  "pcm_sha256": hashlib.sha256(sample.pcm).hexdigest(), "playmode": playmode,
                  "slices": detail, "pads": targets}
        if any(t["destructive"] for t in targets):
            binding = hashlib.sha256(json.dumps({"backup_id": backup_id, "device_id": device_id(live),
                                                 "impact": impact}, sort_keys=True).encode()).hexdigest()
            previous = self._confirmations.pop(confirm, None) if confirm else None
            if previous is None or previous[0] != binding or previous[1] < time.monotonic():
                self._confirmations = {k: v for k, v in self._confirmations.items() if v[1] >= time.monotonic()}
                token = secrets.token_urlsafe(32)
                self._confirmations[token] = (binding, time.monotonic() + CONFIRM_SECONDS)
                return {"status": "needs_confirmation", "impact": impact, "confirm": token,
                        "instruction": "Show this exact impact to the owner; repeat only after approval."}
        entries = [{"kind": "upload", "slot": slot, "frames": sample.frames, "crc": sample.crc, "status": "pending"}]
        entries += [{"kind": "pad", "project": project, "group": group, **t, "slot": slot, "params": params,
                     "status": "pending"} for t in targets]
        record = self.journal.create(device_id(live), backup_id, entries, operation="chop_sample")
        record["slices"] = detail
        self.backups.invalidate()
        upload = entries[0]
        try:
            fresh = snapshot(device)
            if any(fresh[k] != live[k] for k in ("sku", "os_version", "serial", "slots", "pads")):
                raise VerificationFailed("Device state changed after preflight",
                                         next_step="Inspect the device and verify a fresh backup.")
            device.begin_read()
            if device.slot_exists(slot):
                raise VerificationFailed("Reserved slot became occupied", observed=slot,
                                         next_step="Inspect library and create a fresh backup.")
            upload["status"] = "upload_attempted"
            self.journal.save(record)
            device.upload_sample(slot, sample.name, sample.pcm)
            device.greet()
            device.begin_read()
            meta = device.metadata(slot)
            if not isinstance(meta, dict) or meta.get("crc") != sample.crc or meta.get("sample.end") != sample.frames:
                raise VerificationFailed("Uploaded sample CRC or frame count differs", observed=meta,
                                         expected={"crc": sample.crc, "sample.end": sample.frames},
                                         next_step="Inspect the orphaned slot; no pad was assigned.")
            upload["status"] = "uploaded"
            self.journal.save(record)
        except (DeviceError, ValueError, OSError, tarfile.TarError) as e:
            upload["status"] = "failed"
            upload["failure"] = {"error": type(e).__name__, "message": str(e)}
            record["status"] = "partial"
            self.journal.save(record)
            return self._result(record)
        for entry in entries[1:]:
            try:
                current = pad_record(device, project, group, entry["pad"])
                if current != (entry["prior_slot"], entry["prior_length"]):
                    raise VerificationFailed("Destination changed after preflight", observed=current,
                                             next_step="Inspect pads and create a fresh backup.")
                entry["before_record"] = device.read_pad(project, group, entry["pad"])["pad_metadata"]
                entry["status"] = "assignment_attempted"
                self.journal.save(record)
                device.assign_pad(entry["node"], slot)
                actual = pad_record(device, project, group, entry["pad"])
                if actual != (slot, sample.frames):
                    raise VerificationFailed("Stored pad assignment differs after write", observed=actual,
                                             expected=(slot, sample.frames),
                                             next_step="Inspect the journal and pad before retrying.")
                # Side effects are judged against the record as it stood after the assignment, so
                # sym itself (and whatever the device resets when a pad gets a new sound) is not
                # blamed on the trim write; before_record keeps the pre-chop values for undo.
                assigned = device.read_pad(project, group, entry["pad"])["pad_metadata"]
                entry["status"] = "trim_attempted"
                self.journal.save(record)
                requested = {"sample.start": entry["start"], "sample.end": entry["end"], **params}
                device.set_metadata(entry["node"], requested)
                after = device.read_pad(project, group, entry["pad"])["pad_metadata"]
                outcome = compare(requested, assigned, after)
                entry.update(outcome, after_record=after)
                entry["status"] = ("chopped" if not outcome["changed"] and not outcome["dropped"]
                                   else "chopped_with_differences")
                self.journal.save(record)
            except (DeviceError, ValueError, OSError, tarfile.TarError) as e:
                entry["failure"] = {"error": type(e).__name__, "message": str(e)}
                entry["status"] = "failed"
                record["status"] = "partial"
                self.journal.save(record)
                return self._result(record)
        record["status"] = "chopped"
        self.journal.save(record)
        return self._result(record)

    @staticmethod
    def _result(record):
        entries = [{k: v for k, v in e.items() if k not in ("before_record", "after_record")} for e in record["entries"]]
        return {"status": record["status"], "journal_id": record["id"], "slices": record.get("slices"),
                "entries": entries, "transactional": False, "power_cycle_verified": False,
                "verified": "slot CRC and frame count re-read after upload; each pad's stored record and JSON "
                            "re-read after its writes",
                "next_step": "Play the pads to check the slices; take a fresh backup before another write."}

    def undo(self, device):
        greeting = device.greet()
        identity = device_id({"sku": greeting.sku, "serial": greeting.serial})
        record = self.journal.latest(identity, operation="chop_sample")
        if record is None:
            return {"status": "nothing_to_undo"}
        if record["status"] in ("undone",):
            return self._result(record)
        self.backups.invalidate()
        for entry in reversed(record["entries"]):
            if entry["kind"] != "pad" or entry["status"] not in ("chopped", "chopped_with_differences",
                                                                 "trim_attempted", "assignment_attempted",
                                                                 "undo_attempted"):
                continue
            project, group, pad = entry["project"], entry["group"], entry["pad"]
            prior = (entry["prior_slot"], entry["prior_length"])
            current = pad_record(device, project, group, pad)
            if entry["status"] == "undo_attempted" and current == prior:
                entry["status"] = "undone"
                self.journal.save(record)
                continue
            if current[0] != entry["slot"]:
                entry["undo_failure"] = "Pad changed since the chop; no overwrite attempted."
                self.journal.save(record)
                continue
            device.begin_read()
            if entry["prior_slot"] != 0 and not device.slot_exists(entry["prior_slot"]):
                entry["undo_failure"] = "Prior stored slot is absent; restoring stale records is unverified."
                self.journal.save(record)
                continue
            entry["status"] = "undo_attempted"
            self.journal.save(record)
            try:
                before = entry.get("before_record", {})
                restore = {k: before[k] for k in ("sample.start", "sample.end", "sound.playmode", "envelope.release")
                           if k in before}
                device.set_metadata(entry["node"], {"sym": entry["prior_slot"]} | restore)
                actual = pad_record(device, project, group, pad)
                if actual != prior:
                    raise VerificationFailed("Undo did not reproduce prior stored slot/length", observed=actual,
                                             expected=prior, next_step="Inspect the pad and journal.")
                entry["status"] = "undone"
                entry.pop("undo_failure", None)
                self.journal.save(record)
            except (DeviceError, ValueError, OSError, tarfile.TarError) as e:
                entry["undo_failure"] = str(e)
                self.journal.save(record)
                break
        pads = [e for e in record["entries"] if e["kind"] == "pad"]
        record["status"] = "undone" if all(e["status"] in ("undone", "pending") for e in pads) else "undo_partial"
        self.journal.save(record)
        result = self._result(record)
        result["library_slot_left_in_place"] = record["entries"][0]["slot"] if record["entries"][0]["status"] != "pending" else None
        return result
