# Handoff — prove the encoding on the import path

Follows [`pattern-encoding-brief.md`](pattern-encoding-brief.md). Findings are in
[`../research/pattern-encoding.md`](../research/pattern-encoding.md): the
pattern/scene/settings encoding is byte-exact against 314 device-written files
and upstream's builders reproduce it. **The only thing left is whether the
device accepts a file we made.** That is this task, and nothing else.

Sanity check before starting: `python3 tools/pattern_decode.py check <backup.pak>`
prints `failures: 0`.

## The one job: the import ladder

Fresh full backup before each rung. Target a **non-active** project slot. Stop
at the first failure and write it up in `pattern-encoding.md`; do not skip rungs.

1. **Unchanged round trip.** Sample Tool project export → import. Zero new
   bytes. Proves the reference layout (with `settings`, `fx_settings`, 27-byte
   pad records, scene references to missing patterns) passes import at all.
2. **Our container.** Same project, re-zipped by a packer we write, every TAR
   member byte-identical. Packer must copy the device flavour: V7 tar headers
   (no ustar magic), sorted members, dir entries `pads`, `pads/a..d`,
   `patterns`, mtime 0, uid/gid 0, modes 0644/0755; ZIP entries with a leading
   `/`, `meta.json` with `pak_type: "project"` and a fresh `generated_at`.
   Verify offline first: unpack the output and diff every member against the
   source.
3. **Our bytes.** One added note event in one existing pattern, event byte 7 =
   0, header count incremented. If it plays, the encoder is proven.

Rung 3 passing opens `generate_ppak`. Rung 1 or 2 failing means the container,
not the bytes, is what caused the upstream contributor's flash format — also a
result worth having.

Guardrails: backup first, non-active slot, never commit `*.pak`/`*.ppak`/audio.
