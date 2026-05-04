import json, logging, shutil, subprocess
from pathlib import Path
from typing import Any

from . import cli_config
from .epg import EPG
from .epgstation import EPGStation
from .pipeline import EncodePipeline
from .subprocess_utils import run, run_json

logger = logging.getLogger('tstriage.tasks')


def _q(quiet: bool) -> list[str]:
    return ['--quiet'] if quiet else []


def Analyze(item: dict[str, Any], epgStation: EPGStation, quiet: bool):
    path = Path(item['path'])
    destination = Path(item['destination'])
    workingPath = Path(item['path'])

    logger.info('Analyzing to split ...')
    indexPath = destination / '_metadata' / workingPath.with_suffix('.ptsmap').name

    minSilenceLen = item.get('cutter', {}).get('minSilenceLen', 800)
    silenceThresh = item.get('cutter', {}).get('silenceThresh', -80)
    splitPosShift = item.get('cutter', {}).get('splitPosShift', 1)

    result = run(cli_config.tscutter(
        *_q(quiet), 'analyze',
        '--input', str(workingPath),
        '--output', str(indexPath),
        '--length', str(minSilenceLen),
        '--threshold', str(silenceThresh),
        '--shift', str(splitPosShift),
    ))
    if result.returncode != 0:
        raise RuntimeError(f'tscutter analyze failed: {result.stderr}')

    logger.info('Extracting EPG ...')
    epgPath = destination / '_metadata' / workingPath.with_suffix('.epg').name
    EPG.Dump(workingPath, epgPath, quiet=quiet)

    probe_data = run_json(cli_config.tscutter(*_q(quiet), 'probe', '--input', str(workingPath)))
    if probe_data is None:
        raise RuntimeError('tscutter probe failed')

    epg = EPG(epgPath, probe_data['serviceId'], epgStation.GetChannels())
    epg.OutputDesc(destination / workingPath.with_suffix('.yaml').name)

    logger.info('Extracting subtitles ...')
    run(cli_config.tsmarker(
        *_q(quiet), 'prepare-subtitles',
        '--input', str(workingPath),
        '--index', str(indexPath),
    ))

    logoPath = (path.parent / '_tstriage' / f'{epg.Channel()}_{probe_data["width"]}x{probe_data["height"]}').with_suffix('.png')
    if not logoPath.exists():
        run(cli_config.tsmarker(
            *_q(quiet), 'extract-logo',
            '--input', str(workingPath),
            '--index', str(indexPath),
            '--output', str(logoPath),
            '--max-time', '999999',
        ))

    logger.info('Checking audio streams for decode errors...')
    ffmpeg_path = 'ffmpeg'
    ffprobe_path = 'ffprobe'

    result = subprocess.run(
        [ffprobe_path, '-v', 'error', '-select_streams', 'a',
         '-show_entries', 'stream=index', '-of', 'csv=p=0', str(workingPath)],
        capture_output=True, text=True, check=True)
    audio_global_indices = list(dict.fromkeys([line.strip() for line in result.stdout.strip().split('\n') if line.strip()]))

    for audio_pos, global_idx in enumerate(audio_global_indices):
        decode_result = subprocess.run(
            [ffmpeg_path, '-v', 'error', '-err_detect', 'aggressive',
             '-i', str(workingPath), '-map', f'0:a:{audio_pos}',
             '-t', '2', '-f', 'null', '-'],
            capture_output=True, text=True)

        error_lines = [line for line in decode_result.stderr.strip().split('\n')
                       if 'channel element' in line and 'is not allocated' in line]
        if error_lines:
            logger.warning(f'Audio stream {global_idx} (position {audio_pos}) has decode errors:')
            for line in error_lines:
                logger.warning(f'  {line}')
            item['encoder']['fixaudio'] = True


def Mark(item: dict[str, Any], epgStation: EPGStation, quiet: bool):
    path = Path(item['path'])
    destination = Path(item['destination'])
    workingPath = Path(item['path'])

    logger.info('Marking ...')
    indexPath = destination / '_metadata' / workingPath.with_suffix('.ptsmap').name
    markerPath = destination / '_metadata' / workingPath.with_suffix('.markermap').name

    if markerPath.exists() and indexPath.stat().st_mtime > markerPath.stat().st_mtime:
        logger.warning(f'removing {markerPath} ...')
        markerPath.unlink()

    probe_data = run_json(cli_config.tscutter('probe', '--input', str(workingPath)))
    if probe_data is None:
        raise RuntimeError('tscutter probe failed in mark task')
    epgPath = destination / '_metadata' / workingPath.with_suffix('.epg').name
    epg = EPG(epgPath, probe_data['serviceId'], epgStation.GetChannels())
    logoPath = (path.parent / '_tstriage' / f'{epg.Channel()}_{probe_data["width"]}x{probe_data["height"]}').with_suffix('.png')

    run(cli_config.tsmarker(
        *_q(quiet), 'mark',
        '--method', 'subtitles', 'clipinfo', 'logo', 'speech',
        '--input', str(workingPath),
        '--index', str(indexPath),
        '--marker', str(markerPath),
        '--logo', str(logoPath),
    ))

    noEnsemble = item.get('marker', {}).get('noEnsemble', False)
    outputFolder = Path(item['destination'])
    if not noEnsemble:
        searchFolder = outputFolder.parent.parent
        datasetCsv = Path(searchFolder.stem).with_suffix('.csv')

        ds_result = run(cli_config.tsmarker(
            *_q(quiet), 'ensemble-dataset',
            '--input', str(searchFolder),
            '--output', str(datasetCsv),
        ))

        if 'No metadata' not in ds_result.stderr and 'warning' not in ds_result.stderr.lower():
            modelPath = datasetCsv.with_suffix('.pkl')
            run(cli_config.tsmarker(
                *_q(quiet), 'ensemble-train',
                '--input', str(datasetCsv),
                '--output', str(modelPath),
            ))
            run(cli_config.tsmarker(
                *_q(quiet), 'ensemble-predict',
                '--model', str(modelPath),
                '--index', str(indexPath),
                '--marker', str(markerPath),
            ))


def Cut(item: dict[str, str], outputFolder: Path, quiet: bool):
    destination = Path(item['destination'])
    workingPath = Path(item['path'])

    logger.info('Cutting ...')
    indexPath = destination / '_metadata' / workingPath.with_suffix('.ptsmap').name
    markerPath = destination / '_metadata' / workingPath.with_suffix('.markermap').name

    run(cli_config.tsmarker(
        *_q(quiet), 'cut',
        '--input', str(workingPath),
        '--index', str(indexPath),
        '--marker', str(markerPath),
        '--output', str(outputFolder),
    ))


def Confirm(item: dict[str, str], outputFolder: Path):
    path = Path(item['path'])
    destination = Path(item['destination'])

    logger.info(f'Marking ground truth for {path.name} ...')
    markerPath = destination / '_metadata' / (path.stem + '.markermap')
    indexPath = markerPath.with_suffix('.ptsmap')

    result = run(cli_config.tsmarker(
        'groundtruth',
        '--input', str(path),
        '--index', str(indexPath),
        '--marker', str(markerPath),
        '--clips', str(outputFolder),
    ))
    re_encode = json.loads(result.stdout.strip()) if result.stdout.strip() else {'re_encode_needed': False}
    isReEncodingNeeded = re_encode.get('re_encode_needed', False)
    if isReEncodingNeeded:
        logger.warning("*** Re-encoding is needed! ***")
    return isReEncodingNeeded


def Encode(item: dict[str, Any], encoder: str, presets: dict, quiet: bool):
    path = Path(item['path'])
    destination = Path(item['destination'])
    byGroup = item.get('encoder', {}).get('bygroup', False)
    splitNum = item.get('encoder', {}).get('split', 1)
    presetName = item['encoder']['preset']
    cropdetect = item['encoder'].get('cropdetect')
    fixAudio = item['encoder'].get('fixaudio')
    noStrip = item['encoder'].get('nostrip')

    workingPath = Path(item['path'])

    ptsmap_path = destination / '_metadata' / path.with_suffix('.ptsmap').name
    markermap_path = destination / '_metadata' / path.with_suffix('.markermap').name

    outSubtitles = destination / 'Subtitles'
    outSubtitles.mkdir(parents=True, exist_ok=True)

    outFile = destination / workingPath.with_suffix('.mkv').name

    EncodePipeline(
        inFile=workingPath,
        ptsmap_path=ptsmap_path,
        markermap_path=markermap_path,
        outFile=outFile,
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


def Cleanup(item: dict[str, Any]):
    logger.info('Cleaning up ...')
    files = list((Path(item['path']).parent / '_tstriage').glob('*'))
    originalPath = Path(item['path'])
    for path in files:
        if path.stem in originalPath.stem or originalPath.stem in path.stem:
            logger.info(f'removing {path.name} ...')
            if path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
