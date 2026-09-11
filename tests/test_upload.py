import json
from pathlib import Path
from unittest.mock import Mock
import wave

import pytest

from ep133_mcp.device.session import DeviceSession, DeviceRejected
from ep133_mcp.protocol import payloads as P
from ep133_mcp.protocol.packing import unpack
from ep133_mcp.protocol.sysex import Response


def test_upload_payloads_reproduce_captured_frames():
    root = Path(__file__).resolve().parents[1]
    capture = json.loads((root / 'docs/research/fixtures/upload-capture-slot16.json').read_text())
    # Capture is a research artifact with metadata and frames.
    frames = capture['frames'] if isinstance(capture, dict) else capture
    with wave.open(str(root / 'fixtures/phase0-test-tone.wav')) as w:
        pcm = w.readframes(w.getnframes())
    expected = [P.file_put_meta('16_testtone', len(pcm), 16)]
    expected += [P.file_put_data(page, pcm[offset:offset+433])
                 for page, offset in enumerate(range(0, len(pcm), 433))]
    expected += [P.file_put_data(87, b'')]
    actual = [unpack(bytes.fromhex(f['sent_hex'])[9:-1]) for f in frames
              if f['label'].startswith(('upload/put_meta', 'upload/data[', 'upload/terminator'))]
    assert actual == expected


def test_upload_stops_on_rejection_and_has_no_file_info():
    d = DeviceSession()
    d.greet = Mock()
    d.request = Mock(return_value=Response(0, 0, 5, 0, b''))
    d.upload_sample(1, 'tone', bytes(434))
    payloads = [call.args[1] for call in d.request.call_args_list]
    assert payloads == [P.file_init(1), P.file_put_meta('tone', 434, 1),
                        P.file_put_data(0, bytes(433)), P.file_put_data(1, bytes(1)),
                        P.file_put_data(2, b'')]
    d.request = Mock(side_effect=[Response(0, 0, 5, 0, b''), Response(0, 0, 5, 1, b'')])
    with pytest.raises(DeviceRejected):
        d.upload_sample(1, 'tone', bytes(434))
    assert d.request.call_count == 2


def test_upload_page_boundary():
    assert P.file_put_meta('tone', P.MAX_UPLOAD_BYTES, 999)
    with pytest.raises(ValueError):
        P.file_put_meta('tone', P.MAX_UPLOAD_BYTES + 1, 1)
    with pytest.raises(ValueError):
        P.file_put_data(65536, b'')
