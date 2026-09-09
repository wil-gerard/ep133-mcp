"""Phase 0 write test: point one pad at a sample already in the library.

Writes a single FILE metadata SET, {"sym":<slot>}, to one pad node. Nothing is
uploaded, nothing is formatted, no firmware command is sent.

Guards:
  - Refuses to touch an occupied pad unless --force.
  - Reads all 48 pads before and after; reports exactly what changed.
  - Reversible: write slot 0 back to the same node.

Usage:
    uv run --no-project --python 3.12 --with mido --with python-rtmidi \
        python tools/assign_test.py --node 7207 --slot 14
    ... --node 7207 --slot 0      # clear it again

Node numbering: 2000 + 1000*project + 200 + 100*group_index + pad_num,
where pad_num is the visual position, top-to-bottom / left-to-right
(1="7", 4="4", 7="1", 10="."). Confirmed on hardware 2026-09-09: a write to
node 7204 landed on the pad labelled "4".
"""

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / ".upstream" / "ep133-ppak"))

from ep133.sysex import RequestIdAllocator, build_sysex  # noqa: E402
from ep133.transport import EP133Transport  # noqa: E402

PROJECT_BASE = 7000  # active project 5 (project root active=7000)
LABEL = {1: "7", 2: "8", 3: "9", 4: "4", 5: "5", 6: "6",
         7: "1", 8: "2", 9: "3", 10: ".", 11: "0", 12: "ENTER"}
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


def meta_set(t, file_id, obj, ident):
    payload = (
        bytes([7, 1, (file_id >> 8) & 0xFF, file_id & 0xFF])
        + json.dumps(obj, separators=(",", ":")).encode("ascii")
        + b"\x00"
    )
    return req(t, 5, payload, ident)


def all_pads(t, ident):
    return {
        f"{g}/{PROJECT_BASE + 200 + gi * 100 + n}": (
            meta_get(t, PROJECT_BASE + 200 + gi * 100 + n, ident) or {}
        ).get("sym")
        for gi, g in enumerate("ABCD")
        for n in range(1, 13)
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", type=int, required=True, help="pad node id, e.g. 7207")
    ap.add_argument("--slot", type=int, required=True, help="library slot; 0 clears the pad")
    ap.add_argument("--force", action="store_true", help="allow writing over an occupied pad")
    args = ap.parse_args()

    pad_num = args.node % 100
    group = "ABCD"[(args.node % 1000) // 100 - 2]
    key = f"{group}/{args.node}"

    with EP133Transport.open() as t:
        ident = req(t, 1, b"", 0).identity_code
        req(t, 5, bytes([1, 0, 0, 0x40, 0, 0]), ident)

        before = all_pads(t, ident)
        current = before.get(key)
        if current is None:
            sys.exit(f"ABORT: node {args.node} did not read back. No write sent.")
        if current and args.slot and not args.force:
            sys.exit(f"ABORT: node {args.node} is occupied (sym={current}). "
                     f"Pass --force to overwrite. No write sent.")

        print(f"target: node {args.node} = group {group}, pad_num {pad_num}, "
              f'physical label "{LABEL[pad_num]}"')
        print(f"current sym={current} -> writing sym={args.slot}")
        print(f"occupied pads before: {sum(1 for v in before.values() if v)}")

        r = meta_set(t, args.node, {"sym": args.slot}, ident)
        print(f"\nWRITE metadata SET node={args.node} -> status={r.status} ({r.status_text})")

        after = all_pads(t, ident)
        changed = {k: f"{before[k]} -> {after[k]}" for k in before if before[k] != after[k]}
        print(f"occupied pads after:  {sum(1 for v in after.values() if v)}")
        print(f"pads changed: {changed if changed else 'NONE (write had no effect)'}")

        state = REPO / "tools" / "assign_test_state.json"
        state.write_text(json.dumps(
            {"node": args.node, "wrote": args.slot, "before": before, "after": after}, indent=1))
        print(f"state written to {state}")

        if args.slot:
            print(f'\nNow check the hardware: project 5, group {group}. '
                  f'Expect the pad labelled "{LABEL[pad_num]}" to hold sound {args.slot}.')


if __name__ == "__main__":
    main()
