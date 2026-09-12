# Handoff — 2026-09-12 evening: velocity proven, groove fidelity next

From [`session-2026-09-12-undo-delete-handoff.md`](session-2026-09-12-undo-delete-handoff.md).
House rules and hardware facts: [`phase1-handoff.md`](phase1-handoff.md).
The goal and its plan: [`fast-path-reference-to-song.md`](fast-path-reference-to-song.md).

## The owner's goal, in one line

"Give it a YouTube link → build a kit (from the song itself, or from a free
sample library) → put that beat on the EP-133." Every tool in that chain
exists and ran end-to-end once (2026-09-11,
[`reference-to-song-run.md`](../research/reference-to-song-run.md)). What
failed was the *groove*: the owner rejected three transcriptions and
hand-played their own (`P03 d14`, "about 90% there").

## Agreed order (owner, 2026-09-12): 2 → 1 → 3 → 4

| # | Step | Status |
|---|---|---|
| 2 | Velocity experiment on hardware | **done** — see below |
| 1 | Groove fidelity, offline against `d14` (`54u38l9h`, `qfsgupwm`) | **done** 2026-09-12 — [`groove-fidelity-d14.md`](../research/groove-fidelity-d14.md): recall 0.52 → 0.77, precision 0.16 → 0.84; kick exact, pads by sound. Slice contamination filed as `nzcrfqjv` |
| 3 | Second end-to-end run; prove `import_ppak`; reinstall D10–D12 | **next** |
| 4 | Freesound mode (`8c98vpbn`, `mpm79kpo`) | after 3 |

## Done this session

| What | Result | Commit |
|---|---|---|
| Repo public | github.com/wil-gerard/ep133-mcp, MIT `LICENSE` added, stray `default/` fixture removed, `.env` ignored | `1aec33b`, `a0a9a33` |
| Port blocker | None — the only `ep133-mcp` process belonged to this session. `msw0utgj`'s "existing connection" was a previous session's server, long gone | — |
| **Velocity** | Owner recorded soft/hard/soft/hard on one pad into `P03 d06`, again into `d22`. Byte 4 read back **127/54/127/71** and **127/71/127/54**. Byte 4 is velocity; the historical 100 is the no-pressure value. Byte 7 (31,31,31,8 both times) is not. [`velocity-proof.md`](../research/velocity-proof.md); `vvg9s9ml` complete | `6bb115c` |
| Encoder | `encode_events(bars, [(pad, tick, duration, note, velocity)])`; `encode_pattern` writes `'o'` at `SOFT_VELOCITY` 60; decoders name the field `velocity`; `generate_ppak` events form takes `velocity` 1..127 | `6bb115c` |
| Transcription | `transcribe_groove` `tick_pattern` events carry velocity 40..127 from onset strength, loudest hit of each class = 127, normalised per class for both detectors ([`groove.py`](../../src/ep133_mcp/audio/groove.py) `velocity_for`) | `6bb115c` |
| Ground truth | `d14`, `d06`, `d22` events and the D-pad slots saved to [`fixtures/p03-hand-played-2026-09-12.json`](../research/fixtures/p03-hand-played-2026-09-12.json) so step 1 needs no device | this handoff |

`uv run pytest -q` → **515 passed**.

## Device state

OS 2.5.1, active project **3**, 55,078,320 bytes free. P03 group D holds the
extracted kit on D1–D9 (slots 11, 18, 21, 22, 23, 24, 25, 27, 28; D10–D12
stale). New since the last handoff: `d06` and `d22` (4 events each, the
experiment). Between the two reads today `a11` lost 7 events (pad 10, bars
3–4) and `b06` lost 7 (pad 2, bars 3–4) — not caused by the server; both are
in `session-24.pak` if the owner wants them back. Last verified backup:
**`session-24.pak`** (`5544fcf6…`), status `superset` — valid for writes.

Still unheard: playback of a written velocity ≠ 100. The step-3 import is the
first time one goes device-ward; listen for it.

## Step 1 — what the fixture says, so you do not re-derive it

`d14` is 4 bars, 77 events, three pads, all velocity 100 (recorded without
pressure). Per pad, folded to one bar:

| pad | kit class (`extract_kit`) | hits | folded row | musical role |
|---|---|---|---|---|
| 1 | kick | 46 | `x.x.x.x.x.xx.xxx` | **8ths — the hat role** |
| 2 | snare | 23 | `x...x...x...x...` | **four-on-the-floor — the kick role** |
| 3 | hat | 8 | `....x.......x...` | **backbeat — the snare role** |

Durations: pad 1 14–25 ticks, pad 2 32–51, pad 3 44–98. The owner assigned
by ear; our spectral naming (lowest centroid = kick …) put every part on the
wrong pad. That is `54u38l9h`. Our own transcription on the same clip was
kick 16 / snare 12 / hat 41 against their 23 / 8 / 46 — hat band far too
permissive, kick too strict. That is `qfsgupwm`.

Suggested approach (not started):

1. **Scorer first.** A function that takes a `tick_pattern` and the fixture's
   `d14`, matches hits within ±12 ticks per pad, and reports precision /
   recall per pad plus hit-count ratio. Make it a test against the fixture so
   the two fixes below have a number to move.
2. **Roles by rhythm** (`54u38l9h`): after detection, rank the three streams
   by on-beat share and density — the stream that is ~100 % on beats 1–4 is
   the kick role, the one on 2 and 4 the snare role, the densest the hat
   role — and map roles to pads by the kit's *class names* (kick pad gets the
   kick role) so the owner's pads keep meaning. Keep the spectral names as
   `class`, add `role`.
3. **Calibration** (`qfsgupwm`): tune `ONSET_DELTA` / `ONSET_FLOOR` /
   `ONSET_WAIT` in `groove.py` against the scorer; the hat band needs a much
   higher floor or a wait, the kick band a lower floor. Also note
   `min_strength` no longer thins anything under `bands` because envelopes
   are scaled per band — fix or document.
4. Acceptance: ≥ 80 % of `d14`'s hits matched within one 16th on the right
   pad, no pad more than 1.5× the owner's count. Then step 3.

Inputs: clip and stems cached at
`~/.local/state/ep133-mcp/references/9a6267822f49412d/` (`kit/kit.json`,
`kit/groove.json` from the 09-11 run). `analyze_reference` is cached there
too, so no demucs run is needed. BPM 122.95, downbeat 0.44 s.

## Blanked projects errored (2026-09-12 evening)

Every project the owner blanked with `tools/blank_project.py` (all but P3)
errored on the device when selected: the blank zeroed every scene chunk
while the trailer's selected scene stayed 1, and no project the device ever
wrote has chunk 1 zeroed (a fresh project holds `01 01 01 01` in all 99).
Fixed in `d0c04eb`; `ep133-backups/blanks-v2/` holds the corrected blanks.
**P1, P2, P4, P5 re-imported with `import_ppak` — first hardware use, each
read back byte-equal.** P6–P9 still need it; see
[`fast-device-writes.md`](fast-device-writes.md) for how to run the rest
without an approval per call.

## Step 3 — the hardware session

1. Fresh verified backup (one per session, `create_backup` with
   `base=session-24.pak` or Sample Tool). Switch the device off P03.
2. ~~`import_ppak` proof~~ — done 2026-09-12 evening on P1/P2/P4/P5 (above).
   Power-cycle persistence still unchecked.
3. `install_kit` D10–D12 (the 09-11 run timed out on pad 10 mid-upload;
   `install_kit` still has no progress reporting — consider a per-pad call
   shape or at least a retry before a 12-pad install).
4. `transcribe_groove` (fixed) → `generate_ppak` events form with velocities
   → `import_ppak` → owner listens. Write it up in `docs/research/`.

## Step 4 — Freesound

Owner has an API key in `.env` (gitignored; `FREESOUND_API_KEY`, plus
`FREESOUND_CLIENT_ID`). **Nothing reads it yet.** Plan: `.env` loader in the
server, `server_status` reports `freesound: configured` (never the value),
`search_sounds` with token auth, `license:"Creative Commons 0"` default,
download the **HQ preview** (originals need OAuth2 — decided against for
now), convert to 46875 Hz mono 16-bit, attribution sidecar per `8c98vpbn`,
same `install_mapping` shape as `extract_kit`.

## Dex hygiene

The tree is stale in places: `tvyy03x6` / `cn417veu` (`import_ppak`) are
shipped (`c86b811`) but open — close them after the step-3 hardware proof;
`msw0utgj`'s blocker no longer exists; `8zru1ujl` has its hardware evidence
from the 09-11 run.

## Rules unchanged

One MIDI owner (`pgrep -fl ep133-mcp` first; Sample Tool in a browser
counts). One verified backup per session, not per step. Every import into a
non-active project. Never commit `*.pak`, `*.ppak`, clips, stems, slices,
the serial, or `.env`.
