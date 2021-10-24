from pathlib import Path
from datetime import datetime, timedelta
import urllib.request, urllib.parse, shutil, json, time

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
        with self.reservesJsonPath.open(encoding='utf-8') as f:
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

    def GetChannels(self):
        with urllib.request.urlopen(f'{self.url}/api/channels') as response:
            channels = json.load(response)
            return channels

    def GetEPG(self, path, limit=24):
        with urllib.request.urlopen(f'{self.url}/api/recorded?limit={limit}&offset=0&reverse=false') as response:
            recorded = json.load(response)
            for epg in recorded['recorded']:
                filename = urllib.parse.unquote(epg['filename'])
                if Path(filename).stem in Path(path).stem:
                    return epg
    
    def GenerateDescription(self, epg, channels, txtPath):
        with Path(txtPath).open('w', encoding='utf8') as f:
            print(epg['name'], file=f)
            print('', file=f)
            print(epg['description'], file=f)
            print('', file=f)
            print(epg['extended'], file=f)        
            print('', file=f)
            for item in channels:
                if item.get('id') == epg['channelId']:
                    print(f'{item["name"]}', file=f)
                    break
            duration = epg['endAt'] - epg['startAt'] 
            print(f"{time.strftime('%Y-%m-%d %H:%M (%a)', time.localtime(epg['startAt'] / 1000))} ~ {round(duration / 1000 / 60)} mins", file=f)
