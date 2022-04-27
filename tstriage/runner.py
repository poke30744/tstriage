#!/usr/bin/env python3
import argparse, json, time, os
from pathlib import Path
import logging
import unicodedata
from .common import WindowsInhibitor
from .epgstation import EPGStation
from .tasks import Analyze, Mark, Encode, Confirm, Cleanup

logger = logging.getLogger('tstriage.runner')

def FindTsTriageSettings(folder, top):
    folder = Path(folder)
    settingsPath = folder / 'tstriage.json'
    if settingsPath.exists():
        return settingsPath
    elif folder == top:
        defaultSettings = {
            "marker": {
                "noEnsemble": True,
            },
            "encoder": {
                "preset": "drama",
            },
        }
        with settingsPath.open('w') as f:
            json.dump(defaultSettings, f, indent=True)
        return settingsPath
    else:
        return FindTsTriageSettings(folder.parent, top)
class Runner:
    def __init__(self, configuration):
        self.configuration = configuration
        self.uncategoried = Path(self.configuration['Uncategoried'])
        self.destination = Path(configuration['Destination'])
        self.cache = Path(configuration['Cache']).expanduser()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.epgStation = EPGStation(url=configuration['EPGStation'], cache=configuration['Cache']) if 'EPGStation' in configuration else None
        if 'Path' in configuration:
            for key in configuration['Path']:
                pathToAdd = configuration["Path"][key]
                os.environ['PATH'] = f'{os.environ["PATH"]};{pathToAdd}'
        if 'Encoder' in configuration:
            self.encoder = configuration['Encoder']
        else:
            self.encoder = 'h264'
    
    def RefreshNAS(self):
        if self.epgStation is not None:
            self.epgStation.BusyWait()
        categoryFolders = []
        encodedFiles = []
        for path in Path(self.destination).glob('**/*'):
            if path.is_dir() and not path.name in ('_metadata', 'EPG', 'Subtitles'):
                categoryFolders.append(path)
            elif path.suffix == '.mp4':
                encodedFiles.append(path.name)
        # sort by length (long to short)
        categoryFolders.sort(key=lambda item: (-len(str(item)), item))
        with (self.uncategoried / 'categoryFolders.json').open('w') as f:
            json.dump([str(i) for i in categoryFolders], f, ensure_ascii=False, indent=True)
        with (self.uncategoried / 'encodedFiles.json').open('w') as f:
            json.dump([str(i) for i in encodedFiles], f, ensure_ascii=False, indent=True)

    def Categorize(self):
        if not (self.uncategoried / 'categoryFolders.json').exists() or not (self.uncategoried / 'encodedFiles.json').exists():
            self.RefreshNAS()
        with (self.uncategoried / 'categoryFolders.json').open() as f:
            categoryFolders = [ Path(i) for i in json.load(f) ]
        with (self.uncategoried / 'encodedFiles.json').open() as f:
            encodedFiles = json.load(f)
        itemsToProcess = []
        for path in [ path for path in Path(self.uncategoried).glob('*') if path.suffix in ('.ts', '.m2ts') ]:
            # check if this file has been encoded
            hadBeenEncoded = False
            for encodedFile in encodedFiles:
                if path.stem in encodedFile:
                    hadBeenEncoded = True
                    break
            if hadBeenEncoded:
                continue
            # find proper destinations
            encodeTo = None
            for folder in categoryFolders:
                category = folder.name
                if unicodedata.normalize('NFKC', category) in unicodedata.normalize('NFKC', path.stem):
                    encodeTo = folder
                    break
            if encodeTo is not None:
                itemsToProcess.append({
                    'path': str(path),
                    'destination': str(encodeTo),
                })
            else:
                itemsToProcess.append({
                    'path': str(path),
                    'destination': None,
                })
        for item in itemsToProcess:
            actionItemExists = False
            for path in self.uncategoried.glob('*.*'):
                if path.stem == Path(item['path']).stem and not path.suffix in ['.ts', '.m2ts']:
                    actionItemExists = True
                    break
            if not actionItemExists:
                jsonPath = self.uncategoried / f"{Path(item['path']).stem}.categorized"
                with jsonPath.open('w') as f:
                    json.dump(item, f, ensure_ascii=False, indent=True)
    
    def List(self):
        for path in self.uncategoried.glob('*.categorized'):
            with path.open(encoding='utf-8') as f:
                item = json.load(f)
            encodeTo = item["destination"]
            if encodeTo is not None:
                settingsPath = FindTsTriageSettings(encodeTo, self.destination)
                with settingsPath.open() as f:
                    settings = json.load(f)
                newJsonPath = path.with_suffix('.toanalyze')
                path.rename(newJsonPath)
                newItem = {
                    'path': item['path'],
                    'destination': item['destination'],
                    'cache': str(self.cache),
                    'cutter': settings.get('cutter', {}),
                    'marker': settings.get('marker', {}),
                    'encoder': settings.get('encoder', {})
                }
                logger.info(f'Will process: {item["path"]}')
                with newJsonPath.open('w') as f:
                    json.dump(newItem, f, ensure_ascii=False, indent=True)
            else:
                logger.warn(f'More information is needed: {item["path"]}')

    def Analyze(self):
        for path in self.uncategoried.glob('*.toanalyze'):
            with path.open(encoding='utf-8') as f:
                item = json.load(f)
            try:
                Analyze(item=item, epgStation=self.epgStation)
                path.rename(path.with_suffix('.tomark'))
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in analyzing "{path}":')
                path.rename(path.with_suffix('.error'))

    def Mark(self):
        for path in self.uncategoried.glob('*.tomark'):
            with path.open(encoding='utf-8') as f:
                item = json.load(f)
            try:
                Mark(item=item, epgStation=self.epgStation)
                path.rename(path.with_suffix('.toencode'))
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in marking "{path}":')
                path.rename(path.with_suffix('.error'))

    def Encode(self):
        for path in self.uncategoried.glob('*.toencode'):
            with path.open(encoding='utf-8') as f:
                item = json.load(f)
            try:
                encodedFile = Encode(item=item, encoder=self.encoder, epgStation=self.epgStation)
                path.rename(path.with_suffix('.toconfirm'))
                # add encoded items
                with (self.uncategoried / 'categoryFolders.json').open() as f:
                    categoryFolders = json.load(f)
                with (self.uncategoried / 'encodedFiles.json').open() as f:
                    encodedFiles = json.load(f)
                if not item['destination'] in categoryFolders:
                    categoryFolders.append(item['destination'])
                    categoryFolders.sort(key=lambda item: (-len(str(item)), item))
                if not encodedFile in encodedFiles:
                    encodedFiles.append(encodedFile.name)
                with (self.uncategoried / 'categoryFolders.json').open('w') as f:
                    json.dump([str(i) for i in categoryFolders], f, ensure_ascii=False, indent=True)
                with (self.uncategoried / 'encodedFiles.json').open('w') as f:
                    json.dump([str(i) for i in encodedFiles], f, ensure_ascii=False, indent=True)
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(f'in marking "{path}":')
                path.rename(path.with_suffix('.error'))

    def Confirm(self):
        filesToConfirm = []
        for pattern in ('*.toencode', '*.toconfirm', '*.tocleanup'):
            filesToConfirm += list(self.uncategoried.glob(pattern))
        for path in filesToConfirm:
            with path.open(encoding='utf-8') as f:
                item = json.load(f)
            reEncodingNeeded = Confirm(item=item)
            if reEncodingNeeded or path.suffix == '.toencode':
                path.rename(path.with_suffix('.toencode'))
            else:
                path.rename(path.with_suffix('.tocleanup'))

    def Cleanup(self):
        for path in self.uncategoried.glob('*.tocleanup'):
            with path.open(encoding='utf-8') as f:
                item = json.load(f)
            Cleanup(item=item)
            path.unlink()
    
    def Run(self, tasks):
        logger.info(f'running {tasks} ...')
        for task in tasks:
            if task == 'refresh':
                self.RefreshNAS()
            elif task == 'categorize':
                self.Categorize()
            elif task == 'list':
                self.List()
            elif task == 'analyze':
                with WindowsInhibitor() as wi:
                    self.Analyze()
            elif task == 'mark':
                with WindowsInhibitor() as wi:
                    self.Mark()
            elif task == 'encode':
                with WindowsInhibitor() as wi:
                    self.Encode()
            elif task == 'confirm':
                self.Confirm()
            elif task == 'cleanup':
                self.Cleanup()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Python script to triage TS files')
    parser.add_argument('--config', '-c', required=True, help='configuration file path')
    parser.add_argument('--task', '-t', required=True, nargs='+', choices=['refresh', 'categorize', 'list', 'analyze', 'mark', 'confirm', 'encode', 'cleanup'], help='tasks to run')
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