# Handoff — run to MVP without check-ins

Written 2026-09-11. Supersedes the ordering in
[`fast-path-reference-to-song.md`](fast-path-reference-to-song.md) and
[`fast-path-to-composition.md`](fast-path-to-composition.md); their content
(tool shapes, hardware facts, decisions already made) still applies. House
rules in [`phase1-handoff.md`](phase1-handoff.md). `dex list` is the plan of
record.

## MVP

```
owner:  "this part of <YouTube link>, 0:42–1:10"
agent:  fetch_reference → analyze_reference → extract_kit → install_kit
        → transcribe_groove → generate_ppak
owner:  import .ppak in Sample Tool → hears the kit playing the groove
```

Every offline piece of this is buildable now with no owner involvement. There
is exactly **one** owner session at the end, and it does every hardware step
at once. Do not ask for a second one.

## Operating rules for the agent

- Work through the queue below in order. Each item ends with
  `uv run pytest -q` green, a Conventional Commit, `git push`, and the Dex
  task completed with a result. One commit per item; do not batch.
- Ask the owner nothing unless (a) the queue is exhausted and the owner
  session is next, or (b) a step would touch the device, delete files, or
  force-push. Everything else: decide, note the decision in the Dex result,
  move on.
- `docs/research/` gets a page only for device-touching evidence or a finding
  that contradicts an earlier page. Offline work is documented by tests and
  Dex results, not prose.
- Tests never download model weights or hit the network. The Beat This!
  checkpoint is in the torch hub cache and (since 2026-09-11) the demucs
  weights are in the HuggingFace cache; tests that need them skip when
  absent (see `tests/test_analysis.py::beat_this_cached`).
- Never commit `*.pak`, `*.ppak`, downloaded audio, or extracted slices.
  Fixtures are synthesized (`tests/synth_reference.py`).
- Read [`fast-path-reference-to-song.md`](fast-path-reference-to-song.md)
  "Decisions already made" before touching the audio pipeline. Add to that
  list: **Beat This! for beats/downbeats** (librosa fallback), **no madmom,
  essentia, basic-pitch, or drum-stem separators** — evaluated 2026-09-11,
  none is packaged well enough to be worth the dependency; `extract_kit`
  clusters the demucs drums stem itself.

## Where we are

| Piece | State |
|---|---|
| `fetch_reference` | shipped, tested (`xp853rdi`) |
| `analyze_reference` | module + tests done (`src/ep133_mcp/audio/analysis.py`); **not yet an MCP tool** |
| `extract_kit`, `transcribe_groove` | not started |
| `install_kit` | implemented, offline-tested; hardware evidence pending (`8zru1ujl`) |
| Pattern/scene/settings encoding | byte-exact against 314 device files (`docs/research/pattern-encoding.md`) |
| Project packer | not written |
| `generate_ppak` | not started |
| Device accepting a file we wrote | **unknown — the owner session answers it** |

## Queue — offline, in order

1. **Register `analyze_reference` as an MCP tool** — Dex `o6qmvexz`.
   `server.py`, next to `fetch_reference`: args `clip`, `separation="auto"`,
   `beat_tracker="auto"`, `force=False`; same `AudioToolsUnavailable`
   wrapping. Tool description states every estimate is probable and names
   which tracker/separator ran. Test through the stdio harness the way
   `test_server_stdio.py` does for the other tools.

2. **`extract_kit`** — `dg4stw46` (closes `fks0i9lm`). Shape in
   [`fast-path-reference-to-song.md`](fast-path-reference-to-song.md) §A.3.
   Drum classes: onsets on the drums stem → per-onset spectral centroid,
   flatness, and low-band ratio over the first 50 ms → k-means (k=3) →
   label clusters by centroid order (low=kick, mid=snare, high=hat). Exemplar
   = highest onset strength with the longest gap to the next onset. Bass and
   melodic: `librosa.pyin` on each onset's first 200 ms, keep distinct MIDI
   notes. Every slice: trim to `starts_s`, cap 1000 ms, 5 ms fade-out,
   resample to 46875 Hz mono 16-bit with `soxr` via librosa, write
   `slices/<pad>_<class>.wav` + `kit.json`. Report bytes per slice and total
   against the preflight estimate `install_kit` uses. Acceptance: three drum
   classes and both bass notes recovered from `synth_reference` with
   `separation="hpss"`; every slice passes the existing preflight.

3. **`transcribe_groove`** — `osdvduzg`. Onsets on the drums stem →
   nearest kit class by the same features → quantize to 24-tick 16ths from
   `downbeat.value_s` at `bpm.value` → `x`/`.` strings per pad. Bars = clip
   length ÷ bar length rounded to 1/2/4, using `beats_per_bar` only as a
   hint (Beat This! reports 2 on the fixture). Return quantization error
   (mean |onset − grid| in ms) and the pattern in `generate_ppak` shape.
   Acceptance: fixture groove string equals `synth_reference.PATTERN`.

4. **Project packer** — `p4c14hd3`. `protocol/projects.py`
   (or `tools/pack_project.py`): unpack a `.pak` project, repack with the
   device flavour listed in
   [`pattern-encoding-next.md`](pattern-encoding-next.md) rung 2. Acceptance:
   every member of all nine projects in the latest backup round-trips
   byte-identically; `tools/pattern_decode.py check` on the output prints
   `failures: 0`. The backup lives in `~/Documents/ep133-backups/`; the test
   uses a synthesized minimal project, not the backup.

5. **`generate_ppak`** — `bqgv6h7y`, patch-an-existing-project variant as
   specified in [`fast-path-to-composition.md`](fast-path-to-composition.md)
   step 2. Unblocked from the ladder for the offline build: the ladder only
   changes whether it *works*, not what it writes. Acceptance: fixture
   template + `transcribe_groove` output → `.ppak` → `pattern_decode.py
   check` `failures: 0` and the decoded pattern equals the input strings.

6. **Prepare the owner session.** Write the exact command list the owner
   will run (below, filled in with real paths), verify `pgrep -fl ep133_mcp`
   is empty, confirm the latest backup's date. Then stop and tell the owner
   the session is ready. This is the first check-in.

## The one owner session — Dex `rwe8pqs8`

Start-here page for the session agent: [`hardware-run-handoff.md`](hardware-run-handoff.md).
Command list with real paths, prepared 2026-09-11:
[`owner-session.md`](owner-session.md). Queue items 1–6 above are done
(`f53c017`, `8078f2a`, `04ace53`, `f621665`, `e5e46a6`, `b9a47ea`); the
"Where we are" table is superseded by that page's preconditions.

Fresh full Sample Tool backup first. Non-active project throughout. Stop at
the first failure, write it up in `docs/research/`, and continue with the
parts that do not depend on it.

1. Import ladder rungs 1–4 (`pattern-encoding-next.md` + rung 4 from
   `pattern-encoding.md`): export/import unchanged → our container → one
   added note → one new pattern + scene chunk. Backup and diff after each.
2. Owner picks a YouTube link and range. Agent runs fetch → analyze →
   extract; owner plays the slices (`afplay`) and says go.
3. `install_kit` into an empty group of the non-active project. Power cycle.
   `list_pads` and CRC check — this is the `8zru1ujl` hardware evidence;
   record it.
4. `transcribe_groove` → `generate_ppak` with that project as template →
   owner imports in Sample Tool → plays.
5. `undo_last_install`, verify by backup diff — closes `fseydiaq`'s gate.

Write-up: one page `docs/research/reference-to-song-run.md` with what
worked, what did not, byte diffs, and the resulting state of the plan.
Complete `rwe8pqs8`, `pblk0v4j`, `8zru1ujl` as evidence allows.

## After MVP (not now)

Freesound search mode (`mpm79kpo`, `8c98vpbn`), project write over SysEx to
drop the Sample Tool step, drum-stem separation behind `separate()` if the
clustering proves too coarse on real material.

## Guardrails that do not relax

One MIDI owner. Fresh backup before each hardware rung. Non-active project
for every import. Never print the serial. Every device-touching step written
up before the next begins.
