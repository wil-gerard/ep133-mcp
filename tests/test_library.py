import json
import zipfile

import pytest

from ep133_mcp.protocol.projects import pack_project, read_pak
from ep133_mcp.safety.library import export_project, list_samples
from ep133_mcp.safety.errors import InvalidDestination
from test_pack_project import minimal_project
from test_import_project import wav


def backup(tmp_path, missing=False):
    path = tmp_path / 'source.pak'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('/meta.json', json.dumps({'device_version': '2.5.1'}))
        for p in range(1, 10):
            z.writestr(f'/projects/P{p:02}.tar', pack_project(minimal_project(pad7_slot=16 if p == 7 else 0)))
        if not missing:
            z.writestr('/sounds/016 tone.wav', wav())
        z.writestr('/sounds/099 unused.wav', wav(20))
    return path


def test_export_preserves_bytes_and_only_dependencies(tmp_path):
    source = backup(tmp_path)
    out = tmp_path / 'project.ppak'
    report = export_project(source, 7, out)
    meta, projects, sounds = read_pak(out.read_bytes())
    original = read_pak(source.read_bytes())
    assert meta['pak_type'] == 'project'
    assert projects == {7: original[1][7]}
    assert sounds == {'/sounds/016 tone.wav': original[2]['/sounds/016 tone.wav']}
    assert report['included_slots'] == [16]
    with pytest.raises(FileExistsError):
        export_project(source, 7, out)


def test_missing_dependency_refused_and_inventory_retains_stale(tmp_path):
    source = backup(tmp_path, missing=True)
    with pytest.raises(InvalidDestination):
        export_project(source, 7, tmp_path / 'bad.ppak')
    assert not (tmp_path / 'bad.ppak').exists()
    report = list_samples(source=source)
    assert report['unreferenced_slots'] == [99]
    assert report['stale_references'][16][0]['project'] == 7


def test_inventory_counts_all_stored_references(tmp_path):
    report = list_samples(source=backup(tmp_path))
    assert report['count'] == 2
    assert report['samples'][0]['references'][0]['pad'] == 7
    assert report['samples'][0]['pcm_bytes'] == 200
