import os, subprocess, json, unicodedata, time, argparse, re, copy
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

class EPG:
    def Dump(videoPath):
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
            pipeObj = subprocess.Popen(f'mirakurun-epgdump.cmd "{videoPath}" "{epgPath}"')
        else:
            pipeObj = subprocess.Popen(['mirakurun-epgdump', videoPath, epgPath])
        pipeObj.wait()
        
    def __init__(self, path: Path, inputFile: InputFile, channels: dict=None) -> None:
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
                name = unicodedata.normalize('NFKC', name)
                #name = name.replace(chr(8217), "'")
                videoName = unicodedata.normalize('NFKC', self.path.stem)
                if (name in videoName or re.sub(r"\[.*?\]", "", name) in videoName) and item.get('serviceId') == self.ServiceId():
                    for k in item:
                        info[k] = item[k]
        if info == {}:
            raise InvalidTsFormat(f'"{self.path.name}" is invalid!')
        self.info = info
        return self.info

    def ServiceId(self) -> str:
        try:
            return self.__serviceId
        except AttributeError:
            self.__serviceId = self.inputFile.GetInfo()['serviceId']
            return self.__serviceId

    def Channel(self) -> str:
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
