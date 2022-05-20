import os, subprocess, json, unicodedata, time, argparse, re
from pathlib import Path
import logging
import yaml
from tscutter.common import TsFileNotFound, InvalidTsFormat, CheckExtenralCommand
from tscutter.ffmpeg import InputFile

logger = logging.getLogger('tstriage.epg')

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
        return self.inputFile.GetInfo()['serviceId']
    
    def Channel(self) -> str:
        if self.channels is None:
            with (Path(__file__).parent / 'channels.yml').open(encoding='utf-8') as f:        
                self.channels = yaml.load(f, Loader=yaml.FullLoader)
        for item in self.channels:
            if item.get('serviceId') == self.ServiceId():
                return item['name']

    def OutputDesc(self, txtPath: Path) -> None:
        with txtPath.open('w', encoding='utf8') as f:
            print(self.Info()['name'], file=f)
            print('', file=f)
            print(self.Info()['description'], file=f)
            print('', file=f)
            if 'extended' in self.Info():
                for k in self.Info()['extended']:
                    print(k, file=f)
                    print(self.Info()['extended'][k], file=f)
            print('', file=f)
            print(f'Channel: {self.Channel()}', file=f)
            print(f'serviceId: {self.ServiceId()}', file=f)
            print(f"{time.strftime('%Y-%m-%d %H:%M (%a)', time.localtime(self.Info()['startAt'] / 1000))} ~ {round(self.Info()['duration'] / 1000 / 60)} mins", file=f)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Dump EPG from TS files')
    parser.add_argument('--input', '-i', required=True, help='input mpegts path')
    args = parser.parse_args()

    EPG.Dump(args.input)
