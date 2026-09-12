# ep133-mcp v0.1 — package layout and tool contracts

Design for dex `4iq2m1k7`. Every constraint here traces to something observed
on hardware in `docs/research/`; where a decision rests on an unverified protocol
path, it says so and the tool refuses rather than guesses.

## What Phase 0 taught the design

| Finding | Design consequence |
|---|---|
| A live `sym` read resolves a stale slot id to 0, identical to an empty pad | "Empty" must be decided from the **stored** pad record, read via the project TAR — never from `sym` alone |
| Filling a free slot silently armed pads in another project | Slot selection scans **all nine projects' stored records**, not just the target pad |
| Restore did not revert an active-project pad; the live check passed anyway | The server **does not claim to restore**. It verifies backups by diff and undoes only its own journalled writes |
| A status-0 metadata write is not evidence the device acted (`active` latches at boot) | Every write is followed by a read-back of the thing that was supposed to change; for audio, a CRC compare |
| The device rewrites the active project on its own (`scenes` changed unprompted) | No cached device state across tool calls. Re-read before every decision |
| Two processes on one port corrupt request matching | One `DeviceSession` per server process; refuse to start if the port cannot be owned |
| The upload terminator commits; `FILE_INFO` is a no-op | Upload sequence ends at the terminator. No `FILE_INFO` |
| Device `crc` == CRC-32 of the PCM | Install is verified by comparing CRC-32, not by trusting an ACK |
| `FILE_DELETE` is documented but unverified; a bad `03 00` open can wedge the device | Neither is called until verified on hardware. Undo reports what it could not remove |

## Package layout

```
src/ep133_mcp/
  protocol/         pure functions, no I/O — framing, payload builders,
                    pad-record decode, project-TAR parse, CRC. Testable
                    against fixtures with no device. Reuses ep133-ppak
                    (MIT) with attribution where it is verified.
  device/           DeviceSession: owns the MIDI port, request ids,
                    read-mode/write-mode init, page-walking metadata reads,
                    project TAR reads, upload, metadata set. Every method
                    is a fresh device read; nothing is memoised.
  safety/           preflight (WAV, size, pad, slot, stale refs), backup
                    validation, confirmation tokens, the write journal.
  server.py         MCP tools over stdio. Thin: validates arguments,
                    calls safety then device, formats results.
tools/              research scripts. Kept for evidence; not shipped.
fixtures/           synthesised test tone; captured frames live in docs/.
```

Boundaries: `protocol` imports nothing from `device`; `device` imports nothing
from `server`; `safety` may use both. The MCP layer never touches `mido`.

Logging goes to **stderr only**. Stdio transport owns stdout, so a single stray
print would corrupt the MCP stream. Enforced by a test that runs the server
with a captured stdout and asserts it contains only JSON-RPC.

## Device ownership

One `DeviceSession`, opened on first tool call, held for the process lifetime.
Startup verifies exactly one input and one output endpoint named `EP-133`
exist and can be opened; otherwise every tool returns `DeviceUnavailable` with
the endpoint list. There is no retry-and-steal: if Sample Tool or another
process has the port, the user is told, not overridden.

Every write sends `GREET` and a write-mode `FILE_INIT` first, every read a
read-mode one — mirroring the captured working sequences rather than assuming
mode persists.

## The backup contract

The server **cannot produce a full backup** in v0.1. It has no verified
full-library download and no verified restore path. Pretending otherwise is the
one thing this design refuses to do.

Instead, a Sample Tool `.pak` is a **required input** to every write, and the
server's job is to prove it is current:

`verify_backup(path)`:
1. Archive integrity, `meta.json` SKU and OS match the connected device.
2. Library slot set in the archive equals the slot set on the device.
3. All 432 stored pad records, read from the device's project TARs, equal the
   archive's — byte-for-byte on slot and length. Not `sym`; stored fields.
4. Returns `current` / `stale` with the exact differences, and a `backup_id`
   (SHA-256) that write tools must echo.

A write tool refuses if the backup is stale or older than a configurable age.
This makes the device-owner responsible for one thing — running Sample Tool
backup — and the server responsible for proving it was worth doing.

## Restore

There is **no `restore` tool**, because no tool would be honest. What exists:

- `undo_last_install()` — reverts the pad assignments in the most recent
  journal entry by writing the prior `sym` back, then reads back. It reports
  the uploaded slot as **left in the library**, because `FILE_DELETE` is
  unverified. When delete is verified on hardware it becomes part of undo; not
  before.
- `restore_procedure()` — returns the Sample Tool steps and, crucially, the
  verification step: *take a fresh backup after restoring and diff it*, because
  live reads cannot verify a restore. This is guidance, and it is labelled
  guidance.

## Confirmation semantics

Destructive means: overwriting an occupied pad, or filling a slot that any
project's stored record references. Both require a token.

1. The tool is called without `confirm`. It performs full preflight and, if the
   operation is destructive, returns `needs_confirmation` with an **impact
   statement** — every project, group, pad and slot that would change, and what
   is there now — plus a token bound to that exact impact.
2. The caller repeats the call with `confirm=<token>`. If the device state no
   longer matches the impact statement (the device changes on its own), the
   token is rejected and a fresh one issued.

Tokens are never derivable by the caller and never bundled: `install_kit` with
one destructive pad requires a token naming that pad; it does not accept a
blanket "yes". This mirrors Sample Tool's typed `OVERWRITE` gate, and the server
never supplies the token on the user's behalf.

## Tools

### `device_info()` — read-only
SKU, OS, mode, capacity and free bytes (re-read), active project number, port
names. Serial is **omitted by default**; `include_serial=true` returns it.

### `list_pads(project=None)` — read-only
For the requested project (default: active), all 48 pads with both the resolved
`sym` and the **stored** slot and length from the project TAR. Pads whose stored
slot does not exist in the library are flagged `stale_reference`. This is the
view that makes the silent-arming hazard visible.

`fields=true` attaches each pad's whole JSON record as `metadata` (48 extra
metadata reads); the default stays cheap.

### `read_pad(project, group, pad)` — read-only
One pad's complete JSON record from its metadata node, plus the referenced
slot's complete JSON when `sym` resolves. Every key the device returns is
passed through unfiltered — this is the read-back every write in the
full-access epic is proven against, so it must never hide a field the device
added or dropped. Destination is validated before any I/O; `sym` 0 means no
slot read is attempted. Nothing is cached.

### `read_project(project, source=None)` — read-only
The whole project decoded: bpm, 48 stored pad records, every pattern file as
note events (`pad, tick, duration, note, byte4, byte7`) with type-1 fader
automation kept in a separate `automation` list and any other event type
reported raw, the scenes file, and the raw `settings` / `fx_settings` floats.
`source` decodes a `.pak`/`.ppak` on disk instead of the device. No backup
gate. The decoders in `protocol/decode.py` are the same ones
`tools/pattern_decode.py` wraps, so the CLI and the tool cannot disagree.
Field status is whatever `docs/research/pattern-encoding.md` says; the tool
does not upgrade "probable" to "verified".

### `verify_backup(path)` — read-only
As above. Returns `backup_id`.

### `install_sample(path, project, group, pad, backup_id, confirm=None)`
Preflight, in order, all before any write:
1. Backup: `backup_id` matches a verification run this session that returned
   `current`. Otherwise `BackupStale`.
2. WAV: readable, 16-bit PCM, mono. v0.1 accepts only 46875 Hz and returns
   `UnsupportedFormat` with the reason otherwise — resampling is a quality
   decision and is deferred rather than done silently.
3. Size: PCM bytes < free bytes re-read now. `TooLarge` says how many seconds
   at this format *would* fit.
4. Pad: project TAR read; target record must have length 0 **and** a stored
   slot that does not exist. Otherwise destructive → token.
5. Slot: lowest id ≥ 1 that is absent from the library **and** absent from
   every stored pad field across all nine project TARs. If none, `NoSafeSlot`.
Write:
6. Upload (GREET → write `FILE_INIT` → `PUT_META` → chunks → terminator).
7. Read the slot back; `crc` must equal local CRC-32 and `sample.end` the frame
   count. Mismatch → `VerificationFailed`, journal records the slot as
   orphaned, nothing is assigned.
8. Journal `{prior_sym, node, slot}`; metadata SET `{"sym": slot}`; read back.
Returns node, slot, CRC, and the journal id.

### `install_kit(mapping, project, group, backup_id, confirm=None)`
Up to 12 `{pad, path}`. **Not a transaction, and the description says so** —
the device has no transactions. Behaviour: preflight *all* entries first, so a
bad file or a destructive pad fails before anything is written; then apply
sequentially, stopping at the first failure; return exactly which entries
landed, which did not, and one journal id covering the landed set so
`undo_last_install` reverts the partial result.

### `undo_last_install()` / `restore_procedure()`
As under Restore.

### `set_pad(project, group, pad, params, backup_id, confirm=None)`
Per-pad sound parameters through the same `FILE_METADATA_SET` that assigns a
pad. `params` is any subset of the 14 fields upstream lists as honoured
(`sample.start/end`, `sound.playmode`, `envelope.attack/release`,
`sound.pitch/amplitude/pan/mutegroup`, `time.mode`, `sound.bpm/bars/rootnote`,
`midi.channel`); anything else, including `sym`, is refused before I/O.
Preflight: destination valid → values in range and enums strings → backup
current → pad read (`read_pad`) → `sym` non-zero → trim within the slot's
`sample.end`. Then `needs_confirmation` carrying `before` (the current value
of every field to be written) and `after`, plus `auto_paired` when
`envelope.release` was added to complete a playmode. The token is bound to
that impact, so a device that changes the pad between calls invalidates it.

Write: journal `{file_id, requested, before, before_record}` → one metadata
SET of exactly the requested fields → read the pad back → report each field
as `applied`, `changed` (`{requested, stored}`) or `dropped`, and every other
key that moved as `side_effects`. Status is `written` only when nothing was
changed or dropped; otherwise `written_with_differences`. The device's
coupling rules are documented upstream, not observed here, so the tool
reports rather than predicts them. `power_cycle_verified` is always false.

### `set_slot(slot, params, backup_id, confirm=None)`
Same flow on a library slot's record: `name`, `sound.loopstart/loopend`
(trim-only on this device) and the shared `sound.*` / `envelope.*` /
`time.mode` fields. The impact names the slot's `name` and `crc` so the
owner recognises it; a slot's parameters reach every pad that plays it.

### `chop_sample(path, project, group, pads, slices, backup_id, confirm=None, playmode="oneshot")`
One upload, N pads. Slices are planned before any I/O — `{mode: equal,
count}`, `{mode: onsets}` (extract_kit's detector, backtracked starts, the
first N of them; needs the audio extra) or explicit `[{start_s, end_s}]` —
and refused when empty, overlapping or out of order; ends clamp to the file.
Preflight is `install_sample`'s for the slot (backup current, format, size,
lowest slot absent from the library and from every stored record) and
`install_kit`'s for the pads (each pad's prior stored record; occupied means
destructive means token). The impact carries the slot, CRC, every pad's
frame range and prior record, and the detected onset times.

Write, sequentially and not as a transaction: snapshot check → upload → CRC
and frame count read from the slot → per pad: stored record unchanged since
preflight → `sym` assigned and the stored record read (`slot, frames`) →
trim and playmode written in one metadata SET → JSON read back and compared
(`applied` / `changed` / `dropped` / `side_effects`, judged against the
record as it stood after the assignment). Stops at the first failure with
every entry's status in the result. One journal (`chop_sample`).

### `undo_last_chop()`
Reverse order over the chop's pads: a pad whose stored slot is still the
chop's gets `{sym: prior_slot}` plus whatever trim/playmode the record held
before, then its stored record must equal the prior one. A pad that changed
since, or whose prior slot no longer exists, is left alone and reported.
The uploaded slot stays in the library.

### `undo_last_pad_change()`
Writes the `before` values of the latest `set_pad`/`set_slot` journal back
and reads the record. A key the record did not hold before the change cannot
be unset over this interface and is listed as `not_restorable`. Separate from
`undo_last_install`, which only reverts `sym`.

### `check_ppak(path, project)` — read-only
The preflight of the not-yet-possible `import_ppak`, shipped on its own
because Sample Tool asks for a project number instead of reading it from
the file and the P06 mis-import happened that way. Offline: the file is a
`pak_type: project` export holding exactly `/projects/PNN.tar` for the
requested N, the TAR is complete and in the device's own flavour, every
`/sounds` entry is a readable WAV. Against the device: SKU matches, N is
not the active project, every slot the pads reference exists on the device
or is carried in the file (else it imports as a stale reference), no carried
sound targets an occupied slot, and a summary of what project N holds now.
`status` is `ok` or `problems`, each spelled out. The write path stays
absent until Sample Tool's import is captured (Dex `0uxzjixo`); `import_ppak`
will run this, then require a current backup and a token bound to
`would_replace`, journal the previous TAR, write, read the TAR back
byte-for-byte, and diff a fresh backup.

### Deliberately absent
Firmware anything. Format. `FILE_DELETE` on arbitrary slots. Project switching
(`active` only takes effect at boot; exposed as info, not action). Speculative
`03 00` opens on unknown file ids.

## Errors

One typed error family, each carrying `what_failed`, `observed`, `expected` and
`next_step`. `TooLarge` includes the largest duration that fits. `PadOccupied`
and `SlotReferenced` include the impact statement. `DeviceUnavailable` includes
the endpoint list. No error is a bare string.

## Test strategy

- `protocol/`: unit tests against the captured frames in
  `docs/research/fixtures/upload-capture-slot16.json` — the encoder must
  reproduce every sent frame byte-for-byte, and the decoder must parse every
  response. Pad-record decode is tested against both backups' TARs (private,
  not committed; tests skip if absent) and the committed pad snapshot.
- `safety/`: preflight tested with a fake device whose TARs contain stale
  references, to prove slot selection and the impact statement catch them.
- `server.py`: launches over stdio in a subprocess; test asserts stdout is pure
  JSON-RPC and `device_info` returns `DeviceUnavailable` cleanly with no
  hardware present.
- Nothing in CI touches hardware. Hardware runs are manual, logged in
  `docs/research/`, and every one starts with `verify_backup`.
