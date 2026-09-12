# Handoff — the autonomous tools run (2026-09-11, evening)

Ran from [`autonomous-tools-run.md`](autonomous-tools-run.md) at `3ed227a`.
Supersedes nothing: [`session-2026-09-11-handoff.md`](session-2026-09-11-handoff.md)
is still the device-state page and everything it says about the device is
still true, because **this session never reached the device**.

## The one thing to know

The MIDI port was held for the whole run by an `ep133-mcp` server from an
earlier Claude Code session (pid 83186, started 19:10, parent claude pid
88493) — it owns `~/.local/state/ep133-mcp/device.lock`. This session's own
server never opened the port (`server_status` → `session_open: false`), and
the flock refused every script. Killing the stale process was blocked by the
permission classifier. So: **all code, no hardware.** Close that older
session (or kill 83186) before the next run; a fresh `claude` will start a
server with the new tools in it.

Consequences: the nine `.ppak` imports were not verified as done, the
slot-704 delete proof did not run, no research note got an observation, and
every "done when" that needs a read-back stays open. Nothing on the device
changed.

## What shipped

13 commits on `main`, `uv run pytest -q` → **467 passed** (from 416).

| Commit | Tool(s) | State |
|---|---|---|
| `b08ae49` | `read_pad(project, group, pad)`; `list_pads(fields=True)` | code + tests; key set unobserved |
| `125b434`, `d7deb5a` | `read_project(project, source=None)` — decoders moved to `protocol/decode.py`; `tools/pattern_decode.py` wraps them, byte-identical json before/after | offline verified on `session-07.pak` (P03 d14 = the 77 kept events) |
| `3adea6f` | `set_pad`, `set_slot`, `undo_last_pad_change` (`safety/params.py`) | code + tests; never written to hardware |
| `1b4c651` | `chop_sample`, `undo_last_chop` (`safety/chop.py`) | code + tests; onset mode tested on synthetic PCM |
| `c423910` | `generate_ppak` events form: `note` per event, `automation: [{tick, param, value}]` per pattern | all 376 device patterns round-trip modulo byte 7 (2 shortened P09 files excepted) |
| `23baa49` | `check_ppak(path, project)` — the import preflight, read-only | the nine rebuild files all pass it |
| `989b88d` | `diff_project(project, old, new=None)`; `docs/research/fx-and-settings-map.md` | survey + two hypotheses, nothing verified |
| `24a4d8c` | `generate_ppak` `fx={selector, params}` / `settings={params, group_bytes}` by raw index | code + tests |
| `667e484` | `play_note(channel, note, velocity, duration_s)` — channel MIDI, not SysEx PLAY | code + tests |
| `ef5a709` | fix: `delete_slot` raised `TypeError` instead of `DeviceRejected` on a rejected delete | would have broken the delete proof's negative path |
| `516ced7` | `list_files(node, max_depth)`, `stat_file(id)` — FILE_LIST walk + STAT | code + tests |
| `27d141d` | README tool table | |

Every write tool follows the install contract (backup current → impact →
token → journal → write → read back → report applied / changed / dropped /
side effects). `docs/design/tool-contracts.md` has an entry per tool.

## Things learned without the device

- **FILE_LIST exists.** `04 <page u16> <node u16>` — page 0 is what
  ep133-ppak calls `GROUP_DUMP`; phones24's exporter walks the entire
  device from node 0 with it. This is the way to hunt the settings node,
  not blind STAT. `list_files` does it, sounds root skipped by default.
- **SysEx PLAY has no capture anywhere.** Neither phones24 nor krate sends
  it; krate's capture-wishlist calls audition (their opcode 0x76)
  "completely unknown". `play_note` uses plain channel MIDI instead.
- **Fader-function hypothesis** (`fx-and-settings-map.md`): `settings.params[12·g + f]`
  is fader function `f`'s last value for group `g`, `group_bytes[g]` is the
  assigned function, and the pattern automation `param` ids (1, 5, 6) are
  the same `f`. Eight of nine projects fit. `fx_settings` reads as 17 + 17
  (knob A / knob B per effect type). Both need one knob session to confirm.
- `blank_project.py` leaves empty 4-byte pattern files rather than deleting
  them (the blanks show 25–62 patterns, 0 events); harmless, but
  `check_ppak` counts them.

## Pending a human (in the order to do them)

1. **Free the port** (above). Then `device_info`; confirm `active_project`.
2. **Was the rebuild imported?** `list_pads(1)` — group A pads 1–9 stored,
   `read_project(1)` → A01 with 77 events. If not, the nine files in
   `~/Downloads/ep133-rebuild/` are still waiting; `check_ppak(path, N)` on
   each before Sample Tool.
3. **Backup + delete proof** (pre-approved in the previous handoff, only
   after the imports): `create_backup(session-08.pak, base=session-07.pak)`
   → `verify_backup` → `delete_samples([704], backup_id)` → write
   `docs/research/delete-proof.md` → Dex `mzgyw615`. The 46-slot bulk
   delete still needs the owner's yes.
4. **read_pad observation** (`uer6sazm`): `read_pad(3, 'D', 7)` and
   `list_pads(3, fields=True)` → `docs/research/pad-metadata.md` with every
   key, type, range. Two minutes.
5. **set_pad proof** (`wr90izot`), non-active project, fresh backup: trim,
   pitch −2, playmode key, two pads mute-grouped; read back; undo; then a
   power-cycle with the owner → `docs/research/pad-params-proof.md`.
6. **chop proof** (`wwevlmnw`): an 8-way equal chop of a 2-bar break on an
   empty group; play; `undo_last_chop`; a second chop left for the
   power-cycle.
7. **Settings-node hunt** (`m6fyda1s`), last: `list_files(0, max_depth=2)`;
   anything outside `sounds`/`projects` gets `metadata()` before any read →
   `docs/research/global-settings.md`, negative result included.
8. **Owner at the knobs** (`vsa0gzu9`): the step list in
   `fx-and-settings-map.md`, `diff_project(N, old=session-08.pak)` per step.
9. **Owner + Sample Tool** (`0uxzjixo`): capture an import; then the
   pitched-run / fader-sweep import for `rfih4h0f`; `play_note` map for
   `w97sjrl7` (MIDI in on, not recording).

## Dex

Epic `5y8ub9um`: `geb20kgq` complete; `uer6sazm`, `wr90izot`, `wwevlmnw`,
`rfih4h0f`, `cn417veu`, `vsa0gzu9`, `qw5ui16p`, `m6fyda1s`, `w97sjrl7` all
`in_progress` with a "Progress / Blocker / Next step" section in each
description; `tvyy03x6`, `0uxzjixo`, `uxqxhkbb`, `cyxqygzz`, `nv2ns3cs`
untouched. `dex list 5y8ub9um` and this page agree.

## Rules that did not change

One MIDI owner (the flock now proves it — that is what stopped this run).
Every import into a non-active project. Never commit `*.pak`, `*.ppak`,
clips, stems, slices, the serial. Conventional Commits, one per piece.

## The exact next command

With the older session closed, paste:

```
Read docs/handoff/session-2026-09-11-autonomous-handoff.md and work the
"Pending a human" list in order. Steps 1-4 and 7 are yours alone (reads,
plus the pre-approved backup and slot-704 delete); stop before 3 if
list_pads(1) does not show the rebuilt kit on group A. Steps 5-6 write to
a non-active project after a fresh verified backup and are pre-authorized;
their power-cycles, the bulk delete, and steps 8-9 wait for me.
```
