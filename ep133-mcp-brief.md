# ep133-mcp: project brief

> Planning baseline. The [2026-09-07 source audit](docs/research/protocol-audit.md) supersedes the protocol-status claims below: live pad assignment and pattern builders exist upstream, but our hardware proof remains pending.

An MCP server that lets an agent install samples onto a Teenage Engineering EP-133 K.O. II and generate project files for it.

## Why this exists

Three things are true right now:

1. Sample discovery via MCP is solved. Several Freesound MCP servers exist, and Splice ships an official connector.
2. The EP-133 SysEx protocol and `.ppak` project format have been substantially reverse engineered by the community, across three separate repos.
3. Nobody has put an MCP interface over #2.

The one EP-133 MCP server that exists (`benjaminr/mcp-koii`) only sends Note On/Off over notes 36 to 83. It plays the device live. It cannot install a sample, cannot write a pattern, and nothing survives unplugging.

This project fills that gap. It is a composition play, not a replacement: discovery stays with Freesound's servers, we own install and compose.

## Non-goals

- No sample discovery of our own. Compose with existing Freesound MCP servers.
- No GUI. The agent is the interface.
- No firmware operations of any kind. See Safety below.
- No song mode / scene chaining in v1. See Protocol gaps.
- No Splice integration in v1. It fails the free-first goal.

## Prior art to read before writing code

Read these in this order. Do not re-derive what they already document.

| Repo | What it gives you |
|---|---|
| `ep133-krate` | Live SysEx wire layer, capture logs, confidence matrix. Start here. |
| `ZacharySBrown/ep133-ppak` | `PROTOCOL.md` is the best single document: SysEx protocol, `.ppak` archive format, binary pad-record layout. Marks verification status field by field. |
| `garrettjwilke/ep_133_sample_tool` | Electron reimplementation of EP Sample Tool. Author states file transfer is reverse engineered and full sound packs can be pushed from the command line already. Dev tools show raw SysEx. |
| `phones24/ep133-export-to-daw` | Device exposes a structured internal filesystem; backups are that filesystem packaged into an archive. Good for reading device state. |
| `DannyDesert/EP133-skill` | Claude Code skill that generates `.ppak` files. Genre templates and format notes worth borrowing with credit. |
| `benjaminr/mcp-koii` | The existing MCP server. Read it to see what not to duplicate; its live-trigger tools could be absorbed later. |

None of this is official. Teenage Engineering publishes nothing about the protocol. Most community RE is rooted in the EP Sample Tool web app's JS bundle.

## Known protocol gaps (do not assume these are solvable)

Straight from `ep133-ppak`'s own open-questions section:

- **Pattern and scene storage is unmapped.** The project TAR that can be read stops at pads and patterns; the sequencer data lives somewhere else. This is why song mode is out of scope.
- **The project-file write sub-command (`03 00`) opens but its data phase is unmapped.** Live per-pad writes without a `.ppak` round trip are not available yet.
- Several pad-record bytes remain unverified. The repo documents a diff method: take two Sample Tool backups (empty device vs. after a pad assignment) and `cmp -l` them. Use this to verify any byte you depend on.
- BPM appears to be stored as float32 directly rather than halved, but that wants more independent confirmation.
- `sound.bars` clamps to powers of two and ignores fractional bar counts; exact formula unverified.

When our code disagrees with a published table, file an issue upstream rather than silently forking. The RE community explicitly asks for this.

## Device constraints the server must enforce

These are the reason a generic MIDI or file MCP is not good enough here.

- 12 pressure and velocity sensitive pads; 4 groups; patterns hold 12 tracks for samples and MIDI
- 16 mono / 12 stereo voice polyphony
- Any pad can be assigned to a MIDI channel
- Native sampling is 46.875 kHz / 16-bit stereo
- Recording sample rate options: lo 26,250 / mid 32,000 / hi 46,875. Samples at other rates can be imported via Sample Tool.
- Mono sampling up to 40 seconds (OS 2.5); stereo shorter
- OS 2.5 added class-compliant USB audio in and out
- **Memory size varies by unit.** Current units are advertised as 128 MB; legacy units exist at 64 MB. Detect this, never hardcode it.
- The device SKU is required to generate a working `.ppak`. Read it from the device rather than asking the user to type it.

## Phased plan

### Phase 0: de-risk (target: one weekend)

Before writing any MCP code, prove the write path works.

- Clone `ep_133_sample_tool`, capture SysEx traffic during a real sample upload
- Cross-check the frames against `ep133-krate`'s captures
- Deliverable: a bare Python script that puts one WAV on pad A1 and survives a power cycle

If this does not work, stop. Everything downstream depends on it.

### Phase 1: install only (v0.1, shippable)

Tools:

- `device_info` -> SKU, OS version, memory total and free, unit generation
- `list_pads` -> current pad assignments per group
- `install_sample(path, pad, group)` -> validates format, length, and free memory before writing
- `install_kit(mapping)` -> up to 12 files across a group in one transaction

Validation belongs in the server, not the agent: reject a 60-second stereo file for a pad before touching the device, with an error message that says why and what would fit.

Ship at this point. Nothing else does this.

### Phase 2: compose

- `generate_ppak(spec)` -> writes a `.ppak` the user loads via EP Sample Tool
- Patterns only. Emit an explicit note in the tool description that song mode is unsupported and why.
- Pattern spec should accept the grid syntax `mcp-koii` already established (x = hit, o = soft hit, . = rest, one line per instrument, each position a 16th) since it is readable, agent-friendly, and already in the wild.

### Phase 3: curation

- `suggest_kit(vibe, candidates)` -> takes results handed in from a Freesound search and proposes a 12-pad layout, respecting memory budget and mono/stereo tradeoffs
- License filtering and attribution passthrough. Freesound licenses vary per sound (CC0, CC-BY, non-commercial). Filter on license before download, and carry attribution into a sidecar file next to every installed sample. Nobody else does this and it is the difference between a toy and something usable on a release.

### Phase 4: free pack index

Scraper plus curated index of the free EP-133 pack scene (op-forums, Gumroad, itch.io, YouTube description links). No APIs exist here, so this is maintenance-heavy and never finished. Consider shipping it as a public JSON repo others can PR into rather than something owned solo.

## Safety requirements

- **Never touch firmware.** A separate community tool (`ep-unity`) demonstrated cross-flashing between EP-133 and EP-40 by rewriting four SKU bytes. Teenage Engineering's response was a warning that units ship different NOR flash types and densities, with data loss, brick, and warranty risk. Our server does not go near this. No exceptions.
- Take a full backup before any write operation, and expose a `restore` path.
- Any destructive tool (overwrite pad, format) requires explicit confirmation and is never bundled into a batch operation.
- Fail loudly on unverified protocol fields rather than guessing.

## Suggested stack

Python, `mido` for MIDI, official MCP Python SDK, stdio transport. This matches every existing tool in the ecosystem and keeps the door open to upstreaming.

## First moves

1. Open issues on `ep133-ppak` and `ep_133_sample_tool` describing what is being built. Avoids duplicating solved work and recruits reviewers who know the protocol.
2. Phase 0.
3. Phase 1.

Two weeks to something demoable if the SysEx cooperates.
