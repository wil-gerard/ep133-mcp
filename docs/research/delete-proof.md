# FILE_DELETE on OS 2.5.1 — first attempt: rejected

Date 2026-09-11 (late evening). Device SKU TE032AS001, OS 2.5.1, active
project 3, 56 library slots, 27 432 768 bytes free. Backup
`session-08.pak` (sha256 `38e66d6e…20c75`) verified `current` immediately
before, 432 pads compared.

## The call

`delete_samples([704], backup_id)` → `needs_confirmation` with impact
`{slot 704, name "chillee", frames 294014, crc 770168296, referenced_by []}`
(the owner had approved this exact slot in the previous handoff) →
repeated with the token.

On the wire (`DeviceSession.delete_slot`): greet → `FILE_INIT` write mode
(`01 01 <max u32>`) → `06 02 02 C0` (FILE_DELETE, file id 704) → the
device answered **status ≠ 0**. The tool raised `DeviceRejected('device
rejected the delete')`, journal `41af6ca4f1d3468eaa29ed4e2aa4f39c`, entry
`failed`. Slot re-read afterwards: still present. `library_slots` 56 → 56,
`free_bytes` unchanged. The whole call took 95 s (the nine project TARs
are read for the reference check, then the slot is re-read).

## What is known and not known

- The command reaches the device and is answered, so `06 02 <id>` is a
  command the firmware recognises enough to reject; it did not time out
  and the device did not wedge (every read afterwards worked).
- **The reason string was lost.** `delete_slot` attaches `status` and the
  device's ASCII reason to the exception, but `Deleter` serialised only the
  message. Fixed in the same commit as this note (`failure` now carries
  `status` and `reason`); the running server predates the fix, so the
  string is not in this session's record. Upstream's observed set is
  `invalid id`, `invalid area`, `invalid offset`, `unknown command`,
  `not initialized`, `failed to delete`, … — which of them came back
  decides the next move:
  - `failed to delete` → the firmware has a delete path and refused this
    slot; try a slot the MCP uploaded itself (e.g. one of the `mcp_sample`
    kit slots on a scratch copy) in case 704 is protected content.
  - `not initialized` → the delete wants a different `FILE_INIT` mode (the
    read-mode init, or none) before it.
  - `invalid area` / `invalid id` → the `02` byte or the id encoding is not
    what this OS expects for sounds.
  - `unknown command` → FILE_DELETE is not in OS 2.5.1's dispatch and the
    tool should say so up front.
- Nothing was deleted; the slot-704 audio is intact on the device and in
  every backup since session-00.

## Next

Start a fresh server (the fix is in it), re-run the same
`delete_samples([704], …)` once, and record the `reason`. Then either the
one-slot proof completes or one of the branches above is the next
experiment. The 46-slot bulk delete stays parked until a one-slot delete
has been proven by a backup diff.

## Second attempt (2026-09-12, fixed server)

`delete_samples([704], session-12.pak)` → `DeviceRejected`, **status 1,
reason `failed to delete`**. Library 58 before and after, free bytes
unchanged, slot intact. So the `failed to delete` branch above is the one:
next try a slot the MCP uploaded itself — slot 30 (`mcp_sample`) once
B02–B09 are cleared.

## Third attempt (2026-09-12, slot 30 — MCP-uploaded, unreferenced)

`delete_samples([30], session-13.pak)` after `undo_last_chop` had cleared
B02–B09 (`referenced_by []` in the impact) → `DeviceRejected`, **status 1,
reason `failed to delete`** — the same answer as slot 704. Journal
`401b9125…`, library 58 → 58, free bytes 26 409 768 unchanged, slot 30
intact. So the refusal is not about protected or Sample-Tool content:
`06 02 <id>` after a write-mode `FILE_INIT` does not delete anything on
OS 2.5.1. Upstream never verified the command either (PROTOCOL.md ❌).

The tool now says so: `delete_samples`' description states it, and a
`failed to delete` answer sets `next_step` to "delete with Sample Tool;
nothing changed". Untried and parked: FILE_DELETE after a read-mode
`FILE_INIT` or with no init, and other values of the `02` area byte —
each is a speculative write and upstream's wedge history (§11) says not
to send unknown write-side frames without a reason. The 46-slot bulk
delete stays with Sample Tool.

## Fourth attempt (2026-09-12, right frame) — DELETED

The refusals were ours. ep133-krate's `captures/sniffer-delete-hi.bin`
(Sample Tool deleting slot 467 on OS 2.0.5), decoded with our own
`packing.unpack`, shows the delete as packed `04 06 01 53` → payload
**`06 01 D3`**: opcode, u16 BE id, **no `02` byte**, sent straight after
GREET with no `FILE_INIT`, answered status 0, then a burst of unsolicited
`03 03 E8 {"free_space_in_bytes": …}` notifications as the audio is
released. Upstream PROTOCOL.md's `06 02 <fid>` (marked ❌ there) put the
id at the wrong offset: 704 was read as 0x0202 = 514 and 30 as 0x0200 =
512 — no such slots, hence `failed to delete` both times.

`6acbc4b`: `file_delete` is `06 <id>`, `delete_slot` mirrors the capture
(GREET → delete, no write init), the bytes are pinned to the capture in
`tests/test_protocol.py`.

`delete_samples([30], session-13.pak)` → **`deleted`**, journal
`e15b92b6…`. Library 58 → 57, free bytes 26 409 768 → 26 921 268
(+511 500), slot re-read absent. `verify_backup(session-13)` → `stale`,
the only difference `library_slots only_in_backup [30]`; all 432 pad
records unchanged. `session-14.pak` (`7220aa30…`, base session-13, 57
slots reused) written; `tools/diff_backups.py session-13 session-14`:
slots 58 → 57, removed [30], one pad record differing — P01 A02 byte 16
`0x64` → `0x96`, which is the `sound.amplitude` 100 → 150 write from
earlier in the day (session-13 predates it), and incidentally places
`sound.amplitude` at offset 16 of the 27-byte pad record.

The one-slot delete is proven by a backup diff. The unsolicited
free-space frames carry no request id and our reader discards them; the
next command after the delete (the re-read) was answered normally. The
46-slot bulk delete is unblocked and waits on the owner's yes.

Lesson: an upstream table row marked ❌ is an inference, not a capture.
Check krate's captures before concluding a command is unusable.

## Bulk delete, first tranche (2026-09-12, owner's yes)

`keep/DELETABLE-after-rebuild.json` lists 47 slots nothing references
*once the rebuild is imported*. Against `session-14.pak` (current), 43 of
them are still stored by the pre-rebuild projects (P2 alone holds 1, 3, 7,
10, 455, 456, 505–507, 701–707), which `delete_samples` refuses by
design. The four unreferenced now — **12, 15, 476, 704** — were deleted in
one call, journal `16d00312…`, every entry `deleted`: library 57 → 53,
free bytes 26 921 268 → 30 313 536 (+3 392 268). `verify_backup(session-14)`
→ `stale`, only `library_slots only_in_backup [12, 15, 476, 704]`.
`session-15.pak` (`4ebbdc72…`, base session-14, 53 reused) diff vs
session-14: removed [12, 15, 476, 704], **0 pad records differing**.

The list file now carries `deleted` (12, 15, 476, 704; 30 was deleted
earlier) and 43 pending — deletable after the imports, with the owner's
yes standing but a fresh backup and a re-run of the reference check
required, since the imports change which slots are stored.
