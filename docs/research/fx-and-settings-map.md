# `fx_settings` and `settings`: the map so far (2026-09-11)

Dex `vsa0gzu9`. Both files are carried verbatim by `generate_ppak` and modelled
by nobody. The mapping session needs the owner at the knobs; this note holds
what nine device-written projects (`session-07.pak`) say on their own, the
hypotheses that survey produced, and the procedure for the session. Every
row below is **probable** or **guessed** until the diff method confirms it.

Tooling: `diff_project(project, old=<pak>, new=None)` over MCP names every
changed field between a backup and a live read (or two backups);
`tools/diff_projects.py` is the byte-range CLI. Decoders:
`ep133_mcp.protocol.decode`.

## Survey of the nine projects

`settings` (24-byte head with BPM, 48 float32, 4 group bytes, tail):

| P | size | bpm | group bytes | params set (index: value) |
|---|---|---|---|---|
| 01 | 222 | 145.00 | `0 0 5 0` | 0: 1.0, 5: 1.0, 12: 1.0, 17: 0.0, 24: 0.776, 29: 0.761 |
| 02 | 222 | 88.00 | `0 0 0 0` | 0: 1.0, 12: 1.0, 24: 1.0, 36: 1.0, 41: 0.0 |
| 03 | 224 | 123.08 | `0 5 0 5` | 0: 0.350, 5: 0.0, 12: 1.0, 17: 1.0, 24: 1.0, 29: 0.0006, 41: 0.0 |
| 04 | 224 | 123.00 | `5 0 5 0` | 0: 1.0, 1: 0.0, 4: 0.0, 5: 0.04, 6: 0.0, 12: 1.0, 29: 0.0, 36: 1.0, 37: 0.497, 41: 1.0 |
| 05 | 222 | 135.00 | `5 5 5 5` | 0: 1.0, 5: 1.0, 17: 0.0, 24: 1.0, 29: 0.0002, 41: 0.513 |
| 06 | 224 | 123.08 | `0 5 0 5` | (copy of P03) |
| 07 | 220 | 84.00 | `5 5 5 5` | 5: 0.0, 12: 1.0, 13–19, 21–23, 25, 29 |
| 08 | 220 | 101.00 | `1 5 6 5` | 0: 1.0, 1: 0.088, 3: 0.98, 4: 0.0, 5: 0.534, 9: 0.469, 12: 1.0, 17: 0.0, 29: 0.735, 30: 0.723, 40: 0.0, 41: 0.687 |
| 09 | 220 | 90.02 | `0 5 5 0` | 5: 0.0, 12: 1.0, 15: 1.0, 16: 0.0, 17: 0.0, 29: 0.01, 36: 1.0, 41: 1.0 |

"Set" means not `-1.0`. Tail: `00 02`, `00 08`, `00 04 00 00`, `02 02 00 00`,
`00 08`, empty in the 220-byte form.

### Hypothesis: 4 groups × 12 fader functions

Every set index falls in `12·g + f` with `f` small, and the group byte for
`g` names an `f` whose slot is set in the same project:

- P08 group bytes `1 5 6 5` → set slots include 1 (g0), 17 = 12+5 (g1),
  30 = 24+6 (g2), 41 = 36+5 (g3).
- P05 `5 5 5 5` → 5, 17, 29, 41 all set.
- P03 `0 5 0 5` → 0, 17, 24(=0), 41 set.

So `settings.params[12·g + f]` is most likely the **last value of fader
function `f` for group `g`**, and `group_bytes[g]` the **function the fader
is currently assigned to** for that group. Function ids seen: 0, 1, 3, 4, 5,
6, 9 (values) and 0, 1, 5, 6 (assignments). The pattern-file automation
events use param ids 1, 5 and 6 — the same id space, so **automation
`param` = fader function id**. `f = 0` reads ~1.0 in eight projects (a
level?), `f = 5` is 0.0 or 1.0 most often (a switch-like function).
`diff_project` already labels each settings float with this `group` /
`function` reading; the session confirms or kills it.

`fx_settings` (4 zero bytes, selector byte, 3 zero bytes, 34 float32, tail):

| P | size | selector | non-default params (n/256) |
|---|---|---|---|
| 01 | 160 | 0 | 2: 235, 18: 256 |
| 02 | 160 | 2 | 2: 256, 18: 206 |
| 03 | 160 | 1 | 1: 256, 2: 253, 4: 155, 17: 53, 18: 102 |
| 04 | 160 | 2 | 1: 134, 2: 256, 17: 113, 18: 169 |
| 05 | 160 | 0 | 1: 88, 2: 147, 3: 100, 4: 14, 5: 134, 6: 100, 17: 49, 18: 201, 20: 256, 21: 87, 22: 40 |
| 07 | 144 | 2 | 0: 256, 1: 159, 2: 140, 5: 42, 17: 84, 18: 168 |
| 08 | 144 | 2 | 1: 156, 2: 256, 17: 143, 18: 58 |
| 09 | 144 | 6 | 1: 256, 2: 217, 3: 81, 4: 256, 5: 86, 6: 133, 17: 256, 18: 256, 19: 248, 21: 76, 22: 185 |

Default is 0.5 (128/256). Newer-form tail: `00000000 0000003f 00060080 00800080`.

### Hypothesis: two banks of 17

Non-defaults cluster at indices 0–6 and 17–22, and index `i` and `i + 17`
move together (1/18, 2/18 in every project). A 34-float table that splits
as **17 + 17** reads as one row per effect type × two knobs, i.e.
`params[type]` = knob A and `params[17 + type]` = knob B, each type keeping
its last knob positions. That would make the selector an index into the
same 17 rows (0, 1, 2, 6 seen). Whether the device has 17 effect types,
and which is which, is exactly what the session settles.

## The session (owner at the device, agent driving)

1. Park the device on a project the rebuild blanked (P02–P09). Since the
   owner is turning knobs on it, it is the active project during the
   session; that is fine for reading (the device flushes the TAR while
   running — `scenes` was seen to change unprompted) but nothing is written
   over MCP into it.
2. `create_backup(out=session-NN.pak, base=<previous>)` once as the base.
3. Per step, one change, then `diff_project(N, old=session-NN.pak)` — a
   live read, seconds not minutes. Record `field → control`:
   - FX page: select each effect type in order → `selector` value per type.
   - For one type, knob A to max, then knob B to max → which `params[i]`.
   - Repeat for a second type → confirms the 17 + 17 layout or not.
   - Fader: assign it to each function for group A in turn → `group_bytes[0]`
     values and their names; move it → `params[f]`.
   - Same for group B once → confirms the ×12 stride.
   - Anything in the tail bytes / `settings` 216–223 that moves without a
     knob (project save, scene change) gets noted as such.
4. If a step shows a change in `other` (a pad, a pattern), the owner touched
   something else; redo the step.
5. Rewrite the tables below with `verified` rows; move what stays unknown to
   an explicit remainder list.

## Verified map

None yet. The rows above are the hypotheses to test.

## Unknown remainder

- `settings` bytes 0–3, 8–23 (always zero here), tail 220–223.
- `fx_settings` bytes 0–3, 5–7 (always zero here), tail 144–159.
- What `-1.0` in a settings slot means beyond "never touched".
