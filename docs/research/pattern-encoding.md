# Pattern and scene encoding — offline feasibility gate (2026-09-10)

Answers the handoff in [`../handoff/pattern-encoding-brief.md`](../handoff/pattern-encoding-brief.md):
can we read and generate EP-133 pattern and scene data reliably enough to ship
`generate_ppak`? Everything here is offline file analysis of the 2026-09-09
backup (`sha256 121da34a…`, firmware 2.5.1, Sample Tool `pak_release` 1.2.0).
No MIDI port was opened and nothing was imported to the device.

Tooling: [`tools/pattern_decode.py`](../../tools/pattern_decode.py) (stdlib
only; `check` re-runs every invariant cited below against a `.pak`, a bare
`PNN.tar`, or an extracted project directory). Upstream references are to
[ep133-ppak at 93f0f6b](https://github.com/ZacharySBrown/ep133-ppak/tree/93f0f6b1f6110701e6378e2702f39fd7e9a9d687),
the pinned checkout in `.upstream/`.

## Verdict

**Reading is solved. Writing is byte-exact for everything we would want to
emit, but the gate is not open yet, because import behaviour is untested and
upstream's own record of what breaks on import contradicts this device's data.**

- Upstream's `build_pattern` reproduces **309 of 314** device-written pattern
  files byte for byte, except for event byte 7 (which upstream zeroes and the
  device itself zeroes in 1260 of 3678 events). The other 5 files contain a
  second event type — recorded fader automation — that upstream does not model.
- Upstream's `build_scenes` reproduces all eight 712-byte `scenes` files
  except at offsets 1–4 (a "live pattern" chunk upstream zero-fills; the device
  also writes zeros there in one project) and offset 604 (a trailer byte
  upstream calls "scene count", which the data falsifies but which is probably
  benign).
- `settings` matches upstream's template shape, with BPM as float32 LE at
  bytes 4–7, and is present in **9 of 9** device projects. `PROTOCOL.md` §8's
  "no settings file" claim describes an older or hand-built project, not what
  this device produces.
- `fx_settings` is present in 9 of 9 projects and is not modelled upstream at
  all.

What "verified" means below: verified against files the device wrote, across
all nine projects. Nothing here is verified against the device *importing* a
file. That gap, not the encoding, is what stands between us and
`generate_ppak` — see [What would have to be true](#what-would-have-to-be-true-before-importing-a-generated-ppak).

## What a project TAR actually contains

Corrects `PROTOCOL.md` §8 (which lists `pads/` and an empty `patterns/` only).
Every one of the nine projects has:

| Member | Size | Notes |
|---|---|---|
| `fx_settings` | 160 (P01–P05) / 144 (P06–P09) | first member; effect selector + 34 float32 |
| `pads/{a,b,c,d}/pNN` | 26 or 27 | 48 records, always all present |
| `patterns/{a,b,c,d}NN` | 4 + 8n | only patterns that were ever edited; 314 total |
| `scenes` | 712 (P06: 612) | head chunk + 99 scene chunks + trailer |
| `settings` | 222 (P06–P09: 220) | BPM + 48 float32 params |

TAR details a generator must copy: members are in plain sorted order
(`fx_settings`, `pads…`, `patterns…`, `scenes`, `settings`), directory entries
present for `pads`, `pads/a`…`pads/d`, `patterns`, mtime 0, uid/gid 0, mode
0644/0755, **no ustar magic** (old V7-style headers, `magic` field all zeros —
upstream calls it "minimal ustar"; the device evidently reads both).

The two size families (P01–P05 vs P06–P09) are the same layouts with a tail
appended: `settings` +2 bytes, `fx_settings` +16 bytes, `scenes` +100 bytes.
P06–P09 were presumably last saved by an older firmware and never rewritten;
firmware 2.5.1 reads both forms, so the reader is tolerant of the older sizes.
Whether Sample Tool's `.ppak` import is equally tolerant is unknown — emit the
newer sizes.

Pattern count: **314**, not the "400+" in the brief (41+25+58+43+6+4+34+43+60).
89 of them are 4-byte empties.

## Pattern file (`patterns/{group}{NN}`)

### Header (4 bytes)

| Off | Field | Status | Evidence |
|---|---|---|---|
| 0 | `0x00` | verified | 0 in 314/314 |
| 1 | bars, u8 | verified | values 1,2,3,4,6,8,15; 312/314 files have every event inside `bars × 384` ticks, and the two exceptions are explained below |
| 2 | event count, u8 | verified | equals `(size − 4) / 8` in 314/314 |
| 3 | `0x00` | verified | 0 in 314/314 |

384 ticks per bar (96 PPQN at 4/4) is the only grid on which the 4-bar
patterns top out at position 1535 and 8-bar at 3054–3072, and it is
upstream's `TICKS_PER_BAR`.

The two exceptions (P09 `a05`, bars=2, and `a06`, bars=1) are copies of P09
`a07` (bars=4, same max position 1513) whose length was later shortened. So
**the device keeps events past the loop end when a pattern is shortened**, and
its reader tolerates them. Falsifier: a hardware capture of "shorten a pattern"
producing a truncated file instead.

Positions are not required to be quantized: 395 of 3612 note events are off the
6-tick grid (live, unquantized recording).

### Note event (8 bytes, type 0)

```
pos u16 LE | (pad-1)*8 | note | 100 | duration u16 LE | byte7
```

| Off | Field | Status | Evidence |
|---|---|---|---|
| 0–1 | position, ticks, u16 LE | verified | sorted ascending in 314/314 files; max 3054 in an 8-bar pattern |
| 2 bits 7..3 | pad index, 0-based (`pad_file = (b>>3)+1`) | verified | with this mapping, 3147 of 3612 note events land on a pad record with non-zero length; the alternatives (`b>>3`, `(b>>3)+2`) score 914 and 1613. The 465 misses cluster on specific pads with a stale slot id and zero length — pads cleared after the pattern was recorded (P03 `d/p03` alone is 192 of them) |
| 2 bits 2..0 | event type, `0` = note | verified | only values 0 and 1 occur (3612 / 66) |
| 3 | note number | probable | 21..72; 2315/3612 are 60 (C4 = unpitched trigger). Other values appear on pads that also carry pitched runs (e.g. P05 `c01` pad 4: 60, 67, 72). Not confirmed which pitch the device *plays* for a value ≠ 60 |
| 4 | velocity? | **unverified** | `100` in **all 3612** note events, from nine projects and ~a dozen sessions. Either this device never records velocity, or byte 4 is not velocity. Upstream also only ever saw 100. Do not assume the device honours other values |
| 5–6 | duration, ticks, u16 LE | verified | 7..1777, never 0 for notes; upstream's DannyDesert-era "flag bytes" reading is wrong, as upstream already notes |
| 7 | unknown | **unknown** | 43 distinct values; 0 in 1260 events, 6 in 1390, the rest 8/16/97/129/… with no correlation to pad, position, duration, note or bar. Stable across cloned patterns (P01 `a05`–`a08` are byte-identical clones of `a02`), so it is persisted state, not read noise. Could be uninitialised memory at write time. Upstream writes 0; the device itself writes 0 in a third of events, so 0 is at least a device-produced value |

Ties (several events at the same tick) are in no consistent order — looks like
recording order. Upstream's stable sort by position preserves caller order,
which is compatible.

### Parameter event (8 bytes, type 1) — not modelled upstream

```
pos u16 LE | 0x01 | param id | 0x00 | value u16 LE | byte7
```

66 events across 5 patterns (P04 `a04`, P05 `c01`, P07 `b07`/`b08`/`b09`).
Every one has byte 2 = `0x01` exactly (type 1, pad bits 0), byte 4 = 0, and a
value that sweeps smoothly at 6-tick spacing — e.g. P07 `b07`: 12898 → 10147 →
8146 → … → 300 → 0, then 298 → 4699 → … → 32759. That is a recorded fader
gesture. Values span 0..32759 (15-bit). Param ids seen: 1, 5, 6 — plausibly the
fader's assigned function (pitch/time/volume/…), unidentified.

| Field | Status |
|---|---|
| type = 1 means parameter automation | probable (from shape alone; a hardware capture of "record a fader move" would verify) |
| byte 3 = parameter id | guessed |
| bytes 5–6 = value u16 LE, 0..32767 | probable |

Consequence for a generator: it never needs to *emit* these, but a reader
(`pattern_decode.py`) must not decode them as notes, and any "patch an existing
pattern" path must preserve them.

### Round trip through upstream's encoder

Decoding every note-only pattern into upstream `Event`s and re-encoding with
`ep133.song.format.build_pattern(events, bars)`:

| Result | Files |
|---|---|
| byte-identical | 102 |
| identical except event byte 7 | 207 |
| any other difference | **0** |
| skipped (contain type-1 events) | 5 |

So for note data the write side is not "probably understood" — it is
byte-exact against 309 device-written files, modulo a byte we cannot yet name.

### Pattern numbering

Files are `patterns/{group}{NN}` with no slash (upstream's `pattern_filename`
is right). The index is the pattern number the UI shows, 1..99 per group; a
file exists only once the pattern has been edited, which is why P01 has no
`c01` and P03 skips `a15`/`a16`. Highest file index here is 20 (P03 `a20`);
scenes reference indices up to 54. Twelve per group was never a limit.

## `scenes` (712 bytes; 612 in the older form)

```
off 0        0x00
off 1..6     head chunk: [pat_a, pat_b, pat_c, pat_d, num, den]
off 7..600   99 × 6-byte scene chunks, scene k at 7 + 6(k−1)
off 601..711 111-byte trailer (11 bytes in the older form)
```

| Field | Status | Evidence |
|---|---|---|
| byte 0 = 0 | verified | 9/9 |
| bytes 1–4: one pattern index per group | probable | `01 01 01 01` in 8 projects, `00 00 00 00` in P02. Upstream treats 1–6 as zero padding; it is shaped exactly like a scene chunk and is most likely the live/selected pattern per group. Both values are device-produced, so either is safe to write |
| bytes 5–6: time signature num/den | probable | `04 04` in 9/9; no project here is in another signature, so the field is consistent with, not proven to be, the signature |
| chunk[0..3]: pattern index per group, 0 = unset | verified for layout | populated chunks never contain a 0 (0 of 700 entries across 175 populated chunks, P06 included); unpopulated chunks are all-zero in the 712-byte form and `01 01 01 01` in P06's 612-byte form |
| chunk[4..5]: `04 04` on every chunk | verified | 9/9, including unpopulated chunks — upstream emits the same and that matches |
| 612 vs 712 | verified | identical layout; the trailer is 11 bytes instead of 111. The 100 extra bytes are 1 length byte + 99 song positions, i.e. the song-mode section, so 612 is the pre-song-mode file. Answers brief Q4 |
| trailer[0..3] u32 BE | **falsified as "scene count"** | device values 3, 4, 15, 10, 1, 1, 1, 1, 1 against 10, 8, 16, 11, 1, 99, 4, 11, 15 populated scenes. Possibly "current scene" at save time. Any value 1..99 is device-produced; upstream's `len(scenes)` is in that range |
| trailer[4..7] u32 BE = 0 | verified (712 form) | 8/8; P06's 11-byte trailer has `1` here instead |
| trailer[11] = song length, trailer[12..] = positions (scene index) | consistent, not verified | `01` at [11] in 8/8 and [12] = 1,3,9,1,1,1,1,1. Every project has a 1-position song, so this cannot distinguish "song" from "current scene". Song mode is out of scope; recording only that upstream's layout is not contradicted |

**Scenes reference pattern files that do not exist** in 8 of 9 projects (34
references; e.g. P01 scene 1 = `a10 b01 c01 d01` with no `c01` or `d01` file).
So a missing file is the device's own representation of an empty pattern, and
upstream's guidance that silent groups "must point at an actual empty pattern"
is at most a constraint on *imported* files, not on the format. Upstream's
other half — never write 0 in a populated chunk — matches what the device does.

Round trip through upstream's `build_scenes` (4/4, song positions passed
through): all eight 712-byte files identical except offsets 1–4 and 604 as
described. Upstream's assertion that a 712-byte file is required is
contradicted only by the device *holding* a 612-byte one; for a generated file
emit 712.

## `settings` (222 bytes; 220 in the older form)

| Off | Field | Status | Evidence |
|---|---|---|---|
| 0–3 | zero | verified | 9/9 |
| 4–7 | project BPM, float32 LE | probable | 145, 88, 90, 123, 135, 120, 84, 101, 90.02 — all plausible tempos and the only non-zero in the first 24 bytes. P05 decodes to **135.0**; the hardware agent can confirm by reading P05's tempo on the device. Answers brief Q5 for BPM |
| 8–23 | zero | verified | 9/9 |
| 24–215 | 48 × float32 LE, `-1.0` = unset, else 0..1 | probable | 9/9 fit; values like 0.9999, 0.776, 0.474 — fader/mix positions. Layout within the 48 (4 groups × 12?) not decoded |
| 216–219 | one u8 per group | guessed | values 0/1/5/6 per byte; upstream zeroes them. `05 05 05 05` in P05 and P07. Plausibly per-group fader assignment |
| 220–221 | tail, newer form only | unknown | `00 02`, `00 08`, `00 04`, `02 02`, `00 08`; upstream's template has `00 02` |

Time signature is **not** identifiable in `settings` — the only 4/4 markers in
the project are the ones in `scenes`. Brief Q5's second half is therefore
answered "in scenes, not settings", pending a non-4/4 project to confirm.

Upstream's `DEVICE_DEFAULT_SETTINGS` (24 zeros + 48 × `-1.0` + 4 zeros + `00 02`)
is structurally exactly this file; it was extracted from a real
`reference_minimal.ppak`, which is itself evidence that projects carry a
`settings` file. `PROTOCOL.md` §8 ("no settings file… adding one triggered
ERROR CLOCK 43") and `song_writer.py` ("deliberately omitted… ERR 82 / ERROR
8200") are contradicted by both this backup and upstream's own template. The
likeliest reconciliation: their hand-built `settings` had wrong *content*
(e.g. zeros where `-1.0` was needed — `format.py` says as much about
ERR PATTERN 189). The presence of the file is not the trigger.

## `fx_settings` (160 bytes; 144 in the older form)

| Off | Field | Status | Evidence |
|---|---|---|---|
| 0–3 | zero | verified | 9/9 |
| 4 | effect selector, u8 | guessed | 0, 1, 2, 6 observed; bytes 5–7 zero |
| 8–143 | 34 × float32 LE, default 0.5, quantized to n/256 | probable | 9/9; e.g. 0.917969 = 235/256 |
| 144–159 | newer-form tail | verified constant | `00000000 0000003f 00060080 00800080` in 5/5 newer files |

Not modelled upstream. Its absence from generated `.ppak`s is presumably
tolerated (upstream's loads succeeded without it), but a generator working from
a device template should carry it verbatim.

## Answers to the brief's questions

1. **Does upstream's event layout decode real patterns sensibly?** Yes, and
   better than that: it re-encodes 309/314 files byte-exactly modulo byte 7.
   Two gaps: the type-1 parameter events, and byte 4 never varying from 100.
2. **Header bytes?** `00 | bars | count | 00`, verified on all 314.
3. **Slots beyond 12?** Patterns are 1..99 per group; files exist only for
   edited patterns. Max index seen 20 (file) / 54 (scene reference).
4. **Scenes?** Layout above; tractable and already byte-exact via upstream
   except two benign fields. P06's 612 bytes is the pre-song-mode form
   (trailer 11 bytes instead of 111). Song mode remains out of scope — noted,
   not expanded.
5. **BPM / time signature in `settings`?** BPM yes, float32 LE at 4–7 (P05 →
   135.0, awaiting hardware confirmation). Time signature no — it lives in
   `scenes`.
6. **Can we generate a `.ppak` we would load?** Not yet — see next section.

## What would have to be true before importing a generated `.ppak`

The encoding is not the risk; import behaviour is. Every claim above is about
files the device wrote. The contributor who got ERROR CLOCK 43 was also
confident. In order of risk, each a hardware-agent task, each preceded by a
fresh full backup and targeting a non-active project slot:

1. **Sample Tool project export/import round trip of a device project,
   unmodified.** Establishes that the reference layout (with `settings`,
   `fx_settings`, 27-byte pad records, missing pattern files referenced by
   scenes) is accepted by the import path at all. Zero new bytes.
2. **Re-zip of the same project from our own TAR writer**, byte-identical
   members, to separate "content" from "container" (ZIP entry order, leading
   `/`, `meta.json` timestamps, TAR header flavour).
3. **One added note event** in one existing pattern, built with the layout
   above (byte 7 = 0). If the device plays it, the encoder is proven on the
   import path, not just the export path.
4. **One new pattern file plus one scene chunk referencing it.**
5. Only then: a from-scratch project using a device `settings`/`fx_settings`
   as template.

Explicit risk note for whoever imports: a project file that the device rejects
at boot has cost one upstream contributor a SHIFT+ERASE flash format. Do steps
1–2 before any step that changes bytes.

Open questions that block a *from-scratch* generator but not a *patch an
existing project* generator: event byte 7; whether byte 4 is honoured as
velocity; `settings` bytes 216–221; `fx_settings` byte 4.

## Disagreements with upstream, for reporting

Written up here rather than silently forked; candidates for
`docs/upstream/coordination-drafts.md`.

1. `PROTOCOL.md` §8 structure is incomplete: real projects contain populated
   `patterns/`, `scenes`, `settings`, `fx_settings`. §8's "no settings file"
   is contradicted by 9/9 device projects and by upstream's own
   `DEVICE_DEFAULT_SETTINGS` provenance. `PROTOCOL.md` §14 item 4 ("sequencer
   data must live elsewhere") is answered: it lives in the TAR.
2. Pattern events have a **type field** in byte 2's low 3 bits; type 1 is
   fader automation with a u16 value at bytes 5–6. `build_pattern` cannot
   express it, and a parser that assumes `pad = byte2 / 8` misreads these.
3. Event byte 7 is not "mostly 0x00, sometimes 0x08 on the last event": 43
   distinct values, 0 in only a third of events.
4. `scenes` bytes 1–4 are a pattern-index-per-group chunk, not padding;
   trailer bytes 0–3 are not the scene count.
5. A 612-byte `scenes` (11-byte trailer) is a valid device-held form.
6. Scenes may reference pattern files that do not exist; the device does this
   itself.
7. Pattern indices go to 99; upstream's `Pattern.index` docstring already says
   so, but the brief's "12 per group" framing came from §8's empty
   `patterns/`.

Footnotes outside this task's scope, recorded so they are not lost:

- Pad records: the 27-byte form is *not* always "26 + `0x00`" — P02 `c/p01`
  ends in `0x0d` and P03 `a/p01` in `0x02` (corrects a line in
  [backup-verification.md](backup-verification.md)). This device holds 220
  27-byte records, 35 of them in the active project that Phase 0 played
  audibly, which sits awkwardly with upstream's claim that 27-byte records
  cause ERR PATTERN 189 on scene switching.
- P06 fills all 99 scene chunks with `01 01 01 01`; the newer form leaves
  unused chunks zero. A reader must treat both as "unset/default".
