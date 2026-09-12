# Pad and slot metadata on OS 2.5.1 — observed key set

Observed 2026-09-11 (late evening) with `read_pad(3, 'D', 7)` and
`list_pads(3, fields=True)` on the device (SKU TE032AS001, OS 2.5.1, active
project 3). The device answers `FILE_METADATA_GET` for a pad node with one
flat JSON object; a slot node answers with a second, larger one. Both are
passed through unfiltered by `read_pad` and `list_pads(fields=True)`.

## Pad record (node 5xyy, project 3)

An **assigned** pad returns exactly these 12 keys, in this order:

| key | type | observed values | notes |
|---|---|---|---|
| `sym` | int | 1, 2, 5, 8, 11, 13, 18, 21–25, 27, 28, 457, 500 | library slot; 0 = empty |
| `sound.playmode` | str | `oneshot`, `key`, `legato` | no `loop` seen; `PLAYMODES` in `safety/params.py` matches |
| `sample.start` | int | 0 | frames |
| `sample.end` | int | 4355 … 450863 | frames; equals the slot's `sample.end` on every untrimmed pad |
| `envelope.attack` | int | 0 | |
| `envelope.release` | int | 15, 255 | 15 on every `key` pad, 255 on every `oneshot`/`legato` pad — the pairing `PAIRED_RELEASE` assumes |
| `sound.pitch` | float | −12, −11.98, 0 | semitones, two decimals; `-12` came back as an int-looking `-12`, `-11.98` as a float |
| `sound.amplitude` | int | 0, 90, 100, 117, 200 | **0–200, 100 = unity.** `params.py` capped it at 100 before this note; widened to 200 in the same commit |
| `sound.pan` | int | 0 | |
| `sound.mutegroup` | bool | `true`, `false` | |
| `time.mode` | str | `off` | `TIME_MODES` has the rest; none observed here |
| `midi.channel` | int | 0 | |

An **empty or stale** pad (stored slot absent from the library, or 0)
returns only `{"sym": 0}` — no other key. 32 of the 48 pads in project 3 are
in that state. So a `set_pad` on an empty pad has no `before` values to
journal for any sound key, and the tool's "`sym` non-zero" preflight is the
right gate.

Keys upstream lists that did **not** appear on any pad in this project:
`sound.bpm`, `sound.bars`, `sound.rootnote`, `sound.loopstart`,
`sound.loopend`. They may appear once written (upstream honours them on
`FILE_METADATA_SET`) or may be slot-only; unproven either way until the
`set_pad` proof (`wr90izot`) writes one.

## Slot record (node 1000 + slot, slot 25 read through pad D07)

19 keys:

| key | type | value on slot 25 |
|---|---|---|
| `channels` | int | 1 |
| `samplerate` | int | 46875 |
| `format` | str | `s16` |
| `crc` | int | 1651104209 |
| `sound.loopstart` | int | −1 |
| `sound.loopend` | int | −1 |
| `name` | str | `mcp_sample` |
| `sound.amplitude` | int | 100 |
| `sound.playmode` | str | `oneshot` |
| `sound.pan` | int | 0 |
| `sound.pitch` | int | 0 |
| `sound.rootnote` | int | 60 |
| `time.mode` | str | `off` |
| `sound.bpm` | int | 0 |
| `sound.bars` | int | 1 |
| `envelope.attack` | int | 0 |
| `envelope.release` | int | 255 |
| `sample.start` | int | 0 |
| `sample.end` | int | 11429 |

The slot carries the sound-parameter defaults a pad inherits when assigned;
the pad record is the per-project override of the same keys plus
`sym`, `sound.mutegroup` and `midi.channel`. `sound.rootnote`, `sound.bpm`,
`sound.bars` and the loop points live only on the slot in this reading.

## What this settles

- `read_pad` and `list_pads(fields=True)` work on OS 2.5.1 and return the
  whole record; nothing is filtered.
- `SOUND_FIELDS` / `PAD_FIELDS` / `SLOT_FIELDS` in `safety/params.py` name
  every observed key correctly. One range was wrong (`sound.amplitude`).
- `sound.pitch` is a float with two decimals on the device; sending an int is
  fine (`_number` coerces).

## What it does not settle

- Whether a written `sound.bpm` / `sound.bars` / `sound.rootnote` lands on
  the pad record or is redirected to the slot.
- Any `time.mode` value other than `off`.
- The order keys come back in after a partial SET (the read-back in
  `set_pad` compares by key, so this is cosmetic).
