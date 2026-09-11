"""Phase 0: upload one sample, capturing every frame, and settle FILE_INFO.

The open question (docs/research/offline-validation.md): ep133-ppak's
`upload_sample` docstring says FILE_INFO commits the uploaded audio and that
without it the device discards the upload — but the sequence it actually sends
ends at the terminator and never sends FILE_INFO. Either the docstring is wrong
or `upload_sample` is broken.

This settles it inside one session, because an uncommitted upload buffer
probably does not survive a session close:

    preflight (read-only)
    -> GREET, FILE_INIT(write), FILE_PUT_META, data chunks, terminator
    -> CHECK A: did the slot commit?
    -> FILE_INFO
    -> CHECK B: did the slot commit now?

If CHECK A already shows the sample, the terminator commits and the docstring is
wrong. If only CHECK B does, the docstring is right and `upload_sample` is
broken. Either way we captured the frames that prove it.

Safety: aborts if the target slot is occupied, if the PCM will not fit in free
memory, or if the device reports anything but status 0. Writes a sample to the
library; it assigns no pad and touches no project. Recover with a Sample Tool
restore of the verified backup.

    uv run --no-project --python 3.12 --with mido --with python-rtmidi \
        python tools/capture_upload.py --slot 15
"""

import argparse
import json
import sys
import time
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / ".upstream" / "ep133-ppak"))

from ep133 import payloads as P  # noqa: E402
from ep133.commands import TE_SYSEX_FILE, TE_SYSEX_GREET  # noqa: E402
from ep133.sysex import RequestIdAllocator, build_sysex  # noqa: E402
from ep133.transport import EP133Transport  # noqa: E402

WAV = REPO / "fixtures" / "phase0-test-tone.wav"
OUTDIR = REPO / "docs" / "research" / "fixtures"
ALLOC = RequestIdAllocator()
LOG = []


def sanitize(frames, greet_payload):
    """Strip the device serial from captured frames before they are written.

    GREET answers with `...;serial:XXXXXXXX`, so every capture picks the serial
    up unless it is removed. Replaced with an equal-length placeholder so the
    hex framing and payload lengths stay faithful.
    """
    text = greet_payload.decode("utf-8", "replace")
    marker = "serial:"
    if marker not in text:
        return frames
    serial = text.split(marker, 1)[1].split(";")[0].strip("\x00").strip()
    if not serial:
        return frames
    placeholder = ("REDACTED" * ((len(serial) // 8) + 1))[:len(serial)]
    a, b = serial.encode().hex(), placeholder.encode().hex()
    for f in frames:
        for k in ("sent_hex", "response_hex"):
            f[k] = f[k].replace(a, b)
    return frames


def read_pcm(path):
    """Read our own 46875 Hz mono 16-bit fixture. No resampling, no deps."""
    with wave.open(str(path)) as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, 46875):
            raise SystemExit(f"ABORT: {path} must be 46875 Hz mono 16-bit")
        return w.readframes(w.getnframes())


def req(t, cmd, payload, ident, label, timeout=10.0):
    rid = ALLOC.next()
    frame = build_sysex(cmd, payload, rid, identity_code=ident)
    t.send(frame)
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = t.recv(timeout=max(0.05, deadline - time.time()))
        if r and r.request_id == rid and not r.is_request:
            LOG.append({"label": label, "cmd": cmd, "payload_len": len(payload),
                        "sent_hex": frame.hex(), "status": r.status,
                        "status_text": r.status_text, "response_hex": r.raw_data.hex()})
            return r
    raise TimeoutError(f"no response to {label}")


def meta_get(t, file_id, ident, label):
    """Return (exists, parsed_or_None).

    Occupancy is the STATUS, never the parse result. An occupied slot answers
    status 0 with JSON; an empty one answers status 1 "invalid file id". Large
    slot metadata can exceed one page and fail to parse while still existing,
    so treating a parse failure as "empty" would overwrite a real sample.
    """
    body = b""
    for page in range(8):
        r = req(t, 5, bytes([7, 2, (file_id >> 8) & 0xFF, file_id & 0xFF, 0, page]),
                ident, f"{label}[p{page}]")
        if r.status != 0:
            if page == 0:
                return False, None
            break
        chunk = r.raw_data[2:]
        if not chunk:
            break
        body += chunk
        end = body.find(b"\x00")
        if end >= 0:
            body = body[:end]
            break
    try:
        return True, json.loads(body.decode())
    except Exception:
        return True, {"_unparsed_bytes": len(body), "_head": body[:80].decode("utf-8", "replace")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, required=True, help="target library slot, must be empty")
    ap.add_argument("--name", default=None, help="on-device sample name")
    args = ap.parse_args()
    name = args.name or f"{args.slot}_testtone"

    pcm = read_pcm(WAV)
    chunks = P.chunk_pcm(pcm)
    print(f"payload: {len(pcm)} bytes PCM, {len(chunks)} data chunks + terminator")

    with EP133Transport.open() as t:
        ident = req(t, TE_SYSEX_GREET, b"", 0, "preflight/greet").identity_code
        req(t, 5, bytes([1, 0, 0, 0x40, 0, 0]), ident, "preflight/file_init_read")

        _, root = meta_get(t, 1000, ident, "preflight/sample_root")
        root = root or {}
        free = root.get("free_space_in_bytes")
        print(f"free memory: {free} bytes")
        if free is None or free <= len(pcm):
            sys.exit(f"ABORT: {len(pcm)} bytes will not fit in {free}. Nothing sent.")

        occupied, existing = meta_get(t, args.slot, ident, "preflight/target_slot")
        if occupied:
            sys.exit(f"ABORT: slot {args.slot} is occupied: {existing}. Nothing sent.")
        print(f"slot {args.slot} is free (device reports no such file id)")

        # ---- upload, GREET through terminator (exactly upstream's sequence) ----
        seq = [(TE_SYSEX_GREET, b"", "upload/greet"),
               (TE_SYSEX_FILE, P.build_file_init(4 * 1024 * 1024, flags=1), "upload/file_init_write"),
               (TE_SYSEX_FILE, P.build_file_put_meta(name, data_size=len(pcm), channels=1,
                                                     slot=args.slot), "upload/put_meta")]
        for page, chunk in enumerate(chunks):
            seq.append((TE_SYSEX_FILE, P.build_file_put_data(page=page, data=chunk),
                        f"upload/data[{page}]"))
        seq.append((TE_SYSEX_FILE, P.build_file_put_terminator(last_page=len(chunks) - 1),
                    "upload/terminator"))

        for i, (cmd, payload, label) in enumerate(seq):
            r = req(t, cmd, payload, ident, label)
            if r.status != 0:
                sys.exit(f"ABORT at {label}: status={r.status} ({r.status_text}). "
                         f"Upload incomplete; restore the backup if the device looks wrong.")
            if i % 20 == 0 or i == len(seq) - 1:
                print(f"  sent {i + 1}/{len(seq)}  {label}")

        # ---- CHECK A: committed by the terminator alone? ----
        _, a_root = meta_get(t, 1000, ident, "checkA/sample_root")
        a_root = a_root or {}
        committed_a, a_slot = meta_get(t, args.slot, ident, "checkA/target_slot")
        print(f"\nCHECK A (after terminator, before FILE_INFO)")
        print(f"  free: {free} -> {a_root.get('free_space_in_bytes')}")
        print(f"  slot {args.slot}: exists={committed_a} {a_slot}")

        # ---- FILE_INFO ----
        fi = req(t, TE_SYSEX_FILE, P.build_file_info(args.slot), ident, "finalize/file_info")
        print(f"\nFILE_INFO -> status={fi.status} ({fi.status_text})")

        _, b_root = meta_get(t, 1000, ident, "checkB/sample_root")
        b_root = b_root or {}
        committed_b, b_slot = meta_get(t, args.slot, ident, "checkB/target_slot")
        print(f"\nCHECK B (after FILE_INFO)")
        print(f"  free: {free} -> {b_root.get('free_space_in_bytes')}")
        print(f"  slot {args.slot}: exists={committed_b} {b_slot}")

        print("\nVERDICT:", (
            "terminator commits; upload_sample's FILE_INFO docstring is wrong" if committed_a
            else "FILE_INFO commits; upload_sample never sends it and is broken" if committed_b
            else "neither committed - upload path not understood, do not proceed"))

    greet = next((bytes.fromhex(f["response_hex"]) for f in LOG
                  if f["label"] == "preflight/greet"), b"")
    sanitize(LOG, greet)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"upload-capture-slot{args.slot}.json"
    out.write_text(json.dumps({
        "device": "EP-133 TE032AS001 OS 2.5.1",
        "source_wav": "fixtures/phase0-test-tone.wav (synthesised, no third-party rights)",
        "pcm_bytes": len(pcm), "chunks": len(chunks), "slot": args.slot, "name": name,
        "_sanitization": "Device serial replaced with an equal-length placeholder in "
                         "every frame. The audio is our own synthesised fixture.",
        "frames": LOG}, indent=1))
    print(f"\ncapture written to {out} ({len(LOG)} frames)")


if __name__ == "__main__":
    main()
