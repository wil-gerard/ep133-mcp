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
                     "install_sample", "install_kit", "undo_last_install"}


@pytest.mark.asyncio
async def test_server_status_never_opens_the_port():
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            status = _payload(await session.call_tool("server_status"))
    assert status["session_open"] is False
    assert status["write_tools_available"] is True


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
    def list_pads(self, project=None):
        return {'project': project or 1, 'pads': []}
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
