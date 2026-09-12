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

## Power-cycle (owner, 2026-09-12)

After the owner power-cycled the device, `read_pad(1, 'A', 2)` returned
`{start 1000, end 100000, pitch -2, playmode oneshot, release 255}` and
`list_pads(1)` showed A02 `stored_length 99000` — **write 1 persisted
exactly.** `power_cycle_verified` is true for those five fields.

Side observation: the stale pads A01/A05/A08 (stored slots 504/218/132,
absent) showed stored lengths 178570/44104/12646 before the cycle and 0
after — the device rewrites stale pad records on boot.

## Write 3 — B02 (slot 30): the remaining seven fields

Backup `session-11.pak` (`6a06942a…`) verified `current`. Requested
`{envelope.attack: 20, sound.amplitude: 75, sound.pan: -8, time.mode: "bar",
sound.bpm: 88, sound.bars: 2, midi.channel: 5}`.

Result **`written_with_differences`**: `attack`, `amplitude`, `pan`,
`time.mode`, `midi.channel` `applied`; **`sound.bpm` and `sound.bars`
`dropped`** — the same slot-only behaviour as `rootnote`. Journal
`dfedcaee011447aebc896c3ab49e5ebe`. Left in place (not power-cycled yet).

## Write 4 — `set_slot(29)`: the three slot-only keys

Requested `{name: "jimmybk 2bar", sound.rootnote: 48, sound.bpm: 88,
sound.bars: 2}`. Before: `{name mcp_sample, rootnote 60, bpm 87.93, bars 1}`
— the device had already computed **`sound.bpm: 87.93`** for the upload on
its own (255683 frames at 46875 Hz, read as 4 beats → 88.0; the 87.93 is
the device's rounding). Result **`written`**, all four `applied`. Journal
`b7771979f39e41ebb6464a90a5287386`. Not power-cycled yet.

## What is settled

| field | pad write | note |
|---|---|---|
| `sample.start`, `sample.end` | applied | TAR stored length follows the trim |
| `sound.pitch` | applied | −2 read back as −2 |
| `sound.playmode` + paired `envelope.release` | applied | key/15 → oneshot/255 |
| `sound.mutegroup` | applied, undone | bool round-trips |
| `envelope.attack`, `sound.amplitude`, `sound.pan`, `time.mode`, `midi.channel` | applied | write 3, not yet power-cycled |
| `sound.rootnote`, `sound.bpm`, `sound.bars` | **dropped** on a pad; applied on the slot | slot-only on this OS; `set_pad` now refuses them with a pointer to `set_slot` |

So the pad record takes **11** of upstream's 14 fields; the other three
belong to `set_slot`. Power-cycle status: write 1 verified; writes 3 and 4
pending the next cycle (`read_pad(1, 'B', 2)` and its slot 30 / slot 29
metadata).

## Follow-ups

- The `check_ppak` frame counts in `pads_assigned` are trims, not lengths;
  label them so.
- Whether the device's own `sound.bpm` guess on upload (87.93 here) is
  frames/4-beats or an onset analysis: upload a sample of a non-loop length
  and read the slot.
