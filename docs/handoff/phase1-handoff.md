# Handoff — Phase 1, install-only MCP server

Written 2026-09-10 for an agent starting cold. Everything below is in the repo;
this page tells you where, what is proven, and what will hurt you.

## Where things are

| | |
|---|---|
| Branch | `docs/phase0-backup-verification` on `origin` — all work is here, 20+ commits, nothing on `main` yet |
| Plan | `.dex/tasks.jsonl`, tracked in git. `dex list` shows it. **Update it as you go**; it was lost once to a truncated file |
| Design | [`docs/design/tool-contracts.md`](../design/tool-contracts.md) — read this first, every rule traces to hardware evidence |
| Evidence | [`docs/research/`](../research/) — `phase0-proof.md`, `upload-capture.md`, `backup-verification.md`, `protocol-audit.md` |
| Code | `src/ep133_mcp/` — `protocol/` (pure), `device/` (port owner), `server.py` (stdio) |
| Research scripts | `tools/` — evidence, not shipped. Most open the MIDI port |
| Upstream | `.upstream/ep133-ppak` (gitignored, pinned `93f0f6b`) — reference implementation, MIT |
| Backups | `~/Documents/ep133-backups/` — two `.pak` files with `.sha256` sidecars. **Read-only. Never commit.** |
| Test asset | `fixtures/phase0-test-tone.wav` — synthesised, no third-party rights |

Run: `uv sync --extra dev && uv run pytest -q` → 281 pass, with or without the device.
Server: `uv run ep133-mcp` (stdio; logs on stderr).

## State of the plan

Phase 0 is **complete**: upload → assign → full power cycle → pad still assigned,
CRC-32 byte-identical, owner heard it play.

Phase 1 (`zmwcu8kp`):

| Task | State |
|---|---|
| `4iq2m1k7` package + contracts | done |
| `eya5iivi` device_info + list_pads | **done — device_info and list_pads hardware verified** |
| `zjnizqio` backups + restore guidance | done — backup gate and guidance implemented |
| `fazlbs50` preflight | done — WAV, aggregate memory, destination and safe slots |
| `rbv6zfrx` install_sample | **done — shipped server installed, survived a power cycle, owner heard playback** (`phase1-smoke-test.md`) |
| `8zru1ujl` install_kit, `fseydiaq` release | implementation, client tests and local builds ready; kit/undo hardware acceptance is a release gate |

A **separate agent** is working `pblk0v4j` (pattern encoding, Phase 2) offline
in `.claude/worktrees/pattern-encoding`, branch `research/pattern-encoding`.
It does not touch the device or `src/`. Leave it alone.

## Hardware facts you can rely on (all verified on this device)

- **Pad numbering is one index everywhere**: metadata node, SysEx `pad_num`,
  project TAR `pNN`. Visual, top-to-bottom: 1="7", 4="4", 7="1", 10=".".
  Node = `2000 + 1000*project + 200 + 100*group_index + pad_num`. Upstream's
  `PROTOCOL.md` §3.1 translation table is **wrong** — do not apply it.
- **Upload sequence**: GREET → `FILE_INIT` flags=1 → `FILE_PUT_META` → 433-byte
  chunks → empty terminator. The terminator commits. `FILE_INFO` is a no-op.
- **Slot metadata `crc` == CRC-32 of the raw PCM.** Verify installs by comparing.
- **Slot occupancy is the response status**: 0 = exists, 1 = "invalid file id".
  Never infer emptiness from a parse failure; records exceed one page.
- **Pad record**: slot u16 LE at 1–2, length u32 LE at 8–11. Assigned ⇔ length ≠ 0.
- **`active` on the project root** reports the current project and selects the
  project at *next boot*. Writing it does nothing live.
- Device stores samples at their own rate (44.1 kHz samples exist); native
  46875 is the recording rate, not an import constraint.

## The three things that will hurt you

**1. A pad that reads `sym`=0 may not be empty.** A stored slot id whose slot
does not exist resolves to 0 — identical to empty. This device has 36 such
stale ids across 63 pads. Uploading to slots 15 and 16 silently armed two pads
in a project nobody touched. `list_pads` exists to make this visible, and
`install_sample` must scan all nine projects' stored records before picking a
slot. The stored field is only visible in the project TAR.

**2. Restore is not a proven safety net.** A backup diff proved the Sample Tool
restore did *not* revert one active-project pad, and our live-read verification
passed anyway (see point 1 — it resolved to 0 mid-restore). So: **live reads
cannot verify a restore; only a post-restore backup diff can.**
`tools/diff_backups.py` does that. The design therefore has **no `restore`
tool** — `verify_backup` proves a user's `.pak` is current by diffing stored
records, and `undo_last_install` reverts only journalled writes.

**3. One MIDI owner.** Two processes on the port corrupt request/response
matching. The server holds one `DeviceSession`. Do not run `tools/*.py` while
the server is up. Sample Tool in a browser also counts.

## Project reader and list_pads: verified

See [`project-tar-read.md`](../research/project-tar-read.md). With the owner
present and Sample Tool closed, all nine project TARs matched the local
2026-09-11 backup byte-for-byte, and all 432 stored pad records and stale flags
agreed. `list_pads(project=None)` now exposes resolved `sym`, stored slot/length,
node, label, and `stale_reference`; default is the active project.

After response status is removed, project pages have a **two-byte big-endian
page index**, followed by up to 324 data bytes. Timeouts are failures, never EOF.
Only known project ids 3000..11000 are opened. Owner presence and one MIDI owner
remain mandatory for hardware runs. Routine tests now stay offline.

There are 170 nonzero stored references absent from the backup library, 63 with
nonzero length. `stale_reference` includes zero-length records; slot 0 is not
flagged. No delete or cleanup was attempted.

## Next step: owner-attended hardware acceptance

All eight MCP tools are implemented. `safety/` owns backup verification,
preflight, confirmation, durable journalling, install and guarded undo.
The full suite has 281 passing offline tests, including real MCP client
round trips with a fake device. Local wheel/source builds succeed.

The minimum smoke test in [phase1-validation.md](../research/phase1-validation.md)
passed on 2026-09-10: one `install_sample` through the shipped server, power
cycle, journal match, audible playback. `rbv6zfrx` is closed. `8zru1ujl` and
`fseydiaq` stay open until kit and undo are exercised on hardware.

Backups remain private and read-only. A new Sample Tool backup is required
before the hardware run. The default maximum age is 24 hours. Undo leaves
uploaded library slots in place and reports stale prior fields it cannot
faithfully restore; it does not claim full restoration.

## Device state right now

Not the backed-up state. Slots 15, 16 and 17 hold the test tone (15 is
redundant). Project 5 group A pad "1" (node 7207) → slot 16, Phase 0 proof.
Project 5 group D pad "1" (node 7507) → slot 17, Phase 1 smoke test, journal
`c6035922…`. Project 5 pad "4" (7204) → slot 14, restore drift. Project 6 pads "1" and "4" armed by the stale-reference
effect. The device was last on **project 4**. `FILE_DELETE` (`06 02 <fid>`) is
documented but unverified, so nothing has been cleaned up. The 2026-09-11 backup
predates slot 17 and is therefore stale; take a new one before any install.

## Open threads, not blocking

- Cross-check captured frames against `ep133-krate` fixtures (never done).
- Per-sample overhead was 40,920 B for 37,500 B of PCM, inconsistent with the
  backup-derived average. Preflight re-reads free space rather than predicting.
- Why node 7207 reverted on restore while 7204 did not. Unexplained.
- Three upstream reports drafted in `docs/upstream/`, none posted. Owner's call.

## House rules

Conventional Commits. No TODOs or stubs. Every claim in `docs/research/` marked
verified/probable/guessed. Destructive git needs the owner's explicit yes.
Anything that touches the device gets written up before the next step starts —
that discipline caught three wrong conclusions this week.
