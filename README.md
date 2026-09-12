# ep133-mcp

An MCP server for samples, portable projects, selective recovery and live MIDI audition on a Teenage Engineering EP-133 K.O. II.

Development build: `0.1.0.dev0`. The server exposes 40 tools. Automated tests
cover the protocol, backup gates, journals, recovery failures and MCP interface.
Sample upload, pad edits, chops and deletion have hardware evidence. Project
import/undo, selective restore, project switching and live pattern audition
still need hardware acceptance; power-cycle persistence is never inferred from
an acknowledgment. See [new feature evidence](docs/research/portable-recovery.md).

## Run

Requires Python 3.12+ and `uv`. Device evidence is from macOS, EP-133
TE032AS001, OS 2.5.1. Other device firmware and operating systems have not
been validated. The device ownership lock currently requires POSIX `fcntl`.

```sh
uv sync --extra dev
uv run ep133-mcp
```

Close EP Sample Tool and other MIDI programs before connecting through this
server. Exactly one EP-133 input/output pair must be visible. A process lock
prevents two instances of this server from sharing the port; it cannot detect
or exclude unrelated MIDI applications. Logs use stderr; stdout carries MCP.

For a client supporting `mcpServers`, replace the path with your checkout:

```json
{
  "mcpServers": {
    "ep133": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/ep133-mcp", "run", "ep133-mcp"]
    }
  }
}
```

## Tools

| Tool | Behavior |
|---|---|
| `server_status` | Version and MIDI endpoint visibility, without opening ports |
| `device_info` | Identity, memory and active project; serial omitted by default |
| `list_pads` | Stored slot/length, resolved slot and stale references for 48 pads |
| `verify_backup` | Compare a full Sample Tool `.pak` to current device state |
| `install_sample` | Preflight, upload, verify and assign one sample |
| `install_kit` | Preflight 1–12 entries, then apply sequentially; partial failure is possible |
| `undo_last_install` | Revert unchanged journalled assignments; uploaded slots remain |
| `restore_procedure` | Manual restore and verification guidance; no device writes |
| `fetch_reference` | Cache a ≤60 s section of a song (YouTube, Spotify track or local file) as a 44.1 kHz WAV; needs the `audio` extra |
| `analyze_reference` | Tempo, key, downbeat and per-stem onsets for a clip (demucs + Beat This!, with HPSS + librosa fallbacks); every estimate is probable |
| `extract_kit` | Up to 12 one-shots cut from the clip's stems as 46875 Hz mono 16-bit WAVs, with an `install_mapping` for `install_kit` |
| `transcribe_groove` | The clip's drums as `x`/`.` strings per kit pad on a 16th grid, in the shape `generate_ppak` takes |
| `generate_ppak` | Patch a device-written project (BPM, pads, patterns with per-event note and fader automation, scenes, raw fx/settings floats) into a `.ppak` for Sample Tool import; no device writes |
| `create_backup` | Write a full `.pak` by reading the device over SysEx; incremental against a base backup |
| `delete_samples` | Delete unreferenced library slots after confirmation; each slot re-read afterwards and proven by backup diff (`docs/research/delete-proof.md`) |
| `read_pad` | One pad's complete JSON record plus its slot's, unfiltered |
| `read_project` | A project decoded: bpm, pad records, patterns (notes and automation apart), scenes, settings floats; live or from a `.pak` |
| `set_pad` / `set_slot` | Per-pad and per-slot sound parameters (trim, playmode, envelope, pitch, level, pan...) with read-back reporting applied / changed / dropped |
| `chop_sample` | One upload, up to 12 pads trimmed to equal, onset-detected or explicit slices |
| `clear_pads` | Empty whole non-active projects (every stored pad to `sym 0`) so their slots become deletable; one confirmation, one journal |
| `undo_last_pad_change` / `undo_last_chop` / `undo_last_clear` | Write back the journalled previous values |
| `check_ppak` | Preflight a `.ppak` for import: right project, device flavour, referenced slots, what it replaces |
| `diff_project` | Name every changed settings / fx_settings field between two copies of a project |
| `list_files` / `stat_file` | Walk the device's file-id namespace; STAT one id |
| `play_note` | Audition a pad with a MIDI note on the device's port |
| `export_project` | Export one project and only its referenced samples from a backup; preserve WAV metadata |
| `list_samples` / `sample_usage` | Sample metadata and references across all nine projects, live or from a full backup |
| `import_ppak` / `undo_last_import` | Import a project with its samples, explicit slot remapping and read-back; undo restores the exact project preimage |
| `restore_samples` | Restore selected samples from a backup to their original slots; refuse differing occupied slots |
| `restore_project` | Restore one non-active project from a backup, optionally including its samples |
| `set_active_project` | Confirmed project switch with previous value journalled and active value read back |
| `play_pattern` / `stop_playback` / `playback_status` | Cancellable live audition of simultaneous steps patterns with explicit MIDI routing |

Hardware status (OS 2.5.1, 2026-09-12): `read_pad`, `list_pads(fields)`,
`set_pad`, `set_slot`, `undo_last_pad_change`, `chop_sample`, `undo_last_chop`,
`check_ppak`, `list_files` and `stat_file` have run on the device and read back
what they wrote (`docs/research/pad-params-proof.md`, `chop-proof.md`,
`pad-metadata.md`, `global-settings.md`); a `set_pad` and both chops survived a
power-cycle. `delete_samples` passed corrected-frame hardware checks and backup diffs
([delete proof](docs/research/delete-proof.md)); event byte 4 is velocity, read
back from a pressure recording ([velocity proof](docs/research/velocity-proof.md));
`play_note` has not been heard. `docs/handoff/` says what is still pending.

Pad numbers are visual indices from top left to bottom right: 1 is labelled
`7`, 7 is labelled `1`, 10 is `.`, and 12 is `ENTER`. Projects are 1–9; groups
are uppercase A–D. Use `list_pads` to inspect the destination.

## Reference audio (optional)

The reference-song pipeline (`fetch_reference`, `analyze_reference`,
`extract_kit`, `transcribe_groove`) lives behind an optional extra plus `ffmpeg`:

```sh
uv sync --extra dev --extra audio
brew install ffmpeg
```

`server_status` reports whether the extra, `ffmpeg` and `yt-dlp` are present;
without them the tools return `AudioToolsUnavailable` with the install command.
The first `analyze_reference` downloads the demucs weights (HuggingFace cache)
and the Beat This! checkpoint (torch hub cache) once; tests never do.
Spotify links resolve through Spotify's public oEmbed title (plus the track
page's artist when readable) to a YouTube search; the chosen video is reported
so you can pass a YouTube URL instead. Clips are cached under
`~/.local/state/ep133-mcp/references/` (override with `EP133_REFERENCES_DIR`)
with a `source.json` naming the URL and time range. They are for your own
device and personal use; nothing is published and nothing under that directory
belongs in this repository.

## A sample-to-kit walkthrough

1. Call `create_backup` to save a full `.pak` outside this repository.
   Alternatively, save it in Sample Tool, then close Sample Tool before connecting here.
2. Call `device_info` and `list_pads` for the intended project. Call
   `verify_backup` with the absolute `.pak` path. Continue only if it returns
   `status: "current"` or `"superset"`; retain the returned `backup_id`.
3. For one sample, call `install_sample` with `path`, `project`, `group`, `pad`
   and `backup_id`. For a kit, use `install_kit` with a mapping like:

   ```json
   {
     "mapping": [
       {"pad": 1, "path": "/absolute/path/kick.wav"},
       {"pad": 2, "path": "/absolute/path/snare.wav"}
     ],
     "project": 1,
     "group": "A",
     "backup_id": "SHA-256 returned by verify_backup"
   }
   ```

4. If the result is `needs_confirmation`, inspect every impact with the
   device owner. Only after their approval, repeat the same call with the
   returned `confirm` token. Tokens expire after five minutes and are bound
   to the exact device, backup, destination, prior fields and sample content.
5. Check each returned entry. `installed` means CRC, frame count and stored
   assignment were read back; it does not claim power-cycle verification.
   `upload_attempted` may have left an orphaned sample. `assignment_attempted`
   means a write may have landed despite a missing acknowledgment. `pending`
   means that entry was not written. A kit is never reported as atomic.
6. To undo, call `undo_last_install`. It refuses to overwrite a pad that has
   changed since installation and reports prior stale references it cannot
   faithfully restore. Uploaded slots remain; deletion is a separate confirmed operation.
   Take a new full backup and verify it before another install, including
   after a failed install or undo.

## Portable projects and recovery

`export_project(source, project, out)` reads a backup and writes a `.ppak`
containing the original project TAR and only its referenced WAVs. It refuses
missing samples and existing outputs. Use `include_samples=false` for a project
that will use sounds already on the destination.

Run `check_ppak(path, project, slot_map)` before `import_ppak`. A carried sample
can reuse its destination only when PCM and specified metadata match exactly.
For a collision, supply an explicit mapping such as `{"16": 40}`; every pad in
the imported project referring to slot 16 is remapped to 40. The preflight shows
uploads, reuses, memory requirements and existing references affected by filling
an empty slot. Uploads happen before the project write. Failure stops the sequence
and leaves a durable report; it is not an atomic transaction.

`restore_samples(source, slots, backup_id)` restores selected original slots.
`restore_project(source, project, backup_id)` restores one project and its
referenced samples. Both use the same confirmation and verification rules.
Restoration currently accepts mono 46875 Hz 16-bit PCM with supported TNGE
metadata and printable ASCII names up to 20 characters; unsupported formats are
refused without conversion. Confirmed project imports save a private exact
preimage for undo. Undo leaves uploaded audio in place and refuses subsequent
project edits, changed backups or corrupt preimages.

`set_active_project` can explicitly switch away from the intended import target.
It journals the previous project and requires a fresh backup gate and confirmation.
Hardware acceptance for this operation is still pending.

## Live pattern audition

`play_pattern` accepts the `group`, `index`, `bars`, `steps` pattern shape used
by `generate_ppak`. Supply MIDI `routing` entries with `group`, `pad`, `channel`
(1–16) and `note` (0–127). The tool checks the active project's actual sample
assignments; routing remains explicit because the device's global MIDI settings
are not readable. The device must not be recording, since incoming notes could
otherwise enter its pattern.

Playback returns immediately, runs at most 120 seconds and serializes device
operations. `stop_playback` bypasses that lock to cancel and release notes;
`playback_status` reports completion or MIDI errors. Live `o` hits use velocity
60; imported `o` steps currently encode the same velocity as `x`.

## Limits and recovery

- Imports accept only nonempty mono, 16-bit PCM WAV at 46875 Hz. No silent
  conversion or resampling. The upload page limit is 28,376,655 PCM bytes
  (a valid 16-bit file must have an even size); current free memory usually
  imposes a smaller limit. Total kit PCM must be strictly less than free memory.
- Free memory is read before writes. Device allocation overhead is not fully
  characterized, so passing preflight cannot guarantee that an upload fits.
  Transfers stop on the first device rejection and preserve journal state.
- Slot selection excludes occupied slots and **every stored reference across
  all nine projects**, even zero-length records. It never deletes or overwrites
  library audio to make space. Samples currently receive the name `mcp_sample`.
- Backup verification checks ZIP integrity, readable complete WAVs, SKU/OS,
  library occupancy and all 432 stored pad slot/length fields. It does not
  compare live audio content or every project field. Reads are sequential;
  leave the device untouched during verification and installation.
- Backup files default to a maximum age of 24 hours, based on modification
  time. Set `EP133_BACKUP_MAX_AGE_SECONDS` to a finite positive number to change
  this limit. Files and device state are revalidated before writes.
- Private durable journals default to `~/.local/state/ep133-mcp/journal`;
  override with `EP133_JOURNAL_DIR`. Keep this directory for recovery across
  restarts. Journals contain operation metadata, not audio. Exact project preimages and
  restore packages are stored in sibling `project-preimages` and `restore-packages`
  directories; keep these private recovery artifacts alongside the journals.
- Selective sample and project restore tools are available; full-device restore still uses Sample Tool. Restore drift was observed on hardware.
  After a manual Sample Tool restore, save a fresh backup and compare it with
  the intended backup using `uv run python tools/diff_backups.py OLD.pak NEW.pak`.
  Resolved live pad values alone cannot prove restoration.

## Development

```sh
uv run pytest -q
uv build
```

Routine tests never open hardware ports. Manual hardware acceptance is described
in [the validation record](docs/research/phase1-validation.md). The task plan is
tracked in `.dex/tasks.jsonl`; design contracts are in
[docs/design/tool-contracts.md](docs/design/tool-contracts.md).

Protocol framing, upload payloads and 7-bit packing are ported from
[ZacharySBrown/ep133-ppak](https://github.com/ZacharySBrown/ep133-ppak) (MIT),
itself a port of phones24/ep133-export-to-daw. See `NOTICE`.
