from functools import cache
import os, subprocess, json, unicodedata, time, argparse, re, copy
from typing import Optional
from pathlib import Path
import logging
import yaml
from tscutter.common import TsFileNotFound, InvalidTsFormat, CheckExtenralCommand
from tscutter.ffmpeg import InputFile

logger = logging.getLogger('tstriage.epg')

def represent_str(dumper, instance):
    if "\n" in instance:
        return dumper.represent_scalar('tag:yaml.org,2002:str', instance, style='|')
    else:
        return dumper.represent_scalar('tag:yaml.org,2002:str', instance)
yaml.add_representer(str, represent_str)

enclosed_characters_convert_table = {
    '\U0001F14A': '[HV]',
    '\U0001F13F': '[P]',
    '\U0001F14C': '[SD]',
    '\U0001F146': '[W]',
    '\U0001F14B': '[MV]',
    '\U0001F210': '[手]',
    '\U0001F211': '[字]',
    '\U0001F212': '[双]',
    '\U0001F213': '[デ]',
    '\U0001F142': '[S]',
    '\U0001F214': '[二]',
    '\U0001F215': '[多]',
    '\U0001F216': '[解]',
    '\U0001F14D': '[SS]',
    '\U0001F131': '[B]',
    '\U0001F13D': '[N]',
    '\U0001F217': '[天]',
    '\U0001F218': '[交]',
    '\U0001F219': '[映]',
    '\U0001F21A': '[無]',
    '\U0001F21B': '[料]',
    '\U000026BF': '[鍵]',
    '\U0001F21C': '[前]',
    '\U0001F21D': '[後]',
    '\U0001F21E': '[再]',
    '\U0001F21F': '[新]',
    '\U0001F220': '[初]',
    '\U0001F221': '[終]',
    '\U0001F222': '[生]',
    '\U0001F223': '[販]',
    '\U0001F224': '[声]',
    '\U0001F225': '[吹]',
    '\U0001F14E': '[PPV]',
    '\U00003299': '[秘]',
    '\U0001F200': '[ほか]',
}

class EPG:
    @staticmethod
    def Dump(videoPath, quiet: bool=False):
        if os.name == 'nt':
            CheckExtenralCommand('mirakurun-epgdump.cmd')
        else:
            CheckExtenralCommand('mirakurun-epgdump')
        videoPath = Path(videoPath)
        if not videoPath.is_file():
            raise TsFileNotFound(f'"{videoPath.name}" not found!')
        videoPath = Path(videoPath)
        epgPath = videoPath.with_suffix('.epg')
        if os.name == 'nt':
            dumpCmd = f'mirakurun-epgdump.cmd "{videoPath}" "{epgPath}"'
        else:
            dumpCmd = ['mirakurun-epgdump', videoPath, epgPath]
        if quiet:
            subprocess.run(dumpCmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(dumpCmd)

    def __init__(self, path: Path, inputFile: InputFile, channels: Optional[dict]=None) -> None:
        self.path = path
        self.inputFile = inputFile
        with self.path.open(encoding='utf-8') as f:
            self.epg = json.load(f)
        self.channels = channels
    
    def Info(self) -> dict:
        if hasattr(self, 'info'):
            return self.info
        info = {}
        for item in self.epg:
            name = item.get('name')
            if name:
                for k,v in enclosed_characters_convert_table.items():
                    name = name.replace(k, v)
                name = unicodedata.normalize('NFKC', name)
                #name = name.replace('(', '[')
                #name = name.replace(')', ']')
                #name = name.replace(chr(8217), "'")
                videoName = unicodedata.normalize('NFKC', self.path.stem)
                if (name in videoName or re.sub(r"\[.*?\]", "", name) in videoName) and item.get('serviceId') == self.ServiceId():
                    for k in item:
                        info[k] = item[k]
                    break
        if info == {}:
            raise InvalidTsFormat(f'"{self.path.name}" is invalid!')
        self.info = info
        return self.info

    @cache
    def ServiceId(self) -> int:
        return self.inputFile.GetInfo().serviceId

    def Channel(self) -> Optional[str]:
        if self.channels is None:
            with (Path(__file__).parent / 'channels.yml').open(encoding='utf-8') as f:        
                self.channels = yaml.load(f, Loader=yaml.FullLoader)
        for item in self.channels:
            if item.get('serviceId') == self.ServiceId():
                return item['name']

    def OutputDesc(self, txtPath: Path) -> None:
        info = self.Info()
        newInfo = {
            'name': info['name'],
            'description': info['description'],
            'extended': { k : v.replace('\r', '') for k, v in copy.deepcopy(info['extended']).items() }
        }
        for k in info.keys():
            if not k in newInfo:
                newInfo[k] = info[k]
                if k == 'serviceId':
                    newInfo['serviceId_desc'] = self.Channel()
                elif k == 'startAt':
                    newInfo['startAt_desc'] = time.strftime('%Y-%m-%d %H:%M (%a)', time.localtime(self.Info()['startAt'] / 1000))
                elif k == 'duration':
                    newInfo['duration_desc'] = f'{round(info["duration"] / 1000 / 60)} mins'
        with txtPath.open('w', encoding='utf-8') as f:
            yaml.dump(newInfo, f, encoding='utf-8', allow_unicode=True, sort_keys=False, default_flow_style=False)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Dump EPG from TS files')
    parser.add_argument('--input', '-i', required=True, help='input mpegts path')
    args = parser.parse_args()

    EPG.Dump(args.input)
