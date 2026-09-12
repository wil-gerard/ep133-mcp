# Project write over SysEx — from Sample Tool's own source

Date 2026-09-12. Upstream PROTOCOL.md §8.3 calls the project write path
unknown and warns that probing wedged the device. No sniff was needed:
EP Sample Tool is a web app (`https://teenage.engineering/apps/ep-sample-tool`,
bundle `assets/index-C1wBjhTa.js`, 1.8 MB, unminified identifiers), and its
`uploadProjectArchive` is the import.

## What Sample Tool sends

```js
async uploadProjectArchive(file, progress) {
  const nn = file.name.match(/\w*P(\d{2})\.tar/)[1];          // "03"
  const parent = await this.getNodeIdByPath("/projects");     // 2000
  const node   = await this.getNodeIdByPath(`/projects/${nn}`); // 2000 + 1000·N
  const bytes  = await file.arrayBuffer();                    // the TAR, as-is
  await this.put(serial, bytes, nn, parent, node, null, progress, true,
                 [TE_SYSEX_FILE_CAPABILITY_READ], 15e3);
  await this.fileHandler.init(serial);
}
```

`put` → `SysExFilePutInitRequest(nodeId, parentId, flags, size, name, null)`:

```
02 00 <flags u8> <fileId u16 BE> <parentId u16 BE> <size u32 BE> <name> 00
```

with `flags = CAPABILITY_READ(4) | FILE_TYPE_DIR(2) = 6` — the call passes
`isDir = true` (the `!0` argument): a project node is a directory the TAR is
unpacked into, and the device answers flag 5 with status 1 "project
directories are directories" (first attempt, 2026-09-12, no wedge) — then
`SysExFilePutDataRequest(page, chunk)` = `02 01 <page u16 BE> <bytes>` for
each chunk, then an empty page, then `FILE_INIT` again. The project put
uses a 15 s timeout (the commit happens on the terminator). `FILE_INIT`'s
"mode 1" is `TE_SYSEX_FILE_INIT_SUBSCRIBE` — it subscribes the session to
the device's unsolicited notifications (the free-space frames seen after a
delete), which is why our write-mode init is harmless for reads.

This is our proven sample upload (`02 00 05 <slot> <1000> <size> <name> 00
{"channels":1}`) retargeted: flags 6, fileId = the project node, parentId =
2000, name = `NN`, no metadata JSON. `payloads.file_put_project`
builds it; `DeviceSession.write_project` sends it with the sample upload's
433-byte pages and re-inits afterwards.

Also in the bundle: `setActiveProject(path)` is
`setMetadata(/projects, {active: nodeId})` — a live metadata write on
node 2000, not a boot-time setting as upstream assumed. Now exposed through confirmed `set_active_project`; hardware acceptance remains pending.

## Tool

`import_ppak(path, project, backup_id, confirm)` runs `check_ppak`, refuses
on any problem, plans carried samples with explicit slot remapping and confirms with the file's
contents and what the project holds now, journals (`import_ppak`), writes,
reads the project back: byte-equal → `imported`; decoded-equal →
`imported_with_differences`; otherwise `VerificationFailed`.
`undo_last_import` restores an exact private project preimage and refuses subsequent
project edits. See [portable recovery](portable-recovery.md) for the expanded path.

## Hardware

Not yet run. First target: a project the owner is emptying anyway
(2026-09-12: everything but P3), blank `.ppak` from `tools/blank_project.py`
against `session-15.pak`, then `read_project`, a backup diff, and the
owner's power-cycle.
