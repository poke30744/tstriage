#!/usr/bin/env python3
import argparse, json, shutil, sys, pickle, time, os
from pathlib import Path
from datetime import datetime, timedelta
import urllib.request
import tsutils.splitter
import tsutils.epg
import tsutils.encode
import tscutter.analyze
import tsmarker.marker
import tsmarker.ensemble
import tsmarker.common
from .common import CopyWithProgress, WindowsInhibitor, ExtractProgram

class EPGStation:
    def __init__(self, url, cache=None):
        self.url = url
        self.reservesJsonPath = Path('reserves.json') if cache is None else Path(cache).expanduser() / 'reserves.json'
        self._notBusyTill = None
    
    def LoadReservesList(self):
        if self.reservesJsonPath.exists():
            lastModifiedTime = datetime.fromtimestamp(self.reservesJsonPath.stat().st_mtime)
            if datetime.now() - lastModifiedTime > timedelta(hours=8):
                self.reservesJsonPath.unlink()
        if not self.reservesJsonPath.exists():
            with urllib.request.urlopen(f'{self.url}/api/reserves') as response:
                with self.reservesJsonPath.open('wb') as f:
                    shutil.copyfileobj(response, f)
        with self.reservesJsonPath.open() as f:
            return json.load(f)['reserves']

    def IsBusy(self, at=None, duration=None):
        at = datetime.now() if at is None else at
        duration = timedelta(minutes=30) if duration is None else duration    
        for item in self.LoadReservesList():
            startAt = datetime.fromtimestamp(item['program']['startAt'] / 1000)
            endAt = datetime.fromtimestamp(item['program']['endAt'] / 1000)
            if startAt <= at <= endAt or startAt <= (at + duration) <= endAt:
                return True
        return False
    
    def BusyWait(self, granularity=30):
        if self._notBusyTill is None or self._notBusyTill < datetime.now():
            duration = granularity * 2
            while self.IsBusy(duration=timedelta(seconds=duration)):
                time.sleep(duration)
            self._notBusyTill = datetime.now() + timedelta(seconds=granularity)

def Categorize(configuration, epgStation=None):
    if epgStation is not None:
        epgStation.BusyWait()
    # categorize files by folder names in the destination
    categoryFolders = [ path for path in Path(configuration['Categorized']).glob('**/*') if path.is_dir() ]
    categoryFolders.sort(key=lambda item: (-len(str(item)), item))
    filesMoved = []
    for path in Path(configuration['Uncategoried']).glob('*.ts'):
        newPath = None
        for folder in categoryFolders:
            category = folder.name
            if category in path.stem:
                newPath = folder / path.name
                break
        if newPath is None:
            newPath = Path(configuration['Categorized']) / '_Unknown' / path.name # default category
        shutil.move(path, newPath)
        filesMoved.append(newPath)
        print(f'categorized: {newPath}')
    return filesMoved

def FindTsTriageSettings(folder):
    folder = Path(folder)
    settingsPath = folder / 'tstriage.json'
    if settingsPath.exists():
        return settingsPath
    elif folder == Path('/'):
        return None
    else:
        return FindTsTriageSettings(folder.parent)

def List(configuration, epgStation=None):
    if epgStation is not None:
        epgStation.BusyWait()
    categorized = Path(configuration['Categorized'])
    cache = Path(configuration['Cache']).expanduser()
    destination = Path(configuration['Destination'])
    newerThan = configuration['NewerThan']
    
    tsPaths = []
    for path in categorized.glob('**/*.ts'): 
        modifiedTime = datetime.fromtimestamp(path.stat().st_mtime)
        if datetime.now() - modifiedTime < timedelta(days=newerThan):
            if path.parent.stem != '_Unknown':
                tsPaths.append(path)
            
    tsToProcess = []
    processedFilenames = [ path.name for path in Path(destination).glob('**/*.ts') ] + [ path.name for path in Path(destination).glob('**/*.mp4') ]
    for path in tsPaths:
        if all([ path.stem not in filename for filename in processedFilenames ]):
            tsToProcess.append(path)
    
    itemsToProcess = []
    for path in tsToProcess:
        settingsPath = FindTsTriageSettings(path.parent)
        with settingsPath.open() as f:
            settings = json.load(f)
            itemsToProcess.append({
                'path': str(path),
                'cache': str(cache),
                'destination': str(path.parent).replace(str(categorized), str(destination)),
                'cutter': settings.get('cutter', {}),
                'marker': settings.get('marker', {}),
                'encoder': settings.get('encoder', {})
            })

    if len(itemsToProcess) > 0:
        print('Files to process:', file=sys.stderr)
        for item in itemsToProcess:
            print(item['path'], file=sys.stderr)
    return itemsToProcess

def Mark(item, epgStation):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    print('Copying TS file to working folder ...', file=sys.stderr)
    workingPath = cache / path.name
    trimmedPath = cache / path.name.replace('.ts', '_trimmed.ts')
    if not trimmedPath.exists():
        CopyWithProgress(path, workingPath, epgStation=epgStation)
        print('Trimming original TS ...', file=sys.stderr)
        trimmedPath = tsutils.splitter.Trim(videoPath=workingPath, outputPath=trimmedPath)
        workingPath.unlink()

    print('Analyzing to split ...', file=sys.stderr)
    minSilenceLen = item.get('cutter', {}).get('minSilenceLen', 800)
    silenceThresh =  item.get('cutter', {}).get('silenceThresh', -80)
    indexPath = tscutter.analyze.AnalyzeVideo(videoPath=trimmedPath, silenceThresh=silenceThresh, minSilenceLen=minSilenceLen)

    print('Marking ...', file=sys.stderr)
    markerPath = tsmarker.marker.MarkVideo(
        videoPath=trimmedPath,
        indexPath=None,
        markerPath=None,
        methods=['subtitles', 'clipinfo', 'logo'])

    # create the dataset
    noEnsemble = item['marker'].get('noEnsemble', False)
    outputFolder = Path(item['destination'])
    byEnsemble = not noEnsemble
    if byEnsemble and (outputFolder / '_metadata').exists() and len(os.listdir(outputFolder / '_metadata')) > 0:
        datasetCsv = Path(outputFolder.with_suffix('.csv').name)
        tsmarker.ensemble.CreateDataset(
            folder=outputFolder, 
            csvPath=datasetCsv, 
            properties=[ 'subtitles', 'position', 'duration', 'duration_prev', 'duration_next', 'logo'])
    else:
        byEnsemble = False
    
    if byEnsemble:
        # train the model using Adaboost
        dataset = tsmarker.ensemble.LoadDataset(csvPath=datasetCsv)
        columns = dataset['columns']
        clf = tsmarker.ensemble.Train(dataset)

        # predict
        model = clf, columns
        tsmarker.ensemble.Mark(model=model, markerPath=markerPath)

    _, markerMap = tsmarker.common.LoadExistingData(indexPath, markerPath)
    noSubtitles = any([ v['subtitles'] == 0.5 for _, v in markerMap.items() ])

    print('Cutting CMs ...', file=sys.stderr)
    if byEnsemble:
        byMethod = '_ensemble'
    elif noSubtitles:
        byMethod = 'logo'
    else:
        byMethod = 'subtitles'
    _cuttedProgramPath = tsmarker.marker.CutCMs(videoPath=trimmedPath, indexPath=indexPath, markerPath=markerPath, byMethod=byMethod, outputFolder=workingPath.parent / workingPath.stem)

def Confirm(item):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    workingPath = cache / path.name.replace('.ts', '_trimmed.ts')
    print(f'Marking ground truth for {workingPath.name} ...', file=sys.stderr)
    cuttedProgramFolder = cache / path.stem
    markerPath = cache / '_metadata' / (workingPath.stem + '.markermap')
    isReEncodingNeeded = tsmarker.marker.MarkGroundTruth(clipsFolder=cuttedProgramFolder, markerPath=markerPath)
    destination = Path(item['destination'])
    CopyWithProgress(markerPath, destination / '_metadata' / Path(markerPath.name), force=True, epgStation=epgStation)
    if isReEncodingNeeded:
        print("*** Re-encoding is needed! ***")
    return isReEncodingNeeded

def Encode(item, epgStation):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    workingPath = cache / path.name.replace('.ts', '_trimmed.ts')

    print('Extracting EPG ...', file=sys.stderr)
    epgPath, txtPath = tsutils.epg.Dump(workingPath)

    print('Extracting program from TS ...', file=sys.stderr)
    indexPath = cache / '_metadata' / (workingPath.stem + '.ptsmap')
    markerPath = cache / '_metadata' / (workingPath.stem + '.markermap')

    byGroup = item.get('encoder', {}).get('bygroup', False)
    programTsList = ExtractProgram(videoPath=workingPath, indexPath=indexPath, markerPath=markerPath, byGroup=byGroup)
    for programTsPath in programTsList:
        print('Extracting subtitles ...', file=sys.stderr)
        subtitlesPathList = tsutils.subtitles.Extract(programTsPath)
        subtitlesPathList = [ path.replace(path.with_name(path.name.replace('_prog.', '_prog.jpn.'))) for path in subtitlesPathList ]

        if item.get('encoder', {}).get('repack', False):
            strippedTsPath = tsutils.encode.StripAndRepackTS(programTsPath)
        else:
            try:
                strippedTsPath = tsutils.encode.StripTS(programTsPath, fixAudio=True)
            except tsutils.common.EncodingError:
                print('Striping failed again, trying to strip without mapping ...', file=sys.stderr)
                strippedTsPath = tsutils.encode.StripTS(programTsPath, nomap=True)
        programTsPath.unlink()

        preset = item['encoder']['preset']
        cropdetect = item['encoder'].get('cropdetect')
        #encodedPath = EncodeTS(strippedTsPath, preset, cropdetect, 'hevc', 22, strippedTsPath.with_suffix('.mp4'))
        encodedPath = tsutils.encode.EncodeTS(strippedTsPath, preset, cropdetect, 'h264_nvenc', 19, strippedTsPath.with_suffix('.mp4'))

        print('Uploading processed files ...', file=sys.stderr)
        destination = Path(item['destination'])
        CopyWithProgress(encodedPath, destination / encodedPath.name.replace('_stripped', ''), epgStation=epgStation)
        if subtitlesPathList:
            for path in subtitlesPathList:
                CopyWithProgress(path, destination / Path('Subtitles') / Path(path.name), force=True, epgStation=epgStation)
    CopyWithProgress(indexPath, destination / '_metadata' / Path(indexPath.name), force=True, epgStation=epgStation)
    CopyWithProgress(epgPath, destination / 'EPG' / Path(epgPath.name), force=True, epgStation=epgStation)
    CopyWithProgress(txtPath, destination / Path(txtPath.name), force=True, epgStation=epgStation)    

def Cleanup(item):
    print('Cleaning up ...', file=sys.stderr)
    cache = Path(item['cache'])
    originalPath = Path(item['path'])
    for path in cache.glob('*'):
        if path.stem in originalPath.stem or originalPath.stem in path.stem:
            print(f'removing {path.name} ...', file=sys.stderr)
            if path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Python script to triage TS files')
    parser.add_argument('--config', '-c', required=True, help='configuration file path')
    parser.add_argument('--task', '-t', required=True, nargs='+', choices=['categorize', 'list', 'mark', 'confirm', 'encode', 'cleanup'], help='tasks to run')
    parser.add_argument('--daemon', '-d', type=int, help='keep running')

    args = parser.parse_args()

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
                    Confirm(item=item)
                for path in cache.glob('*.toconfirm'):
                    with path.open(encoding='utf-8') as f:
                        item = json.load(f)
                    reEncodingNeeded = Confirm(item=item)
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
