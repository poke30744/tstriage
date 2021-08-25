import pytest
import tstriage.epgstation
from datetime import datetime, timedelta

def test_IsEPGStationStatusBusy():
    epgStation = tstriage.epgstation.EPGStation(url='http://raspberrypi4.local:8888')
    busy = epgStation.IsBusy()
    busy = epgStation.IsBusy(datetime.now() + timedelta(hours=1))
    busy = epgStation.IsBusy(datetime.now(), timedelta(hours=1))
    pass