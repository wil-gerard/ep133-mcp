# Groove fidelity against the owner's d14 — 2026-09-12

Step 1 of [`session-2026-09-12-velocity-handoff.md`](../handoff/session-2026-09-12-velocity-handoff.md),
run offline against [`fixtures/p03-hand-played-2026-09-12.json`](fixtures/p03-hand-played-2026-09-12.json)
(`d14`: 4 bars, 77 events, three pads) and the cached clip `9a6267822f49412d`.
Scorer: `src/ep133_mcp/audio/score.py`, test: `tests/test_score.py`.

## Result

`transcribe_groove(clip, detector="bands")`, scored at ±12 ticks per pad:

| stage | recall | precision | pad 1 (hat role) | pad 2 (kick role) | pad 3 (snare role) |
|---|---|---|---|---|---|
| before (union fold, pads by class name) | 0.52 | 0.16 | 19/46 of 65 | 13/23 of 30 | 8/8 of 159 |
| + consensus fold, pads by sound (`025ca03`) | 0.74 | 0.88 | 36/46 of 42 | 16/23 of 16 | 5/8 of 7 |
| + bars vote by power, loudest retrigger (`cbd7a29`) | **0.77** | **0.84** | 36/46 of 42 | 16/23 of 16 | 7/8 of 12 |

("matched/truth of detected".) Every pad within 1.5× the owner's count.
The 0.80 acceptance bar was not reached; the ceiling is 70/77 = 0.91 and
the rest is the owner's variation, not detection — see below.

## What was wrong

1. **The pads.** `01_kick.wav` is a 50 ms thump under a 200 ms hat tail
   (whole file: centroid 2423 Hz); `02_snare.wav` is a crack over a 250 ms
   kick body (99 % below 150 Hz); `03_hat.wav` the same. `extract_kit` names
   by the first 50 ms; the slice runs to the next onset on the stem. The owner
   heard the whole slice and played pad 2 as the kick, pad 1 as the hat.
   `groove.pad_roles` now measures each slice as it sounds and routes the
   kick/snare/hat streams accordingly; the result reports `class` and `role`.
   Root cause is filed as dex `nzcrfqjv`.
2. **The fold.** A 30 s clip is 15 bars; folding onto 4 bars by union kept
   every intro tick and fill from every bar: 254 events. Now each clip bar
   covering a cell votes with the power of the class's loudest hit in it, on
   the cells inside the clip only (`CONSENSUS` 0.5). A silent bar abstains,
   a bar of faint intro ticks barely counts, a fill in one bar of four is
   dropped.
3. **Retriggers.** Two onsets within `MIN_INTERVAL_STEPS` kept the first;
   a 0.31 pre-echo a third of a step before a 0.93 backbeat stood for it.
   The loudest now places the hit.

`ONSET_FLOOR` / `ONSET_DELTA` / `ONSET_WAIT` were grid-searched against the
scorer (floors 0.15–0.5 per band, wait 3/5): no combination moved the score.
The thresholds were never the problem.

## What remains, and why it is not detection

- **Kick 16/23.** The owner's 23 include 7 flams (two hits 3–6 ticks apart
  on one downbeat). A 16th grid holds one; ours has all 16 downbeats and
  nothing else.
- **Snare 7/8.** Bar 3 beat 4 is missing: the song's backbeat in clip bar 14
  fails `LOW_DOMINANCE` (mid/low 0.09 against 0.10) and bars 6/10 have no
  snare there. The 4 extras are real hits in the song (a snare on the "e" of
  1 in clip bars 5, 6, 11, 12) the owner did not play.
- **Hat 36/46.** The owner's late-bar 16th runs (`.xx.xxxx`) fall 15–17 ticks
  from the song's (`xxxxx.`); ±16 gives 37, ±24 gives 38. That is the "about
  90 %" the owner named.

## For step 3

The pattern that goes device-ward is `tick_pattern` from
`detector="bands"`: kick on pad 2 (all 16 downbeats), snare on pad 3, hat on
pad 1, velocities 40–127 per cell (median of the folds). Listen for the
snare on the "e" of 1 in bars 1 and 4 — it is the song's, and the owner may
want it gone (`min_strength` 0.65 on the snare band removes it).
