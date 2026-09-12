# Global settings node — hunt on OS 2.5.1 (negative result)

Question (Dex `m6fyda1s`): is there a file node holding the device-wide
settings — quantize, swing, metronome, MIDI in/out, sync — that
`FILE_METADATA_GET` could read and a `device_settings()` tool could expose?

Method, 2026-09-11 late evening, device on project 3, all read-only:
`list_files` (FILE_LIST, opcode `04`, page 0) walked from node 0, then
`stat_file` (STAT) on ids the walk did not list. Every id was written to a
log before it was sent (`stat-probe-ids.txt` in the session scratchpad).

## The whole reachable tree

```
0                        root
├── 1000  sounds          folder   children: nodes 1..999 = slot n, name "nnn.pcm", flags 29
└── 2000  projects        folder
    └── N000 "0N"        folder   N = 1..9  (3000 .. 11000)
        └── N100 groups  folder   (only child of the project folder)
            ├── N200 A   folder   children N201..N212, name "01".."12", flags 29, size 0
            ├── N300 B
            ├── N400 C
            └── N500 D
```

FILE_LIST at depth 2 from the root returned exactly 11 entries (2 roots + 9
projects). Inside project 3: `5000` lists only `groups`; `5100` lists
A–D; `5200` lists the 12 pad files. Folders are flags 14, files flags 29.

## STAT on the gaps

| id | answer |
|---|---|
| 1, 2, 3, 10 | exist: `001.pcm` … `010.pcm`, parent 1000, sizes 1803452 / 1543124 / 470398 / 294016 — the slot ids are the file ids |
| 999 | invalid (slot 999 is empty; an empty slot is not a node) |
| 1999 | invalid |
| 2001, 2100 | invalid — nothing between `projects` and the first project |
| 5001 | invalid — nothing between a project folder and `groups` |
| 12000, 13000 | invalid — nothing after project 9 |
| 65535 | invalid |

## What this says

- The SysEx file namespace on OS 2.5.1 is sounds + projects + pads, full
  stop. There is no listed node for global settings, and the obvious
  unlisted ids on either side of the known ranges answer *invalid id*.
- The per-project `settings` (222–224 bytes) and `fx_settings` (144–160
  bytes) that appear in the project TAR are **not** file nodes either: the
  project folder's FILE_LIST does not enumerate them and `5001` does not
  exist. They are only reachable through the project export that
  `read_project` / `create_backup` already use. Whatever device-wide state
  lives on the device is either inside those members (the
  `fx-and-settings-map.md` hypothesis for fader functions already puts
  per-group state there) or behind a command that is not a file read.
- A `device_settings()` tool therefore has no node to read. Quantize, swing
  and metronome, if they are exposed at all, must be found by the diff
  method: change one on the device, `diff_project(N, old=…)` on the active
  project, and see whether `settings` moves. That is step 8 of the owner's
  knob session (`vsa0gzu9`), not a SysEx hunt.

## Not done

- No contiguous sweep of 2001–2999 or 12000–65535 — the ranges were spot
  checked at their edges. A full sweep is ~64k STATs at the device's pace
  and, given the walk above, unlikely to find a node the directory itself
  does not list.
- FILE_LIST pages > 0 were not requested for the root; the 11-entry answer
  fit one page, and ep133-ppak's GROUP_DUMP reads page 0 only.
