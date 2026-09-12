"""check_ppak, the import preflight, and import_ppak, the write.

The write path is what Sample Tool's own bundle does in `uploadProjectArchive` (read from the
web app, not sniffed): FILE_PUT_META aimed at the project's node under /projects with the
two-digit name, then FILE_PUT_DATA pages and an empty terminator - the sample upload proven in
docs/research/upload-capture.md, retargeted (payloads.file_put_project, DeviceSession.write_project,
docs/research/project-write.md). Sample Tool asks which project rather than reading it from the
file: the P06 mis-import in docs/handoff/session-2026-09-11-handoff.md is exactly the mistake
check_ppak exists to catch beforehand.

check_ppak is the read-only half: given a .ppak and the project number the owner intends, say
whether the file is a single-project export in the device's own flavour, whether it is for that
project, whether that project is the active one (never import into it), which library slots its
pads reference and whether each one exists on the device or is carried inside the file, and what
the project currently holds that the import would replace. import_ppak runs it first and refuses
on any problem, then writes, reads the project back and compares. Undo restores an exact private preimage while checking backup integrity and refusing
subsequent edits. Included samples are uploaded or reused before the project write;
explicit slot mapping rewrites only the imported project's references.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import struct
import tarfile
import time
import wave
import zipfile

from ..device import DeviceError
from ..protocol import decode as D
from ..protocol.projects import pack_project, project_entry, read_pak, stored_pads, unpack_project
from .errors import InvalidDestination, VerificationFailed
from .install import device_id
from .recovery import plan_sounds, sound_impact, upload_sounds

CONFIRM_SECONDS = 300

SOUND_NAME = re.compile(r"^/sounds/(\d+) (.*)\.wav$")
MAX_PPAK_BYTES = 128 * 1024 * 1024


def _sound_slots(sounds: dict[str, bytes]) -> dict[int, dict]:
    """{slot: {name, frames, channels}} for every /sounds entry, or InvalidDestination on a bad one."""
    out = {}
    for name, data in sounds.items():
        match = SOUND_NAME.match(name)
        if match is None:
            raise InvalidDestination("Unrecognised sound entry in the .ppak", observed=name,
                                     next_step="Sound entries are /sounds/<slot> <name>.wav.")
        slot = int(match[1])
        try:
            with wave.open(io.BytesIO(data), "rb") as wav:
                out[slot] = {"name": match[2], "frames": wav.getnframes(), "channels": wav.getnchannels(),
                             "samplerate": wav.getframerate()}
        except (wave.Error, EOFError) as e:
            raise InvalidDestination("Sound entry is not a readable WAV", observed=name,
                                     next_step=f"Re-export the sound: {e}") from e
    return out


def inspect_ppak(path: str | Path, project: int) -> dict:
    """Everything about the file that needs no device: format, flavour, project, pads, sounds."""
    path = Path(path).expanduser()
    if type(project) is not int or not 1 <= project <= 9:
        raise InvalidDestination("project must be 1..9", observed=project, next_step="Pick the target project.")
    if not path.is_file():
        raise InvalidDestination("ppak not found", observed=str(path), next_step="Check the path.")
    if path.stat().st_size > MAX_PPAK_BYTES:
        raise InvalidDestination("ppak exceeds the size limit", observed=path.stat().st_size,
                                 expected=f"at most {MAX_PPAK_BYTES} bytes", next_step="This is not a project export.")
    try:
        raw = path.read_bytes()
        meta, projects, sounds = read_pak(raw)
    except (zipfile.BadZipFile, ValueError, KeyError, OSError) as e:
        raise InvalidDestination("File is not a readable .pak/.ppak", observed=str(e),
                                 next_step="Export the project again.") from e
    problems = []
    if meta.get("pak_type") != "project":
        problems.append(f"pak_type is {meta.get('pak_type')!r}, not 'project' (a full backup is not an import file)")
    if sorted(projects) != [project]:
        problems.append(f"file holds project(s) {sorted(projects)}, not P{project:02d} - Sample Tool asks for "
                        "the number and does not read it from the file")
    tar = projects.get(project)
    summary = None
    if tar is not None:
        try:
            files = unpack_project(tar)
            stored_pads(tar)
            summary = D.decode_project(tar)
        except (ValueError, tarfile.TarError) as e:
            problems.append(f"project TAR is not a complete project: {e}")
            files = None
        if files is not None and pack_project(files) != tar:
            problems.append("project TAR is not in the device's own flavour (headers or member order differ)")
    try:
        included = _sound_slots(sounds)
    except InvalidDestination as e:
        problems.append(str(e) + f": {e.detail.get('observed')}")
        included = {}
    referenced = sorted({p["stored_slot"] for p in (summary["pads"] if summary else []) if p["stored_slot"]})
    if any(not 1 <= slot <= 999 for slot in referenced):
        problems.append("project references slots outside 1..999")
    out = {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "path": str(path), "project": project, "entry": project_entry(project), "bytes": path.stat().st_size,
        "meta": {k: meta.get(k) for k in ("pak_type", "device_sku", "device_version", "pak_release", "generated_at")},
        "tar_bytes": len(tar) if tar is not None else None,
        "referenced_slots": referenced, "included_sounds": included, "problems": problems,
    }
    if summary:
        out["contents"] = {
            "bpm": summary["bpm"],
            # The stored length is the pad's trim (end - start), not the slot's frame count:
            # docs/research/pad-params-proof.md.
            "pads_assigned": [{"group": p["group"], "pad": p["pad"], "slot": p["stored_slot"], "trim_frames": p["stored_length"]}
                              for p in summary["pads"] if p["stored_slot"]],
            "patterns": [{"group": p["group"], "index": p["index"], "bars": p["bars"], "events": len(p["events"]),
                          "automation": len(p["automation"])} for p in summary["patterns"]],
            "scenes": [{"scene": s["scene"], "A": s["A"], "B": s["B"], "C": s["C"], "D": s["D"]}
                       for s in (summary["scenes"] or {"scenes": []})["scenes"]],
            "members": summary["members"],
        }
    return out


def check_ppak(path: str | Path, project: int, device, slot_map=None) -> dict:
    """inspect_ppak plus what the device says: active project, slot presence, what would be replaced."""
    report = inspect_ppak(path, project)
    greeting = device.greet()
    device.begin_read()
    active = device.active_project()
    report["device"] = {"sku": greeting.sku, "os_version": greeting.os_version, "active_project": active}
    if report["meta"]["device_sku"] not in (None, greeting.sku):
        report["problems"].append(f"file was exported for SKU {report['meta']['device_sku']}, device is {greeting.sku}")
    if active == project:
        report["problems"].append(f"P{project:02d} is the active project; the device rewrites it on its own and "
                                  "an import there cannot be verified - park the device on another project first")
    present, missing = [], []
    for slot in report["referenced_slots"]:
        (present if device.slot_exists(slot) else missing).append(slot)
    report["slots"] = {"on_device": present, "absent_from_device": missing,
                       "absent_and_not_included": [s for s in missing if s not in report["included_sounds"]],
                       "included_but_already_on_device": [s for s in report["included_sounds"] if s in present]}
    if report["slots"]["absent_and_not_included"]:
        report["problems"].append("pads reference slots that are neither on the device nor in the file: "
                                  f"{report['slots']['absent_and_not_included']} - they would import as stale references")
    if report["included_sounds"] or slot_map:
        try:
            _, _, sounds = read_pak(Path(path).expanduser().read_bytes())
            plan = plan_sounds(sounds, device, slot_map)
            report["sound_plan"] = [{k: v for k, v in e.items() if k != "pcm"} for e in plan]
            report["required_pcm_bytes"] = sum(len(e["pcm"]) for e in plan if e["action"] == "upload")
            report["free_bytes"] = device.sample_root().free_space_in_bytes
            # Remapping must not change a reference to a different, non-included sound.
            untouched = set(report["referenced_slots"]) - set(report["included_sounds"])
            if any(e["slot"] in untouched for e in plan):
                report["problems"].append("slot_map collides with a referenced sound not carried by this file")
        except (DeviceError, ValueError, KeyError, wave.Error, EOFError) as e:
            report["problems"].append(str(e))
    try:
        current = D.decode_project(device.project_tar(project))
        report["would_replace"] = {
            "bpm": current["bpm"],
            "pads_assigned": sum(1 for p in current["pads"] if p["stored_slot"]),
            "patterns": len(current["patterns"]),
            "events": sum(len(p["events"]) for p in current["patterns"]),
            "scenes": len(current["scenes"]["scenes"]) if current["scenes"] else 0,
        }
    except (ValueError, tarfile.TarError) as e:
        report["would_replace"] = {"unreadable": str(e)}
    report["status"] = "ok" if not report["problems"] else "problems"
    report["instruction"] = (f"import_ppak writes {Path(report['path']).name} to project {project} over SysEx "
                             "(or import it with Sample Tool and choose that project when it asks); take a "
                             "fresh backup first." if not report["problems"] else "Fix the problems before importing.")
    return report


def _project_summary(tar: bytes) -> dict:
    """What a read-back is compared on when the bytes differ: everything decode_project sees."""
    d = D.decode_project(tar)
    return {"bpm": d["bpm"],
            "pads": [(p["group"], p["pad"], p["stored_slot"], p["stored_length"]) for p in d["pads"]],
            "patterns": [(p["group"], p["index"], p["bars"], p["events"], p["automation"]) for p in d["patterns"]],
            "scenes": d["scenes"], "members": d["members"]}


class Importer:
    REVERTABLE = ("imported", "imported_with_differences", "write_attempted", "undo_attempted", "failed", "undo_failed")

    def __init__(self, backups, journal):
        self.backups = backups
        self.journal = journal
        self._confirmations = {}

    def import_ppak(self, path, project, backup_id, device, confirm=None, slot_map=None):
        report = check_ppak(path, project, device, slot_map)
        if report["problems"]:
            raise InvalidDestination("check_ppak found problems; nothing written", observed=report["problems"],
                                     next_step="Fix the file or pick another project, then retry.")
        live = self.backups.require_current(backup_id, device)
        backup_path = self.backups.path_of(backup_id)
        raw = Path(path).expanduser().read_bytes()
        if hashlib.sha256(raw).hexdigest() != report["source_sha256"]:
            raise InvalidDestination("Import file changed during preflight", next_step="Retry with the intended file.")
        _, projects, sounds = read_pak(raw)
        plan = plan_sounds(sounds, device, slot_map)
        tar = projects[project]
        if any(e["source_slot"] != e["slot"] for e in plan):
            files = unpack_project(tar)
            mapping = {e["source_slot"]: e["slot"] for e in plan}
            for pad in stored_pads(tar):
                name = f"pads/{pad['group'].lower()}/p{pad['pad']:02}"
                record = bytearray(files[name])
                struct.pack_into("<H", record, 1, mapping.get(pad["stored_slot"], pad["stored_slot"]))
                files[name] = bytes(record)
            tar = pack_project(files)
        impact = {"project": project, "path": report["path"], "tar_bytes": len(tar),
                  "tar_sha256": hashlib.sha256(tar).hexdigest(),
                  "contents": {k: (len(v) if isinstance(v, list) else v)
                               for k, v in report["contents"].items() if k != "members"},
                  "events": sum(p["events"] for p in report["contents"]["patterns"]),
                  "would_replace": report["would_replace"], "referenced_slots": report["referenced_slots"]}
        impact["sounds"] = sound_impact(plan, live)
        impact["source_sha256"] = hashlib.sha256(raw).hexdigest()
        impact["prior_sha256"] = hashlib.sha256(device.project_tar(project)).hexdigest()
        binding = hashlib.sha256(json.dumps({"backup_id": backup_id, "device_id": device_id(live),
                                             "impact": impact}, sort_keys=True).encode()).hexdigest()
        previous = self._confirmations.pop(confirm, None) if confirm else None
        if previous is None or previous[0] != binding or previous[1] < time.monotonic():
            self._confirmations = {k: v for k, v in self._confirmations.items() if v[1] >= time.monotonic()}
            token = secrets.token_urlsafe(32)
            self._confirmations[token] = (binding, time.monotonic() + CONFIRM_SECONDS)
            return {"status": "needs_confirmation", "impact": impact, "confirm": token,
                    "undo": "undo_last_import restores the exact project preimage; uploaded samples remain",
                    "instruction": "Show this exact impact to the owner; repeat only after approval."}
        device.begin_read()
        prior = device.project_tar(project)
        if device.active_project() == project or hashlib.sha256(prior).hexdigest() != impact["prior_sha256"]:
            raise VerificationFailed("Target changed after confirmation", next_step="Inspect and retry preflight.")
        # Backup verification permits supersets and does not compare every project field.
        # Keep an exact private preimage so undo never substitutes a different backup project.
        cache = self.journal.directory.parent / "project-preimages"
        cache.mkdir(mode=0o700, parents=True, exist_ok=True)
        prior_path = cache / (impact["prior_sha256"] + ".tar")
        if prior_path.exists():
            if prior_path.read_bytes() != prior:
                raise VerificationFailed("Stored project preimage changed", next_step="Inspect the private recovery cache.")
        else:
            import os
            with prior_path.open("xb") as stream:
                stream.write(prior)
                stream.flush()
                os.fsync(stream.fileno())
            prior_path.chmod(0o600)
            fd = os.open(cache, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        entry = {"kind": "project", "project": project, "node": 2000 + 1000 * project, "path": report["path"],
                 "tar_bytes": len(tar), "tar_sha256": impact["tar_sha256"], "backup_path": backup_path,
                 "prior_sha256": hashlib.sha256(prior).hexdigest(), "prior_path": str(prior_path),
                 "prior_bytes": len(prior), "status": "pending"}
        record = self.journal.create(device_id(live), backup_id, [entry], operation="import_ppak")
        record["sounds"] = [e | {"status": "pending"} for e in impact["sounds"]]
        self.journal.save(record)
        self.backups.invalidate()
        try:
            upload_sounds(plan, device, self.journal, record, live)
            if device.active_project() == project or device.project_tar(project) != prior:
                raise VerificationFailed("Target project changed during sample upload", next_step="Inspect the journal and take a fresh backup.")
        except (DeviceError, OSError, ValueError) as e:
            record["status"] = "partial"
            entry["failure"] = {"error": type(e).__name__, "message": str(e)}
            self.journal.save(record)
            return self._result(record)
        return self._write(record, entry, device, tar, "imported")

    def _write(self, record, entry, device, tar, ok_status):
        project = entry["project"]
        entry["status"] = "write_attempted" if ok_status == "imported" else "undo_attempted"
        self.journal.save(record)
        try:
            device.write_project(project, tar)
            device.begin_read()
            after = device.project_tar(project)
            entry["read_back_sha256"] = hashlib.sha256(after).hexdigest()
            if after == tar:
                entry["verified"], entry["differences"] = "bytes", []
            else:
                want, got = _project_summary(tar), _project_summary(after)
                wanted_files, actual_files = unpack_project(tar), unpack_project(after)
                entry["differences"] = [k for k in want if want[k] != got[k]]
                entry["differences"].extend(name for name in sorted(set(wanted_files) | set(actual_files))
                                            if wanted_files.get(name) != actual_files.get(name))
                entry["verified"] = "decoded" if not entry["differences"] else "mismatch"
                entry["read_back_sha256"] = hashlib.sha256(after).hexdigest()
                if entry["differences"]:
                    raise VerificationFailed("Project read back differs from what was written",
                                             observed=entry["differences"],
                                             next_step="read_project the project and compare; restore from the backup if wrong.")
            entry["status"] = ok_status if entry["verified"] == "bytes" else ok_status + "_with_differences"
            entry.pop("failure", None)
            record["status"] = entry["status"]
        except (DeviceError, ValueError, OSError, tarfile.TarError) as e:
            entry["status"] = "failed" if ok_status == "imported" else "undo_failed"
            entry["failure"] = {"error": type(e).__name__, "message": str(e), **getattr(e, "detail", {})}
            record["status"] = "partial" if ok_status == "imported" else "undo_partial"
        self.journal.save(record)
        return self._result(record)

    @staticmethod
    def _result(record):
        entry = record["entries"][0]
        return {**entry, "status": record["status"], "entry_status": entry["status"], "journal_id": record["id"],
                "transactional": False, "power_cycle_verified": False, "sounds": record.get("sounds", []),
                "next_step": "read_project to inspect it; take a fresh backup before another write."}

    def undo(self, device):
        greeting = device.greet()
        identity = device_id({"sku": greeting.sku, "serial": greeting.serial})
        records = self.journal.records(identity, operation="import_ppak")
        if not records:
            return {"status": "nothing_to_undo"}
        record = next((r for r in records if r["entries"][0]["status"] in self.REVERTABLE), None)
        if record is None:
            return self._result(records[0])
        entry = record["entries"][0]
        backup = Path(entry["backup_path"])
        if not backup.is_file():
            entry["undo_failure"] = f"backup {backup} is no longer readable"
            record["status"] = "undo_partial"
            self.journal.save(record)
            return self._result(record)
        device.begin_read()
        if device.active_project() == entry["project"]:
            raise InvalidDestination("Cannot undo into the active project", next_step="Select a different project first.")
        current = device.project_tar(entry["project"])
        current_hash = hashlib.sha256(current).hexdigest()
        if entry["status"] in ("failed", "undo_attempted", "undo_failed") and current_hash == entry["prior_sha256"]:
            entry["status"] = record["status"] = "undone"
            entry.pop("failure", None)
            self.journal.save(record)
            return self._result(record)
        if current_hash != entry.get("read_back_sha256", entry["tar_sha256"]):
            raise VerificationFailed("Project changed since import; undo refused", next_step="Inspect current state and use an explicit selective restore.")
        raw = backup.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record["backup_id"]:
            raise VerificationFailed("Undo backup content changed", next_step="Recover the original verified backup.")
        _, projects, _ = read_pak(raw)
        prior = Path(entry["prior_path"]).read_bytes() if entry.get("prior_path") else projects[entry["project"]]
        if hashlib.sha256(prior).hexdigest() != entry["prior_sha256"]:
            raise VerificationFailed("Project preimage does not match journal", next_step="Recover the original preimage before undoing.")
        self.backups.invalidate()
        return self._write(record, entry, device, prior, "undone")
