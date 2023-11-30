from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import urllib.request, urllib.parse, shutil, json, time, logging, argparse, io

logger = logging.getLogger('tstriage.epgstation')

class EPGStation:
    def __init__(self, url: str, recorded: Optional[Path]=None):
        self.url = url
    
    def GetChannels(self) -> dict:
        with urllib.request.urlopen(f'{self.url}/api/channels') as response:
            channels = json.load(response)
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
    epg = epgStation.GetEPG(path=args.input)
    channels = epgStation.GetChannels()
    print(epgStation.GenerateDescription(epg, channels))