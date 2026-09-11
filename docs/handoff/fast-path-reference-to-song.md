# Handoff — reference song → matching kit → EP-133 → patterns, all through MCP

Written 2026-09-10. Supersedes the ordering in
[`fast-path-to-composition.md`](fast-path-to-composition.md) by adding the
front of the pipeline: the owner points at part of a song and the agent builds
a kit that sounds like it. House rules and hardware facts live in
[`phase1-handoff.md`](phase1-handoff.md). `dex list` is the plan of record;
this work is Dex phase `reference-kit` (see bottom).

## Target loop

```
owner:  "this part of <YouTube/Spotify link>, 0:42–1:10"
agent:  fetch_reference → analyze_reference → extract_kit → install_kit
        → generate_ppak (groove transcribed from the reference)
owner:  import .ppak in Sample Tool → play
```

Every arrow is an MCP tool call. The Sample Tool import is the only manual
step and is the stretch goal to remove (project write over SysEx).

## Decisions already made — do not reopen

**Audio source: YouTube, via `yt-dlp` + `ffmpeg`.** Spotify no longer offers
audio to third parties (previews are mostly gone, the audio-features API was
withdrawn for new apps in late 2024). Accept Spotify links anyway: resolve
title/artist from Spotify's public oEmbed endpoint (no auth), search YouTube
for `"<artist> <title>"`, take the top result, and report which video was used
so the owner can override with an explicit URL. One code path downstream.

**"Matching sounds" = slice the reference itself, first.** Separating stems
and cutting one-shots from the clip is the tightest possible match and needs
no search, no catalogue, no license negotiation. Searching a free library by
timbre (Freesound, CC0 filter) is the second mode, built after the first works
and reusing its analysis. The sliced kit is for the owner's own device and
personal use; the tool writes a sidecar naming source URL and time range and
never publishes anything. Say this once in the tool description, not in
every response.

**Patterns come from the same analysis.** Onset detection per stem, quantized
to the 16th grid at the detected BPM, gives `x`/`.` strings per pad — exactly
the `generate_ppak` input already specified. "Recreate this groove" falls out
of the kit extraction for free.

**Device memory is the binding constraint.** 4,014,252 B free at 46875 Hz
mono 16-bit ≈ 42 s total. A 12-pad one-shot kit at ≤ 1 s each fits; a loop
kit does not. `extract_kit` must trim to onsets, cap per-slice length, report
bytes, and hand the result to the existing preflight rather than guessing.

## Build order

### A. Offline audio pipeline — no hardware, start now

Optional dependency group `audio` in `pyproject.toml`: `yt-dlp`, `librosa`,
`soundfile`, `numpy`, `demucs` (torch; CPU is fine for a 30 s clip). `ffmpeg`
is a system dependency; `server_status` should report whether it and `yt-dlp`
are on PATH so the agent can tell the owner what to install. Core install must
not pull any of this; tools return a structured `AudioToolsUnavailable` error
with the install command when the extra is missing.

1. **`fetch_reference(url, start_s, end_s)`** → local WAV under
   `~/.local/state/ep133-mcp/references/<sha>/clip.wav`, 44.1 k stereo, plus
   `source.json` (url, video id, title, start/end). Cap clips at 60 s. Spotify
   URLs go through the oEmbed → YouTube search hop described above. Cache by
   (video id, start, end).
2. **`analyze_reference(clip)`** → `bpm` (librosa beat track, with confidence
   and half/double-time alternates), `key` (chroma template match, probable),
   `downbeat_s`, and per-stem onset lists after demucs (`drums`, `bass`,
   `other`, `vocals`). Cache next to the clip.
3. **`extract_kit(clip, want)`** → up to 12 one-shots. `want` defaults to
   `kick, snare, hat, perc×3, bass×3, melodic×3`. Drum hits: onsets on the
   drums stem, clustered by spectral centroid/flatness into kick/snare/hat,
   pick the cleanest exemplar per class (highest onset strength, least overlap
   with next onset). Bass/melodic: onsets on the bass/other stems, pitch via
   pyin, pick distinct notes, trim to the note. Every slice: trim to onset,
   fade out over the last 5 ms, cap 1000 ms, resample to 46875 Hz mono 16-bit
   with `soundfile`/`librosa` (the format `install_kit` requires — preflight
   already rejects anything else). Return `{pad, path, class, note, bytes,
   source_s}` plus total bytes, and write `kit.json` beside the files.
4. **`transcribe_groove(clip, kit)`** → `patterns` in `generate_ppak` shape:
   bars (1/2/4 from clip length), one `x`/`.` string per assigned pad, built
   by assigning each drum onset to the nearest kit class and quantizing to
   24-tick 16ths. Report quantization error so the agent can pick bar count.

Acceptance for A: a committed synthesized fixture (a few bars of generated
kick/snare/hat/bass at a known BPM — **not** a copyrighted clip) round-trips:
detected BPM within 1 %, three drum classes recovered, groove string matches
the generator's pattern. Tests never hit the network; `fetch_reference` is
tested against a local file URL.

### B. Import ladder — the one hardware unknown — `pblk0v4j`

Unchanged from `fast-path-to-composition.md` step 1: prove the device accepts
a project file we wrote. Prep the packer offline, then one owner session,
fresh backup, non-active project, rungs 1–4. Can run in parallel with A; A
does not depend on it, `generate_ppak` does.

### C. `generate_ppak` — `bqgv6h7y`

Patch-an-existing-project generator as already specified; input is now
produced by `transcribe_groove` + `install_kit` results rather than typed by
hand. BPM from `analyze_reference`.

### D. First end-to-end run — owner session

Fresh backup. Owner picks a clip. Agent: fetch → analyze → extract → owner
reviews the 12 slices (play them locally; `afplay` on macOS) → `install_kit`
into an empty group of a non-active project → `generate_ppak` → owner imports
→ plays. Write it up in `docs/research/` like every other hardware step.

### E. Second mode — library search — `mpm79kpo`, `8c98vpbn`

Once D works: `search_sounds(descriptor)` against Freesound's API (needs a
free key), filter `license:"Creative Commons 0"` by default, rank by the same
centroid/duration features `extract_kit` computes, download, convert, and
write the attribution sidecar `8c98vpbn` already specifies. Same output shape
as `extract_kit` so `install_kit` and `transcribe_groove` need no changes.

### F. Stretch — project write over SysEx

Capture Sample Tool's project import as `upload-capture.md` did for samples.
Removes the last manual step. Only after D.

## Off the critical path

`8zru1ujl`/`fseydiaq` (install_kit + undo hardware gate for v0.1 release).
Session D exercises `install_kit` on hardware anyway; record it as evidence
for `8zru1ujl` while you are there, but do not let it delay D.

## Guardrails that do not relax

One MIDI owner (`pgrep -fl ep133_mcp` first; Sample Tool in a browser counts).
Fresh backup before each hardware step; non-active project for imports. Never
commit `*.pak`/`*.ppak`, downloaded audio, or extracted slices; fixtures are
synthesized. Never print the serial. Every device-touching step written up
before the next begins. Conventional Commits; no TODOs or stubs.
