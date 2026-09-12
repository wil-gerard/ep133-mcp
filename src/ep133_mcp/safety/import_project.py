"""check_ppak: the preflight an import_ppak tool will run, shipped ahead of the write path.

Importing a project over SysEx is not possible yet: FILE_PUT_META / FILE_PUT_DATA are proven for
samples, and whether they accept a project file id - and with what metadata - is unknown until
Sample Tool's own import is captured (Dex 0uxzjixo). Until then every .ppak goes through Sample
Tool by hand, and Sample Tool asks which project rather than reading it from the file: the P06
mis-import in docs/handoff/session-2026-09-11-handoff.md is exactly the mistake this check exists
to catch beforehand.

So this is the read-only half: given a .ppak and the project number the owner intends, say
whether the file is a single-project export in the device's own flavour, whether it is for that
project, whether that project is the active one (never import into it), which library slots its
pads reference and whether each one exists on the device or is carried inside the file, and what
the project currently holds that the import would replace. import_ppak, when the write path is
proven, runs this first and refuses on any problem.
"""

from __future__ import annotations

import io
from pathlib import Path
import re
import tarfile
import wave
import zipfile

from ..protocol import decode as D
from ..protocol.projects import pack_project, project_entry, read_pak, stored_pads, unpack_project
from .errors import InvalidDestination

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
        meta, projects, sounds = read_pak(path.read_bytes())
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
    out = {
        "path": str(path), "project": project, "entry": project_entry(project), "bytes": path.stat().st_size,
        "meta": {k: meta.get(k) for k in ("pak_type", "device_sku", "device_version", "pak_release", "generated_at")},
        "tar_bytes": len(tar) if tar is not None else None,
        "referenced_slots": referenced, "included_sounds": included, "problems": problems,
    }
    if summary:
        out["contents"] = {
            "bpm": summary["bpm"],
            "pads_assigned": [{"group": p["group"], "pad": p["pad"], "slot": p["stored_slot"], "frames": p["stored_length"]}
                              for p in summary["pads"] if p["stored_slot"]],
            "patterns": [{"group": p["group"], "index": p["index"], "bars": p["bars"], "events": len(p["events"]),
                          "automation": len(p["automation"])} for p in summary["patterns"]],
            "scenes": [{"scene": s["scene"], "A": s["A"], "B": s["B"], "C": s["C"], "D": s["D"]}
                       for s in (summary["scenes"] or {"scenes": []})["scenes"]],
            "members": summary["members"],
        }
    return out


def check_ppak(path: str | Path, project: int, device) -> dict:
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
    if report["slots"]["included_but_already_on_device"]:
        report["problems"].append("file carries sounds for slots already occupied on the device: "
                                  f"{report['slots']['included_but_already_on_device']} - Sample Tool would ask to overwrite")
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
    report["instruction"] = (f"Import {Path(report['path']).name} with Sample Tool and choose project {project} when "
                             "it asks; take a fresh backup first." if not report["problems"] else
                             "Fix the problems before importing.")
    report["write_path"] = ("not available: importing over SysEx is unproven (Dex tvyy03x6); "
                            "this check is what import_ppak will run first")
    return report
