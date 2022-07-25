import pytest
import tstriage.epgstation
from datetime import datetime, timedelta

def test_IsEPGStationStatusBusy():
    epgStation = tstriage.epgstation.EPGStation(url='http://acepc-gk3.local:8888')
    busy1 = epgStation.IsBusy()
    busy2 = epgStation.IsBusy(datetime.now() + timedelta(hours=1))
    busy3 = epgStation.IsBusy(datetime.now(), timedelta(hours=1))
    pass

def test_GetKeywords():
    epgStation = tstriage.epgstation.EPGStation(url='http://acepc-gk3.local:8888')
    keywords = epgStation.GetKeywords()
    assert len(keywords) > 0
