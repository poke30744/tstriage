import json
import time
from pathlib import Path

import pytest

from tstriage.epg import EPG

RECORDING = '2026年09月21日12時30分00秒-【連続テレビ小説】ひよっこ（３４）「響け若人のうた」[解][字]'
START_AT = int(time.mktime(time.strptime('20260921123000', '%Y%m%d%H%M%S'))) * 1000


def _epg(tmpPath: Path, items: list) -> EPG:
    epgPath = tmpPath / f'{RECORDING}.epg'
    epgPath.write_text(json.dumps(items, ensure_ascii=False), encoding='utf-8')
    return EPG(epgPath, 1024)


def test_info_matches_by_name(tmp_path):
    items = [{'serviceId': 1024, 'startAt': 0,
              'name': '【連続テレビ小説】ひよっこ（３４）「響け若人のうた」🈖🈑',
              'description': '乙女寮に来た綿引'}]
    info = _epg(tmp_path, items).Info()
    assert info['description'] == '乙女寮に来た綿引'


def test_info_falls_back_to_start_time(tmp_path):
    items = [
        {'serviceId': 1025, 'name': 'ニュース', 'startAt': START_AT},
        {'serviceId': 1024, 'name': 'ニュース', 'startAt': START_AT - 3600 * 1000},
        {'serviceId': 1024, 'name': 'ニュース', 'startAt': START_AT, 'description': '本編'},
    ]
    info = _epg(tmp_path, items).Info()
    assert info['name'] == 'ニュース'
    assert info['description'] == '本編'


def test_info_raises_when_nothing_matches(tmp_path):
    items = [{'serviceId': 1024, 'name': '別の番組', 'startAt': START_AT + 3600 * 1000}]
    with pytest.raises(RuntimeError, match='is invalid!'):
        _epg(tmp_path, items).Info()
