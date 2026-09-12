# Handoff — 2026-09-12 afternoon, steps 1–4 done (delete works), imports next

From [`session-2026-09-12-undo-journal-handoff.md`](session-2026-09-12-undo-journal-handoff.md).

## Done on the device (project 2 active, P1 written)

| Step | Result | Commit |
|---|---|---|
| 1 | `Chopper.undo` walks chop journals newest → oldest (`Journal.records`) | `1f6bbd0` |
| 2a | First hardware undo stopped on B09: a `sym 0` write after the power-cycle left the old trim length in the project record, `(0, 175813)`; before the cycle the same write read `(0, 0)`. Both undo paths now prove a cleared pad by stored slot 0 + JSON `sym 0` and report the leftover length in `undo_note` | `374aa3c` |
| 2b | `undo_last_chop` ×2 → `fd6d507e` undone (B02–B09), `adb595c9` undone (D01/D02/D05); ×3 reports, no writes. `list_pads(1)`: all eleven `sym 0`, lengths left in the records (expected to zero on next boot). Owner had confirmed both chops sound right → `wwevlmnw` complete → [`chop-proof.md`](../research/chop-proof.md) | — |
| — | `session-13.pak` (`582c8a28…`, base session-12, 58 slots reused) written, verified `current` three times (after the amplitude write and the rejected delete too) | — |
| 3 | `set_pad(1,'A',2,{sound.amplitude:150})` → `written`, `changed {}`, `dropped []`, journal `47d0cd4f` | — |
| 4a | `delete_samples([30])` → status 1 `failed to delete`, same as 704 | `825448a` |
| 4b | Owner asked "are we sure?" — no: ep133-krate's `sniffer-delete-hi.bin` shows Sample Tool's delete is **`06 <id>`**, no `02` byte, no FILE_INIT. Ours put the id at the wrong offset. Fixed, pinned to the capture | `6acbc4b` |
| 4c | `delete_samples([30])` → **`deleted`**: 58 → 57 slots, +511 500 bytes free, slot re-read absent, `session-14.pak` (`7220aa30…`, base session-13) diff vs session-13 shows only the removal → `mzgyw615` complete (positive) → [`delete-proof.md`](../research/delete-proof.md) | `0b60d62` |

`uv run pytest -q` → **474 passed**.

## Device state

OS 2.5.1, 26 921 268 bytes free, **57 slots** (30 deleted; 29 present and
referenced only by the hand-assigned P1 B01; 704 intact). P1: A02
amplitude 150; B01 holds slot 29 untrimmed; B02–B09, D01/D02/D05 empty
with stale lengths. Nothing else written. Last verified backup:
`session-14.pak`.

## Next (in order)

1. **Sample Tool imports** — owner, previous handoff step 3: close this
   session's server first (one MIDI owner), park on 9, import 01–08, move
   to 1, import 09. The imports replace P1, so the A02/B01 test state goes
   with them. Afterwards `list_pads(1)` must show slots 11,18,21–28 on
   A1–A9 and `read_project(1)` A01 with 77 events; take `session-14.pak`
   (base session-13).
2. **Bulk delete** (`mzgyw615` done-when met): the 46 parked slots in
   `keep/DELETABLE-after-rebuild.json`, plus 29 once B01 is cleared, via
   `delete_samples` — after the imports (they may re-reference slots), with
   a fresh verified backup, and only on the owner's yes. Read every entry;
   diff the backups afterwards.
3. Owner's next power-cycle: `list_pads(1)` should show the eleven leftover
   lengths zeroed — confirms the note in `chop-proof.md`.
4. `vsa0gzu9`, `0uxzjixo`, `rfih4h0f`, `w97sjrl7` as in the previous
   handoff.

Rules unchanged: one MIDI owner; every import into a non-active project;
never commit `*.pak`, `*.ppak`, clips, stems, slices, the serial.
