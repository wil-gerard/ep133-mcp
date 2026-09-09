"""Compare every pad on the device against a Sample Tool .pak backup.

Read-only: GREET, read-mode FILE_INIT and FILE_METADATA_GET only.

For each of the 432 pads (9 projects x 4 groups x 12 pads) it compares the live
`sym` (library slot id, 0 = unassigned) against the slot decoded from the
project TAR in the backup, and buckets the result:

  agree       live slot == backup slot
  dangling    backup names a slot that no longer exists in the library, and the
              device reports the pad empty. Expected: a project TAR keeps the
              slot id of a sample deleted from the library.
  UNEXPLAINED anything else - the device and the backup genuinely disagree.

A restore is verified when unexplained == 0 and the pads you changed are back
to their backup values.

Pad node id = 2000 + 1000*project + 200 + 100*group_index + pad_num, where
pad_num is the visual position top-to-bottom / left-to-right, the same index
the project TAR uses for pNN. Confirmed on hardware 2026-09-09.

Usage:
    uv run --no-project --python 3.12 --with mido --with python-rtmidi \
        python tools/verify_against_backup.py <backup.pak> [--expect NODE=SLOT ...]
"""

import argparse
import io
import json
import re
import struct
import sys
import tarfile
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / ".upstream" / "ep133-ppak"))

from ep133.sysex import RequestIdAllocator, build_sysex  # noqa: E402
from ep133.transport import EP133Transport  # noqa: E402

ALLOC = RequestIdAllocator()


def req(t, cmd, payload, ident, timeout=5.0):
    rid = ALLOC.next()
    t.send(build_sysex(cmd, payload, rid, identity_code=ident))
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = t.recv(timeout=max(0.05, deadline - time.time()))
        if r and r.request_id == rid and not r.is_request:
            return r
    raise TimeoutError(f"no response to cmd={cmd}")


def meta_get(t, file_id, ident):
    r = req(t, 5, bytes([7, 2, (file_id >> 8) & 0xFF, file_id & 0xFF, 0, 0]), ident)
    if r.status != 0:
        return None
    body = r.raw_data[2:]
    end = body.find(b"\x00")
    return json.loads((body[:end] if end >= 0 else body).decode())


def read_backup(pak):
    """Return (slots_present, {(project, group_index, pad_num): slot})."""
    z = zipfile.ZipFile(pak)
    sounds = {int(m.group(1)) for n in z.namelist() if (m := re.match(r"/sounds/(\d+) ", n))}
    pads = {}
    for name in sorted(n for n in z.namelist() if re.match(r"/projects/P\d+\.tar$", n)):
        pi = int(re.search(r"P(\d+)\.tar$", name).group(1))
        tf = tarfile.open(fileobj=io.BytesIO(z.read(name)))
        members = set(tf.getnames())
        for gi, g in enumerate("abcd"):
            for p in range(1, 13):
                member = f"pads/{g}/p{p:02d}"
                if member not in members:
                    continue
                rec = tf.extractfile(member).read()
                length = struct.unpack_from("<I", rec, 8)[0]
                pads[(pi, gi, p)] = struct.unpack_from("<H", rec, 1)[0] if length else 0
    return sounds, pads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pak", help="path to the .pak backup")
    ap.add_argument("--expect", action="append", default=[], metavar="NODE=SLOT",
                    help="assert a specific node holds a specific slot, e.g. 7204=0")
    args = ap.parse_args()

    sounds, backup = read_backup(args.pak)
    print(f"backup: {len(sounds)} library slots, {len(backup)} pad records")

    agree = dangling = 0
    unexplained = []
    live_nodes = {}
    with EP133Transport.open() as t:
        ident = req(t, 1, b"", 0).identity_code
        req(t, 5, bytes([1, 0, 0, 0x40, 0, 0]), ident)
        root = meta_get(t, 1000, ident) or {}
        print(f"device: capacity={root.get('max_capacity')} free={root.get('free_space_in_bytes')}")

        for (pi, gi, p), expected in sorted(backup.items()):
            node = 2000 + pi * 1000 + 200 + gi * 100 + p
            live = (meta_get(t, node, ident) or {}).get("sym")
            live_nodes[node] = live
            if live == expected:
                agree += 1
            elif expected and expected not in sounds and live == 0:
                dangling += 1
            else:
                unexplained.append((node, f"P{pi:02d}/{'ABCD'[gi]}/p{p:02d}", expected, live))

    total = agree + dangling + len(unexplained)
    print(f"\npads compared: {total}")
    print(f"  agree:       {agree}")
    print(f"  dangling:    {dangling}  (backup slot deleted from library, device reports empty)")
    print(f"  UNEXPLAINED: {len(unexplained)}")
    for node, label, exp, live in unexplained[:40]:
        print(f"     node {node} {label}: backup={exp} live={live}")

    failed = bool(unexplained)
    for spec in args.expect:
        node_s, _, slot_s = spec.partition("=")
        node, want = int(node_s), int(slot_s)
        got = live_nodes.get(node)
        ok = got == want
        failed |= not ok
        print(f"\nexpect node {node} == {want}: {'PASS' if ok else f'FAIL (live={got})'}")

    print("\nRESULT:", "PASS" if not failed else "FAIL")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
