"""Offline selection is shared by both replay CLIs and lists without reading."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from mcp_toolcall_lab import jev_shim, prompt_experiment
from mcp_toolcall_lab.fixture_selection import fixture_paths


def test_filename_stem_ors_exclusions_and_symlinks(tmp_path):
    for name in ('first.json', 'second.json', 'other.txt'):
        (tmp_path / name).write_text('not JSON')
    assert [p.name for p in fixture_paths(tmp_path, patterns=('first', '*.json', 'first*'), exclude=('second',))] == ['first.json']
    with pytest.raises(FileNotFoundError):
        fixture_paths(tmp_path, patterns=('FIRST',))
    (tmp_path / 'directory.json').mkdir()
    try:
        (tmp_path / 'link.json').symlink_to(tmp_path / 'first.json')
    except (OSError, NotImplementedError):
        pass
    assert [p.name for p in fixture_paths(tmp_path)] == ['first.json', 'second.json']


@pytest.mark.parametrize('module', [jev_shim, prompt_experiment])
def test_list_does_not_read_replay_or_touch_output(tmp_path, monkeypatch, capsys, module):
    (tmp_path / 'selected.json').write_text('invalid JSON')
    def forbidden(*args, **kwargs):
        raise AssertionError('list must not read or replay')
    monkeypatch.setattr(Path, 'read_text', forbidden)
    monkeypatch.setattr(module, 'load_case', forbidden)
    monkeypatch.setattr(module, 'replay_case', forbidden)
    args = ['replay', '--fixtures', str(tmp_path), '--case', 'selected', '--list']
    assert module.main(args) == 0
    assert json.loads(capsys.readouterr().out) == {'cases': ['selected.json']}
    output = tmp_path / 'out.jsonl'
    output.write_bytes(b'keep existing audit')
    assert module.main(args + ['--out', str(output)]) == 2
    assert output.read_bytes() == b'keep existing audit'


@pytest.mark.parametrize('module', [jev_shim, prompt_experiment])
def test_replay_selection_preserves_existing_case_results_and_errors(tmp_path, capsys, module):
    all_cases = module.replay_fixtures()
    first = sorted(module.DEFAULT_FIXTURE_DIR.glob('*.json'))[0]
    selected = module.replay_fixtures(patterns=(first.stem,))
    assert len(selected) == 1 and selected[0] in all_cases
    (tmp_path / 'broken.json').write_text('invalid JSON')
    for options in (['--case', 'missing'], ['--case', ''], []):
        assert module.main(['replay', '--fixtures', str(tmp_path)] + options) == 2
        assert capsys.readouterr().out == ''


@pytest.mark.parametrize('module', ['jev_shim', 'prompt_experiment'])
def test_standalone_list_without_site_packages(tmp_path, module):
    (tmp_path / 'sample.json').write_text('not JSON')
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    result = subprocess.run([sys.executable, '-S', '-m', 'mcp_toolcall_lab.' + module, 'replay',
                             '--fixtures', str(tmp_path), '--case', 'sample', '--list'],
                            env=env, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['cases'] == ['sample.json']


@pytest.mark.parametrize('options,error', [({'patterns': '*'}, TypeError), ({'patterns': ('',)}, ValueError),
                                         ({'exclude': (1,)}, ValueError)])
def test_invalid_rules_are_rejected_on_empty_root(tmp_path, options, error):
    with pytest.raises(error):
        fixture_paths(tmp_path, **options)
