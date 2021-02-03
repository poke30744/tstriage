#!/usr/bin/env python3
import argparse, json, shutil, sys, pickle, time, os
from pathlib import Path
from datetime import datetime, timedelta
import tsutils.splitter
import tsutils.epg
import tsutils.encode
import tscutter.analyze
import tsmarker.marker
import tsmarker.ensemble
import tsmarker.common
from .common import CopyWithProgress, WindowsInhibitor, ExtractProgram

def Categorize(configuration):
    categories = [ path.name for path in Path(configuration['Categorized']).glob('*') if path.is_dir() ]
    filesMoved = []
    for path in Path(configuration['Uncategoried']).glob('*.ts'):
        modifiedTime = datetime.fromtimestamp(path.stat().st_mtime)
        if datetime.now() - modifiedTime > timedelta(minutes=10):
            fileCategory = '_Unknown'
            for category in categories:
                if category in path.stem:
                    fileCategory = category
                    break
            newPath = Path(str(path).replace(str(path.parent), str(Path(configuration['Categorized']) / fileCategory)))
            shutil.move(path, newPath)
            filesMoved.append(newPath)
    return filesMoved

def List(configuration):
    categorized = Path(configuration['Categorized'])
    cache = Path(configuration['Cache']).expanduser()
    destination = Path(configuration['Destination'])
    newerThan = configuration['NewerThan']
    matchedFiles = []
    for path in categorized.glob('*/*.ts'): 
        modifiedTime = datetime.fromtimestamp(path.stat().st_mtime)
        if datetime.now() - modifiedTime < timedelta(days=newerThan):
            for _, group in configuration['Groups'].items():
                if any([ keyword == path.parent.name for keyword in group['Keywords'] ]): 
                    matchedFiles.append({
                        'path': path,
                        'cache': str(cache),
                        'destination': str(destination / path.parent.stem),
                        'cutter': group.get('Cutter', {}),
                        'marker': group.get('Marker', {}),
                        'encoder': group.get('Encoder', {})
                    })
    filesToProcess = []
    processedFilenames = [ path.name for path in Path(destination).glob('**/*.ts') ] + [ path.name for path in Path(destination).glob('**/*.mp4') ]
    for item in matchedFiles:
        if all([ item['path'].stem not in filename for filename in processedFilenames ]):
            item['path'] = str(item['path'])
            filesToProcess.append(item)
    maxFilesToProcess = configuration['MaxFilesToProcess']
    if len(filesToProcess) > maxFilesToProcess:
        filesToProcess = filesToProcess[:maxFilesToProcess]
    if filesToProcess is not []:
        print('Files to process:', file=sys.stderr)
        for item in filesToProcess:
            print(item['path'], file=sys.stderr)
    return filesToProcess

def Mark(queue):
    for item in queue:
        path = Path(item['path'])
        cache = Path(item['cache']).expanduser()
        print('Copying TS file to working folder ...', file=sys.stderr)
        workingPath = cache / path.name
        trimmedPath = cache / path.name.replace('.ts', '_trimmed.ts')
        if not trimmedPath.exists():
            CopyWithProgress(path, workingPath)
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
            methods=['subtitles', 'clipinfo'])

        # create the dataset
        outputFolder = Path(item['destination'])
        byEnsemble = True
        if outputFolder.exists() and len(os.listdir(outputFolder)) > 0:
            datasetCsv = Path(outputFolder.with_suffix('.csv').name)
            tsmarker.ensemble.CreateDataset(
                folder=outputFolder, 
                csvPath=datasetCsv, 
                properties=[ 'subtitles', 'position', 'duration', 'duration_prev', 'duration_next'])
        elif len(os.listdir(outputFolder.parent)) > 0:
            datasetCsv = Path(outputFolder.parent.with_suffix('.csv').name)
            tsmarker.ensemble.CreateDataset(
                folder=outputFolder.parent, 
                csvPath=datasetCsv, 
                properties=[ 'subtitles', 'position', 'duration', 'duration_prev', 'duration_next'])
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
        
        _, markerMap = tsmarker.common.LoadExistingData(indexPath, markerPath)
        if '_groundtruth' in list(markerMap.values())[0]:
            byMethod = '_groundtruth'
        elif noSubtitles:
            byMethod = '_ensemble'
        else:
            byMethod = 'subtitles'

        print('Cutting CMs ...', file=sys.stderr)
        _cuttedProgramPath = tsmarker.marker.CutCMs(videoPath=trimmedPath, indexPath=indexPath, markerPath=markerPath, byMethod=byMethod, outputFolder=workingPath.parent / workingPath.stem)

def Confirm(queue):
     for item in queue:
        path = Path(item['path'])
        cache = Path(item['cache']).expanduser()
        workingPath = cache / path.name.replace('.ts', '_trimmed.ts')

        print(f'Marking ground truth for {workingPath.name} ...', file=sys.stderr)
        cuttedProgramFolder = cache / path.stem
        markerPath = cache / '_metadata' / (workingPath.stem + '.markermap')
        tsmarker.marker.MarkGroundTruth(clipsFolder=cuttedProgramFolder, markerPath=markerPath)

def Encode(queue):
    for item in queue:
        path = Path(item['path'])
        cache = Path(item['cache']).expanduser()
        workingPath = cache / path.name.replace('.ts', '_trimmed.ts')

        print('Extracting EPG ...', file=sys.stderr)
        epgPath, txtPath = tsutils.epg.Dump(workingPath)

        print('Extracting program from TS ...', file=sys.stderr)
        indexPath = cache / '_metadata' / (workingPath.stem + '.ptsmap')
        markerPath = cache / '_metadata' / (workingPath.stem + '.markermap')
        programTsPath = ExtractProgram(videoPath=workingPath, indexPath=indexPath, markerPath=markerPath)

        print('Extracting subtitles ...', file=sys.stderr)
        subtitlesPathList = tsutils.subtitles.Extract(programTsPath)
        subtitlesPathList = [ path.replace(path.with_name(path.name.replace('_prog.', '_prog.jpn.'))) for path in subtitlesPathList ]
    
        print('Striping TS ...', file=sys.stderr)
        if item.get('encoder', {}).get('repack', False):
            print('Striping and repack TS ...', file=sys.stderr)
            strippedTsPath = tsutils.encode.StripAndRepackTS(programTsPath)
        else:
            print('Striping TS ...', file=sys.stderr)
            strippedTsPath = tsutils.encode.StripTS(programTsPath)
        programTsPath.unlink()

        print('Encoding TS ...', file=sys.stderr)
        preset = item['encoder']['preset']
        cropdetect = item['encoder'].get('cropdetect')
        #encodedPath = EncodeTS(strippedTsPath, preset, cropdetect, 'hevc', 22, strippedTsPath.with_suffix('.mp4'))
        encodedPath = tsutils.encode.EncodeTS(strippedTsPath, preset, cropdetect, 'h264_nvenc', 19, strippedTsPath.with_suffix('.mp4'))

        print('Uploading processed files ...', file=sys.stderr)
        destination = Path(item['destination'])
        CopyWithProgress(encodedPath, destination / encodedPath.name.replace('_stripped', ''))
        CopyWithProgress(indexPath, destination / '_metadata' / Path(indexPath.name), force=True)
        CopyWithProgress(markerPath, destination / '_metadata' / Path(markerPath.name), force=True)
        CopyWithProgress(epgPath, destination / 'EPG' / Path(epgPath.name), force=True)
        CopyWithProgress(txtPath, destination / Path(txtPath.name), force=True)
        if subtitlesPathList:
            for path in subtitlesPathList:
                CopyWithProgress(path, destination / Path('Subtitles') / Path(path.name), force=True)

def Cleanup(queue):
    print('Cleaning up ...', file=sys.stderr)
    for item in queue:
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
    parser.add_argument('--daemon', '-d', help='keep running')

    args = parser.parse_args()

    WindowsInhibitor.inhibit()

    configurationPath = Path(args.config)
    with configurationPath.open() as f:
        configuration = json.load(f)
        cache = Path(configuration['Cache']).expanduser()
        cache.mkdir(parents=True, exist_ok=True)
    
    for task in args.task:
        if task == 'categorize':
            Categorize(configuration)
        elif task == 'list':
            queue = List(configuration)
            with (cache / configurationPath.name).with_suffix('.list').open('w', encoding='utf-8') as f:
                json.dump(queue, f, ensure_ascii=False, indent=True)
        elif task == 'mark':
            with (cache / configurationPath.name).with_suffix('.list').open(encoding='utf-8') as f:
                queue = json.load(f)
            Mark(queue=queue)
        elif task == 'confirm':
            with (cache / configurationPath.name).with_suffix('.list').open(encoding='utf-8') as f:
                queue = json.load(f)
            Confirm(queue=queue)
        elif task == 'encode':
            with (cache / configurationPath.name).with_suffix('.list').open(encoding='utf-8') as f:
                queue = json.load(f)
            Encode(queue=queue)
        elif task == 'cleanup':
            with (cache / configurationPath.name).with_suffix('.list').open(encoding='utf-8') as f:
                queue = json.load(f)
            Cleanup(queue=queue)

    WindowsInhibitor.uninhibit()
