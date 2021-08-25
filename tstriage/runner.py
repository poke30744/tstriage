#!/usr/bin/env python3
import argparse, json, time
from pathlib import Path
import logging
from .common import WindowsInhibitor
from .epgstation import EPGStation
from .tasks import Categorize, List, Mark, Encode, Confirm, Cleanup

logger = logging.getLogger('tstriage.runner')

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Python script to triage TS files')
    parser.add_argument('--config', '-c', required=True, help='configuration file path')
    parser.add_argument('--task', '-t', required=True, nargs='+', choices=['categorize', 'list', 'mark', 'confirm', 'encode', 'cleanup'], help='tasks to run')
    parser.add_argument('--daemon', '-d', type=int, help='keep running')

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    WindowsInhibitor.inhibit()

    configurationPath = Path(args.config)
    with configurationPath.open() as f:
        configuration = json.load(f)
        cache = Path(configuration['Cache']).expanduser()
        cache.mkdir(parents=True, exist_ok=True)
    
    epgStation = EPGStation(url=configuration['EPGStation'], cache=configuration['Cache']) if 'EPGStation' in configuration else None

    while True:
        for task in args.task:
            if task == 'categorize':
                Categorize(configuration, epgStation)
            elif task == 'list':
                existingWorkItemPathList = []
                for pattern in ('*.tomark', '*.toconfirm', '*.toconfirm', '*.toencode', '*.tocleanup'):
                    for path in cache.glob(pattern):
                        with Path(path).open() as f:
                            item = json.load(f)
                        existingWorkItemPathList.append(item['path'])
                queue = List(configuration, epgStation)
                newItemQueue = []
                for item in queue:
                    if item['path'] not in existingWorkItemPathList:
                        newItemQueue.append(item)
                maxFilesToProcess = configuration['MaxFilesToProcess']
                existingSeats = maxFilesToProcess - len(existingWorkItemPathList)
                if len(newItemQueue) > existingSeats:
                    newItemQueue = newItemQueue[:existingSeats]
                for item in newItemQueue:
                    itemPath = cache / (Path(item['path']).stem + '.tomark')
                    with itemPath.open('w', encoding='utf-8') as f:
                        json.dump(item, f, ensure_ascii=False, indent=True)
            elif task == 'mark':
                for path in cache.glob('*.tomark'):
                    with path.open(encoding='utf-8') as f:
                        item = json.load(f)
                    Mark(item=item, epgStation=epgStation)
                    path.rename(path.with_suffix('.toencode'))
            elif task == 'encode':
                for path in cache.glob('*.toencode'):
                    with path.open(encoding='utf-8') as f:
                        item = json.load(f)
                    Encode(item=item, epgStation=epgStation)
                    path.rename(path.with_suffix('.toconfirm'))
            elif task == 'confirm':
                for path in cache.glob('*.toencode'):
                    with path.open(encoding='utf-8') as f:
                        item = json.load(f)
                    Confirm(item=item, epgStation=epgStation)
                for path in cache.glob('*.toconfirm'):
                    with path.open(encoding='utf-8') as f:
                        item = json.load(f)
                    reEncodingNeeded = Confirm(item=item, epgStation=epgStation)
                    if reEncodingNeeded:
                        path.rename(path.with_suffix('.toencode'))
                    else:
                        path.rename(path.with_suffix('.tocleanup'))
            elif task == 'cleanup':
                for path in cache.glob('*.tocleanup'):
                    with path.open(encoding='utf-8') as f:
                        item = json.load(f)
                    Cleanup(item=item)

        if args.daemon is None:
            break
        else:
            print(f'.{args.daemon}.', end="")
            time.sleep(args.daemon)

    WindowsInhibitor.uninhibit()
