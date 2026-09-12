"""The server must launch over stdio and keep stdout clean.

A successful MCP handshake and tool round-trip through the real stdio client
is the proof: any stray byte on stdout would break the JSON-RPC stream and the
client would fail. Never opens a hardware port. Device errors are tested with a fake session.
"""

import json
import sys

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVER = StdioServerParameters(command=sys.executable, args=["-m", "ep133_mcp.server"])


def _payload(result) -> dict:
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    text = "".join(c.text for c in result.content if getattr(c, "text", None))
    return json.loads(text)


@pytest.mark.asyncio
async def test_handshake_lists_tools():
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
    assert tools == {"device_info", "server_status", "list_pads", "verify_backup", "restore_procedure",
                     "read_pad", "read_project", "install_sample", "install_kit", "undo_last_install", "delete_samples", "create_backup", "fetch_reference",
                     "analyze_reference", "extract_kit", "transcribe_groove", "generate_ppak"}


@pytest.mark.asyncio
async def test_server_status_never_opens_the_port():
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            status = _payload(await session.call_tool("server_status"))
    assert status["session_open"] is False
    assert status["write_tools_available"] is True
    assert set(status["audio"]) == {"extra_installed", "missing_modules", "ffmpeg", "yt_dlp", "install"}


def test_fetch_reference_without_audio_extra_is_structured(tmp_path, monkeypatch):
    """Core install: the tool exists and refuses with the install command, never a traceback."""
    import importlib.util
    from ep133_mcp import server as module
    from ep133_mcp.audio import deps

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name: None if name in deps.PYTHON_MODULES else real(name))
    monkeypatch.setattr(deps, "which", lambda name: None)
    wav = tmp_path / "ref.wav"
    wav.write_bytes(b"RIFF")
    result = module.fetch_reference(str(wav))
    assert result["error"] == "AudioToolsUnavailable"
    assert deps.EXTRA_INSTALL in result["next_step"]
    assert module.fetch_reference("https://youtu.be/dQw4w9WgXcQ")["error"] == "AudioToolsUnavailable"
    analyzed = module.analyze_reference(str(wav), separation="hpss", beat_tracker="librosa")
    assert analyzed["error"] == "AudioToolsUnavailable"
    assert deps.EXTRA_INSTALL in analyzed["next_step"]
    assert module.extract_kit(str(wav))["error"] == "AudioToolsUnavailable"
    assert module.transcribe_groove(str(wav))["error"] == "AudioToolsUnavailable"
    assert module.server_status()["audio"]["extra_installed"] is False


@pytest.mark.asyncio
async def test_analyze_reference_through_client(tmp_path):
    """Fixture clip in, probable estimates out, over the real stdio stream; no model weights."""
    pytest.importorskip("librosa", reason="audio extra not installed")
    import synth_reference

    truth = synth_reference.write(tmp_path / "ref", bpm=97.0, bars=4)
    args = {"clip": str(truth["clip"]), "separation": "hpss", "beat_tracker": "librosa"}
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool = next(t for t in (await session.list_tools()).tools if t.name == "analyze_reference")
            assert "probable" in tool.description
            assert set(tool.input_schema["properties"]) == {"clip", "separation", "beat_tracker", "force"}
            record = _payload(await session.call_tool("analyze_reference", args))
            assert record["status"] == "analyzed"
            assert record["separation"] == {"method": "hpss", "model": None}
            assert record["beat_tracker"] == {"method": "librosa", "checkpoint": None}
            assert abs(record["bpm"]["value"] - 97.0) / 97.0 < 0.03 and record["bpm"]["probable"]
            assert record["key"]["name"] == "A minor"
            assert record["stems"]["vocals"] is None
            assert record["stems"]["drums"]["onsets_s"]
            cached = _payload(await session.call_tool("analyze_reference", {"clip": str(truth["clip"])}))
            assert cached["status"] == "cached" and cached["bpm"] == record["bpm"]
            missing = _payload(await session.call_tool("analyze_reference", {"clip": str(tmp_path / "nope.wav")}))
            assert missing["error"] == "InvalidReference"
            bad = _payload(await session.call_tool("analyze_reference", {**args, "separation": "spleeter"}))
            assert bad["error"] == "InvalidReference"
            extracted = _payload(await session.call_tool("extract_kit", {**args, "want": ["kick", "snare", "hat"]}))
            assert [s["class"] for s in extracted["slices"]] == ["kick", "snare", "hat"]
            assert extracted["install_mapping"][0] == {"pad": 1, "path": extracted["slices"][0]["path"]}
            groove = _payload(await session.call_tool("transcribe_groove", {**args, "downbeat_s": 0.0}))
            assert groove["pattern"]["steps"]["1"].startswith("x.......")
            assert set(groove["pattern"]["steps"]) == {"1", "2", "3"} and groove["quantization"]["mean_ms"] < 20


def test_device_info_unavailable_is_structured(monkeypatch):
    from ep133_mcp import server as module
    from ep133_mcp.device import DeviceUnavailable

    def unavailable():
        raise DeviceUnavailable("offline test", next_step="Connect the device")

    monkeypatch.setattr(module, "_device", unavailable)
    info = module.device_info()
    assert info["error"] == "DeviceUnavailable"
    assert "next_step" in info


@pytest.mark.asyncio
async def test_all_tools_through_client_with_fake_device(tmp_path):
    import os
    import zipfile
    from pathlib import Path
    from test_install import FakeDevice
    from test_preflight import wav_file

    pak = tmp_path / 'backup.pak'
    fake = FakeDevice()
    with zipfile.ZipFile(pak, 'w') as z:
        z.writestr('/meta.json', json.dumps({'device_name': 'EP-133', 'device_sku': 'TE032AS001',
                   'device_version': '2.5.1', 'pak_type': 'user'}))
        for p in range(1, 10):
            z.writestr(f'/projects/P{p:02}.tar', fake.project_tar(p))
        z.writestr('/sounds/016 tone.wav', (Path(__file__).parents[1] / 'fixtures/phase0-test-tone.wav').read_bytes())
    wav = wav_file(tmp_path)
    code = '''
from types import SimpleNamespace
from ep133_mcp import server as module
from test_install import FakeDevice
class Device(FakeDevice):
    def greet(self):
        return SimpleNamespace(product='EP-133', mode='normal', sku='TE032AS001',
            base_sku='TE032AS001', os_version='2.5.1', sw_version='2.5.1', bl_version='1', serial='private')
    def sample_root(self):
        return SimpleNamespace(max_capacity=64000000, free_space_in_bytes=self.free, native_rate=46875)
    def active_project(self):
        return 1
    def list_pads(self, project=None, fields=False):
        return {'project': project or 1, 'pads': [], 'fields': fields}
    def read_pad(self, project, group, pad):
        return {'project': project, 'group': group, 'pad': pad, 'sym': 0, 'pad_metadata': {'sym': 0}, 'slot_metadata': None}
    def close(self):
        pass
module._session = Device()
module.main()
'''
    params = StdioServerParameters(command=sys.executable, args=['-c', code], env={
        **os.environ, 'PYTHONPATH': str(Path(__file__).parent),
        'EP133_JOURNAL_DIR': str(tmp_path / 'journal')})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            info = _payload(await session.call_tool('device_info'))
            assert info['sku'] == 'TE032AS001' and 'serial' not in info
            assert _payload(await session.call_tool('list_pads'))['project'] == 1
            assert _payload(await session.call_tool('list_pads', {'project': 2, 'fields': True}))['fields'] is True
            assert _payload(await session.call_tool('read_pad', {'project': 1, 'group': 'A', 'pad': 7}))['sym'] == 0
            bad = _payload(await session.call_tool('read_pad', {'project': 1, 'group': 'E', 'pad': 7}))
            assert bad['error'] == 'InvalidDestination'
            assert _payload(await session.call_tool('restore_procedure'))['type'] == 'guidance'
            invalid = await session.call_tool('install_sample', {'path': str(wav)})
            assert invalid.is_error
            refused = _payload(await session.call_tool('install_sample', {
                'path': str(wav), 'project': 1, 'group': 'A', 'pad': 1, 'backup_id': 'unknown'}))
            assert refused['error'] == 'BackupStale'
            backup = _payload(await session.call_tool('verify_backup', {'path': str(pak)}))
            assert backup['status'] == 'current'
            installed = _payload(await session.call_tool('install_kit', {
                'mapping': [{'pad': 1, 'path': str(wav)}, {'pad': 2, 'path': str(wav)}],
                'project': 1, 'group': 'A', 'backup_id': backup['backup_id']}))
            assert installed['status'] == 'installed'
            assert len(installed['entries']) == 2
            stale = _payload(await session.call_tool('install_sample', {
                'path': str(wav), 'project': 1, 'group': 'A', 'pad': 3, 'backup_id': backup['backup_id']}))
            assert stale['error'] == 'BackupStale'
            undo = _payload(await session.call_tool('undo_last_install'))
            assert undo['status'] == 'undone' and undo['library_slots_left_in_place'] == [1, 2]
            from ep133_mcp.protocol import projects as P
            from test_pack_project import minimal_project
            template = tmp_path / 'template.pak'
            with zipfile.ZipFile(template, 'w') as z:
                z.writestr('/meta.json', json.dumps({'device_version': '2.5.1', 'device_sku': 'TE032AS001'}))
                z.writestr('/projects/P07.tar', P.pack_project(minimal_project()))
            pattern = {'group': 'A', 'index': 2, 'bars': 1, 'steps': {'1': 'x...x...x...x...'}}
            written = _payload(await session.call_tool('generate_ppak', {
                'out': str(tmp_path / 'out.ppak'), 'project': 7, 'template_pak': str(template),
                'bpm': 97.0, 'patterns': [pattern], 'scenes': [{'scene': 1, 'A': 2, 'B': 1, 'C': 1, 'D': 1}]}))
            assert written['status'] == 'written' and written['verified_on_device'] is False
            assert {m['member'] for m in written['manifest']} == {'settings', 'patterns/a02', 'scenes'}
            assert written['patterns_written'] == {'patterns/a02': pattern['steps']}
            offline = _payload(await session.call_tool('read_project', {'project': 7, 'source': str(template)}))
            assert offline['bpm'] == 120.0 and len(offline['pads']) == 48 and offline['source'] == str(template)
            assert [p['index'] for p in offline['patterns']] == [1, 3]
            missing = _payload(await session.call_tool('read_project', {'project': 2, 'source': str(template)}))
            assert missing['error'] == 'InvalidInput'
            live_read = _payload(await session.call_tool('read_project', {'project': 1}))
            assert live_read['source'] == 'device' and live_read['bpm'] is None and live_read['patterns'] == []
            live = _payload(await session.call_tool('generate_ppak', {
                'out': str(tmp_path / 'live.ppak'), 'project': 1, 'bpm': 97.0}))
            assert live['error'] == 'InvalidInput' and 'flavour' in live['message']   # fake device writes ustar
            assert not (tmp_path / 'live.ppak').exists()
