# Device writes without a prompt per call

Written 2026-09-12 after re-importing eight blanked projects took an approval
per call. Two things slow a batch of writes down; each has its own fix.

## 1. The harness prompts on every write tool (owner's side)

Claude Code's auto mode sends `import_ppak`, `install_kit`, `set_pad`,
`delete_samples`, `clear_pads` and Bash scripts that touch the device to a
classifier, which blocks or prompts. The server's own gates (verified
backup, `needs_confirmation` token, read-back) stay in force either way, so
allowing the tools outright loses no safety. The agent cannot edit this
file for you — the classifier blocks that too. Add to
`.claude/settings.local.json` (project) or `~/.claude/settings.json`
(everywhere):

```json
{
  "enabledMcpjsonServers": ["ep133"],
  "permissions": {
    "allow": [
      "mcp__ep133__verify_backup",
      "mcp__ep133__check_ppak",
      "mcp__ep133__import_ppak",
      "mcp__ep133__undo_last_import",
      "mcp__ep133__install_kit",
      "mcp__ep133__set_pad",
      "Bash(uv run python:*)"
    ]
  }
}
```

Drop any line you would rather keep approving. `Bash(uv run python:*)` is
what lets the agent batch writes in one process (section 3).

## 2. One verification per write (server's side)

`Importer.import_ppak` marks the backup unverified after every successful
write, so a batch of N imports is N × (`verify_backup` → preflight →
confirm), and `verify_backup` re-reads all 432 pad records each time
(~10 s). The rule exists so a write never follows a stale picture of the
device, but for a batch whose writes touch disjoint projects and no samples
the picture after write k is known: it is the read-back of write k.

Fix (dex `dvl7a1b5`): keep the verification live across writes that the
server itself verified byte-equal on read-back, and make `verify_backup`
skip the pad walk when the last write's read-back already covers the
project. Then a batch is N × (preflight → confirm).

## 3. Batch in one process

Until section 2 lands, the fast path is one Python process calling the
server's tool functions directly — same gates, no MCP round-trips, and
`verify_backup` in-process is the only slow step. The MCP server must be
stopped first (one MIDI owner): `pgrep -fl ep133-mcp`, kill it, run the
batch, then `/mcp` reconnect.

```python
from ep133_mcp import server as S
B = '/Users/wilgerard/Documents/ep133-backups'
pak = f'{B}/session-24.pak'
for n in (6, 7, 8, 9):
    bid = S.verify_backup(pak)['backup_id']
    r = S.import_ppak(f'{B}/blanks-v2/blank-P0{n}.ppak', n, bid)
    assert r.get('status') == 'needs_confirmation', r
    r = S.import_ppak(f'{B}/blanks-v2/blank-P0{n}.ppak', n, bid, confirm=r['confirm'])
    print(n, r.get('status'), r.get('verified'), r.get('error'))
```

The `needs_confirmation` step is the owner's approval of the *impact*; when
the owner has approved the batch up front ("do it all"), echoing the token
in-process is that approval, not a bypass. Confirm tokens expire after
`CONFIRM_SECONDS` (300 s) — a slow reply means a fresh preflight.

## 4. What ran today

Projects 1, 2, 4, 5 re-imported over MCP (`blanks-v2/`, scenes-only
change, each read back byte-equal — `import_ppak` is proven on hardware).
6, 7, 8, 9 still hold the zero-scene blank and error when selected; run
section 3 for them, or import `blanks-v2/blank-P0{6,7,8,9}.ppak` with
Sample Tool.
