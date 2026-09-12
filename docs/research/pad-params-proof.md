# set_pad on OS 2.5.1 — first writes, read back

Date 2026-09-11 → 12 (after midnight). Device SKU TE032AS001, OS 2.5.1,
active project 3. Every write went to **project 1** (not active; due to be
replaced by the rebuild import anyway). Backups: `session-08.pak`
(`38e66d6e…`) before the first write, `session-09.pak` (`263424e3…`) before
the second — each `verify_backup` → `current` first.

## Write 1 — A02 (slot 500, 203522 frames): trim + pitch + playmode

Requested `{sample.start: 1000, sample.end: 100000, sound.pitch: -2,
sound.playmode: "oneshot"}`; the tool paired `envelope.release: 255`.

Before: `{start 0, end 203522, pitch 0, playmode key, release 15}`.
Result **`written`**: all five fields `applied`, `changed {}`, `dropped []`,
`side_effects {}`. Journal `117e1aacff7e429a81c049e67a496f84`.

Then `verify_backup(session-08)` → **`stale`**: project 1 A02 stored pad
`{slot 500, length 203522}` on the backup vs `{slot 500, length 99000}` on
the device. So the **stored length in the project TAR pad record is
`sample.end − sample.start`**, i.e. the trim, not the slot's frame count.
This is the first direct observation of that field's meaning; `list_pads`'
`stored_length` and `check_ppak`'s frames-per-pad both read it.

**Left in place** for the power-cycle check.

## Write 2 — A07 (slot 1 "sleepcycl2"): mute group + root note

Requested `{sound.mutegroup: true, sound.rootnote: 48}` (A04 already had
`mutegroup: true`, so A04 + A07 are the mute-grouped pair).

Before: `{mutegroup false}` — `sound.rootnote` absent from the pad record.
Result **`written_with_differences`**: `mutegroup` `applied`;
**`sound.rootnote` `dropped`** — the pad record does not take it. Slot 1's
own `sound.rootnote` stayed 60, so the value was ignored, not redirected
to the slot. Journal `de22b4de282e4e849d8cbb6749a99084`.

`undo_last_pad_change` → **`undone`**: `mutegroup false` written back and
read back; `not_restorable: ["sound.rootnote"]` (it was never there).

## What is settled

| field | pad write | note |
|---|---|---|
| `sample.start`, `sample.end` | applied | TAR stored length follows the trim |
| `sound.pitch` | applied | −2 read back as −2 |
| `sound.playmode` + paired `envelope.release` | applied | key/15 → oneshot/255 |
| `sound.mutegroup` | applied, undone | bool round-trips |
| `sound.rootnote` | **dropped** | slot-only on this OS |

Not yet written on a pad: `envelope.attack`, `sound.amplitude`,
`sound.pan`, `time.mode`, `sound.bpm`, `sound.bars`, `midi.channel`.
Given `rootnote`, expect `sound.bpm` and `sound.bars` to be slot-only too;
`set_slot` is the tool for those three.

`power_cycle_verified: false` on everything above — that is the owner's
step: power-cycle, then `read_pad(1, 'A', 2)` must still show
`{start 1000, end 100000, pitch -2, playmode oneshot, release 255}`.

## Follow-ups

- `set_pad` should refuse `sound.rootnote` / `sound.bpm` / `sound.bars` on a
  pad (or pass them to the slot explicitly) once `set_slot` confirms they
  land there; until then a `dropped` report is honest and harmless.
- The `check_ppak` frame counts in `pads_assigned` are trims, not lengths;
  label them so.
