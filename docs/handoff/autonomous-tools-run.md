# Autonomous run: build the full-access tools

The owner has pre-authorized this session to work through Dex epic
`5y8ub9um` (**Full device access through MCP**) without checking in.
Build as many tools as possible, commit each one, keep Dex current, and
leave a handoff at the end. Do not stop to ask questions; make the call,
record it in the task, move on.

## Read first, in this order

1. `docs/handoff/session-2026-09-11-handoff.md` — device state, rules, what
   wedges the MIDI interface.
2. `dex show 5y8ub9um --full`, then `dex list 5y8ub9um` and `dex list --ready`.
3. `docs/research/pattern-encoding.md` and `.upstream/ep133-ppak/PROTOCOL.md`
   §4–§6 — the field tables the new tools are built on.
4. `docs/design/tool-contracts.md` — every new tool gets a contract entry in
   the same shape.

## What is pre-authorized

- **Reads** of any kind against the device, any time.
- **Writes to non-active projects** after a fresh `create_backup` +
  `verify_backup` → `current`. The `needs_confirmation` token flow is
  satisfied by this document: show the impact in your notes, then proceed.
- The three steps the previous handoff already approved (backup → slot-704
  delete proof → 47-slot cleanup), *only if* the nine `.ppak` imports have
  been done — check `list_pads` on P01 for the owner's groove and the kit on
  group A before assuming. The bulk delete still gets a yes from the owner;
  everything else in that list does not.

## What is not

- Anything on the **active project**. Read `project_base(active)` first and
  stay off it.
- Deleting samples beyond the approved list. Committing `*.pak`, `*.ppak`,
  clips, stems, slices, or the serial.
- `git revert`/`reset --hard`/history rewriting.
- Tasks whose description says **owner present** or needs Sample Tool
  running: `0uxzjixo` (capture Sample Tool's import), the SysEx `PLAY`
  probe in `w97sjrl7`, `nv2ns3cs`. Build whatever code those need, mark the
  hardware step as blocked on the owner, and list them in the handoff.

## The loop

```
dex list --ready            # pick highest priority; read-only tasks first
dex start <id>
build + tests (uv run pytest > /tmp/pytest.log; read the log — piping to
  head hangs, see memory)
hardware check where allowed; read back; note what the device did
git commit  (Conventional Commits, one commit per piece of work)
dex complete <id> --result "<what, decisions, verification>" --commit <sha>
dex edit <id> --description "..."   # when a finding changes the plan
```

If a task cannot be finished, do not complete it: `dex edit` the description
with progress, blocker, and next step, leave it `in_progress`, pick the next.
Create new subtasks when work splits; delete nothing.

## Order that avoids stalling yourself

1. `uer6sazm` read_pad, `geb20kgq` read_project — read-only, needed by all.
2. `wr90izot` set_pad — code + offline tests + a non-active-project hardware
   check. Power-cycle needs a human: mark "written and read back; power-cycle
   pending" and carry on.
3. `wwevlmnw` chop_sample — same.
4. `rfih4h0f` sequencer extras — the code half (accept `note`, `automation`,
   emit them, tests). Hardware verification rides the next import.
5. `tvyy03x6` project write — only `cn417veu`'s scaffolding (contract,
   preflight, tests against a fixture) can be done alone; the capture is the
   owner's.
6. `qw5ui16p` / `vsa0gzu9` — the mapping needs the owner at the knobs; do
   the tooling (a `diff_project_floats` helper that names offsets) so the
   session with them is fast.
7. **Last, after everything is committed:** `m6fyda1s` settings-node hunt by
   `STAT` only (documented safe), in small id ranges, logging each id before
   sending. A wedge here costs a power-cycle and nothing else. Never
   `FILE_READ_OPEN` an unknown id.

## Proof standard

A write is proven when: written, read back identical, a fresh backup diffs
as predicted. "Persisted across power-cycle" is a separate claim; without a
human it stays pending and is said so in the result. An ACK is not a proof.

## End of session

- `docs/handoff/session-<date>-handoff.md`: tools shipped (commit table),
  what the device did that the docs did not predict, what is pending a human
  (power-cycles, the capture, the bulk-delete yes), and the exact next
  command.
- `dex show 5y8ub9um` and the handoff must agree.
- All tests green, working tree clean, nothing untracked but `.claude/`.
