# chop_sample on OS 2.5.1 — two chops, one undo

Date 2026-09-12 (after midnight). Source: `~/Documents/ep133-library/007 088jimmybk.wav`
(5.45 s, 2 bars at 88 bpm), converted with ffmpeg to mono s16 46875 Hz
(255683 frames). Project 1, not active. Backups `session-09.pak`
(`263424e3…`) and `session-10.pak` (`c5e58d92…`), each verified `current`
before the write that used it.

## Chop 1 — group D pads 1–8, `{mode: equal, count: 8}`, oneshot

Journal `adb595c9671f4e2390cc3d24daf215b1`. Upload → **slot 29**, CRC
469709327, 255683 frames, verified. Eight pads assigned and trimmed to
31960-frame slices (0.68 s each), every one `chopped` with `changed {}`,
`dropped []`, `side_effects {}`. D01, D02, D05 held stale references
(stored slots 549, 543, 558 — absent from the library, `sym` 0), which the
preflight rightly treated as non-destructive.

`undo_last_chop` → **`undo_partial`**: D03, D04, D06, D07, D08 `undone`
(`sym 0` written, stored record read back as `(0, 0)`); D01, D02, D05
refused with "Prior stored slot is absent; restoring stale records is
unverified." Slot 29 left in the library.

That refusal was the wrong call: a stale reference *is* an empty pad, and
the stale number cannot be written back anyway. Fixed in `c638ad2`
(chop and install undo now clear such pads to `sym 0` and say so in
`undo_note`). The server that ran this session predates the fix, so
**D01, D02, D05 still hold slot 29** slices 1, 2 and 5 — part of the
power-cycle set below, or the next fresh server's `undo_last_chop` clears
them.

## Chop 2 — group B pads 2–9, `{mode: onsets}`, key

Journal `fd6d507e2ff3443183129b38c8fececa`. Upload → **slot 30** (same
audio, same CRC — the library does not dedupe). Detector found 25 onsets;
the first 8 were used: 0, 0.317, 0.655, 1.016, 1.191, 1.518, 1.584, 1.704 s.
All eight pads `chopped` cleanly with `key`/`release 15`.

Observation: "first N onsets" front-loads the chop — B09 got 1.70 → 5.45 s,
nearly 70 % of the break, while B07 got 65 ms. For a drum break the owner
will usually want the N *strongest* onsets or an even pick across the
detected list; the detected times are returned so the explicit
`[{start_s, end_s}]` form can be used, but a `pick: "strongest" | "spread"`
option would save that round-trip.

**Left in place** for the power-cycle check.

## Done-when status (`wwevlmnw`)

- 8-slice chop shows in `list_pads` with distinct trims — yes, both times
  (read back per pad).
- Undo removes all 8 assignments — 5 of 8 on the old code; all 8 with
  `c638ad2` (unit-tested, not yet run on hardware).
- Plays correctly / survives power-cycle — owner's ears and power switch.
  `play_note` was not sent: the channel→group / note→pad map is unknown
  (`w97sjrl7`) and nothing could be heard from here.

Library cost of this proof: slots 29 and 30, 511 366 bytes each; both are
unreferenced once the pads are cleared and go on the delete list —
which waits on `delete-proof.md`.
