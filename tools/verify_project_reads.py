"""Manual hardware check. Owner must be present; close all other MIDI users.

Usage: uv run python tools/verify_project_reads.py /path/to/backup.pak
Read-only. Validates the backup checksum before opening known projects 1..9.
Prints hashes/counts only; never copies backup content into the repository.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from ep133_mcp.device.session import DeviceSession
from ep133_mcp.protocol.projects import stored_pads


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    digest = hashlib.sha256(args.backup.read_bytes()).hexdigest()
    if digest != Path(str(args.backup) + ".sha256").read_text().split()[0]:
        raise ValueError("backup checksum mismatch")
    with zipfile.ZipFile(args.backup) as z:
        expected = {p: z.read(f"/projects/P{p:02}.tar") for p in range(1, 10)}
        slots = {int(m.group(1)) for n in z.namelist()
                 if (m := re.match(r"/sounds/(\d+) ", n))}
    with DeviceSession() as d:
        for project, backup_tar in expected.items():
            live = d.project_tar(project)
            expected_pads = stored_pads(backup_tar)
            actual = d.list_pads(project)
            differences = []
            for pad, backup_pad in zip(actual["pads"], expected_pads, strict=True):
                for field in ("group", "pad", "stored_slot", "stored_length"):
                    if pad[field] != backup_pad[field]:
                        differences.append({"group": pad["group"], "pad": pad["pad"], "field": field})
                stale = backup_pad["stored_slot"] != 0 and backup_pad["stored_slot"] not in slots
                if pad["stale_reference"] != stale:
                    differences.append({"group": pad["group"], "pad": pad["pad"], "field": "stale_reference"})
            print(json.dumps({"project": project, "bytes": len(live),
                              "sha256": hashlib.sha256(live).hexdigest(),
                              "exact_tar_match": live == backup_tar,
                              "pad_count": len(actual["pads"]),
                              "stale_references": sum(p["stale_reference"] for p in actual["pads"]),
                              "differences": differences}), flush=True)
            if differences:
                raise RuntimeError("pad comparison failed; stopped")


if __name__ == "__main__":
    main()
