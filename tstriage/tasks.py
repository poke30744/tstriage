from pathlib import Path
import shutil, logging, os, json
from tscutter.common import EncodingError
import tscutter.analyze, tsmarker.marker, tsmarker.common, tsmarker.ensemble
from .common import CopyWithProgress, ExtractPrograms
from .splitter import Trim
from .epg import Dump
from .encode import StripAndRepackTS, StripTS, EncodeTS

logger = logging.getLogger('tstriage.tasks')

def Mark(item, epgStation):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    logger.info('Copying TS file to working folder ...')
    workingPath = cache / path.name
    trimmedPath = cache / path.name.replace(path.suffix, '_trimmed.ts')
    if not trimmedPath.exists():
        CopyWithProgress(path, workingPath, epgStation=epgStation)
        logger.info('Trimming original TS ...')
        trimmedPath = Trim(videoPath=workingPath, outputPath=trimmedPath)
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

    # cut the video by marking result for review
    if '_groundtruth' in list(markerMap.items())[0][1]:
        byMethod = '_groundtruth'
    elif byEnsemble:
        byMethod = '_ensemble'
    elif noSubtitles:
        byMethod = 'logo'
    else:
        byMethod = 'subtitles'
    logger.info(f'Cutting CMs by {byMethod} ...')
    _cuttedProgramPath = tsmarker.marker.CutCMs(videoPath=trimmedPath, indexPath=indexPath, markerPath=markerPath, byMethod=byMethod, outputFolder=workingPath.parent / workingPath.stem)

    # upload marking results
    destination = Path(item['destination'])
    CopyWithProgress(indexPath, destination / '_metadata' / Path(indexPath.name), force=True)
    CopyWithProgress(markerPath, destination / '_metadata' / Path(markerPath.name), force=True)

def Confirm(item):
    path = Path(item['path'])
    destination = Path(item['destination'])
    cache = Path(item['cache']).expanduser()
    workingPath = cache / path.name.replace(path.suffix, '_trimmed.ts')
    logger.info(f'Marking ground truth for {workingPath.name} ...')
    cuttedProgramFolder = cache / path.stem
    markerPath = destination / '_metadata' / (workingPath.stem + '.markermap')
    isReEncodingNeeded = tsmarker.marker.MarkGroundTruth(clipsFolder=cuttedProgramFolder, markerPath=markerPath)
    if isReEncodingNeeded:
        logger.warning("*** Re-encoding is needed! ***")
    return isReEncodingNeeded

def Encode(item, encoder, epgStation):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    destination = Path(item['destination'])
    workingPath = cache / path.name.replace(path.suffix, '_trimmed.ts')

    logger.info('Extracting EPG ...')
    epgPath, txtPath = Dump(workingPath)

    logger.info('Extracting program from TS ...')
    indexPath = destination / '_metadata' / (workingPath.stem + '.ptsmap')
    markerPath = destination / '_metadata' / (workingPath.stem + '.markermap')

    byGroup = item.get('encoder', {}).get('bygroup', False)
    splitNum = item.get('encoder', {}).get('split', 1)
    programTsList = ExtractPrograms(videoPath=workingPath, indexPath=indexPath, markerPath=markerPath, byGroup=byGroup, splitNum=splitNum)
    for programTsPath in programTsList:
        logger.info('Extracting subtitles ...')
        subtitlesPathList = tsmarker.subtitles.Extract(programTsPath)
        subtitlesPathList = [ path.replace(path.with_name(path.name.replace('_prog.', '_prog.jpn.'))) for path in subtitlesPathList ]

        if item.get('encoder', {}).get('repack', False):
            strippedTsPath = StripAndRepackTS(programTsPath)
        else:
            try:
                strippedTsPath = StripTS(programTsPath, fixAudio=True)
            except EncodingError:
                logger.info('Striping failed again, trying to strip without mapping ...')
                strippedTsPath = StripTS(programTsPath, nomap=True)
        programTsPath.unlink()

        preset = item['encoder']['preset']
        cropdetect = item['encoder'].get('cropdetect')
        encodedPath = EncodeTS(strippedTsPath, preset, cropdetect, encoder, strippedTsPath.with_suffix('.mp4'))

        logger.info('Uploading processed files ...')
        encodedFile = destination / encodedPath.name.replace('_stripped', '')
        CopyWithProgress(encodedPath, encodedFile, epgStation=epgStation)
        if subtitlesPathList:
            for path in subtitlesPathList:
                CopyWithProgress(path, destination / Path('Subtitles') / Path(path.name), force=True, epgStation=epgStation)
    CopyWithProgress(epgPath, destination / 'EPG' / Path(epgPath.name), force=True, epgStation=epgStation)
    CopyWithProgress(txtPath, destination / Path(txtPath.name), force=True, epgStation=epgStation)

    # add encoded items
    with open('categoryFolders.json') as f:
        categoryFolders = json.load(f)
    with open('encodedFiles.json') as f:
        encodedFiles = json.load(f)
    if not str(destination) in categoryFolders:
        categoryFolders.append(str(destination))
        categoryFolders.sort(key=lambda item: (-len(str(item)), item))
    if not encodedFile in encodedFiles:
        encodedFiles.append(str(encodedFile))
    with open('categoryFolders.json', 'w') as f:
        json.dump([str(i) for i in categoryFolders], f, ensure_ascii=False, indent=True)
    with open('encodedFiles.json', 'w') as f:
        json.dump([str(i) for i in encodedFiles], f, ensure_ascii=False, indent=True)

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
