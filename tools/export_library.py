#!/usr/bin/env python3
"""Extract every library sound from a .pak backup into a local sound library.

    export_library.py BACKUP.pak --out DIR

Writes each `/sounds/NNN name.wav` to DIR under its own name and DIR/manifest.json
describing the library: per slot the file name, byte size, sha256, WAV format
where the header parses, every pad record in every project that stores the
slot (project, group, pad, stored length), and `used` — whether any pad
record in any project stores the slot. A slot nobody stores is what "unused"
means on this device; stale references (stored slots that no longer exist) are
listed separately so they are not mistaken for use.

Existing files whose sha256 matches are left alone; a file with different
content is never overwritten (that is an error). Prints a summary and the
unused slots with their sizes. Reads only the backup; never opens MIDI. Exit
status 0 on success, 1 when an existing file conflicts.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ep133_mcp.protocol.projects import read_pak, stored_pads  # noqa: E402


def slot_of(name: str) -> int | None:
    head = name[len("/sounds/"):].split(" ", 1)[0]
    return int(head) if head.isdigit() else None


def wav_format(data: bytes) -> dict | None:
    try:
        with wave.open(io.BytesIO(data)) as w:
            return {"channels": w.getnchannels(), "sample_rate": w.getframerate(),
                    "sample_width": w.getsampwidth(), "frames": w.getnframes()}
    except (wave.Error, EOFError):
        return None


def library(pak: bytes) -> dict:
    meta, projects, sounds = read_pak(pak)
    references: dict[int, list[dict]] = {}
    for number, tar in sorted(projects.items()):
        for pad in stored_pads(tar):
            if pad["stored_slot"]:
                references.setdefault(pad["stored_slot"], []).append(
                    {"project": number, "group": pad["group"], "pad": pad["pad"], "stored_length": pad["stored_length"]})
    slots = {}
    for name, data in sorted(sounds.items()):
        slot = slot_of(name)
        if slot is None:
            continue
        slots[slot] = {
            "file": name[len("/sounds/"):], "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "wav": wav_format(data), "referenced_by": references.get(slot, []), "used": slot in references,
        }
    stale = {slot: refs for slot, refs in references.items() if slot not in slots}
    return {
        "source": {"device_version": meta.get("device_version"), "pak_type": meta.get("pak_type"),
                   "generated_at": meta.get("generated_at"), "sha256": hashlib.sha256(pak).hexdigest()},
        "slots": {str(slot): slots[slot] for slot in sorted(slots)},
        "stale_references": {str(slot): stale[slot] for slot in sorted(stale)},
    }


def export(pak_path: Path, out: Path) -> int:
    pak = pak_path.read_bytes()
    manifest = library(pak)
    _, _, sounds = read_pak(pak)
    out.mkdir(parents=True, exist_ok=True)
    written = skipped = 0
    for slot, entry in manifest["slots"].items():
        target = out / entry["file"]
        data = sounds["/sounds/" + entry["file"]]
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
                print(f"refusing to overwrite {target}: content differs from slot {slot} in the backup")
                return 1
            skipped += 1
            continue
        target.write_bytes(data)
        written += 1
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    slots = manifest["slots"]
    unused = [(int(s), e) for s, e in slots.items() if not e["used"]]
    total = sum(e["bytes"] for e in slots.values())
    print(f"{len(slots)} slots, {total} bytes; wrote {written}, already present {skipped}; manifest {out / 'manifest.json'}")
    print(f"used: {len(slots) - len(unused)}, unused: {len(unused)} ({sum(e['bytes'] for _, e in unused)} bytes)")
    for slot, entry in unused:
        print(f"  unused {slot:3d}  {entry['bytes']:9d}  {entry['file']}")
    if manifest["stale_references"]:
        print(f"stale references (stored slots that do not exist): {len(manifest['stale_references'])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("backup", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return export(args.backup, args.out)


if __name__ == "__main__":
    sys.exit(main())
