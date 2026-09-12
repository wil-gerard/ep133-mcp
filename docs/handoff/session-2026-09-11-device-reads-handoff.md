# Handoff — the device reads-and-writes run (2026-09-11 → 12)

Ran from [`session-2026-09-11-autonomous-handoff.md`](session-2026-09-11-autonomous-handoff.md)
at `bd50af4`. This session **reached the device** (stale server pid 83186
killed, lock freed, `device_info` → OS 2.5.1, active project 3, 27.4 MB
free), did the read-only steps, then — on the owner's "do it all" — the
pre-authorized writes too. **Project 1 has been written** (see the device
state below); the library gained slots 29 and 30; nothing was deleted.

## Device state now (differs from the previous page)

All in **project 1** (not active; the rebuild import will replace it):

| where | what | left for |
|---|---|---|
| A02 (slot 500) | trim 1000..100000, pitch −2, playmode oneshot / release 255 | **power-cycle verified** |
| B02 (slot 30) | attack 20, amplitude 75, pan −8, time.mode bar, midi.channel 5 (bpm/bars dropped) | power-cycle check: `read_pad(1,'B',2)` |
| slot 29 | renamed `jimmybk 2bar`, rootnote 48, bpm 88, bars 2 via `set_slot` | power-cycle check (same `read_pad`, slot 29 is on D01) |
| A04 + A07 | mute group: A04 was already `true`; A07 was set `true` then **undone** back to `false` | nothing |
| D01, D02, D05 | slot 29 slices 1, 2, 5 of the jimmybk break (undo refused their stale priors; fixed in `c638ad2`, not yet run on hardware) | **power-cycle verified**; a fresh server's `undo_last_chop` clears them |
| D03, D04, D06–D08 | cleared to `sym 0` by the undo | nothing |
| B02–B09 | slot 30, onset-mode chop, playmode `key` / release 15 | **power-cycle verified** (eight distinct trims) |
| library | +slot 29, +slot 30 (both `jimmybk-2bar`, 255683 frames, CRC 469709327), 704 still present | delete list |

Latest backup: `session-11.pak` (`6a06942a…`), taken after the power-cycle
and before writes 3–4; those changed only JSON metadata (no stored
slot/length), so `verify_backup` still reports it `current`. Take
`session-12.pak` with `base=session-11.pak` before anything that moves a
stored record.

## What was done

| Step | Result | Commit |
|---|---|---|
| 1 | Port freed, `device_info` ok | — |
| 2 | Rebuild not imported. All nine `~/Downloads/ep133-rebuild/*.ppak` preflighted **against the live device** with `check_ppak` — table below | — |
| 4 | `read_pad(3,'D',7)` + `list_pads(3, fields=True)`: full OS 2.5.1 key set → [`pad-metadata.md`](../research/pad-metadata.md). `sound.amplitude` runs 0–200; bound widened. Dex `uer6sazm` complete | `4fe6a4a` |
| 7 | `list_files(0, 2)` + STAT probes: no settings node; the namespace is sounds + projects + groups + pads → [`global-settings.md`](../research/global-settings.md). Dex `m6fyda1s` complete (negative) | `ba09b32` |
| 3 | `session-08.pak` verified → `delete_samples([704])` → **device rejected FILE_DELETE**, slot intact, no wedge. The reason string was dropped by the tool; fixed → [`delete-proof.md`](../research/delete-proof.md) | `1734630` |
| 5 | `set_pad` on P1: trim/pitch/playmode/mutegroup **applied**, `sound.rootnote` **dropped** (slot-only), undo works, TAR stored length = trim → [`pad-params-proof.md`](../research/pad-params-proof.md) | `8748de5` |
| 6 | Two chops (equal ×8 oneshot; onsets ×8 key) land clean; undo partial on stale priors → fix → [`chop-proof.md`](../research/chop-proof.md) | `c638ad2`, `ebd5931` |
| owner | **Power-cycle**: A02 write, both chops all persisted; stale pad lengths zeroed on boot | — |
| 5b | The remaining seven `set_pad` fields on B02 (five applied, bpm/bars dropped) and `set_slot(29)` (all applied); `set_pad` now refuses the three slot-only keys | `0ecb04d`, `8d1c9dc` |

| offline | `chop_sample` onset `pick`: `first` / `strongest` / `spread` (the break that front-loaded now spans the clip with either alternative); `check_ppak` reports `trim_frames`; README hardware status | `ee28a91`, `cd8f8e8`, `b29109c` |

`uv run pytest -q` → **469 passed**.

### check_ppak, live

| file | → project | status | would replace on the device |
|---|---|---|---|
| 01 kit-and-groove | 1 | problems: carries sounds for slots 11,18,21–25,27,28 that are already on the device — Sample Tool will ask to overwrite (same audio; either answer is fine) | 145 bpm, 24 pads, 41 patterns, 362 events, 10 scenes |
| 02 blank | 2 | ok | 88 bpm, 15 pads, 25 patterns, 63 events, 8 scenes |
| 03 blank | 3 | problems: **P03 is the active project** — park on another project first (the README already says start on 9) | 123.08 bpm, 48 pads, 62 patterns, 815 events, 18 scenes |
| 04 blank | 4 | ok | 123 bpm, 46 pads, 43 patterns, 97 events, 11 scenes |
| 05 blank | 5 | ok | 135 bpm, 38 pads, 6 patterns, 18 events, 13 scenes |
| 06 blank | 6 | ok | 123.08 bpm, 48 pads, 62 patterns, 781 events, 18 scenes — looks like a near-copy of P03; check that is what you want emptied |
| 07 blank | 7 | ok | 84 bpm, 29 pads, 34 patterns, 376 events, 4 scenes |
| 08 blank | 8 | ok | 101 bpm, 15 pads, 43 patterns, 772 events, 11 scenes |
| 09 blank | 9 | ok | 90.02 bpm, 17 pads, 60 patterns, 1182 events, 15 scenes |

Every file is a single-project export in the device's flavour (SKU
TE032AS001, OS 2.5.1, pak 1.2.0). File 01 references only slots that exist.

## Things learned on the device

- **FILE_DELETE is answered with an error on OS 2.5.1** — at least for
  slot 704 after a write-mode `FILE_INIT`. Which error is the next fact
  to get (the fix that keeps it is `1734630`; needs a fresh server).
- **The project TAR's stored pad length is the trim** (`end − start`), not
  the slot length: A02 went 203522 → 99000 after the trim write.
- **`sound.rootnote`, `sound.bpm`, `sound.bars` are slot-only**: a pad SET
  drops them and the slot is untouched; `set_slot` applies them. The pad
  record takes 11 of upstream's 14 fields.
- **The device computes `sound.bpm` for an upload** (87.93 on slot 29
  before anyone wrote it).
- **Stale pad records are rewritten on boot**: stored lengths on A01/A05/A08
  went from nonzero to 0 across the power-cycle.
- **A stale pad reference cannot be written back** (there is nothing to
  point at), so an undo over one must clear the pad; both undos now do.
- **The library does not dedupe uploads**: slots 29 and 30 hold identical
  audio (same CRC).
- Onset-mode chops take the *first* N onsets; on a break that
  front-loads the slices.

- **Pad record = 12 keys**, slot record = 19 keys; empty/stale pads return
  only `{"sym": 0}`. `envelope.release` is 15 on every `key` pad and 255 on
  every `oneshot`/`legato` pad — the pairing `set_pad` assumes. `sound.pitch`
  is a two-decimal float. `sound.bpm/bars/rootnote` and the loop points
  appeared only on the slot.
- **Slot n is file id n** (`001.pcm`, parent 1000). The project TAR's
  `settings`/`fx_settings`/`patterns`/`scenes` are *not* file nodes: a
  project folder lists only `groups`, and `5001` is an invalid id. So
  there is nothing for a `device_settings()` tool to read; quantize / swing
  / metronome go to the diff method in the knob session.
- The MCP server holds `device.lock` for its whole life once it has opened
  the port, so a side script cannot share the device with a running
  session; large reads come back through the tool.

## Pending a human (in order)

1. **Owner**: do B02–B09 and D01/D02/D05 sound right? Then one more
   power-cycle (for B02's five fields and slot 29's four).
2. **Fresh server, four calls** (no owner needed): `read_pad(1,'B',2)`
   (B02 + slot 30) and `read_pad(1,'D',1)` (slot 29) → if identical,
   `dex complete wr90izot`. `verify_backup(session-11.pak)` →
   `delete_samples([704], backup_id)` once more, record the `reason` →
   branch per `delete-proof.md`. `undo_last_chop` (journal `adb595c9…`) →
   D01/D02/D05 cleared → with the owner's "sounds right", `dex complete
   wwevlmnw`. One `set_pad` with `sound.amplitude: 150` to see 0..200 land.
   Optionally a third chop with `pick: "strongest"` on the same break to
   hear the difference.
3. **Sample Tool imports** — the nine files, README order (park on 9, do
   01–08, move to 1, do 09). Answer the slot-overwrite prompt on 01 either
   way. The imports wipe the P1 test writes above, so do 1 first. Then
   `list_pads(1)` must show slots 11,18,21–28 on A1–A9 and
   `read_project(1)` A01 with 77 events.
4. **Bulk delete** (`mzgyw615`): only after a one-slot delete is proven;
   slots 29 and 30 join `keep/DELETABLE-after-rebuild.json`. Needs the
   owner's yes.
5. **Owner at the knobs** (`vsa0gzu9`): the step list in
   `fx-and-settings-map.md`, `diff_project(N, old=<latest>.pak)` per step —
   now also the only route to quantize/swing/metronome.
6. **Owner + Sample Tool** (`0uxzjixo`): capture an import; then the
   pitched-run / fader-sweep import for `rfih4h0f`; `play_note` map for
   `w97sjrl7`.

## Dex

Epic `5y8ub9um`: `geb20kgq`, `uer6sazm`, `m6fyda1s` complete; `wr90izot`
and `wwevlmnw` in_progress with tonight's results and the power-cycle
blocker in their descriptions; `qw5ui16p`, `cn417veu`, `vsa0gzu9`,
`rfih4h0f`, `w97sjrl7` in_progress, untouched tonight; `tvyy03x6`,
`0uxzjixo`, `uxqxhkbb`, `cyxqygzz`, `nv2ns3cs` untouched. `mzgyw615`
(under `zmwcu8kp`) in_progress with the rejected delete recorded.
`dex list 5y8ub9um` and this page agree.

## Rules that did not change

One MIDI owner. Every import into a non-active project. Never commit
`*.pak`, `*.ppak`, clips, stems, slices, the serial. Conventional Commits,
one per piece.

## The exact next command

After the second power-cycle, close this session (its server has the old
delete/undo/amplitude code) and paste into a fresh one:

```
Read docs/handoff/session-2026-09-11-device-reads-handoff.md and do all
of "Pending a human" step 2 (verified backup first; the slot-704 delete,
the undo and the P1 write are pre-authorized; project 1 only). The
slices sound right: <yes/no>. Stop before step 3. Dex epic 5y8ub9um, same
rules as before; one chore(dex) commit at the end; push when green.
```
