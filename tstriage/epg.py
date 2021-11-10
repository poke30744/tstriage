import os, subprocess, json, unicodedata, time, argparse, re
from pathlib import Path
import logging
import yaml
from tscutter.common import TsFileNotFound, InvalidTsFormat, CheckExtenralCommand

logger = logging.getLogger('tstriage.epg')

def Dump(videoPath, quiet=False):
    if os.name == 'nt':
        CheckExtenralCommand('mirakurun-epgdump.cmd')
    else:
        CheckExtenralCommand('mirakurun-epgdump')
    with (Path(__file__).parent / 'channels.yml').open(encoding='utf-8') as f:        
        channels = yaml.load(f, Loader=yaml.FullLoader)
    videoPath = Path(videoPath)
    if not videoPath.is_file():
        raise TsFileNotFound(f'"{videoPath.name}" not found!')
    videoPath = Path(videoPath)
    epgPath = videoPath.with_suffix('.epg')
    txtPath = videoPath.with_suffix('.txt')
    if not epgPath.exists():
        if os.name == 'nt':
            pipeObj = subprocess.Popen(f'mirakurun-epgdump.cmd "{videoPath}" "{epgPath}"')
        else:
            pipeObj = subprocess.Popen(['mirakurun-epgdump', videoPath, epgPath])
        pipeObj.wait()
    else:
        logger.warning(f'skipping {str(videoPath)} ...')
    info = {}
    with epgPath.open(encoding='utf-8') as f:
        epg = json.load(f)
        for item in epg:
            name = item.get('name')
            if name:
                name = unicodedata.normalize('NFKC', name)
                #name = name.replace(chr(8217), "'")
                videoName = unicodedata.normalize('NFKC', videoPath.stem)
                if name in videoName or re.sub(r"\[.*?\]", "", name) in videoName:
                    for k in item:
                        info[k] = item[k]
    if info == {}:
        raise InvalidTsFormat(f'"{videoPath.name}" is invalid!')
    with txtPath.open('w', encoding='utf8') as f:
        print(info['name'], file=f)
        print('', file=f)
        print(info['description'], file=f)
        print('', file=f)
        if 'extended' in info:
            for k in info['extended']:
                print(k, file=f)
                print(info['extended'][k], file=f)
        print('', file=f)
        for item in channels:
            if item.get('serviceId') == info['serviceId']:
                print(f'{item["name"]}', file=f)
                break
        print(f'serviceId: {info["serviceId"]}', file=f)
        print(f"{time.strftime('%Y-%m-%d %H:%M (%a)', time.localtime(info['startAt'] / 1000))} ~ {round(info['duration'] / 1000 / 60)} mins", file=f)
    return epgPath, txtPath

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Dump EPG from TS files')
    parser.add_argument('--quiet', '-q', action='store_true', help="don't output to the console")
    parser.add_argument('--input', '-i', required=True, help='input mpegts path')
    args = parser.parse_args()

    Dump(args.input, args.quiet)
