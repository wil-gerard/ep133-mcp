# ep133-mcp

An MCP server that installs samples onto a Teenage Engineering EP-133 K.O. II.

Status: Phase 0 (hardware proof) complete; Phase 1 (install-only server) in
progress. Design and tool contracts: `docs/design/tool-contracts.md`. Hardware
evidence: `docs/research/`.

Run the server over stdio:

    uv run ep133-mcp

Logs go to stderr. Stdout is the MCP stream and is never written to directly.

Protocol framing and 7-bit packing are ported from
[ZacharySBrown/ep133-ppak](https://github.com/ZacharySBrown/ep133-ppak) (MIT),
itself a port of phones24/ep133-export-to-daw. See `NOTICE`.
