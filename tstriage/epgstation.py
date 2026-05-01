from pathlib import Path
from typing import Optional
import urllib.request, urllib.parse, json, logging

logger = logging.getLogger('tstriage.epgstation')

class EPGStation:
    def __init__(self, url: str):
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
    
    def GetKeywords(self) -> list[str]:
        keywords = []
        with urllib.request.urlopen(f'{self.url}/api/rules?offset=0&limit=99&type=normal&isHalfWidth=true') as response:
            rules = json.load(response)
            for rule in rules['rules']:
                searchOption = rule['searchOption']
                keyword = searchOption['keyword']
                keywords.append(keyword)
        return keywords
