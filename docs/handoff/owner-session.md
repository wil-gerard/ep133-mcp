# The one owner session — command list

Prepared 2026-09-11 at `b9a47ea` for Dex `rwe8pqs8`. Plan and rules:
[`mvp-autonomous-run.md`](mvp-autonomous-run.md); ladder definitions:
[`pattern-encoding-next.md`](pattern-encoding-next.md) and
[`../research/pattern-encoding.md`](../research/pattern-encoding.md). Every
device-touching step below is written up before the next begins, in
`docs/research/reference-to-song-run.md`.

## Preconditions (checked at preparation time)

| Check | Result |
|---|---|
| `uv run pytest -q` | 394 passed |
| `pgrep -fl ep133_mcp` | empty |
| Latest backup | `~/Documents/ep133-backups/EP-133_E3PUI2T7_2026-09-11_backup.pak`, `generated_at` 2026-09-11T01:14:50Z, sha256 `8f582ff8…`. **Stale**: it predates slot 17 (`phase1-handoff.md`, "Device state right now"). Step 0 takes a fresh one |
| `tools/pattern_decode.py check` on it | failures: 0 (314 patterns) |
| `tools/pack_project.py verify` on it | P01–P09 identical |
| ffmpeg | 9.0.1 at `/opt/homebrew/bin/ffmpeg` (installed 2026-09-11 with `brew install ffmpeg`) |
| yt-dlp | `.venv/bin/yt-dlp` |
| Model weights | demucs htdemucs in `~/.cache/huggingface/hub/models--adefossez--HTDemucs` (demucs 4.1 uses the HF cache, not torch hub); Beat This! `final0` in `~/.cache/torch/hub/checkpoints` |
| Default pipeline on the fixture | demucs + Beat This!: 120.0 BPM, downbeat 0.0, A minor, kit and groove recovered |

Target project: **P05** (newer file format, our Phase 0/1 test project;
`a/p07` = slot 16 test tone, `d/p07` = slot 17 test tone). If `device_info`
reports `active_project: 5`, use **P03** instead and substitute 3 for 5
everywhere below — do not switch the active project to make room.

Paths: `B=~/Documents/ep133-backups`. Backups are named
`$B/session-NN.pak` in order; each gets a `.sha256` sidecar. Nothing under
`$B` is ever committed.

## Step 0 — fresh backup, quiet bus

Owner:

```sh
# Sample Tool (browser): full backup -> save as $B/session-00.pak, then close the tab.
cd ~/Documents/ep133-backups && shasum -a 256 session-00.pak > session-00.pak.sha256
```

Agent (server connected, MCP client only — no `tools/*.py` while the server is up):

1. `server_status` → `audio.extra_installed: true`, `ffmpeg` and `yt_dlp` set, device visible.
2. `device_info` → note `active_project`, `sample_free_bytes`, OS 2.5.1. Serial stays off.
3. `verify_backup(path="$B/session-00.pak")` → `status: current`; keep `backup_id`.
4. `list_pads(project=5)` → record group A pad 7 (slot 16, length 18750) and group D pad 7 (slot 17, length 18750). If either differs, stop and reconcile with `phase1-handoff.md` before continuing.

Owner, with the server stopped (offline checks):

```sh
uv run python tools/pattern_decode.py check $B/session-00.pak      # failures: 0
uv run python tools/pack_project.py verify $B/session-00.pak        # P01..P09 identical
```

## Step 1 — import ladder

Backup after each rung: `$B/session-0N.pak` + sha256, then close Sample Tool.
Diff after each rung (server stopped):

```sh
uv run python tools/diff_projects.py $B/session-0{N-1}.pak $B/session-0N.pak
uv run python tools/diff_backups.py  $B/session-0{N-1}.pak $B/session-0N.pak --project 5
```

**Rung 1 — unchanged round trip.** Owner: Sample Tool → export project 5 →
`$B/rung1-P05-export.ppak`; import the same file into project 5; backup
`session-01`. Then:

```sh
unzip -l $B/rung1-P05-export.ppak                                  # expect /projects/P05.tar, /sounds/…, /meta.json
uv run python tools/pack_project.py verify $B/rung1-P05-export.ppak  # P05 identical → the export is the device flavour
```

Diff `session-00` → `session-01`: expected **no differences** in P05. Any
byte the import rewrites is recorded as a finding.

**Rung 2 — our container.** Server stopped:

```sh
uv run python tools/pack_project.py $B/rung1-P05-export.ppak --project 5 --out $B/rung2-P05.ppak
```

Owner imports `rung2-P05.ppak` into project 5; backup `session-02`. Expected
diff `session-01` → `session-02`: **no differences**. If Sample Tool refuses
the file, the container (ZIP flags/extra fields, `generated_at`) is the
finding — compare `unzip -v` of the two `.ppak`s.

**Rung 3 — one added note.** Server stopped, pick an empty 16th S in `a01`
for pad 7:

```sh
uv run python tools/pattern_decode.py dump $B/session-02.pak --project P05
```

Then, server running, the agent calls
`generate_ppak(out="$B/rung3-P05.ppak", project=5, template_pak="$B/session-02.pak",
patterns=[{"group": "A", "index": 1, "add": [{"pad": 7, "step": S}]}], include_sounds=true)`
→ manifest shows `patterns/a01` **extended**, `events_added: 1`, header
count +1, nothing else. Owner imports into project 5, plays pattern A01,
listens for the test tone at step S. Backup `session-03`; expected diff:
`patterns/a01` 8 bytes longer, count byte +1, no other member.

**Rung 4 — new pattern file plus scene chunk.** Agent:

`generate_ppak(out="$B/rung4-P05.ppak", project=5, template_pak="$B/session-03.pak",
patterns=[{"group": "D", "index": 2, "bars": 1, "steps": {"7": "x...x...x...x..."}},
{"group": "A", "index": 90, "bars": 1, "steps": {"1": "................"}},
{"group": "B", "index": 90, "bars": 1, "steps": {"1": "................"}},
{"group": "C", "index": 90, "bars": 1, "steps": {"1": "................"}}],
scenes=[{"scene": 4, "A": 90, "B": 90, "C": 90, "D": 2}], include_sounds=true)`

Scene 4 is unpopulated in P05 as of the 2026-09-11 backup (populated: 2, 3,
6, 14, 22, 23, 25, 33, 56, 66, 89, 98) — re-check in the manifest that
`scenes` changes exactly 4 bytes at offset 25 (`00000000` → `5a5a5a02`; the
`04 04` is already there in unpopulated chunks). Dry-run against the
2026-09-11 backup: `failures: 0`, manifest as predicted. The three empty 4-byte
patterns exist so silent groups point at a file that exists (upstream's
import caution) while matching the device's own empties. Owner imports,
selects scene 4, expects the D-pad-7 test tone on every beat. Backup
`session-04`; expected diff: `+ patterns/d02 (36 bytes)`, three
`+ patterns/{a,b,c}90 (4 bytes)`, `~ scenes @25+4`.

Stop at the first rung that fails; write it up; continue with step 2 (which
does not depend on the ladder) and skip step 4.

## Step 2 — the reference (owner picks the link and range)

Agent: `fetch_reference(url, start_s, end_s)` (≤ 60 s) → `analyze_reference(clip)`
(default `auto`: demucs + Beat This!; report which ran) → `extract_kit(clip)`.
Owner auditions (server may stay up; no MIDI involved):

```sh
for f in "<kit_dir>"/*.wav; do echo "$f"; afplay "$f"; done
```

Owner says go, or asks for another range / `want`. Nothing here touches the
device.

## Step 3 — install the kit (Dex `8zru1ujl` hardware evidence)

Owner: fresh backup `$B/session-05.pak` if anything changed since
`session-04`; otherwise reuse `session-04`. Agent:

1. `verify_backup(path)` → `backup_id`.
2. `install_kit(mapping=<extract_kit.install_mapping>, project=5, group="D", backup_id=…)`
   → `needs_confirmation` with the exact impact (group D pads, including the
   test tone on pad 7). Owner reads it and approves; agent repeats with
   `confirm`. Result `installed`, every entry `installed`, `journal_id` noted.
3. Owner power-cycles the device (full off/on). Server stays up.
4. `device_info`, `list_pads(project=5)` → group D stored slot/length equal
   the install entries' `slot`/`frames`.
5. Server stopped, owner present: `uv run python tools/verify_installed_journal.py`
   → CRC per installed slot matches the journal.
6. Owner: backup `$B/session-06.pak`; diff `session-04` → `session-06`
   (pads/d records only). Owner plays the D pads: audible.

## Step 4 — the groove on the device

Agent: `transcribe_groove(clip, group="D", index=3)` → report `quantization`
and the strings; then

`generate_ppak(out="$B/song-P05.ppak", project=5, template_pak="$B/session-06.pak",
bpm=<analysis bpm>, patterns=[<transcribe_groove.pattern>], scenes=[{"scene": 5, "A": 90, "B": 90, "C": 90, "D": 3}], include_sounds=true)`

(the `{a,b,c}90` empties already exist from rung 4; if rung 4 was skipped,
add them as in rung 4). Owner imports, selects scene 5, plays: the kit plays
the groove at the clip's tempo. Backup `session-07`; diff.

## Step 5 — undo (Dex `fseydiaq` gate)

Agent: `undo_last_install` → `undone`, `library_slots_left_in_place` listed.
Owner: backup `$B/session-08.pak`. Server stopped:

```sh
uv run python tools/diff_projects.py $B/session-06.pak $B/session-08.pak --project 5   # pads/d back to session-04 values
uv run python tools/diff_projects.py $B/session-04.pak $B/session-08.pak --project 5   # only the patterns/scenes from step 4 remain
```

## Write-up and closure

`docs/research/reference-to-song-run.md`: per step, what was run, what the
device did, the byte diffs, verified/probable/guessed marks. Then
`dex complete rwe8pqs8`, and `pblk0v4j` / `8zru1ujl` / `fseydiaq` as the
evidence allows, each with a result naming the session backups.
