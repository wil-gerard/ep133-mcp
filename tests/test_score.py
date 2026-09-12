"""score_pattern against the owner's hand-played d14 (fixtures/p03-hand-played-2026-09-12.json)."""
import json
from pathlib import Path

import pytest

from ep133_mcp.audio import groove
from ep133_mcp.audio.score import score_pattern

FIXTURE = Path(__file__).parent.parent / 'docs' / 'research' / 'fixtures' / 'p03-hand-played-2026-09-12.json'
CLIP = Path.home() / '.local' / 'state' / 'ep133-mcp' / 'references' / '9a6267822f49412d' / 'clip.wav'


@pytest.fixture(scope='module')
def d14():
    return json.loads(FIXTURE.read_text())['patterns']['d14']


def events(bars, *hits):
    return {'bars': bars, 'events': [{'pad': pad, 'tick': tick} for pad, tick in hits]}


def test_identity_is_perfect(d14):
    score = score_pattern(d14, d14)
    assert score['recall'] == score['precision'] == 1.0 and score['accepted']
    assert score['pads']['1']['truth'] == 46 and score['pads']['2']['truth'] == 23 and score['pads']['3']['truth'] == 8


def test_matches_within_tolerance_and_wraps():
    truth = events(1, (1, 0), (1, 96), (2, 380))
    got = events(1, (1, 12), (1, 96 + 13), (2, 4))
    score = score_pattern(got, truth)
    assert score['pads']['1'] == {'truth': 2, 'detected': 2, 'matched': 1, 'precision': 0.5, 'recall': 0.5,
                                  'ratio': 1.0, 'mean_offset_ticks': 12.0}
    assert score['pads']['2']['matched'] == 1 and score['pads']['2']['mean_offset_ticks'] == 8.0
    assert score['matched'] == 2 and not score['accepted']


def test_each_hit_matches_once():
    score = score_pattern(events(1, (1, 10)), events(1, (1, 0), (1, 20)))
    assert score['pads']['1']['matched'] == 1


def test_candidate_tiles_and_folds_to_truth_length():
    truth = events(2, (1, 0), (1, 384))
    assert score_pattern(events(1, (1, 0)), truth)['recall'] == 1.0
    folded = score_pattern(events(4, (1, 0), (1, 384), (1, 768), (1, 1152)), truth)
    assert folded['pads']['1'] == {'truth': 2, 'detected': 4, 'matched': 2, 'precision': 0.5, 'recall': 1.0,
                                   'ratio': 2.0, 'mean_offset_ticks': 0.0}
    assert not folded['accepted']


def test_pad_only_one_side_plays():
    score = score_pattern(events(1, (2, 0)), events(1, (1, 0)))
    assert score['pads']['1']['recall'] == 0.0 and score['pads']['2']['precision'] == 0.0
    assert score['pads']['2']['ratio'] == float('inf') and score['recall'] == 0.0


@pytest.mark.skipif(not CLIP.exists(), reason='reference clip cache 9a6267822f49412d is not on this machine')
def test_bands_transcription_of_the_reference(d14):
    """The clip the owner played d14 over (docs/handoff/session-2026-09-12-velocity-handoff.md, step 1).
    Roles: the kit's 'kick' slice sounds like a hat and its 'snare' slice like a kick, and the owner
    played them that way; the four-on-the-floor must land on pad 2."""
    record = groove.transcribe_groove(CLIP, detector='bands')
    assert {p['pad']: p['role'] for p in record['pads']} == {1: 'hat', 2: 'kick', 3: 'snare'}
    assert record['quantization']['folds'] == 4
    score = score_pattern(record['tick_pattern'], d14)
    assert score['pads']['2']['recall'] >= 0.65 and score['pads']['2']['precision'] == 1.0
    assert score['recall'] >= 0.7 and all(p['ratio'] <= 1.5 for p in score['pads'].values())
