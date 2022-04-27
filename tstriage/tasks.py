from pathlib import Path
import shutil, logging, os, json
from tscutter.common import EncodingError
import tscutter.analyze, tsmarker.marker, tsmarker.common, tsmarker.ensemble
from .common import CopyWithProgress, ExtractPrograms
from .epg import Dump
from .encode import StripAndRepackTS, StripTS, EncodeTS

logger = logging.getLogger('tstriage.tasks')

def Analyze(item, epgStation):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    destination = Path(item['destination'])

    logger.info('Copying TS file to working folder ...')
    workingPath = cache / path.name
    CopyWithProgress(path, workingPath, epgStation=epgStation)

    logger.info('Analyzing to split ...')
    indexPath = destination / '_metadata' / workingPath.with_suffix('.ptsmap').name
    minSilenceLen = item.get('cutter', {}).get('minSilenceLen', 800)
    silenceThresh =  item.get('cutter', {}).get('silenceThresh', -80)
    tscutter.analyze.AnalyzeVideo(videoPath=workingPath, indexPath=indexPath, silenceThresh=silenceThresh, minSilenceLen=minSilenceLen)

def Mark(item, epgStation):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    destination = Path(item['destination'])

    logger.info('Copying TS file to working folder ...')
    workingPath = cache / path.name
    CopyWithProgress(path, workingPath, epgStation=epgStation)
    
    logger.info('Marking ...')
    indexPath = destination / '_metadata' / workingPath.with_suffix('.ptsmap').name
    markerPath = destination / '_metadata' /  workingPath.with_suffix('.markermap').name
    tsmarker.marker.MarkVideo(videoPath=workingPath, indexPath=indexPath, markerPath=markerPath, methods=['subtitles', 'clipinfo', 'logo'])

    noEnsemble = item['marker'].get('noEnsemble', False)
    outputFolder = Path(item['destination'])
    byEnsemble = not noEnsemble
    if byEnsemble:
        # find metadata folder
        if (outputFolder / '_metadata').exists() and len(os.listdir(outputFolder / '_metadata')) > 10:
            metadataPath = outputFolder
            logger.info(f'Using metadata in {metadataPath} ...')
        else:
            metadataPath = outputFolder.parent
            logger.info(f'Trying to use metadata in {metadataPath} ...')
        # generate dataset
        datasetCsv = workingPath.with_suffix('.csv')
        df = tsmarker.ensemble.CreateDataset(
            folder=metadataPath, 
            csvPath=datasetCsv, 
            properties=[ 'subtitles', 'position', 'duration', 'duration_prev', 'duration_next', 'logo'])
        if df is not None:
            # train the model using Adaboost
            dataset = tsmarker.ensemble.LoadDataset(csvPath=datasetCsv)
            columns = dataset['columns']
            clf = tsmarker.ensemble.Train(dataset)
            # predict
            model = clf, columns
            tsmarker.ensemble.Mark(model=model, markerPath=markerPath)
        else:
            logger.warn(f'No metadata is found in {metadataPath}!')
            byEnsemble = False

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
    tsmarker.marker.CutCMs(videoPath=workingPath, indexPath=indexPath, markerPath=markerPath, byMethod=byMethod, outputFolder=workingPath.parent / workingPath.stem)

def Confirm(item):
    path = Path(item['path'])
    destination = Path(item['destination'])
    cache = Path(item['cache']).expanduser()
    workingPath = cache / path
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
    workingPath = cache / path.name

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
        strippedTsPath.unlink()

        logger.info('Uploading processed files ...')
        encodedFile = destination / encodedPath.name.replace('_stripped', '')
        CopyWithProgress(encodedPath, encodedFile, epgStation=epgStation)
        if subtitlesPathList:
            for path in subtitlesPathList:
                CopyWithProgress(path, destination / Path('Subtitles') / Path(path.name), force=True, epgStation=epgStation)
    CopyWithProgress(epgPath, destination / 'EPG' / Path(epgPath.name), force=True, epgStation=epgStation)
    CopyWithProgress(txtPath, destination / Path(txtPath.name), force=True, epgStation=epgStation)
    return encodedFile

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
