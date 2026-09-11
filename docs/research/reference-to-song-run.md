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
- **Byte diff (`session-03.pak`, sha256 `08483497…`)**: the "restore failed"
  message did not recur; the next backup succeeded. `diff_projects.py
  session-02 session-03`: `patterns/a01` 4 → 12 bytes and **the member on the
  device is byte-identical to the one we generated** (`00 04 01 00 60 00 00 3c
  64 18 00 00`) — header count 1, event tick 96 / pad 1 / note 60 / vel 100 /
  dur 24 / byte 7 = 0 (**verified**). **Rung 3 passes: the pattern encoder is
  proven on the device.**
- Three other members changed because the owner opened and played P03 on the
  device (which the rung requires), none of them in our file: `scenes` scene 1
  `a04 → a01` (the owner selected A.01 while scene 1 was current) and `d05 →
  d01` (the referenced `d05` does not exist; the device normalized it), trailer
  `@604: 0f → 01`; `settings` 222 → 224 bytes with param bytes 94, 95, 120
  changed — the same growth P04 showed after use, so **224 bytes is what the
  device writes on save** (**verified** twice); `pads/b/p02 @16: 64 → 00`
  (meaning unknown, **guessed**: a per-pad parameter rewritten on load).
  Consequence for step 5: the undo diff must be read against these device
  writes, not against a pristine `session-02`.
- `check` on `session-03` fails only on the 224-byte `settings` files (P03,
  P04) — the checker's assumption, not the device.

### Rung 4 — new pattern file plus scene chunk: PASS

- `generate_ppak(out=rung4-P03.ppak, project=3, template_pak=session-03.pak,
  patterns=[D11 1 bar pad 9 "x...x...x...x...", A90/B90/C90 1 bar empty],
  scenes=[{scene: 16, A: 90, B: 90, C: 90, D: 11}], include_sounds=true)` →
  manifest `+a90 +b90 +c90` (4 bytes each), `+d11` (36 bytes), `scenes
  @97+4: 00000000 → 5a5a5a0b`. Scene chunks are 6 bytes from offset 7, so
  scene 16 = 7 + 15·6 = 97 (the P05 script's "offset 25" is scene 4).
- Owner imported into P03, selected scene 16 (hold MAIN + dial), pressed
  play: **slot 505 (D pad index 9, printed "3") on every beat, one-bar loop,
  A/B/C silent**; the group buttons read A.90 B.90 C.90 D.11 (**verified** by
  ear and display, owner report).
- `session-04.pak` (sha256 `36cb1580…`), `verify_backup` → `current`.
  `diff_projects.py session-03 session-04`: exactly the five manifest changes
  — `d11`, `a90`, `b90`, `c90` and `scenes[97:101]` **byte-identical to the
  generated file** (**verified**) — plus one device write, `scenes @604: 01 →
  10`; 0 pad records differing.
- Finding: **`scenes` byte 604 is the currently selected scene, 1-based**
  (`0x10` = 16 after selecting scene 16; `0f → 01` in rung 3 after sitting on
  scene 1; P04 `0a → 01` between the pre-session backups). Upstream calls it
  "scene count"; `pattern-encoding.md` already recorded the data falsifying
  that name. **verified** on three observations.

**Ladder result: rungs 1–4 all pass. Everything `generate_ppak` emits — the
container, an appended event, new pattern files, empty patterns and a scene
chunk — is accepted by Sample Tool, stored byte-for-byte, and plays.**

## Interlude — sound library export and device cleanup (owner request)

- `tools/export_library.py session-04.pak --out ~/Documents/ep133-library`
  (new tool, `c75fccf`): 109 slots, 58.7 MB of WAV — equal to the device's
  62.85 MB capacity minus the 3.97 MB free, so the backup holds the entire
  sample space. `manifest.json` records size, sha256, WAV format and every
  referencing pad record per slot. 46 slots are stored by at least one pad in
  the nine projects; 63 are stored by none; 118 stored slot ids point at
  nothing (stale references). Slot 15 (Phase 0 test tone) is "used" only
  through P06 A4's length-0 record — the stale-reference arming from
  `phase1-handoff.md`.
- The owner deleted 62 of the 63 unused slots in Sample Tool (kept 704).
  `session-05.pak` (sha256 `f0bf2a3e…`): `diff_backups.py session-04
  session-05` → library 109 → 47, the removed set is exactly 62 members of
  the unused list, **0 pad records differing**; `diff_projects.py` → no
  differences in any project (**verified**). `verify_backup` → `current`;
  `device_info` → `sample_free_bytes` 3,973,332 → **27,637,368**.
- `device_info` also now reports `active_project: 3`: the owner has been
  operating P03 on the device, and `active` is the loaded project. The device
  is switched to another project before step 3's install and step 4's import
  so P03 is non-active again for every write.
- Deleting from the agent side is deferred to Dex `mzgyw615` (`delete_samples`:
  FILE_DELETE `06 02 <fid>` is documented upstream but unverified here; a
  slot-15 proof comes first).

## Step 2 — the reference: done (offline)

- Owner's pick: `https://www.youtube.com/watch?v=Blgk9KCLkKk` ("Mr. James
  Barth & A.D. – Above The Skyline"), 0–30 s. `fetch_reference` → 44.1 kHz
  stereo clip, cache `~/.local/state/ep133-mcp/references/9a6267822f49412d/`.
- `analyze_reference` (`separation.method: demucs/htdemucs`,
  `beat_tracker.method: beat_this/final0`): **122.95 BPM**, downbeat 0.44 s,
  4/4, A# minor at confidence 0.29 — all **probable**. Downbeats regular
  every ~1.95 s to 25.8 s, then irregular in the last 4 s.
- `extract_kit`: 12/12 pads filled, `unfilled: []`, `required_pcm_bytes`
  237,288 (2.53 s at 46875 Hz). Drum clusters kick 42 / snare 66 / hat 51
  onsets. Bass slices carry +24 to +56 dB of gain (bass stem −46.8 dBFS);
  melodic +16 to +31 dB. Owner auditioned all 12 via `afplay` and approved.

## Step 3 — install the kit: PARTIAL, device timeout on pad 10

- Preconditions: `device_info` → `active_project: 5` (owner had switched off
  P03), `sample_free_bytes` 27,637,368; `verify_backup(session-05)` →
  `current`.
- `install_kit(mapping=<extract_kit.install_mapping>, project=3, group="D",
  backup_id=f0bf2a3e…)` → `needs_confirmation`: 12 entries, slots 11, 18,
  21–25, 27–30, 32; one destructive entry (D9: prior slot 505 / 18750, live).
  Checked before approval: no P03 pattern except our `d11` hits D pad 9, and
  slot 505 stays in the library (P02 D1, P05 B1, P05 D1 store it). Owner
  approved; the call was repeated with `confirm`.
- The confirmed call ran for minutes with no progress output; the owner
  interrupted the MCP client twice, but **the server kept working** — the
  interrupt only detaches the client. Journal
  `~/.local/state/ep133-mcp/journal/27753ad8d6d249efb4911c5a4fef3d40.json`,
  status **`partial`**: pads 1–9 `installed` (slots 11, 18, 21, 22, 23, 24,
  25, 27, 28 with CRCs), pad 10 **failed** with `DeviceTimeout: no response
  to command 0x05 (request 964)` during the slot-29 upload, pads 11–12
  `pending`. Afterwards `server_status` reports `session_open: true` and
  `device_info` hangs: the device is not answering MIDI (**verified** from
  the journal and the hang; cause **guessed** — device-side stall mid
  transfer).
- Lessons for the tool: `install_kit` needs progress reporting or a per-pad
  call shape; a 12-sample kit is minutes of silent SysEx and the owner cannot
  tell a stall from work. Interrupting the client must be documented as
  harmless-but-blind.

## Step 3 (continued) — 9-pad kit persists and CRC-matches

Decision after the timeout: **keep the 9 installed pads, do not install
10–12 now.** `undo_last_install` reverts only the latest journal
([install.py:136](../../src/ep133_mcp/safety/install.py)); a second install
would orphan this journal and break the step-5 undo gate. Step 4 sequences
only kick/snare/hat, so the melodic pads are not needed for the run. They can
be installed after step 5.

- After the owner power-cycled, `list_pads(project=3)` shows D1–D9 with the
  installed slots (11, 18, 21, 22, 23, 24, 25, 27, 28) and their frame counts;
  D10–D12 remain stale references (**verified**: survives reboot).
- Server stopped (its lock released), `tools/verify_installed_journal.py` →
  `status: matched`, journal `27753ad8…`: all 9 entries `expected_crc ==
  actual_crc` and `stored_slot`/`stored_length` equal to the journal. **The
  installed PCM is byte-correct on the device after a full power cycle**
  (**verified** — this is the Dex `8zru1ujl` hardware evidence, resting on
  `session-05.pak`).
- `verify_backup(session-05)` before the reboot reported `stale` with exactly
  the 9 new library slots and the 9 D pad records changed, backup vs device —
  the expected drift after an install, not a fault.
- Note on the interrupt: killing/stopping the MCP client does not stop the
  server or the in-flight upload. The confirmed install kept running to its
  timeout after the owner interrupted; the journal is the source of truth.
