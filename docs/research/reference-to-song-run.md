# Reference-to-song hardware run (2026-09-11)

The owner session scripted in [`../handoff/owner-session.md`](../handoff/owner-session.md)
(Dex `rwe8pqs8`). Every claim is marked **verified** (device evidence or a byte
diff), **probable** (model output or an inference from one observation) or
**guessed**. Backups live in `~/Documents/ep133-backups/` as `session-NN.pak`
with `.sha256` sidecars; none is committed. Firmware 2.5.1.

## Step 0 — fresh backup, quiet bus

- `session-00.pak`, sha256 `f403dae7…`, taken by the owner in Sample Tool
  before anything ran. `verify_backup` → `current`, 432 pads compared, no
  differences (**verified**).
- `device_info`: `active_project: 5`, `sample_free_bytes: 3973332` (3.97 MB),
  OS 2.5.1. **The target project is therefore P03**, per the script's fallback;
  every step below substitutes 3 for 5.
- `list_pads(project=5)` matches the script: A7 = slot 16 / 18750, D7 = slot
  17 / 18750 (**verified**).
- `pack_project.py verify session-00.pak`: P01–P09 identical (**verified**).
- `pattern_decode.py check session-00.pak`: `failures: 1` — `P04/settings:
  unexpected size 224`. `diff_projects.py` between the 2026-09-11 backup and
  `session-00` shows only: P04 `settings` 222 → 224 bytes (param float at
  offset 44 `0 → 0x3d260ad0` ≈ 0.0406, tail gains `00 00`), P04 `scenes
  @604+1: 0a → 01`, and P05 `pads/d/p07` 26 → 27 bytes (the slot-17 record).
  Both backups are device-written, so **the device writes `settings` at 222 or
  224 bytes** (**verified**); the checker's fixed 222 is the stale assumption.
  P04 is not touched by this session.

### P03 as found (from `session-00`, offline)

- Group A pads with sound: 1 (slot 2), 2 (slot 1), 10 (slot 1). Pad 7 is a
  stale reference (slot 106, length 0) — silent. Group D: only pad 9 (slot 505,
  stored length 18750); pad 7 stale (slot 60). 36 of 48 pads are stale
  references.
- Scenes 1–15 and 54 populated; `a01` exists with 4 bars and 0 events; `d02`
  exists (8 bars, 48 events); `d05`, `d11`, `d12`, `a90`, `b90`, `c90` absent.
- Substitutions for the ladder: rung 3 adds pad **1** at step 4 (0-based) in
  `a01`; rung 4 creates **`d11`** with pad **9** on every beat and patches
  scene **16**; step 4 uses `d12` and scene **17**.
- Dry runs of rungs 3 and 4 against `session-00` (offline, scratchpad output)
  gave the predicted manifests: rung 3 `patterns/a01` 4 → 12 bytes, header
  byte 2 `00 → 01`, nothing else; rung 4 `+a90 +b90 +c90` (4 bytes each),
  `+d11` (36 bytes), `scenes @97+4: 00000000 → 5a5a5a0b`. Scene chunks are 6
  bytes from offset 7, so scene 16 is at 7 + 15·6 = 97.

### One-MIDI-owner note

The MCP server is the Claude Code session's own transport and stays up. The
four `.pak` tools (`pattern_decode`, `pack_project`, `diff_projects`,
`diff_backups`) import nothing that opens a MIDI port, so they were run with
the server up; `verify_installed_journal.py` opens a `DeviceSession` and is
run by the owner with the server stopped.

## Step 1 — import ladder

### Rung 1 — unchanged round trip: PASS

- Owner exported P03 from Sample Tool (`rung1-P03-export.ppak`, sha256
  `6df3f498…`, 10 ZIP members: `/projects/P03.tar`, 8 `/sounds/*.wav`,
  `/meta.json` with `pak_type: project`, `pak_release: 1.2.0`) and imported
  the same file back into P03.
- Offline: `pack_project.py verify` on the export → P03 identical (the export
  is the device flavour); `diff_projects.py session-00 rung1-P03-export.ppak
  --project 3` → no differences. The 8 sounds are exactly the slots the P03
  pad records reference, matching what `generate_ppak include_sounds=true`
  selects.
- `session-01.pak` (sha256 `c10d0bb7…`), `verify_backup` → `current`.
- `diff_projects.py session-00 session-01` (all projects): **no differences**.
  `diff_backups.py --project 3`: library 109 → 109 slots, 0 pad records
  differing. **The import rewrote no bytes anywhere** (**verified**).

### Rung 2 — our container: PASS

- `tools/pack_project.py rung1-P03-export.ppak --project 3 --out rung2-P03.ppak`
  (sha256 `cc659357…`): `/projects/P03.tar` byte-identical to the export, the
  same 8 sound entries, fresh `generated_at`. `unzip -v` of the two containers
  differs only in timestamps, deflate output size and `meta.json` length
  (301 vs 302 bytes); both are `Defl:N` with leading-`/` names.
- Owner imported it into P03 (Sample Tool accepted it without error — owner
  report). `session-02.pak` (sha256 `3ed36a89…`), `verify_backup` → `current`.
- `diff_projects.py session-01 session-02`: **no differences**;
  `diff_backups.py --project 3`: 109 → 109 slots, 0 pad records.
- Limit of the evidence: for rungs 1 and 2 a clean diff is indistinguishable
  from "nothing was imported"; the proof that Sample Tool actually writes what
  we hand it is rung 3, where the diff must show our bytes (**verified** that
  the container is not rejected; **probable** that the round trip wrote
  identical bytes rather than skipping the write).

### Rung 3 — our bytes (one added note): PLAYS, byte diff pending

- `generate_ppak(out=rung3-P03.ppak, project=3, template_pak=session-02.pak,
  patterns=[{group: A, index: 1, add: [{pad: 1, step: 4}]}], include_sounds=true)`
  → manifest: `patterns/a01` **extended** 4 → 12 bytes, `events_added: 1`,
  one changed byte (`@2: 00 → 01`, the header count), 8 sounds included.
  sha256 `4ff5d362…`. Offline: `diff_projects.py session-02 rung3-P03.ppak
  --project 3` → only `patterns/a01: 4 -> 12 bytes`; `pattern_decode.py check`
  → `failures: 0`; the decoded event is pad 1, tick 96 (step 4), note 60,
  velocity 100, duration 24, byte 7 = 0.
- Owner imported it into P03 with Sample Tool (accepted, no error) and played
  pattern A.01 in project 3: **pad 1's sound plays on beat 2 of bar 1**, at the
  position the file specifies (**verified** by ear, owner report). Pad 1 is
  the pad printed "7" (top-left) — the index-vs-label trap from
  `phase1-handoff.md`; the pad printed "1" (index 7) is a stale reference in
  P03 and silent, as expected.
- After the successful import, Sample Tool showed a **"restore failed"** error
  when the owner went to take the `session-03` backup. No `.pak` reached
  Downloads. Which operation was actually invoked is not yet established; no
  further device write happens on either side until a fresh backup is taken
  and diffed against `session-02`. Expected diff if only the import happened:
  `patterns/a01` 8 bytes longer, count byte +1, nothing else.
- Housekeeping: duplicate `.pak`/`.ppak` copies in `~/Downloads` were
  hash-checked against `~/Documents/ep133-backups` and trashed (disk was at
  3.2 GB free).
