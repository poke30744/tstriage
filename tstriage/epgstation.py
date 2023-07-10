from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import urllib.request, urllib.parse, shutil, json, time, logging, argparse, io

logger = logging.getLogger('tstriage.epgstation')

class EPGStation:
    def __init__(self, url: str, cache: Optional[Path]=None, recorded: Optional[Path]=None):
        self.url = url
        self.reservesJsonPath = Path('reserves.json') if cache is None else Path(cache).expanduser() / 'reserves.json'
        self.channelsJsonPath = Path('channels.json') if cache is None else Path(cache).expanduser() / 'channels.json'
        self._notBusyTill = None
        self.recorded = recorded
    
    def LoadReservesList(self) -> dict:
        if self.reservesJsonPath.exists():
            lastModifiedTime = datetime.fromtimestamp(self.reservesJsonPath.stat().st_mtime)
            if datetime.now() - lastModifiedTime > timedelta(hours=8):
                self.reservesJsonPath.unlink()
        if not self.reservesJsonPath.exists():
            with urllib.request.urlopen(f'{self.url}/api/reserves?isHalfWidth=true') as response:
                with self.reservesJsonPath.open('wb') as f:
                    shutil.copyfileobj(response, f)
        with self.reservesJsonPath.open(encoding='utf-8') as f:
            return json.load(f)['reserves']

    def IsBusy(self, at=None, duration=None) -> bool:
        at = datetime.now() if at is None else at
        duration = timedelta(minutes=30) if duration is None else duration    
        for item in self.LoadReservesList():
            if not item['isOverlap'] and not item['isSkip']:
                startAt = datetime.fromtimestamp(item['startAt'] / 1000)
                endAt = datetime.fromtimestamp(item['endAt'] / 1000)
                if startAt <= at <= endAt or startAt <= (at + duration) <= endAt:
                    return True
        return False
    
    def BusyWait(self, granularity=30) -> None:
        if self._notBusyTill is None or self._notBusyTill < datetime.now():
            duration = granularity * 2
            while self.IsBusy(duration=timedelta(seconds=duration)):
                time.sleep(duration)
            self._notBusyTill = datetime.now() + timedelta(seconds=granularity)

    def GetChannels(self) -> dict:
        if self.channelsJsonPath.exists():
            with self.channelsJsonPath.open(encoding='utf8') as f:
                return json.load(f)
        else:
            with urllib.request.urlopen(f'{self.url}/api/channels') as response:
                channels = json.load(response)
            with self.channelsJsonPath.open('w', encoding='utf8') as f:
                json.dump(channels, f)
            return channels

    def GetEPG(self, path, limit=24) -> Optional[dict]:
        hyphenPos = path.stem.find('-')
        keyword = path.stem[hyphenPos+1 :]
        with urllib.request.urlopen(f'{self.url}/api/recorded?isHalfWidth=true&limit={limit}&keyword={urllib.parse.quote(keyword)}') as response:
            recorded = json.load(response)
            for epg in recorded['records']:
                filename = urllib.parse.unquote(epg['videoFiles'][0]['filename'])
                if Path(filename).stem in Path(path).stem:
                    return epg
    
    def GenerateDescription(self, epg, channels) -> str:
        with io.StringIO() as f:
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
            duration: float = epg['endAt'] - epg['startAt'] 
            print(f"{time.strftime('%Y-%m-%d %H:%M (%a)', time.localtime(epg['startAt'] / 1000))} ~ {round(duration / 1000 / 60)} mins", file=f)
            return f.getvalue()

    def GetKeywords(self) -> list[str]:
        keywords = []
        with urllib.request.urlopen(f'{self.url}/api/rules?offset=0&limit=99&type=normal&isHalfWidth=true') as response:
            rules = json.load(response)
            for rule in rules['rules']:
                searchOption = rule['searchOption']
                keyword = searchOption['keyword']
                keywords.append(keyword)
        return keywords

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='EPGStation client')
    subparsers = parser.add_subparsers(required=True, title='subcommands', dest='command')

    subparser = subparsers.add_parser('status', help='get server status')
    subparser.add_argument('--server', '-s', required=True, help='EPGStation server URL')

    subparser = subparsers.add_parser('info', help='get EPG information of the recorded mpegts file')
    subparser.add_argument('--server', '-s', required=True, help='EPGStation server URL')
    subparser.add_argument('--input', '-i', required=True, help='input mpegts path')
    
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    epgStation = EPGStation(url=args.server)
    if args.command == 'status':
        print(f'EPGStation busy status: {epgStation.IsBusy()}')
    elif args.command == 'info':
        epg = epgStation.GetEPG(path=args.input)
        channels = epgStation.GetChannels()
        print(epgStation.GenerateDescription(epg, channels))