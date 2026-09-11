# Handoff — fastest path to "an agent composes on the EP-133 through MCP"

Written 2026-09-10 after the Phase 1 smoke test passed. Read
[`phase1-handoff.md`](phase1-handoff.md) for house rules and hardware facts;
this page only orders the remaining work by what gets the owner to a playable,
agent-made beat soonest. `dex list` is the plan of record; update it as you go.

## Target loop

```
agent: suggest_kit (local folder) → install_kit → generate_ppak
owner: import .ppak in Sample Tool → play
```

Everything except the import step is MCP. Removing the import step is a
stretch goal (step 4), not on the critical path.

## Where we are

| Capability | State |
|---|---|
| Install WAVs to pads via MCP | **shipped and hardware-verified** (install_sample; install_kit offline only) |
| Pattern / scene / settings bytes | decoded byte-exact against 314 device files ([`../research/pattern-encoding.md`](../research/pattern-encoding.md)) |
| Device *accepting* a file we wrote | **never tested — the one real unknown** |
| Finding / converting samples | nothing built |

## Step 1 — import ladder (hardware, one owner session) — `pblk0v4j`

Follow [`pattern-encoding-next.md`](pattern-encoding-next.md) exactly. Rungs
1–3 there, plus rung 4 from `pattern-encoding.md` ("one new pattern file plus
one scene chunk"). Fresh full Sample Tool backup first; the 2026‑09‑11 backup
is stale. Target a non-active project. Stop at first failure and write it up.

Why first: if rung 1 or 2 fails, the container is the problem and
`generate_ppak` changes shape; if rung 3 fails, pattern generation is off the
table. Nothing else in this plan is worth building until this is known.

Prep before the session (offline): write the packer (`tools/pack_project.py`
or in `protocol/projects.py`) and prove it round-trips every member of all
nine projects from the backup byte-identically. Then the session is import,
observe, backup, diff — four times.

Owner time: one sitting. Do **not** fold the install_kit/undo release gate
into this session; it is not on the path to composition.

## Step 2 — `generate_ppak` as a patch generator — `bqgv6h7y`

Build the *patch an existing project* variant, not from-scratch. It sidesteps
every open question (event byte 7, velocity byte 4, `settings` 216–221,
`fx_settings` byte 4): take a device-written project TAR as the template and
change only proven fields.

Input, validated, everything else rejected:
- `template_project` 1..9 (read live via the existing TAR reader, or from a backup)
- `bpm` float (settings bytes 4–7, probable — confirm on device during step 1
  by reading P05's tempo; expected 135.0)
- `pads`: pad index → library slot, from `install_kit` results (27-byte records)
- `patterns`: `{group, index, bars, steps}` where `steps` is one `x`/`o`/`.`
  string per pad on a 16th grid (24 ticks); `x` → note 60, byte 4 = 100,
  duration 24; `o` maps to the same until velocity is proven — say so in the
  response rather than pretending
- `scenes`: list of per-group pattern indexes; one scene chunk each. No song
  section beyond the 1-position default.

Output: `.ppak` written to a user path, with a manifest of every byte range
changed, plus the instruction to import via Sample Tool into a stated project.
No device I/O. Acceptance: fixture round trip through `tools/pattern_decode.py
check` with `failures: 0`, then one owner-authorized import that plays.

## Step 3 — samples: local first — narrows `mpm79kpo`, defers `8c98vpbn`

Fastest useful `suggest_kit`: scan a local folder the owner names, return up
to 12 `{pad, path, reason, bytes_after_conversion}`. Skip license sidecars and
the external index; the owner's own samples carry no attribution burden.
Phase 3/4 as written stay in Dex for later.

Needed alongside it: a `prepare_sample` tool (or conversion inside
`suggest_kit`) that produces the mono 16-bit 46875 Hz WAV `install_kit`
demands, from whatever the folder holds. Preflight already rejects anything
else. Use the same memory estimate preflight uses; do not predict overhead.

## Step 4 — stretch: write the project over SysEx, drop Sample Tool

Sample Tool imports projects over WebMIDI, so a project write protocol exists.
Capture it the way `upload-capture.md` captured sample upload, targeting a
non-active project with a fresh backup. If it is `FILE_INIT`/`PUT_META`/chunks
against the project node, the loop becomes fully MCP. Only after step 2 works.

## Release gate, off the critical path

`8zru1ujl` and `fseydiaq` still need install_kit + undo on hardware. Do it in
whatever owner session comes next after the ladder, with its own fresh backup.
Build stays experimental until then; nothing above depends on it.

## Guardrails that do not relax

One MIDI owner (`pgrep -fl ep133_mcp` before device steps; a stuck client
held the port tonight). Fresh backup before each hardware rung. Non-active
project for imports. Never commit `*.pak`/`*.ppak`/audio or print the serial.
Every device-touching step written up before the next begins.
