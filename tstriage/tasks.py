from pathlib import Path
import shutil, logging, json, os
from datetime import datetime, timedelta
from .common import CopyWithProgress, ExtractProgram
import tsutils.splitter, tsutils.epg, tscutter.analyze, tsmarker.marker, tsmarker.common, tsmarker.ensemble

logger = logging.getLogger('tstriage.tasks')

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
        logger.info(f'categorized: {newPath}')
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
        logger.info('Files to process:')
        for item in itemsToProcess:
            logger.info(item['path'])
    return itemsToProcess

def Mark(item, epgStation):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    logger.info('Copying TS file to working folder ...')
    workingPath = cache / path.name
    trimmedPath = cache / path.name.replace('.ts', '_trimmed.ts')
    if not trimmedPath.exists():
        CopyWithProgress(path, workingPath, epgStation=epgStation)
        logger.info('Trimming original TS ...')
        trimmedPath = tsutils.splitter.Trim(videoPath=workingPath, outputPath=trimmedPath)
        workingPath.unlink()

    logger.info('Analyzing to split ...')
    minSilenceLen = item.get('cutter', {}).get('minSilenceLen', 800)
    silenceThresh =  item.get('cutter', {}).get('silenceThresh', -80)
    indexPath = tscutter.analyze.AnalyzeVideo(videoPath=trimmedPath, silenceThresh=silenceThresh, minSilenceLen=minSilenceLen)

    logger.info('Marking ...')
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

    logger.info('Cutting CMs ...')
    if '_groudtruth' in list(markerMap.items())[0][1]:
        byMethod = '_groundtruth'
    elif byEnsemble:
        byMethod = '_ensemble'
    elif noSubtitles:
        byMethod = 'logo'
    else:
        byMethod = 'subtitles'
    _cuttedProgramPath = tsmarker.marker.CutCMs(videoPath=trimmedPath, indexPath=indexPath, markerPath=markerPath, byMethod=byMethod, outputFolder=workingPath.parent / workingPath.stem)

def Confirm(item, epgStation):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    workingPath = cache / path.name.replace('.ts', '_trimmed.ts')
    logger.info(f'Marking ground truth for {workingPath.name} ...')
    cuttedProgramFolder = cache / path.stem
    markerPath = cache / '_metadata' / (workingPath.stem + '.markermap')
    isReEncodingNeeded = tsmarker.marker.MarkGroundTruth(clipsFolder=cuttedProgramFolder, markerPath=markerPath)
    destination = Path(item['destination'])
    CopyWithProgress(markerPath, destination / '_metadata' / Path(markerPath.name), force=True, epgStation=epgStation)
    if isReEncodingNeeded:
        logger.warning("*** Re-encoding is needed! ***")
    return isReEncodingNeeded

def Encode(item, epgStation):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    workingPath = cache / path.name.replace('.ts', '_trimmed.ts')

    logger.info('Extracting EPG ...')
    epgPath, txtPath = tsutils.epg.Dump(workingPath)

    logger.info('Extracting program from TS ...')
    indexPath = cache / '_metadata' / (workingPath.stem + '.ptsmap')
    markerPath = cache / '_metadata' / (workingPath.stem + '.markermap')

    byGroup = item.get('encoder', {}).get('bygroup', False)
    programTsList = ExtractProgram(videoPath=workingPath, indexPath=indexPath, markerPath=markerPath, byGroup=byGroup)
    for programTsPath in programTsList:
        logger.info('Extracting subtitles ...')
        subtitlesPathList = tsutils.subtitles.Extract(programTsPath)
        subtitlesPathList = [ path.replace(path.with_name(path.name.replace('_prog.', '_prog.jpn.'))) for path in subtitlesPathList ]

        if item.get('encoder', {}).get('repack', False):
            strippedTsPath = tsutils.encode.StripAndRepackTS(programTsPath)
        else:
            try:
                strippedTsPath = tsutils.encode.StripTS(programTsPath, fixAudio=True)
            except tsutils.common.EncodingError:
                logger.info('Striping failed again, trying to strip without mapping ...')
                strippedTsPath = tsutils.encode.StripTS(programTsPath, nomap=True)
        programTsPath.unlink()

        preset = item['encoder']['preset']
        cropdetect = item['encoder'].get('cropdetect')
        #encodedPath = EncodeTS(strippedTsPath, preset, cropdetect, 'hevc', 22, strippedTsPath.with_suffix('.mp4'))
        encodedPath = tsutils.encode.EncodeTS(strippedTsPath, preset, cropdetect, 'h264_nvenc', 19, strippedTsPath.with_suffix('.mp4'))

        logger.info('Uploading processed files ...')
        destination = Path(item['destination'])
        CopyWithProgress(encodedPath, destination / encodedPath.name.replace('_stripped', ''), epgStation=epgStation)
        if subtitlesPathList:
            for path in subtitlesPathList:
                CopyWithProgress(path, destination / Path('Subtitles') / Path(path.name), force=True, epgStation=epgStation)
    CopyWithProgress(indexPath, destination / '_metadata' / Path(indexPath.name), force=True, epgStation=epgStation)
    CopyWithProgress(epgPath, destination / 'EPG' / Path(epgPath.name), force=True, epgStation=epgStation)
    CopyWithProgress(txtPath, destination / Path(txtPath.name), force=True, epgStation=epgStation)    

def Cleanup(item):
    logger.info('Cleaning up ...')
    cache = Path(item['cache'])
    originalPath = Path(item['path'])
    for path in cache.glob('*'):
        if path.stem in originalPath.stem or originalPath.stem in path.stem:
            logger.info(f'removing {path.name} ...')
            if path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
