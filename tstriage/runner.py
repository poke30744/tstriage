#!/usr/bin/env python3
import argparse, json, time, os
from pathlib import Path
import logging
from .common import WindowsInhibitor
from .epgstation import EPGStation
from .tasks import Categorize, List, Mark, Encode, Confirm, Cleanup

logger = logging.getLogger('tstriage.runner')

class Runner:
    def __init__(self, configuration):
        self.configuration = configuration
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

    def List(self):
        existingWorkItemPathList = []
        for pattern in ('*.tomark', '*.toconfirm', '*.toconfirm', '*.toencode', '*.tocleanup', '.error'):
            for path in self.cache.glob(pattern):
                with Path(path).open(encoding='utf-8') as f:
                    item = json.load(f)
                existingWorkItemPathList.append(item['path'])
        queue = List(self.configuration, self.epgStation)
        newItemQueue = []
        for item in queue:
            if item['path'] not in existingWorkItemPathList:
                newItemQueue.append(item)
        maxFilesToProcess = self.configuration['MaxFilesToProcess']
        existingSeats = maxFilesToProcess - len(existingWorkItemPathList)
        if len(newItemQueue) > existingSeats:
            newItemQueue = newItemQueue[:existingSeats]
        for item in newItemQueue:
            itemPath = self.cache / (Path(item['path']).stem + '.tomark')
            with itemPath.open('w', encoding='utf-8') as f:
                json.dump(item, f, ensure_ascii=False, indent=True)

    def Mark(self):
        for path in self.cache.glob('*.tomark'):
            with path.open(encoding='utf-8') as f:
                item = json.load(f)
            try:
                Mark(item=item, epgStation=self.epgStation)
                path.rename(path.with_suffix('.toencode'))
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(F'in marking "{path}":')
                path.rename(path.with_suffix('.error'))

    def Encode(self):
        for path in self.cache.glob('*.toencode'):
            with path.open(encoding='utf-8') as f:
                item = json.load(f)
            try:
                Encode(item=item, encoder=self.encoder, epgStation=self.epgStation)
                path.rename(path.with_suffix('.toconfirm'))
            except KeyboardInterrupt:
                raise
            except:
                logger.exception(F'in marking "{path}":')
                path.rename(path.with_suffix('.error'))

    def Confirm(self):
        for path in self.cache.glob('*.toencode'):
            with path.open(encoding='utf-8') as f:
                item = json.load(f)
            Confirm(item=item, epgStation=self.epgStation)
        for path in self.cache.glob('*.toconfirm'):
            with path.open(encoding='utf-8') as f:
                item = json.load(f)
            reEncodingNeeded = Confirm(item=item, epgStation=self.epgStation)
            if reEncodingNeeded:
                path.rename(path.with_suffix('.toencode'))
            else:
                path.rename(path.with_suffix('.tocleanup'))

    def Cleanup(self):
        for path in self.cache.glob('*.tocleanup'):
            with path.open(encoding='utf-8') as f:
                item = json.load(f)
            Cleanup(item=item)
    
    def Run(self, tasks):
        logger.info(f'running {tasks} ...')
        for task in tasks:
            if task == 'categorize':
                Categorize(self.configuration, self.epgStation)
            elif task == 'list':
                self.List()
            elif task == 'mark':
                self.Mark()
            elif task == 'encode':
                self.Encode()
            elif task == 'confirm':
                self.Confirm()
            elif task == 'cleanup':
                self.Cleanup()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Python script to triage TS files')
    parser.add_argument('--config', '-c', required=True, help='configuration file path')
    parser.add_argument('--task', '-t', required=True, nargs='+', choices=['categorize', 'list', 'mark', 'confirm', 'encode', 'cleanup'], help='tasks to run')
    parser.add_argument('--daemon', '-d', type=int, help='keep running')

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    configurationPath = Path(args.config)
    with configurationPath.open(encoding='utf-8') as f:
        configuration = json.load(f)
    
    runner = Runner(configuration)

    with WindowsInhibitor() as wi:
        while True:
            runner.Run(args.task)
            if args.daemon is None:
                break
            else:
                print(f'.{args.daemon}.', end="")
                time.sleep(args.daemon)