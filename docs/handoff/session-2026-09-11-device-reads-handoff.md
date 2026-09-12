# Handoff — the device-reads run (2026-09-11, late evening)

Ran from [`session-2026-09-11-autonomous-handoff.md`](session-2026-09-11-autonomous-handoff.md)
at `bd50af4`. This session **reached the device** (stale server pid 83186
killed, lock freed, `device_info` → OS 2.5.1, active project 3, 27.4 MB
free) and worked the "Pending a human" list as far as the owner's rule
allowed. Nothing on the device was written.

## Where the list stopped and why

Step 2 answered **no**: `list_pads(1)` shows the old project 1 (A03/A06/A09
empty, A01/A05/A08 stale references to slots 504/218/132), not the rebuilt
kit. The owner's instruction was "stop before 3 if list_pads(1) does not
show the rebuilt kit", so steps 3, 5 and 6 — the backup, the slot-704
delete proof, the `set_pad` proof and the chop proof — were **not run**.
The read-only steps (2's preflights, 4, 7) were completed.

## What was done

| Step | Result | Commit |
|---|---|---|
| 1 | Port freed, `device_info` ok | — |
| 2 | Rebuild not imported. All nine `~/Downloads/ep133-rebuild/*.ppak` preflighted **against the live device** with `check_ppak` — table below | — |
| 4 | `read_pad(3,'D',7)` + `list_pads(3, fields=True)`: full OS 2.5.1 key set → [`docs/research/pad-metadata.md`](../research/pad-metadata.md). Found `sound.amplitude` runs 0–200 (200 stored on P03 A01/A02); `params.py` bound widened. Dex `uer6sazm` complete | `4fe6a4a` |
| 7 | `list_files(0, 2)` + STAT probes: no settings node exists; the file namespace is sounds + projects + groups + pads only → [`docs/research/global-settings.md`](../research/global-settings.md). Dex `m6fyda1s` complete (negative result) | `ba09b32` |

`uv run pytest -q` → **467 passed**.

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

1. **Sample Tool imports** — the nine files, README order (park on 9, do
   01–08, move to 1, do 09). Answer the slot-overwrite prompt on 01 either
   way. Then `list_pads(1)` must show slots 11,18,21–28 on A1–A9 and
   `read_project(1)` A01 with 77 events.
2. **Backup + delete proof** (`mzgyw615`, pre-approved): `create_backup(session-08.pak, base=session-07.pak)`
   → `verify_backup` → `delete_samples([704], backup_id)` → `docs/research/delete-proof.md`.
   The 46-slot bulk delete still needs the owner's yes.
3. **set_pad proof** (`wr90izot`), non-active project, that same backup:
   trim, pitch −2, playmode key, two pads mute-grouped; read back; undo;
   power-cycle with the owner → `docs/research/pad-params-proof.md`. Also
   settles where a written `sound.bpm/bars/rootnote` lands.
4. **chop proof** (`wwevlmnw`): 8-way equal chop of a 2-bar break on an
   empty group; play; `undo_last_chop`; a second chop left for the
   power-cycle.
5. **Owner at the knobs** (`vsa0gzu9`): the step list in
   `fx-and-settings-map.md`, `diff_project(N, old=session-08.pak)` per step —
   now also the only route to quantize/swing/metronome.
6. **Owner + Sample Tool** (`0uxzjixo`): capture an import; then the
   pitched-run / fader-sweep import for `rfih4h0f`; `play_note` map for
   `w97sjrl7`.

If the owner would rather not wait for the imports, steps 2–4 above can run
now against the current projects: every non-active project is about to be
replaced by a blank anyway, so a `set_pad`/chop there costs nothing.

## Dex

Epic `5y8ub9um`: `geb20kgq`, `uer6sazm`, `m6fyda1s` complete; `wr90izot`
(dated progress appended), `wwevlmnw`, `qw5ui16p`, `cn417veu`, `vsa0gzu9`,
`rfih4h0f`, `w97sjrl7` in_progress; `tvyy03x6`, `0uxzjixo`, `uxqxhkbb`,
`cyxqygzz`, `nv2ns3cs` untouched. `mzgyw615` (under `zmwcu8kp`) has a
dated blocker appended. `dex list 5y8ub9um` and this page agree.

## Rules that did not change

One MIDI owner. Every import into a non-active project. Never commit
`*.pak`, `*.ppak`, clips, stems, slices, the serial. Conventional Commits,
one per piece.

## The exact next command

After the nine imports, paste:

```
Read docs/handoff/session-2026-09-11-device-reads-handoff.md and work its
"Pending a human" list from step 1's verification onward. Steps 2-4 are
pre-authorized (fresh verified backup first, non-active project only);
their power-cycles, the bulk delete and steps 5-6 wait for me. Dex epic
5y8ub9um, same rules as before; one chore(dex) commit at the end; push
when green.
```
