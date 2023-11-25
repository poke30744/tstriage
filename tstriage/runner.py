#!/usr/bin/env python3
import argparse, json, os, socket
from itertools import chain
from pathlib import Path
import logging
import unicodedata
import psutil
import yaml
from tqdm import tqdm
from .epgstation import EPGStation
from .tasks import Analyze, Mark, Cut, Encode, Confirm, Cleanup
from .nas import NAS

logger = logging.getLogger('tstriage.runner')

class Runner:
    def __init__(self, configuration, quiet: bool):
        self.configuration = configuration
        self.quiet = quiet
        self.cache = Path(configuration['Cache']).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)
        if 'Path' in configuration:
            for key in configuration['Path']:
                pathToAdd = configuration["Path"][key]
                os.environ['PATH'] = f'{os.environ["PATH"]};{pathToAdd}'
        self.encoder = configuration['Encoder']
        self.presets = configuration['Presets']
        self.epgStation = EPGStation(url=configuration['EPGStation'], cache=configuration['Cache'], recorded=configuration['Uncategoried'])
        self.nas = NAS(
            recorded=Path(self.configuration['Uncategoried']),
            destination=Path(configuration['Destination']))
    
    # wait for other instances to finish
    def SingleInstanceWait(self):
        allProcesses = psutil.process_iter(attrs=['pid', 'name'])
        currentProcess = psutil.Process()
        for process in allProcesses:
            if process.info['name'] == currentProcess.name and process.info['pid'] != currentProcess.pid: # type: ignore
                logger.info(f'waiting for process {process.info["pid"]} to finish ...') # type: ignore
                process.wait()

    def Categorize(self):
        for path in tqdm(self.nas.RecordedFiles(), desc="Categorizing", disable=self.quiet):
            if self.nas.HadBeenEncoded(path) or self.nas.HasActionItem(path):
                continue
            destination = None
            for keyword in sorted(self.epgStation.GetKeywords(), key=len, reverse=True):
                if unicodedata.normalize('NFKC', keyword) in unicodedata.normalize('NFKC', path.stem):
                    epg = self.epgStation.GetEPG(path)
                    if epg is None:
                        break
                    with (Path(__file__).parent / 'event.yml').open(encoding='utf-8') as f:        
                        eventDesc = yaml.load(f, Loader=yaml.FullLoader)
                        genre = epg['genre1'] if 'genre1' in epg else epg['genre2']
                    genreDesc = eventDesc['Genre'][str(genre)]
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
            path = path.rename(path.with_suffix(f'.toanalyze.{socket.gethostname()}'))
            try:
                Analyze(item=item, epgStation=self.epgStation, quiet=self.quiet)
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
            path = path.rename(path.with_suffix(f'.toanalyze.{socket.gethostname()}'))
            try:
                Mark(item=item, epgStation=self.epgStation, quiet=self.quiet)
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
            path = path.rename(path.with_suffix(f'.toanalyze.{socket.gethostname()}'))
            try:
                Cut(item=item, quiet=self.quiet)
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
            path = path.rename(path.with_suffix(f'.toanalyze.{socket.gethostname()}'))
            try:
                encodedFile = Encode(item=item, encoder=self.encoder, presets=self.presets, quiet=self.quiet)
                path.unlink()
                self.nas.CreateActionItem(item, '.toconfirm')
                self.nas.AddEncodedFile(encodedFile)
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in encoding "{path}":')
                path.rename(path.with_suffix('.error'))

    def Confirm(self):
        for path in chain(self.nas.ActionItems('.toencode'), self.nas.ActionItems('.toconfirm'), self.nas.ActionItems('.tocleanup')):
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
        self.SingleInstanceWait()

        logger.info(f'running {tasks} ...')
        for task in tasks:
            if task == 'categorize':
                self.Categorize()
            elif task == 'list':
                self.List()
            elif task == 'analyze':
                self.Analyze()
            elif task == 'mark':
                self.Mark()
            elif task == 'cut':
                self.Cut()
            elif task == 'encode':
                self.Encode()
            elif task == 'confirm':
                self.Confirm()
            elif task == 'cleanup':
                self.Cleanup()

def main():
    parser = argparse.ArgumentParser(description='Python script to triage TS files')
    parser.add_argument('--config', '-c', default='tstriage.config.yml', help='configuration file path')
    parser.add_argument('--quiet', '-q', action='store_true', default=False, help='disable progress bar')
    parser.add_argument('--task', '-t', required=True, nargs='+', choices=['categorize', 'list', 'analyze', 'mark', 'cut', 'confirm', 'encode', 'cleanup'], help='tasks to run')

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    configurationPath = Path(args.config)
    with configurationPath.open(encoding='utf-8') as f:
        configuration = yaml.safe_load(f)
    
    runner = Runner(configuration, quiet=args.quiet)
    runner.Run(args.task)

if __name__ == "__main__":
    main()