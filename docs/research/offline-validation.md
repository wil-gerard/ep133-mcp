# Offline protocol validation — 2026-09-08

Upstream: [ep133-ppak at 93f0f6b1f6110701e6378e2702f39fd7e9a9d687](https://github.com/ZacharySBrown/ep133-ppak/tree/93f0f6b1f6110701e6378e2702f39fd7e9a9d687).

## Results

- `tests/test_packing.py` and `tests/test_payloads.py`: **20 passed**.
- `tests/test_assign_pad.py`: **29 passed**, including payload comparison against eight upstream assignment fixtures.
- These tests exercise pure encoding and stored captures. No MIDI port was opened and no device message was sent by the tests. Passing tests do not establish persistence, capacity detection or recoverability.

The assignment fixture `assign_p01_A_pad7_sym1.syx` supports project 1, group A, physical label 1, file ID 3207. This checks the upstream fixture, not the state of the user's device.

## Reproduce

In a checkout of the exact revision above, run:

```sh
uv run --no-project --python 3.12 --with pytest --with numpy --with soundfile --with pydantic python -m pytest tests/test_packing.py tests/test_payloads.py -q
uv run --no-project --python 3.12 --with pytest --with numpy --with soundfile --with pydantic python -m pytest tests/test_assign_pad.py -q
```

These commands use an isolated uv environment and do not install the upstream project into this repository. Dependency versions are resolved by uv; the source revision is pinned, but this is not a locked build environment.

## Upload finalization discrepancy — RESOLVED 2026-09-10

The terminator commits the upload; FILE_INFO is accepted but changes nothing.
`upload_sample` is correct and its docstring is wrong. See
[upload capture](upload-capture.md) for the in-session probe and the frame
capture. Original note follows.

### Original note

`ep133/client.py:upload_sample` says FILE_INFO is needed to commit uploaded audio. Its implementation only sends the sequence returned by `generate_upload_payloads`, which ends at the empty terminator. `tests/test_payloads.py:test_full_reproduction` intentionally excludes the final captured FILE_INFO, and `test_finalize_is_file_info` checks that message separately.

Thus the passing fixture tests do not prove the high-level client sends the complete captured sequence. Do not conclude from this alone that uploads fail or that FILE_INFO causes persistence; resolve its actual role from a controlled hardware capture and power-cycle verification. The bare proof must account for this discrepancy explicitly.

## Device access

The macOS IORegistry USB tree shows other connected devices but no EP-133. This is more useful than the earlier empty system_profiler response. A separate mido/python-rtmidi endpoint enumeration returned:

```json
{"inputs": [], "outputs": []}
```

Enumeration sent no MIDI messages. The hardware baseline remains blocked on an accessible, powered EP-133; backup, writes, restore and power-cycle checks have not happened.
