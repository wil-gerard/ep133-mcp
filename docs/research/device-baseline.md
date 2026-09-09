# Device baseline — 2026-09-08

Read-only hardware observations. Both MIDI endpoints are named `EP-133`. No sample, pad, project or firmware writes performed. Serial number omitted.

| Field | Observed value |
|---|---|
| Product | EP-133 |
| SKU | TE032AS001 |
| OS / software | 2.0.2 |
| Mode | normal |
| Sample capacity | 62,853,120 bytes |
| Free sample space | 4,096,092 bytes |
| Native rate | 46,875 Hz |
| Reported sample-rate range | 1–65,535 Hz |
| Reported channels / encoding | 1 or 2 / s16 |
| Active project | 5 (project root active=7000) |
| Project 5, A1 | Unassigned: sym=0, file ID 7207 |
| Project 1, A1 | Occupied: sym=1, file ID 3207 |

## Verified read sequence

Use observed device identity byte `0x33`, request IDs matched to responses, and upstream Packed7 framing. GREET and every metadata request below returned status 0.

1. GREET: top-level command `01`, empty payload.
2. FILE_INIT for reads: top-level command `05`, unpacked payload `01 00 00 40 00 00` (flags 0, 4 MiB max response).
3. Sample-root metadata: command `05`, payload `07 02 03 E8 00 00` (file ID 1000, page 0). This returned max_capacity, free_space_in_bytes and format capabilities. This resolves the memory-query gap for this device/OS only.
4. Project-root metadata: payload `07 02 07 D0 00 00` (file ID 2000). Returned active=7000.
5. Read all 48 pad nodes in project 5: 7201–7212, 7301–7312, 7401–7412 and 7501–7512. A1 is physical label 1, SysEx position 7, node 7207.

Metadata payloads contained two leading zero bytes for the observed page 0 response and null-terminated JSON. Parsing required a successful status, the observed prefix and complete valid JSON; no repair or guesswork.

The reported rate range is metadata, not proof that every rate/length imports successfully. Capacity is the reported sample store, not an inferred physical flash size or unit-generation guarantee.

## Remaining prerequisites

A full sounds-and-projects backup and restore procedure are still required. Browser automation tools are unavailable in this session, so Sample Tool backup requires user interaction. A current sample-slot inventory and fresh occupancy/memory check are required before selecting a write destination. Project 5 A1 is an empty candidate, not authorization to overwrite a slot or unrelated project.

No hardware upload, readback, restore or power-cycle proof has occurred. Phase 0 remains open.

## Post-update verification — 2026-09-09

After the user performed the firmware update, read-only checks report OS/software **2.5.1**, normal mode, SKU/base SKU TE032AS001. Sample capacity remains 62,853,120 bytes and free space remains 4,096,092 bytes. Active project is still 5 (7000), and its physical A1 pad (7207) remains unassigned (sym=0). GREET, read-mode FILE_INIT and metadata requests all returned status 0. No device writes were performed. This check covers A1, not all 48 pads or full data integrity. A new full sounds-and-projects backup on the updated OS is still pending.

## Full backup and addressing correction — 2026-09-09

A full sounds-and-projects backup now exists and is verified against the live
device; see [backup verification](backup-verification.md). Read-only session
again reports OS/software 2.5.1, SKU TE032AS001, capacity 62,853,120 B, free
4,096,092 B, active project 5. No writes.

**The "Verified read sequence" above is correct.** Physical label "1" is SysEx
position 7, node 7207 in project 5. Confirmed on hardware 2026-09-09: an
authorized `{"sym":14}` write to node 7207 loaded the pad labelled "1", and the
same write to node 7204 loaded the pad labelled "4". A short-lived revision of
this file claimed node 7204 was physical "1"; that was wrong and is retracted.

What did change is our understanding of the **project TAR**: its `pNN` files use
the same numbering as the metadata nodes and `assign_pad`, not the bottom-up
numbering ep133-ppak's `PROTOCOL.md` §3 documents. See
[backup verification](backup-verification.md).

`sym` is the sample-library **slot id**, not a boolean. Project 5 has 9 occupied
pads carrying slots 9, 10, 460, 495, 505, 505, 509, 932 and 933.

Pads whose stored slot has since been deleted from the library read `sym` = 0
live while the project TAR keeps the stale slot id. Occupancy must be read from
the device, not inferred from a project file.
