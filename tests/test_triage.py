import pytest
import tstriage.epgstation

def test_GetKeywords():
    epgStation = tstriage.epgstation.EPGStation(url='http://acepc-gk3.local:8888')
    keywords = epgStation.GetKeywords()
    assert len(keywords) > 0
