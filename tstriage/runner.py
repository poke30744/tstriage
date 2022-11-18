#!/usr/bin/env python3
import argparse, json, time, os
from pathlib import Path
import logging
import unicodedata
import yaml
from tqdm import tqdm
from .common import WindowsInhibitor
from .epgstation import EPGStation
from .tasks import Analyze, Mark, Cut, Encode, Confirm, Cleanup
from .nas import NAS

logger = logging.getLogger('tstriage.runner')

class Runner:
    def __init__(self, configuration):
        self.configuration = configuration
        self.cache = Path(configuration['Cache']).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)
        if 'Path' in configuration:
            for key in configuration['Path']:
                pathToAdd = configuration["Path"][key]
                os.environ['PATH'] = f'{os.environ["PATH"]};{pathToAdd}'
        if 'Encoder' in configuration:
            self.encoder = configuration['Encoder']
        else:
            self.encoder = 'h264'
        self.epgStation = EPGStation(url=configuration['EPGStation'], cache=configuration['Cache'], recorded=configuration['Uncategoried']) if 'EPGStation' in configuration else None
        self.nas = NAS(
            recorded=Path(self.configuration['Uncategoried']),
            destination=Path(configuration['Destination']),
            epgStation=self.epgStation)

    def Categorize(self):
        for path in tqdm(self.nas.RecordedFiles(), desc="Categorizing"):
            if self.nas.HadBeenEncoded(path) or self.nas.HasActionItem(path):
                continue
            destination = None
            for keyword in reversed(sorted(self.epgStation.GetKeywords())):
                if unicodedata.normalize('NFKC', keyword) in unicodedata.normalize('NFKC', path.stem):
                    epg = self.epgStation.GetEPG(path)
                    if epg is None:
                        break
                    with (Path(__file__).parent / 'event.yml').open(encoding='utf-8') as f:        
                        eventDesc = yaml.load(f, Loader=yaml.FullLoader)
                    genreDesc = eventDesc['Genre'][str(epg['genre1'])]
                    destination = self.nas.destination / genreDesc / keyword
                    break
            item = {
                'path': str(path),
                'destination': str(destination),
            }
            self.nas.CreateActionItem(item, '.categorized')
    
    def List(self):
        for path in self.nas.ActionItems('.categorized'):
            item = self.nas.LoadActionItem(path)
            encodeTo = item["destination"]
            if encodeTo != 'None':
                with self.nas.FindTsTriageSettings(folder=Path(encodeTo)).open() as f:
                    settings = json.load(f)
                path.unlink()
                newItem = {
                    'path': item['path'],
                    'destination': item['destination'],
                    'cache': str(self.cache),
                    'cutter': settings.get('cutter', {}),
                    'marker': settings.get('marker', {}),
                    'encoder': settings.get('encoder', {})
                }
                self.nas.CreateActionItem(newItem, '.toanalyze')
                logger.info(f'Will process: {item["path"]}')
            else:
                logger.warn(f'More information is needed: {item["path"]}')

    def Analyze(self):
        for path in self.nas.ActionItems('.toanalyze'):
            item = self.nas.LoadActionItem(path)
            try:
                Analyze(item=item, epgStation=self.epgStation)
                path.unlink()
                self.nas.CreateActionItem(item, '.tomark')
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in analyzing "{path}":')
                path.rename(path.with_suffix('.error'))

    def Mark(self):
        for path in self.nas.ActionItems('.tomark'):
            item = self.nas.LoadActionItem(path)
            try:
                Mark(item=item, epgStation=self.epgStation)
                path.unlink()
                self.nas.CreateActionItem(item, '.tocut')
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in marking "{path}":')
                path.rename(path.with_suffix('.error'))

    def Cut(self):
        for path in self.nas.ActionItems('.tocut'):
            item = self.nas.LoadActionItem(path)
            try:
                Cut(item=item, epgStation=self.epgStation)
                path.unlink()
                self.nas.CreateActionItem(item, '.toencode')
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in cutting "{path}":')
                path.rename(path.with_suffix('.error'))

    def Encode(self):
        for path in self.nas.ActionItems('.toencode'):
            item = self.nas.LoadActionItem(path)
            try:
                encodedFile = Encode(item=item, encoder=self.encoder, epgStation=self.epgStation)
                path.unlink()
                self.nas.CreateActionItem(item, '.toconfirm')
                self.nas.AddEncodedFile(encodedFile)
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in marking "{path}":')
                path.rename(path.with_suffix('.error'))

    def Confirm(self):
        for path in self.nas.ActionItems('.toencode') + self.nas.ActionItems('.toconfirm') + self.nas.ActionItems('.tocleanup'):
            item = self.nas.LoadActionItem(path)
            reEncodingNeeded = Confirm(item=item)
            path.unlink()
            if reEncodingNeeded or path.suffix == '.toencode':
                self.nas.CreateActionItem(item, '.toencode')
            else:
                self.nas.CreateActionItem(item, '.tocleanup')

    def Cleanup(self):
        for path in self.nas.ActionItems('.tocleanup'):
            item = self.nas.LoadActionItem(path)
            Cleanup(item=item)
            path.unlink()
    
    def Run(self, tasks):
        logger.info(f'running {tasks} ...')
        for task in tasks:
            if task == 'categorize':
                self.Categorize()
            elif task == 'list':
                self.List()
            elif task == 'analyze':
                with WindowsInhibitor():
                    self.Analyze()
            elif task == 'mark':
                with WindowsInhibitor():
                    self.Mark()
            elif task == 'cut':
                    self.Cut()
            elif task == 'encode':
                with WindowsInhibitor():
                    self.Encode()
            elif task == 'confirm':
                self.Confirm()
            elif task == 'cleanup':
                self.Cleanup()

def main():
    parser = argparse.ArgumentParser(description='Python script to triage TS files')
    parser.add_argument('--config', '-c', required=True, help='configuration file path')
    parser.add_argument('--task', '-t', required=True, nargs='+', choices=['categorize', 'list', 'analyze', 'mark', 'cut', 'confirm', 'encode', 'cleanup'], help='tasks to run')
    parser.add_argument('--daemon', '-d', type=int, help='keep running')

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    configurationPath = Path(args.config)
    with configurationPath.open(encoding='utf-8') as f:
        configuration = json.load(f)
    
    runner = Runner(configuration)

    while True:
        runner.Run(args.task)
        if args.daemon is None:
            break
        else:
            print(f'.{args.daemon}.', end="")
            time.sleep(args.daemon)

if __name__ == "__main__":
    main()