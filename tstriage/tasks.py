from pathlib import Path
import shutil, logging, os
from tscutter.ffmpeg import InputFile
from tscutter.analyze import AnalyzeVideo
import tsmarker.common
from tsmarker import subtitles, logo, clipinfo, ensemble, groundtruth
from .common import CopyWithProgress
from .epg import EPG
from .epgstation import EPGStation
from .pipeline import ExtractLogoPipeline, EncodePipeline, PtsMap, MarkerMap

logger = logging.getLogger('tstriage.tasks')

def Analyze(item, epgStation: EPGStation):
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
    AnalyzeVideo(inputFile=InputFile(workingPath), indexPath=indexPath, silenceThresh=silenceThresh, minSilenceLen=minSilenceLen)

    logger.info('Extracting EPG ...')
    EPG.Dump(workingPath)
    epgPath = workingPath.with_suffix('.epg')
    txtPath = workingPath.with_suffix('.txt')
    CopyWithProgress(epgPath, destination / '_metadata' / epgPath.name)
    epg = EPG(epgPath, epgStation.GetChannels())
    epg.OutputDesc(destination / txtPath.name)
    epgPath.unlink()

    logger.info('Extracting subtitles ...')
    for sub in subtitles.Extract(workingPath):
        if sub.suffix == '.ass':
             CopyWithProgress(sub, destination / '_metadata' / sub.with_suffix('.ass.original').name)
        sub.unlink()
    
    logoPath = (path.parent / '_tstriage' / epg.Channel()).with_suffix('.png')
    if not logoPath.exists():
        ExtractLogoPipeline(inFile=workingPath, ptsMap=PtsMap(indexPath), outFile=logoPath)

def Mark(item, epgStation: EPGStation):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    destination = Path(item['destination'])

    logger.info('Copying TS file to working folder ...')
    workingPath = cache / path.name
    CopyWithProgress(path, workingPath, epgStation=epgStation)
    
    logger.info('Marking ...')
    indexPath = destination / '_metadata' / workingPath.with_suffix('.ptsmap').name
    markerPath = destination / '_metadata' /  workingPath.with_suffix('.markermap').name
    subtitles.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(videoPath=workingPath, assPath=destination / '_metadata' / path.with_suffix('.ass.original').name)
    clipinfo.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(videoPath=workingPath, quiet=False)
    logo.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(videoPath=workingPath, quiet=False)

    noEnsemble = item['marker'].get('noEnsemble', False)
    outputFolder = Path(item['destination'])
    byEnsemble = not noEnsemble
    if byEnsemble:
        # find metadata folder
        if (outputFolder / '_metadata').exists() and len(list((outputFolder / '_metadata').glob('*.markermap'))) > 5:
            metadataPath = outputFolder
            normalize = False
            logger.info(f'Using metadata in {metadataPath} ...')
        else:
            metadataPath = outputFolder.parent
            normalize = True
            logger.info(f'Trying to use metadata in {metadataPath} ...')
        # generate dataset
        datasetCsv = workingPath.with_suffix('.csv')
        df = ensemble.CreateDataset(
            folder=metadataPath, 
            csvPath=datasetCsv, 
            properties=[ 'subtitles', 'position', 'duration', 'duration_prev', 'duration_next', 'logo'],
            normalize=normalize)
        if df is not None:
            # train the model using Adaboost
            dataset = ensemble.LoadDataset(csvPath=datasetCsv)
            columns = dataset['columns']
            clf = ensemble.Train(dataset)
            # predict
            model = clf, columns
            ensemble.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(model, normalize=normalize)
        else:
            logger.warn(f'No metadata is found in {metadataPath}!')
            byEnsemble = False

def Cut(item, epgStation: EPGStation):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    destination = Path(item['destination'])

    logger.info('Copying TS file to working folder ...')
    workingPath = cache / path.name
    CopyWithProgress(path, workingPath, epgStation=epgStation)
    
    logger.info('Cutting ...')
    indexPath = destination / '_metadata' / workingPath.with_suffix('.ptsmap').name
    markerPath = destination / '_metadata' /  workingPath.with_suffix('.markermap').name
    markerMap = tsmarker.common.MarkerMap(markerPath, PtsMap(indexPath))

    # cut the video by marking result for review
    if '_groundtruth' in markerMap.Properties():
        byMethod = '_groundtruth'
    elif '_ensemble' in markerMap.Properties():
        byMethod = '_ensemble'
    else:
        byMethod = 'subtitles'
    logger.info(f'Cutting CMs by {byMethod} ...')
    markerMap.Cut(videoPath=workingPath, byMethod=byMethod, outputFolder=workingPath.with_suffix(''))

def Confirm(item):
    path = Path(item['path'])
    destination = Path(item['destination'])
    cache = Path(item['cache']).expanduser()
    workingPath = cache / path
    logger.info(f'Marking ground truth for {workingPath.name} ...')
    cuttedProgramFolder = cache / path.stem
    markerPath = destination / '_metadata' / (workingPath.stem + '.markermap')
    indexPath = markerPath.with_suffix('.ptsmap')
    isReEncodingNeeded = groundtruth.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(clipsFolder=cuttedProgramFolder)
    if isReEncodingNeeded:
        logger.warning("*** Re-encoding is needed! ***")
    return isReEncodingNeeded

def Encode(item, encoder: str, epgStation: EPGStation):
    path = Path(item['path'])
    destination = Path(item['destination'])
    byGroup = item.get('encoder', {}).get('bygroup', False)
    splitNum = item.get('encoder', {}).get('split', 1)
    preset = item['encoder']['preset']
    cropdetect = item['encoder'].get('cropdetect')
    ptsMap = PtsMap(destination / '_metadata' / path.with_suffix('.ptsmap').name)
    markerMap = MarkerMap(destination / '_metadata' /  path.with_suffix('.markermap').name, ptsMap)

    logger.info('Copying TS file to working folder ...')
    cache = Path(item['cache']).expanduser()
    workingPath = cache / path.name
    CopyWithProgress(path, workingPath, epgStation=epgStation)
    
    outFile = workingPath.with_suffix('.mp4')
    EncodePipeline(
        inFile=workingPath,
        ptsMap=ptsMap,
        markerMap=markerMap,
        outFile=outFile,
        byGroup=byGroup,
        splitNum=splitNum,
        preset=preset,
        cropdetect=cropdetect,
        encoder=encoder)

    logger.info('Uploading processed files ...')
    for p in cache.glob('*.*'):
        if p.stem == path.stem:
            if p.suffix == '.mp4':
                CopyWithProgress(p, destination / p.name, epgStation=epgStation)
            elif p.suffix in ('.ass', '.srt'):
                CopyWithProgress(p, destination / 'Subtitles' / p.name, epgStation=epgStation)
    return outFile

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
