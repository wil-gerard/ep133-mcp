"""fetch_reference: local files, faked yt-dlp and Spotify hops, caching, limits.

No test touches the network or a real yt-dlp/ffmpeg: the subprocess and HTTP
seams in ep133_mcp.audio.reference are replaced, and audio is synthesized.
"""

import json
import subprocess
import wave

import pytest

np = pytest.importorskip('numpy', reason='audio extra not installed')
sf = pytest.importorskip('soundfile', reason='audio extra not installed')

from ep133_mcp.audio import deps
from ep133_mcp.audio import reference as ref
from ep133_mcp.audio.errors import AudioToolsUnavailable, FetchFailed, InvalidReference

RATE = 22050
SECONDS = 3.0


@pytest.fixture(autouse=True)
def references_dir(tmp_path, monkeypatch):
    monkeypatch.setenv('EP133_REFERENCES_DIR', str(tmp_path / 'references'))
    return tmp_path / 'references'


def tone(tmp_path, *, rate=RATE, channels=2, seconds=SECONDS, name='tone.wav'):
    t = np.arange(int(rate * seconds)) / rate
    mono = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    data = np.stack([mono] * channels, axis=1) if channels > 1 else mono
    path = tmp_path / name
    sf.write(str(path), data, rate, subtype='PCM_16')
    return path


# --- classification -------------------------------------------------------------

@pytest.mark.parametrize('url, kind', [
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'youtube'),
    ('https://youtu.be/dQw4w9WgXcQ?t=42', 'youtube'),
    ('https://music.youtube.com/watch?v=dQw4w9WgXcQ', 'youtube'),
    ('https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT?si=x', 'spotify'),
    ('spotify:track:4cOdK2wGLETKBW3PvgPWqT', 'spotify'),
    ('file:///tmp/x.wav', 'file'),
    ('/tmp/x.wav', 'file'),
])
def test_classify(url, kind):
    assert ref.classify(url) == kind


@pytest.mark.parametrize('url', ['', '   ', 'https://soundcloud.com/x/y', 'ftp://host/x.wav', 'https://youtube.com.evil.example/watch?v=x'])
def test_classify_rejects(url):
    with pytest.raises(InvalidReference):
        ref.classify(url)


@pytest.mark.parametrize('url, vid', [
    ('https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1', 'dQw4w9WgXcQ'),
    ('https://youtu.be/dQw4w9WgXcQ', 'dQw4w9WgXcQ'),
    ('https://www.youtube.com/shorts/abc_-123', 'abc_-123'),
    ('https://www.youtube.com/embed/abc', 'abc'),
    ('https://www.youtube.com/playlist?list=PL1', None),
    ('https://www.youtube.com/watch?v=bad%20id', None),
])
def test_youtube_video_id(url, vid):
    assert ref.youtube_video_id(url) == vid


# --- clip bounds ----------------------------------------------------------------

def test_clip_bounds_defaults_and_cap():
    assert ref.clip_bounds(0, None, 200.0) == (0.0, 60.0)
    assert ref.clip_bounds(190, None, 200.0) == (190.0, 200.0)
    assert ref.clip_bounds(10, None, None) == (10.0, 70.0)
    assert ref.clip_bounds(42, 70, 200.0) == (42.0, 70.0)


@pytest.mark.parametrize('start, end, duration, match', [
    (-1, 10, 100.0, '>= 0'),
    (100, 110, 100.0, 'past the end'),
    (10, 5, 100.0, 'greater than start'),
    (10, 10, 100.0, 'greater than start'),
    (0, 101, 100.0, 'past the end'),
    (0, 61, 100.0, 'longer than the cap'),
    ('0', 10, 100.0, 'finite number'),
    (True, 10, 100.0, 'finite number'),
    (0, float('nan'), 100.0, 'finite number'),
])
def test_clip_bounds_rejects(start, end, duration, match):
    with pytest.raises(InvalidReference, match=match):
        ref.clip_bounds(start, end, duration)


# --- local files ----------------------------------------------------------------

def test_fetch_local_file_url_writes_clip_and_sidecar(tmp_path, references_dir):
    src = tone(tmp_path)
    result = ref.fetch_reference(src.as_uri(), 0.5, 2.0)
    assert result['status'] == 'fetched'
    assert result['sample_rate'] == ref.CLIP_RATE and result['channels'] == 2
    assert result['frames'] == int(1.5 * ref.CLIP_RATE)
    assert result['source']['kind'] == 'file' and result['source']['title'] == 'tone.wav'
    assert result['source']['duration_s'] == pytest.approx(SECONDS)
    with wave.open(result['clip'], 'rb') as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (2, 2, ref.CLIP_RATE)
        assert w.getnframes() == result['frames']
    sidecar = json.loads((references_dir / result['cache_dir'].split('/')[-1] / 'source.json').read_text())
    assert sidecar['source']['url'] == src.as_uri()
    assert sidecar['start_s'] == 0.5 and sidecar['end_s'] == 2.0
    assert 'status' not in sidecar

    again = ref.fetch_reference(src.as_uri(), 0.5, 2.0)
    assert again['status'] == 'cached' and again['clip'] == result['clip']


def test_fetch_local_path_keeps_native_rate_and_mono(tmp_path):
    src = tone(tmp_path, rate=44100, channels=1, seconds=1.0)
    result = ref.fetch_reference(str(src))
    assert result['channels'] == 1 and result['frames'] == 44100
    assert result['end_s'] == pytest.approx(1.0)


def test_fetch_local_downmixes_more_than_two_channels(tmp_path):
    src = tone(tmp_path, rate=44100, channels=4, seconds=0.5)
    assert ref.fetch_reference(str(src))['channels'] == 2


def test_fetch_local_range_checked_against_duration(tmp_path):
    src = tone(tmp_path)
    with pytest.raises(InvalidReference, match='past the end'):
        ref.fetch_reference(str(src), 1.0, SECONDS + 1)


def test_fetch_local_missing_or_unreadable(tmp_path):
    with pytest.raises(InvalidReference, match='not found'):
        ref.fetch_reference(str(tmp_path / 'nope.wav'))
    junk = tmp_path / 'junk.wav'
    junk.write_bytes(b'not audio at all')
    with pytest.raises(InvalidReference, match='not readable'):
        ref.fetch_reference(str(junk))


def test_local_cache_key_changes_when_file_changes(tmp_path):
    src = tone(tmp_path, seconds=1.0)
    first = ref.fetch_reference(str(src))
    tone(tmp_path, seconds=2.0)
    second = ref.fetch_reference(str(src))
    assert second['status'] == 'fetched' and second['clip'] != first['clip']


# --- yt-dlp and Spotify through the seams -----------------------------------------

class FakeYtDlp:
    """Answers --dump-single-json probes and writes audio.flac on download."""

    def __init__(self, tmp_path, *, video_id='dQw4w9WgXcQ', duration=SECONDS, search_hits=1):
        self.audio = tone(tmp_path, name='full.wav')
        self.video_id, self.duration, self.search_hits = video_id, duration, search_hits
        self.probes, self.downloads = [], []

    def info(self):
        return {'id': self.video_id, 'title': 'Test Video', 'uploader': 'Tester', 'duration': self.duration,
                'webpage_url': f'https://www.youtube.com/watch?v={self.video_id}'}

    def __call__(self, cmd, timeout):
        target = cmd[-1]
        if '--dump-single-json' in cmd:
            self.probes.append(target)
            if target.startswith('ytsearch1:'):
                entries = [self.info()] * self.search_hits
                out = {'_type': 'playlist', 'entries': entries}
            else:
                out = self.info()
            return subprocess.CompletedProcess(cmd, 0, json.dumps(out), '')
        assert cmd[cmd.index('-o') + 1].endswith('audio.%(ext)s')
        assert cmd[cmd.index('--audio-format') + 1] == 'flac'
        self.downloads.append(target)
        data, rate = sf.read(str(self.audio))
        sf.write(cmd[cmd.index('-o') + 1].replace('%(ext)s', 'flac'), data, rate)
        return subprocess.CompletedProcess(cmd, 0, '', '')


@pytest.fixture
def tools_on_path(monkeypatch):
    monkeypatch.setattr(deps, 'which', lambda name: f'/fake/bin/{name}')


def test_youtube_fetch_downloads_once_and_caches(tmp_path, references_dir, tools_on_path, monkeypatch):
    fake = FakeYtDlp(tmp_path)
    monkeypatch.setattr(ref, '_run', fake)
    url = 'https://youtu.be/dQw4w9WgXcQ'
    result = ref.fetch_reference(url, 0, 1)
    assert result['status'] == 'fetched' and result['frames'] == ref.CLIP_RATE
    assert result['source']['media_id'] == 'dQw4w9WgXcQ' and result['source']['title'] == 'Test Video'
    assert fake.probes == [url] and fake.downloads == ['https://www.youtube.com/watch?v=dQw4w9WgXcQ']
    assert (references_dir / 'sources' / 'dQw4w9WgXcQ' / 'audio.flac').is_file()

    # Same range: cache hit with no yt-dlp call at all.
    assert ref.fetch_reference('https://www.youtube.com/watch?v=dQw4w9WgXcQ', 0, 1)['status'] == 'cached'
    assert len(fake.probes) == 1
    # New range: probe for duration, but the FLAC source is reused.
    assert ref.fetch_reference(url, 1, 2.5)['status'] == 'fetched'
    assert len(fake.probes) == 2 and len(fake.downloads) == 1


def test_youtube_range_rejected_before_download(tmp_path, tools_on_path, monkeypatch):
    fake = FakeYtDlp(tmp_path, duration=100.0)
    monkeypatch.setattr(ref, '_run', fake)
    with pytest.raises(InvalidReference, match='past the end'):
        ref.fetch_reference('https://youtu.be/dQw4w9WgXcQ', 90, 120)
    assert fake.downloads == []


def test_youtube_probe_failure_is_structured(tools_on_path, monkeypatch):
    monkeypatch.setattr(ref, '_run', lambda cmd, timeout: subprocess.CompletedProcess(cmd, 1, '', 'ERROR: private'))
    with pytest.raises(FetchFailed, match='resolve') as e:
        ref.fetch_reference('https://youtu.be/dQw4w9WgXcQ')
    assert e.value.detail['observed'] == 'ERROR: private'


def test_youtube_timeout_is_structured(tools_on_path, monkeypatch):
    def slow(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)
    monkeypatch.setattr(ref, '_run', slow)
    with pytest.raises(FetchFailed, match='timed out'):
        ref.fetch_reference('https://youtu.be/dQw4w9WgXcQ')


def test_download_without_flac_is_structured(tmp_path, tools_on_path, monkeypatch):
    fake = FakeYtDlp(tmp_path)
    monkeypatch.setattr(ref, '_run', lambda cmd, timeout: fake(cmd, timeout) if '--dump-single-json' in cmd
                        else subprocess.CompletedProcess(cmd, 0, '', ''))
    with pytest.raises(FetchFailed, match='audio.flac'):
        ref.fetch_reference('https://youtu.be/dQw4w9WgXcQ', 0, 1)


OEMBED = json.dumps({'title': 'Never Gonna Give You Up', 'provider_name': 'Spotify'}).encode()
PAGE = b'<html><meta name="music:musician_description" content="Rick &amp; Astley"/></html>'


def test_spotify_resolves_via_oembed_and_search(tmp_path, tools_on_path, monkeypatch):
    fake = FakeYtDlp(tmp_path)
    requests = []

    def http(url):
        requests.append(url)
        return OEMBED if url.startswith(ref.SPOTIFY_OEMBED) else PAGE

    monkeypatch.setattr(ref, '_run', fake)
    monkeypatch.setattr(ref, '_http_get', http)
    result = ref.fetch_reference('spotify:track:4cOdK2wGLETKBW3PvgPWqT', 0, 1)
    assert fake.probes == ['ytsearch1:Rick & Astley Never Gonna Give You Up']
    assert requests[0] == ref.SPOTIFY_OEMBED + 'https%3A%2F%2Fopen.spotify.com%2Ftrack%2F4cOdK2wGLETKBW3PvgPWqT'
    assert result['source']['kind'] == 'youtube' and result['source']['media_id'] == 'dQw4w9WgXcQ'
    assert result['source']['resolved_from'] == {
        'spotify_url': 'https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT',
        'oembed_title': 'Never Gonna Give You Up', 'artist': 'Rick & Astley',
        'search_query': 'Rick & Astley Never Gonna Give You Up'}
    # The direct YouTube URL for the chosen video shares the cache entry.
    assert ref.fetch_reference('https://youtu.be/dQw4w9WgXcQ', 0, 1)['status'] == 'cached'


def test_spotify_artist_lookup_is_best_effort(tmp_path, tools_on_path, monkeypatch):
    fake = FakeYtDlp(tmp_path)

    def http(url):
        if url.startswith(ref.SPOTIFY_OEMBED):
            return OEMBED
        raise OSError('blocked')

    monkeypatch.setattr(ref, '_run', fake)
    monkeypatch.setattr(ref, '_http_get', http)
    result = ref.fetch_reference('https://open.spotify.com/intl-de/track/4cOdK2wGLETKBW3PvgPWqT?si=abc', 0, 1)
    assert fake.probes == ['ytsearch1:Never Gonna Give You Up']
    assert result['source']['resolved_from']['artist'] is None


def test_spotify_failures_are_structured(tmp_path, tools_on_path, monkeypatch):
    monkeypatch.setattr(ref, '_http_get', lambda url: (_ for _ in ()).throw(OSError('offline')))
    with pytest.raises(FetchFailed, match='oEmbed'):
        ref.fetch_reference('https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT')
    with pytest.raises(InvalidReference, match='track links'):
        ref.fetch_reference('https://open.spotify.com/album/4cOdK2wGLETKBW3PvgPWqT')
    fake = FakeYtDlp(tmp_path, search_hits=0)
    monkeypatch.setattr(ref, '_run', fake)
    monkeypatch.setattr(ref, '_http_get', lambda url: OEMBED)
    with pytest.raises(FetchFailed, match='no results'):
        ref.fetch_reference('https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT')


# --- missing tools --------------------------------------------------------------

def test_missing_yt_dlp_is_reported_with_install_step(monkeypatch):
    monkeypatch.setattr(deps, 'which', lambda name: None)
    with pytest.raises(AudioToolsUnavailable) as e:
        ref.fetch_reference('https://youtu.be/dQw4w9WgXcQ')
    assert e.value.detail['observed'] == {'missing_tool': 'yt-dlp'}
    assert deps.EXTRA_INSTALL in e.value.detail['next_step']


def test_missing_ffmpeg_blocks_download_not_probe(tmp_path, monkeypatch):
    fake = FakeYtDlp(tmp_path)
    monkeypatch.setattr(ref, '_run', fake)
    monkeypatch.setattr(deps, 'which', lambda name: '/fake/yt-dlp' if name == 'yt-dlp' else None)
    with pytest.raises(AudioToolsUnavailable) as e:
        ref.fetch_reference('https://youtu.be/dQw4w9WgXcQ', 0, 1)
    assert e.value.detail['observed'] == {'missing_tool': 'ffmpeg'}
    assert 'brew install ffmpeg' in e.value.detail['next_step']
    assert fake.probes and not fake.downloads


def test_missing_extra_is_reported(tmp_path, monkeypatch):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name: None if name == 'soundfile' else real(name))
    probe = deps.probe()
    assert probe['extra_installed'] is False and probe['missing_modules'] == ['soundfile']
    assert probe['install'] == {'extra': deps.EXTRA_INSTALL, 'system': deps.SYSTEM_INSTALL}
    with pytest.raises(AudioToolsUnavailable, match='not installed'):
        ref.fetch_reference(str(tone(tmp_path)))


# --- server surface -------------------------------------------------------------

def test_server_tool_returns_structured_errors(tmp_path):
    from ep133_mcp import server as module

    bad = module.fetch_reference('https://soundcloud.com/x')
    assert bad['error'] == 'InvalidReference' and 'next_step' in bad
    src = tone(tmp_path, rate=44100, channels=1, seconds=0.25)
    assert module.fetch_reference(str(src))['status'] == 'fetched'
    status = module.server_status()
    assert set(status['audio']) == {'extra_installed', 'missing_modules', 'ffmpeg', 'yt_dlp', 'install'}
