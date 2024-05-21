from pathlib import Path
import shutil, logging
import tempfile
from typing import Dict
from tscutter.ffmpeg import InputFile
from tscutter.analyze import AnalyzeVideo
import tsmarker.common
from tsmarker import subtitles, logo, clipinfo, speech, ensemble, groundtruth
from tsmarker.pipeline import PtsMap, ExtractLogoPipeline
from tsmarker.speech.MarkerMap import PrepareSubtitles
from .common import CopyWithProgress2
from .epg import EPG
from .epgstation import EPGStation
from .pipeline import MarkerMap, EncodePipeline

logger = logging.getLogger('tstriage.tasks')

def CacheTS(item: Dict[str, str], quiet: bool) -> Path:
    path = Path(item['path'])
    if item['cache'] is not None:
        logger.info('Copying TS file to working folder ...')
        cache = Path(item['cache']).expanduser()
        workingPath = cache / path.name
        CopyWithProgress2(path, workingPath, quiet=quiet)
        return workingPath
    else:
        return path

def Analyze(item, epgStation: EPGStation, quiet: bool):
    path = Path(item['path'])
    destination = Path(item['destination'])
    workingPath = CacheTS(item, quiet)

    logger.info('Analyzing to split ...')
    indexPath = destination / '_metadata' / workingPath.with_suffix('.ptsmap').name
    minSilenceLen = item.get('cutter', {}).get('minSilenceLen', 800)
    silenceThresh =  item.get('cutter', {}).get('silenceThresh', -80)
    splitPosShift = item.get('cutter', {}).get('splitPosShift', 1)
    inputFile = InputFile(workingPath)
    AnalyzeVideo(inputFile=inputFile, indexPath=indexPath, silenceThresh=silenceThresh, minSilenceLen=minSilenceLen, splitPosShift=splitPosShift, quiet=quiet)

    logger.info('Extracting EPG ...')
    epgPath = destination / '_metadata' / workingPath.with_suffix('.epg').name
    EPG.Dump(workingPath, epgPath, quiet=quiet)
    epg = EPG(epgPath, inputFile,  epgStation.GetChannels())
    epg.OutputDesc(destination / workingPath.with_suffix('.yaml').name)

    logger.info('Extracting subtitles ...')
    PrepareSubtitles(path, PtsMap(indexPath), quiet=quiet)
    
    info = inputFile.GetInfo()
    logoPath = (path.parent / '_tstriage' / f'{epg.Channel()}_{info.width}x{info.height}').with_suffix('.png')
    if not logoPath.exists():
        ExtractLogoPipeline(inFile=workingPath, ptsMap=PtsMap(indexPath), outFile=logoPath, maxTimeToExtract=999999, quiet=quiet)

def Mark(item, epgStation: EPGStation, bertService: str, quiet: bool):
    path = Path(item['path'])
    destination = Path(item['destination'])
    workingPath = CacheTS(item, quiet)

    logger.info('Marking ...')
    indexPath = destination / '_metadata' / workingPath.with_suffix('.ptsmap').name
    markerPath = destination / '_metadata' /  workingPath.with_suffix('.markermap').name
    if markerPath.exists() and indexPath.stat().st_mtime > markerPath.stat().st_mtime:
         logger.warn(f'removing {markerPath} ...')
         markerPath.unlink()
    subtitles.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(videoPath=workingPath, assPath=destination / '_metadata' / path.with_suffix('.ass.original').name)
    clipinfo.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(videoPath=workingPath, quiet=quiet)
    inputFile = InputFile(workingPath)
    epg = EPG(destination / '_metadata' / path.with_suffix('.epg').name, inputFile, epgStation.GetChannels())
    info = inputFile.GetInfo()
    logoPath = (path.parent / '_tstriage' / f'{epg.Channel()}_{info.width}x{info.height}').with_suffix('.png')
    logo.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(videoPath=workingPath, logoPath=logoPath, quiet=quiet)
    speech.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(videoPath=workingPath, apiUrl=bertService + '/api/mark/speech', quiet=quiet)

    noEnsemble = item['marker'].get('noEnsemble', False)
    outputFolder = Path(item['destination'])
    byEnsemble = not noEnsemble
    if byEnsemble:
        searchFolder = outputFolder.parent.parent
        normalize = True
        # # find metadata folder
        # if (outputFolder / '_metadata').exists() and len(list((outputFolder / '_metadata').glob('*.markermap'))) > 5:
        #     metadataPath = outputFolder
        #     normalize = False
        #     logger.info(f'Using metadata in {metadataPath} ...')
        # else:
        #     metadataPath = outputFolder.parent
        #     normalize = True
        #     logger.info(f'Trying to use metadata in {metadataPath} ...')
        with tempfile.TemporaryDirectory(prefix='EnsembleDataSet_') as tmpFolder:
            # generate dataset
            datasetCsv = Path(tmpFolder) / workingPath.with_suffix('.csv').name
            df = ensemble.CreateDataset(
                folder=searchFolder, 
                csvPath=datasetCsv, 
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
                logger.warn(f'No metadata is found in {searchFolder}!')
                byEnsemble = False

def Cut(item: Dict[str, str], outputFolder: Path, quiet: bool):
    destination = Path(item['destination'])
    workingPath = CacheTS(item, quiet)

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
    markerMap.Cut(videoPath=workingPath, byMethod=byMethod, outputFolder=outputFolder, quiet=quiet)

def Confirm(item: Dict[str, str], outputFolder: Path):
    path = Path(item['path'])
    destination = Path(item['destination'])

    logger.info(f'Marking ground truth for {path.name} ...')
    markerPath = destination / '_metadata' / (path.stem + '.markermap')
    indexPath = markerPath.with_suffix('.ptsmap')
    isReEncodingNeeded = groundtruth.MarkerMap(markerPath, PtsMap(indexPath)).MarkAll(clipsFolder=outputFolder)
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

    workingPath = Path(item['path'])
    outFile = workingPath.with_suffix('.mp4')
    outSubtitles = destination / 'Subtitles'
    outSubtitles.mkdir(parents=True, exist_ok=True)
    EncodePipeline(
        inFile=workingPath,
        ptsMap=ptsMap,
        markerMap=markerMap,
        outFile=destination / workingPath.with_suffix('.mp4').name,
        outSubtitles=outSubtitles,
        byGroup=byGroup,
        splitNum=splitNum,
        preset=presets[presetName],
        cropdetect=cropdetect,
        encoder=encoder,
        fixAudio=fixAudio,
        noStrip=noStrip,
        quiet=quiet)
    srtPath = destination / 'Subtitles' / workingPath.with_suffix('.srt').name
    if srtPath.exists():
        newSrtPath = srtPath.with_suffix('.ssrrtt')
        shutil.copy(srtPath, newSrtPath)
        srtPath.unlink()
    else:
        try:
            outSubtitles.rmdir()
        except OSError:
            pass
    return outFile

def Cleanup(item):
    logger.info('Cleaning up ...')
    files = list(Path(item['cache']).glob('*')) if item['cache'] is not None else []
    files += list((Path(item['path']).parent / '_tstriage').glob('*'))
    originalPath = Path(item['path'])
    for path in files:
        if path.stem in originalPath.stem or originalPath.stem in path.stem:
            logger.info(f'removing {path.name} ...')
            if path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
