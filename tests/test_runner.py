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


@pytest.mark.parametrize('task, suffix, nextSuffix', [
    ('Encode', 'toencode', 'toindex'),
    ('Index', 'toindex', 'tomark'),
    ('Mark', 'tomark', 'tocut'),
    ('Cut', 'tocut', 'toconfirm'),
])
def test_task_continues_after_a_failed_item(tmp_path, task, suffix, nextSuffix):
    runner = _runner(tmp_path)
    for stem in ('a', 'b'):
        _actionItem(tmp_path, stem, suffix)

    processed = []

    def fakeTask(item, **kwargs):
        if item['path'].endswith('a.m2ts'):
            raise RuntimeError('boom')
        processed.append(Path(item['path']).name)

    with patch(f'tstriage.runner.{task}', side_effect=fakeTask):
        with pytest.raises(RuntimeError, match=f'{task.lower()} failed for: a'):
            getattr(runner, task)()

    assert processed == ['b.m2ts']                        # the later item was still processed
    assert not (tmp_path / '_tstriage' / f'b.{suffix}').exists()
    assert (tmp_path / '_tstriage' / f'b.{nextSuffix}').exists()
    assert (tmp_path / '_tstriage' / f'a.{suffix}.error').exists()
