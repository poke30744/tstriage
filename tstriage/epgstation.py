from pathlib import Path
from datetime import datetime, timedelta
import urllib.request, shutil, json, time

class EPGStation:
    def __init__(self, url, cache=None):
        self.url = url
        self.reservesJsonPath = Path('reserves.json') if cache is None else Path(cache).expanduser() / 'reserves.json'
        self._notBusyTill = None
    
    def LoadReservesList(self):
        if self.reservesJsonPath.exists():
            lastModifiedTime = datetime.fromtimestamp(self.reservesJsonPath.stat().st_mtime)
            if datetime.now() - lastModifiedTime > timedelta(hours=8):
                self.reservesJsonPath.unlink()
        if not self.reservesJsonPath.exists():
            with urllib.request.urlopen(f'{self.url}/api/reserves') as response:
                with self.reservesJsonPath.open('wb') as f:
                    shutil.copyfileobj(response, f)
        with self.reservesJsonPath.open() as f:
            return json.load(f)['reserves']

    def IsBusy(self, at=None, duration=None):
        at = datetime.now() if at is None else at
        duration = timedelta(minutes=30) if duration is None else duration    
        for item in self.LoadReservesList():
            startAt = datetime.fromtimestamp(item['program']['startAt'] / 1000)
            endAt = datetime.fromtimestamp(item['program']['endAt'] / 1000)
            if startAt <= at <= endAt or startAt <= (at + duration) <= endAt:
                return True
        return False
    
    def BusyWait(self, granularity=30):
        if self._notBusyTill is None or self._notBusyTill < datetime.now():
            duration = granularity * 2
            while self.IsBusy(duration=timedelta(seconds=duration)):
                time.sleep(duration)
            self._notBusyTill = datetime.now() + timedelta(seconds=granularity)