# Velocity proof (2026-09-12)

Dex `vvg9s9ml`. Every claim is **verified** (read back from the device) unless
marked otherwise. No writes by the MCP server; the owner recorded, the server
read. Firmware 2.5.1, project 3, backup `session-24.pak` (`5544fcf6…`) verified
`superset` before the read.

## Question

Event byte 4 had been `100` in all 3,612 note events ever read from this
device, including the owner's hand-played `d14`, so
[`pattern-encoding.md`](pattern-encoding.md) could not say whether it was
velocity. A transcription with every hit at one level loses the dynamics that
make a break a break.

## Method

`read_project(3)` before and after. The owner recorded, with REC + PLAY, four
hits on one pad into an empty one-bar pattern, **soft on beat 1, hard on 2,
soft on 3, hard on 4**, then the same on a second pad in a second pattern.
Quantize off; pads pressed were the ones printed "1" and "2" (indices 7, 8).

## Result

| pattern | pad | tick | velocity (byte 4) | duration | byte 7 |
|---|---|---|---|---|---|
| `d06` | 7 | 81 | **127** | 88 | 31 |
| `d06` | 7 | 182 | **54** | 17 | 31 |
| `d06` | 7 | 280 | **127** | 82 | 31 |
| `d06` | 7 | 368 | **71** | 21 | 8 |
| `d22` | 8 | 78 | **127** | 91 | 31 |
| `d22` | 8 | 192 | **71** | 22 | 31 |
| `d22` | 8 | 289 | **127** | 72 | 31 |
| `d22` | 8 | 377 | **54** | 20 | 8 |

The owner played ~10–16 ticks ahead of the click, so each hit sits just before
its beat and the beat-1 hit was captured at tick 368/377, before the loop
point. Reading the rows as beats 2, 3, 4, 1: **hard, soft, hard, soft** — the
order played. Hard hits saturate at 127; soft ones landed at 54–71. Note
length tracks the pressure too (hard ≈ 85 ticks, soft ≈ 20).

- **Byte 4 is velocity**, 1..127, and the device records it when pad pressure
  is read. The historical `100` is the value stored without pressure.
- **Byte 7 is not velocity**: 31 on the first three hits and 8 on the last, in
  both patterns; the same 31/8 split `d14` shows. Still unexplained.
- Pad numbering on the record side agrees with the play side: the pads printed
  "1"/"2" recorded as indices 7/8, the same indices `list_pads` and the encoder
  use (rung 3 played encoder pad 1 on the pad printed "7").
- Side effect seen between the two reads, not caused by the server: `a11` lost
  7 events (pad 10, bars 3–4) and `b06` lost 7 (pad 2, bars 3–4). Both are in
  `session-24.pak`.

## Not proven

Whether the device *plays* a written byte 4 at that level. The next
`generate_ppak` → import that carries a transcription will be the first time a
value ≠ 100 goes device-ward; listen for it (**probable** that it is honoured,
since the sequencer stores and uses what it records).

## Consequence for the tools

- `protocol/patterns.py`: `encode_events` takes `(pad, tick, duration, note,
  velocity)`; `encode_pattern` writes `'o'` at `SOFT_VELOCITY` = 60 and `'x'`
  at 100; `decode_pattern` / `pattern_events` name the field `velocity`.
- `generate_ppak` events form accepts `velocity` 1..127 per hit.
- `transcribe_groove`: each `tick_pattern` event carries a velocity 40..127
  from its onset strength, scaled so the loudest hit of each class is 127.
