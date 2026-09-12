"""generate_ppak: patch a device-written project and wrap it as a .ppak.

A from-scratch project would have to guess event byte 7, settings bytes
216-221 and fx_settings byte 4. Patching sidesteps all of them: the
template is a project TAR the device wrote (read live or taken from a
backup), and only the fields proven in docs/research/pattern-encoding.md
change - BPM, pad records' stored slot/length, whole pattern files, and
scene chunks. Everything else, including fx_settings and every unexplained
byte, is carried verbatim, and the manifest lists every byte range that
differs from the template.

Whether the device accepts the file is the import ladder's question
(docs/handoff/pattern-encoding-next.md); this module only decides what the
bytes are. No device I/O here: callers pass the template TAR bytes.
"""

from __future__ import annotations

from pathlib import Path

from . import patterns as enc
from .projects import (build_ppak, pack_project, project_meta, read_pak, referenced_sounds, stored_pads,
                       unpack_project)

PAD_FIELDS = {"group", "pad", "slot", "frames"}
PATTERN_FIELDS = {"group", "index", "bars", "steps"}
PATTERN_ADD_FIELDS = {"group", "index", "add"}
PATTERN_EVENT_FIELDS = {"group", "index", "bars", "events"}
SCENE_FIELDS = {"scene", "A", "B", "C", "D"}
VELOCITY_NOTE = (f"'o' steps are written at velocity {enc.SOFT_VELOCITY} and 'x' at {enc.DEFAULT_VELOCITY}: event "
                 "byte 4 is velocity (a pressure recording stored 127 hard, 54..71 soft; "
                 "docs/research/velocity-proof.md). The events form takes any 1..127 per hit.")


class GenerateError(ValueError):
    """Input rejected; nothing was written."""


def _require_fields(item, fields: set[str], what: str) -> dict:
    if not isinstance(item, dict) or set(item) != fields:
        raise GenerateError(f"each {what} entry needs exactly {sorted(fields)}: {item!r}")
    return item


def _ranges(before: bytes, after: bytes) -> list[dict]:
    """Contiguous differing byte ranges between two equal-length members."""
    out = []
    start = None
    for i in range(len(before)):
        same = before[i] == after[i]
        if not same and start is None:
            start = i
        if same and start is not None:
            out.append({"offset": start, "length": i - start, "before": before[start:i].hex(), "after": after[start:i].hex()})
            start = None
    if start is not None:
        out.append({"offset": start, "length": len(before) - start, "before": before[start:].hex(), "after": after[start:].hex()})
    return out


FX_FIELDS = {"selector", "params"}
SETTINGS_FIELDS = {"params", "group_bytes"}


def _indexed(table, what: str) -> dict[int, float]:
    if not isinstance(table, dict):
        raise GenerateError(f"{what} must be an object of index: value")
    out = {}
    for key, value in table.items():
        index = int(key) if isinstance(key, str) and key.isdigit() else key
        out[index] = value
    return out


def patch_project(template: bytes, bpm: float | None = None, pads: list[dict] | None = None,
                  patterns: list[dict] | None = None, scenes: list[dict] | None = None,
                  fx: dict | None = None, settings: dict | None = None) -> tuple[bytes, list[dict]]:
    """(patched TAR, manifest). Raises GenerateError for anything outside the proven fields."""
    try:
        files = unpack_project(template)
        stored_pads(template)
    except ValueError as e:
        raise GenerateError(f"template is not a complete project TAR: {e}") from e
    if pack_project(files) != template:
        raise GenerateError("template TAR is not in the device's own flavour; take it from a backup or a live read")
    original = dict(files)
    manifest: list[dict] = []
    extended: set[str] = set()
    try:
        if bpm is not None:
            if "settings" not in files:
                raise GenerateError("template has no settings file to hold the BPM")
            files["settings"] = enc.patch_bpm(files["settings"], bpm)
        for item in pads or []:
            _require_fields(item, PAD_FIELDS, "pads")
            if item["group"] not in enc.GROUPS or type(item["pad"]) is not int or not 1 <= item["pad"] <= 12:
                raise GenerateError(f"pad destination must be group A..D and pad 1..12: {item!r}")
            name = f"pads/{item['group'].lower()}/p{item['pad']:02d}"
            files[name] = enc.patch_pad_record(files[name], item["slot"], item["frames"])
        seen = set()
        for item in patterns or []:
            if isinstance(item, dict) and "add" in item:
                _require_fields(item, PATTERN_ADD_FIELDS, "patterns (add form)")
                name = enc.pattern_member(item["group"], item["index"])
                if name in seen:
                    raise GenerateError(f"pattern {name} given twice")
                seen.add(name)
                if name not in files:
                    raise GenerateError(f"pattern {name} does not exist in the template; use the steps form to create it")
                hits = item["add"]
                if not isinstance(hits, list) or not hits or not all(
                        isinstance(h, dict) and set(h) == {"pad", "step"} for h in hits):
                    raise GenerateError(f"add for {name} must be a non-empty list of {{pad, step}}")
                files[name] = enc.add_events(files[name], [(h["pad"], h["step"]) for h in hits])
                extended.add(name)
                continue
            if isinstance(item, dict) and "events" in item:
                # Tick-accurate form: the device stores 24 ticks per 16th and its own recordings
                # use all of them, so a transcription keeps the onset where it actually fell.
                _require_fields(item, PATTERN_EVENT_FIELDS | ({"automation"} if "automation" in item else set()),
                                "patterns (events form)")
                name = enc.pattern_member(item["group"], item["index"])
                if name in seen:
                    raise GenerateError(f"pattern {name} given twice")
                seen.add(name)
                events = item["events"]
                if not isinstance(events, list) or not events or not all(
                        isinstance(e, dict) and {"pad", "tick"} <= set(e) <= {"pad", "tick", "duration", "note",
                                                                                "velocity"}
                        for e in events):
                    raise GenerateError(f"events for {name} must be a non-empty list of "
                                        "{pad, tick, duration?, note?, velocity?}")
                automation = item.get("automation", [])
                if not isinstance(automation, list) or not all(
                        isinstance(a, dict) and set(a) == {"tick", "param", "value"} for a in automation):
                    raise GenerateError(f"automation for {name} must be a list of {{tick, param, value}}")
                files[name] = enc.encode_events(item["bars"], [(e["pad"], e["tick"],
                                                                e.get("duration", enc.STEP_DURATION),
                                                                e.get("note", enc.NOTE),
                                                                e.get("velocity", enc.DEFAULT_VELOCITY))
                                                               for e in events],
                                                [(a["tick"], a["param"], a["value"]) for a in automation])
                continue
            _require_fields(item, PATTERN_FIELDS, "patterns")
            name = enc.pattern_member(item["group"], item["index"])
            if name in seen:
                raise GenerateError(f"pattern {name} given twice")
            seen.add(name)
            steps = item["steps"]
            if not isinstance(steps, dict) or not steps:
                raise GenerateError(f"pattern {name} needs steps: {{pad: 'x...'}}")
            rows = {}
            for pad, row in steps.items():
                key = int(pad) if isinstance(pad, str) and pad.isdigit() else pad
                rows[key] = row
            files[name] = enc.encode_pattern(item["bars"], rows)
        if fx is not None:
            if not isinstance(fx, dict) or not fx or set(fx) - FX_FIELDS:
                raise GenerateError(f"fx takes {sorted(FX_FIELDS)}: {fx!r}")
            if "fx_settings" not in files:
                raise GenerateError("template has no fx_settings file")
            files["fx_settings"] = enc.patch_fx_settings(files["fx_settings"], fx.get("selector"),
                                                         _indexed(fx.get("params", {}), "fx.params"))
        if settings is not None:
            if not isinstance(settings, dict) or not settings or set(settings) - SETTINGS_FIELDS:
                raise GenerateError(f"settings takes {sorted(SETTINGS_FIELDS)}: {settings!r}")
            if "settings" not in files:
                raise GenerateError("template has no settings file")
            group_bytes = settings.get("group_bytes", {})
            if not isinstance(group_bytes, dict):
                raise GenerateError("settings.group_bytes must be an object of group: value")
            files["settings"] = enc.patch_settings_params(files["settings"],
                                                          _indexed(settings.get("params", {}), "settings.params"),
                                                          group_bytes)
        for item in scenes or []:
            _require_fields(item, SCENE_FIELDS, "scenes")
            if "scenes" not in files:
                raise GenerateError("template has no scenes file")
            files["scenes"] = enc.patch_scene(files["scenes"], item["scene"],
                                             {g: item[g] for g in enc.GROUPS})
    except ValueError as e:
        raise GenerateError(str(e)) from e

    for name in sorted(set(files) | set(original)):
        before, after = original.get(name), files.get(name)
        if before == after:
            continue
        entry = {"member": name}
        if before is None:
            entry.update(action="added", bytes=len(after))
        elif name in extended:
            entry.update(action="extended", bytes_before=len(before), bytes=len(after),
                         events_added=(len(after) - len(before)) // enc.EVENT_SIZE,
                         ranges=_ranges(before[:enc.PATTERN_HEADER], after[:enc.PATTERN_HEADER]))
        elif name.startswith("patterns/"):
            dropped = enc.decode_pattern(before)["events"]
            entry.update(action="replaced", bytes_before=len(before), bytes=len(after),
                         events_dropped=len(dropped),
                         parameter_events_dropped=sum(e["type"] != enc.EVENT_TYPE_NOTE for e in dropped))
        else:
            entry.update(action="patched", bytes=len(after), ranges=_ranges(before, after))
        manifest.append(entry)
    return pack_project(files), manifest


def generate_ppak(template: bytes, project: int, out: str | Path, meta: dict, *, bpm: float | None = None,
                  pads: list[dict] | None = None, patterns: list[dict] | None = None,
                  scenes: list[dict] | None = None, sounds: dict[str, bytes] | None = None,
                  fx: dict | None = None, settings: dict | None = None) -> dict:
    """Patch the template, write <out>.ppak, and describe every change. meta is a backup's meta.json."""
    out = Path(out).expanduser()
    if out.suffix != ".ppak":
        raise GenerateError("output path must end in .ppak")
    if out.exists():
        raise GenerateError(f"refusing to overwrite {out}")
    if not any((bpm is not None, pads, patterns, scenes, fx, settings)):
        raise GenerateError("nothing to change: give bpm, pads, patterns, scenes, fx or settings")
    tar, manifest = patch_project(template, bpm, pads, patterns, scenes, fx, settings)
    chosen = referenced_sounds(tar, sounds) if sounds else {}
    data = build_ppak(project, tar, project_meta(meta), chosen)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    decoded = {}
    written = unpack_project(tar)
    for item in patterns or []:
        name = enc.pattern_member(item["group"], item["index"])
        if "add" in item:
            decoded[name] = {"events": len(enc.decode_pattern(written[name])["events"]),
                             "added": [dict(h) for h in item["add"]]}
        elif "events" in item:
            events = enc.pattern_events(written[name])
            off = [min(e["tick"] % enc.TICKS_PER_STEP, enc.TICKS_PER_STEP - e["tick"] % enc.TICKS_PER_STEP)
                   for e in events]
            automation = enc.pattern_automation(written[name])
            decoded[name] = {"events": len(events),
                             "off_grid_ticks": {"mean": round(sum(off) / len(off), 1), "max": max(off)}
                             if off else None,
                             "steps": enc.pattern_steps(written[name], strict=False),
                             "notes": sorted({e["note"] for e in events}),
                             "automation": len(automation)}
            if automation:
                decoded[name]["automation_params"] = sorted({a["param"] for a in automation})
        else:
            decoded[name] = {str(pad): row for pad, row in enc.pattern_steps(written[name]).items()}
    return {
        "status": "written", "ppak": str(out), "bytes": len(data), "project": project,
        "tar_bytes": len(tar), "template_bytes": len(template), "manifest": manifest,
        "patterns_written": decoded, "sounds_included": sorted(chosen),
        "velocity": VELOCITY_NOTE if any(
            enc.SOFT_HIT in row for item in patterns or [] for row in item.get("steps", {}).values()) else None,
        "instruction": (f"Import {out.name} with Sample Tool into project {project}, which must not be the active "
                        "project. Take a full backup first. If the device rejects the file, do not power-cycle "
                        "before writing up what happened."),
        "verified_on_device": False,
    }


def template_from_pak(path: str | Path, project: int) -> tuple[bytes, dict, dict[str, bytes]]:
    """(template TAR, meta, sounds) for one project of a .pak/.ppak on disk."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise GenerateError(f"template pak not found: {path}")
    try:
        meta, projects, sounds = read_pak(path.read_bytes())
    except Exception as e:  # zip or json errors: the file is not a pak
        raise GenerateError(f"template is not a readable pak: {e}") from e
    if project not in projects:
        raise GenerateError(f"pak holds no project P{project:02d}")
    return projects[project], meta, sounds
