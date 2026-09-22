import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tstriage.runner import Runner

CONFIG = {
    'Encoder': {},
    'Presets': {},
    'EPGStation': 'http://localhost:1',
}


def _runner(tmpPath: Path) -> Runner:
    configuration = CONFIG | {'Uncategoried': str(tmpPath), 'Destination': str(tmpPath)}
    return Runner(configuration, quiet=True)


def _actionItem(tmpPath: Path, stem: str, suffix: str) -> Path:
    """Create an action item the way CreateActionItem does: <stem>.<suffix> for recording <stem>.m2ts."""
    (tmpPath / '_tstriage').mkdir(exist_ok=True)
    (tmpPath / 'dest').mkdir(exist_ok=True)
    path = tmpPath / '_tstriage' / f'{stem}.{suffix}'
    path.write_text(json.dumps({'path': f'{stem}.m2ts', 'destination': 'dest'}), encoding='utf-8')
    return path


def test_analyze_continues_after_a_failed_item(tmp_path):
    runner = _runner(tmp_path)
    for stem in ('a', 'b'):
        _actionItem(tmp_path, stem, 'toanalyze')

    analyzed = []

    def fakeAnalyze(item, epgStation, quiet, progress=None):
        if item['path'].endswith('a.m2ts'):
            raise RuntimeError('boom')
        analyzed.append(Path(item['path']).name)

    with patch('tstriage.runner.Analyze', side_effect=fakeAnalyze):
        with pytest.raises(RuntimeError, match='analyze failed for: a'):
            runner.Analyze()

    assert analyzed == ['b.m2ts']                        # the later item was still processed
    assert not (tmp_path / '_tstriage' / 'b.toanalyze').exists()
    assert (tmp_path / '_tstriage' / 'b.tomark').exists()
    assert (tmp_path / '_tstriage' / 'a.toanalyze.error').exists()
