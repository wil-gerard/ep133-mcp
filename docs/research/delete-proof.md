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
