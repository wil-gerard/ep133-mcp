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

Run: `uv sync --extra dev && uv run pytest -q` → 219 pass, with or without the device.
Server: `uv run ep133-mcp` (stdio; logs on stderr).

## State of the plan

Phase 0 is **complete**: upload → assign → full power cycle → pad still assigned,
CRC-32 byte-identical, owner heard it play.

Phase 1 (`zmwcu8kp`):

| Task | State |
|---|---|
| `4iq2m1k7` package + contracts | done |
| `eya5iivi` device_info + list_pads | **device_info done; list_pads is the next thing** (in progress) |
| `zjnizqio` backups + restore | blocked on `eya5iivi` — contracts already decided, see below |
| `fazlbs50` preflight | blocked on `eya5iivi` |
| `rbv6zfrx` install_sample | blocked on both |
| `8zru1ujl` install_kit, `fseydiaq` release | later |

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

## Next step, concretely: `list_pads`

It must return, per pad, both the resolved `sym` **and** the stored slot/length
from the project TAR, flagging `stale_reference` where the stored slot is
absent from the library.

That needs the one unverified protocol path: **reading a project TAR live**.
Upstream documents it in `.upstream/ep133-ppak/ep133/project_reader.py`:
read-mode `FILE_INIT`, then `03 00 <fid u16 BE> <u32 0>`, then `03 01 <page u16 BE>`
until a page comes back short; each page has a 3-byte `00 00 NN` header.

**Upstream warns a speculative `03 00` on the wrong file id can wedge the device
into an error state needing a power cycle.** So:
- Only open known project ids: `3000 + 1000*(N-1)` for N in 1..9.
- Have the owner nearby the first time.
- Verify the bytes against `P0N.tar` inside the 2026-09-11 backup before
  trusting the reader. `tools/diff_backups.py` shows what to expect.
- Done when `list_pads` on hardware agrees with the backup for all 432 pads.

After that, `zjnizqio` and `fazlbs50` are unblocked and their contracts are
already written.

## Device state right now

Not the backed-up state. Slots 15 and 16 hold the test tone (15 is redundant).
Project 5 pad "1" (node 7207) → slot 16, our proof. Project 5 pad "4" (7204) →
slot 14, restore drift. Project 6 pads "1" and "4" armed by the stale-reference
effect. The device was last on **project 4**. `FILE_DELETE` (`06 02 <fid>`) is
documented but unverified, so nothing has been cleaned up. The 2026-09-11 backup
captures this state exactly.

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
