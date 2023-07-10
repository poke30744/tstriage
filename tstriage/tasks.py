from pathlib import Path
import shutil, logging
from tscutter.ffmpeg import InputFile
from tscutter.analyze import AnalyzeVideo
import tsmarker.common
from tsmarker import subtitles, logo, clipinfo, ensemble, groundtruth
from tsmarker.pipeline import PtsMap, ExtractLogoPipeline
from .common import CopyWithProgress2
from .epg import EPG
from .epgstation import EPGStation
from .pipeline import MarkerMap, EncodePipeline

logger = logging.getLogger('tstriage.tasks')

def Analyze(item, epgStation: EPGStation, quiet: bool):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    destination = Path(item['destination'])

    logger.info('Copying TS file to working folder ...')
    workingPath = cache / path.name
    CopyWithProgress2(path, workingPath, quiet=quiet)

    logger.info('Analyzing to split ...')
    indexPath = destination / '_metadata' / workingPath.with_suffix('.ptsmap').name
    minSilenceLen = item.get('cutter', {}).get('minSilenceLen', 800)
    silenceThresh =  item.get('cutter', {}).get('silenceThresh', -80)
    splitPosShift = item.get('cutter', {}).get('splitPosShift', 1)
    inputFile = InputFile(workingPath)
    AnalyzeVideo(inputFile=inputFile, indexPath=indexPath, silenceThresh=silenceThresh, minSilenceLen=minSilenceLen, splitPosShift=splitPosShift, quiet=quiet)

    logger.info('Extracting EPG ...')
    EPG.Dump(workingPath, quiet=quiet)
    epgPath = workingPath.with_suffix('.epg')
    CopyWithProgress2(epgPath, destination / '_metadata' / epgPath.name, quiet=quiet)
    epg = EPG(epgPath, inputFile,  epgStation.GetChannels())
    epg.OutputDesc(destination / workingPath.with_suffix('.yaml').name)
    epgPath.unlink()

    logger.info('Extracting subtitles ...')
    for sub in subtitles.Extract(workingPath):
        if sub.suffix == '.ass':
             CopyWithProgress2(sub, destination / '_metadata' / sub.with_suffix('.ass.original').name, quiet=quiet)
        sub.unlink()
    
    info = inputFile.GetInfo()
    logoPath = (path.parent / '_tstriage' / f'{epg.Channel()}_{info.width}x{info.height}').with_suffix('.png')
    if not logoPath.exists():
        ExtractLogoPipeline(inFile=workingPath, ptsMap=PtsMap(indexPath), outFile=logoPath, quiet=quiet)

def Mark(item, epgStation: EPGStation, quiet: bool):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    destination = Path(item['destination'])

    logger.info('Copying TS file to working folder ...')
    workingPath = cache / path.name
    inputFile = InputFile(workingPath)
    CopyWithProgress2(path, workingPath, quiet=quiet)
    
    logger.info('Marking ...')
    indexPath = destination / '_metadata' / workingPath.with_suffix('.ptsmap').name
    markerPath = destination / '_metadata' /  workingPath.with_suffix('.markermap').name
    if markerPath.exists() and indexPath.stat().st_mtime > markerPath.stat().st_mtime:
         logger.warn(f'removing {markerPath} ...')
         markerPath.unlink()
    subtitles.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(videoPath=workingPath, assPath=destination / '_metadata' / path.with_suffix('.ass.original').name)
    clipinfo.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(videoPath=workingPath, quiet=quiet)
    epg = EPG(destination / '_metadata' / path.with_suffix('.epg').name, inputFile, epgStation.GetChannels())
    info = inputFile.GetInfo()
    logoPath = (path.parent / '_tstriage' / f'{epg.Channel()}_{info.width}x{info.height}').with_suffix('.png')
    logo.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(videoPath=workingPath, logoPath=logoPath, quiet=quiet)

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
            normalize=normalize,
            quiet=quiet)
        if df is not None:
            # train the model using Adaboost
            dataset = ensemble.LoadDataset(csvPath=datasetCsv)
            columns = dataset['columns']
            clf = ensemble.Train(dataset, quiet=quiet)
            # predict
            model = clf, columns
            ensemble.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(model, normalize=normalize)
        else:
            logger.warn(f'No metadata is found in {metadataPath}!')
            byEnsemble = False

def Cut(item, quiet: bool):
    path = Path(item['path'])
    cache = Path(item['cache']).expanduser()
    destination = Path(item['destination'])

    logger.info('Copying TS file to working folder ...')
    workingPath = cache / path.name
    CopyWithProgress2(path, workingPath, quiet=quiet)
    
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
    markerMap.Cut(videoPath=workingPath, byMethod=byMethod, outputFolder=workingPath.with_suffix(''), quiet=quiet)

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

def Encode(item, encoder: str, presets: dict, quiet: bool):
    path = Path(item['path'])
    destination = Path(item['destination'])
    byGroup = item.get('encoder', {}).get('bygroup', False)
    splitNum = item.get('encoder', {}).get('split', 1)
    presetName = item['encoder']['preset']
    cropdetect = item['encoder'].get('cropdetect')
    fixAudio = item['encoder'].get('fixaudio')
    noStrip = item['encoder'].get('nostrip')
    ptsMap = PtsMap(destination / '_metadata' / path.with_suffix('.ptsmap').name)
    markerMap = MarkerMap(destination / '_metadata' /  path.with_suffix('.markermap').name, ptsMap)

    logger.info('Copying TS file to working folder ...')
    cache = Path(item['cache']).expanduser()
    workingPath = cache / path.name
    CopyWithProgress2(path, workingPath, quiet=quiet)
    
    outFile = workingPath.with_suffix('.mp4')
    EncodePipeline(
        inFile=workingPath,
        ptsMap=ptsMap,
        markerMap=markerMap,
        outFile=outFile,
        byGroup=byGroup,
        splitNum=splitNum,
        preset=presets[presetName],
        cropdetect=cropdetect,
        encoder=encoder,
        fixAudio=fixAudio,
        noStrip=noStrip,
        quiet=quiet)

    logger.info('Uploading processed files ...')
    for p in cache.glob('*.*'):
        if path.stem in p.stem:
            if p.suffix == '.mp4':
                CopyWithProgress2(p, destination / p.name, quiet=quiet)
            elif p.suffix in ('.ass', '.srt'):
                CopyWithProgress2(p, destination / 'Subtitles' / p.name, quiet=quiet)
    
    logger.info('Uploading triage file ...')
    tstriageFolder = path.parent / '_tstriage'
    for p in tstriageFolder.glob('*.*'):
        if p.stem == path.stem:
            CopyWithProgress2(p, destination / '_metadata' / p.name, quiet=quiet)

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
