# Handoff — after the first hardware run (2026-09-11)

Written at `409e1b6`. Supersedes [`owner-session.md`](owner-session.md) as the
starting point: that script ran, and this is what it found. The older pages are
still true about the device; read [`phase1-handoff.md`](phase1-handoff.md)
§"Hardware facts" and §"The three things that will hurt you" before touching
anything. Evidence for everything below is in
[`../research/reference-to-song-run.md`](../research/reference-to-song-run.md).

## The one thing to know

**The import ladder passed end to end.** `generate_ppak` is proven on hardware:
the container, an appended event, new pattern files, empty patterns and a scene
chunk are all accepted by Sample Tool, stored byte for byte, and play. Rungs
1–4 are done and do not need repeating. Dex `pblk0v4j` can close on that
evidence.

**Backups no longer need Sample Tool.** `create_backup` reads the whole device
over SysEx — nine project TARs plus every library slot's audio — and writes a
`.pak` that `verify_backup` accepts and whose 56 sound entries are
byte-identical to Sample Tool's own backup of the same device. About 50 s with
an unchanged library, because slots whose CRC already appears in a base `.pak`
are copied rather than re-read (cold read is ~25 KiB/s, so ~23 min for 35 MB).

**Importing a project still needs Sample Tool.** That is the only manual step
left, and the blocker on full autonomy.

## Device state right now

- OS 2.5.1, `active_project` 3, `sample_free_bytes` 27,432,768, library 56
  slots.
- P03 holds the 9-sample kit on group **D** pads 1–9 (slots 11, 18, 21, 22, 23,
  24, 25, 27, 28), rung 4's `d11`/scene 16, and the owner's hand-played
  `d14`/scene 19.
- **P06 is wrong**: it holds a copy of P03 from a mis-import (Sample Tool asks
  which project and does not read it from the filename — check the number every
  time). The pending rebuild blanks P06, so this fixes itself.
- Current verified backup: `~/Documents/ep133-backups/session-07.pak`, sha256
  `79ee50b8…`, made by `create_backup`.
- `~/Documents/ep133-backups/keep/` holds what must not be lost:
  `MASTER-2026-09-11-kit-installed.pak` (the restore-to point),
  `YOUR-GROOVE-d14-P03.ppak` and `.json` (the owner's 77-event pattern),
  `DELETABLE-after-rebuild.json`.
- `~/Documents/ep133-library/` holds all 109 sounds that were ever on the
  device, with a usage manifest (`tools/export_library.py`).

## What the owner is waiting on

Nine `.ppak` files sit in `~/Downloads/ep133-rebuild/` with a `README.txt`.
The owner imports them in Sample Tool: device parked on **project 9**, import
`01`…`08`, then move to **project 1** and import `09`. File `NN` goes to
project `NN`.

- `01` rebuilds P01: the kit on group **A** pads 1–9, the owner's groove as
  pattern **A01** (all 77 events at the ticks they were played), scene 1,
  123.08 BPM. Everything else in P01 cleared.
- `02`–`09` empty those projects (`tools/blank_project.py`).
- No sound is deleted: emptying a project frees no sample space.

**After those imports, do this without asking** — the owner has approved the
shape of it and is tired of manual steps:

1. `create_backup(out=".../session-08.pak", base=".../session-07.pak")`, then
   `verify_backup` → `current`.
2. **The delete proof.** `delete_samples([704], backup_id)` — one 1.1 MB slot
   the owner already judged unused. `FILE_DELETE` is documented upstream and
   has never run on this hardware, so this call is the experiment: check the
   entry says `deleted` and not `not_deleted`, that `free_bytes` actually rose,
   and diff a fresh backup. Write `docs/research/delete-proof.md` either way; a
   negative result is a result. Then close Dex `mzgyw615`.
3. If it proves out, show the owner the impact for the remaining 46 slots in
   `keep/DELETABLE-after-rebuild.json` (47 total, 35 MB) and wait for an
   explicit yes before echoing the confirm token. Flag that slots 1, 2, 5, 8,
   13, 457, 500 and 505 are factory sounds only unreferenced because the
   rebuild emptied the old projects.

## What was built this session

| Commit | What |
|---|---|
| `7f6ae1c` | `settings` is 220, 222 **or 224** bytes; the device writes 224 on save |
| `c75fccf` | `tools/export_library.py` — every sound + a usage manifest |
| `371f07b` | `transcribe_groove` rejects a kit from another clip (exemplars are clip-relative times) |
| `850a8d3` | `min_strength` — drop ghost notes while velocity is flat |
| `e5c7ed2` | `pick_exemplar` excludes timbral outliers |
| `f05bbc8` | `detector="bands"` — per-band drum detection so coincident hits are both heard |
| `a828b43` | tick-accurate pattern events with per-event durations, end to end |
| `b9d1833` | `tools/blank_project.py` — empty a project for import |
| `b8985c1` | `delete_samples` (built, **never run on hardware**) |
| `6afc5a8`, `409e1b6` | `create_backup` — a full `.pak` read off the device, byte-identical WAVs |

`uv run pytest -q` → **416 passed**.

## Why the generated groove was wrong, and what is still wrong

The owner rejected three generated grooves and hand-played their own. Timing
was fine (40 of 43 onsets within 40 ms) and classification roughly matched the
clusterer; the losses were ours:

1. **We quantized to 16ths and the device does not.** Their pattern had 3 of 77
   events on a step, a mean 4.1 ticks (21 ms) off. `transcribe_groove` had
   measured that displacement and thrown it away. Fixed in `a828b43` — use
   `tick_pattern`, not `pattern`, when feel matters.
2. **Spectral names are not musical roles.** Their four-on-the-floor landed on
   the pad our kit called *snare*. Open: Dex `54u38l9h`.
3. **Band detection is mistuned** — our hat row had 41 hits to their 8. Open:
   Dex `qfsgupwm`. Note `min_strength` does nothing under `bands` (envelopes are
   normalised per band); fix or document it.
4. **Velocity is the ceiling.** Byte 4 is 100 in all 77 of their hand-played
   events, so velocity is *not* event byte 4 as the device records it. Until
   that is solved every hit plays at one level and a break cannot sound right.
   Open: Dex `vvg9s9ml`, which now has that evidence.

## Open device facts learned

- `scenes` byte 604 is the **currently selected scene**, 1-based — not a scene
  count as upstream calls it (three observations).
- A scene chunk is four group pattern indices then `04 04`; unpopulated means
  the four are zero.
- `FILE_READ_OPEN` / `FILE_READ_DATA` read a **sample slot** as well as a
  project: slot 17 streamed `017.pcm`, crc32 equal to the slot metadata's crc
  and byte-identical to the WAV payload in Sample Tool's backup.
- A read left open and not drained to EOF **wedges the interface** — opening
  project 2 then project 3 stopped the device answering even GREET until a power
  cycle. `DeviceSession.read_file` now re-inits read mode before each open.
- Interrupting the MCP client does **not** stop an in-flight operation; the
  server keeps going. The journal is the source of truth.
- `install_kit` on 12 samples timed out on pad 10 (`DeviceTimeout` on command
  0x05) after 9 succeeded. It needs progress reporting or a per-pad shape; a
  12-sample kit is minutes of silent SysEx.

## The next real piece of work: importing without Sample Tool

`FILE_PUT_META` / `FILE_PUT_DATA` are proven for samples. If they accept a
project file id, `.ppak` import stops being manual and the loop closes.

Known, from read-only recon: project N is fid `2000 + 1000*N` (project 2 =
4000) and opens readable; the projects root 2000 rejects with "invalid id";
sample files are named `NNN.pcm`. The write metadata shape is a **guess** —
`file_put_meta` builds `02 00 05 <fid> <parent 1000> <size> <name> 00 {json}`
for samples, and the parent and json for a project are unknown.

Do this with the device in a state you can afford to lose, a fresh
`create_backup` in hand, and the owner present. The read-only probing alone
wedged the interface once. Target a project the rebuild blanks anyway.

## Rules that did not change

- One MIDI owner. No `tools/*.py` that opens a `DeviceSession` while the server
  is up; Sample Tool counts too. The four `.pak` tools (`pattern_decode`,
  `pack_project`, `diff_projects`, `diff_backups`) are pure file readers and are
  safe alongside the server.
- Every import goes into a **non-active** project.
- Live reads cannot verify a revert; only a post-backup diff can.
- Never commit `*.pak`, `*.ppak`, clips, stems or slices. Never print the serial.
- Conventional Commits, one per piece of work, no history rewriting.

## A note on how the owner works

They will tell you when something sounds wrong, and they are right more often
than the tooling is — the groove diagnosis started from "it still sucked" and
ended in three real bugs. They pushed back on the number of backups; that was
fair, and the answer was that only `install_kit` truly requires one. Prefer
doing the work over asking; ask when the choice is theirs (what to wipe, what to
delete) and show the real impact when you do.
