import contextlib, json, logging, shutil, subprocess
from pathlib import Path
from typing import Any

from . import cli_config
from ._progress import SubprocessProgress
from .epg import EPG
from .epgstation import EPGStation
from .subprocess_utils import run, run_json, run_long, run_pipe

logger = logging.getLogger('tstriage.tasks')


def _q(quiet: bool) -> list[str]:
    return ['--quiet'] if quiet else []


def _pq(quiet: bool) -> list[str]:
    flags = ['--progress']
    if quiet:
        flags.append('--quiet')
    return flags


def Encode(item: dict[str, Any], epgStation: EPGStation, encoder: str, presets: dict, quiet: bool, progress: SubprocessProgress | None = None):
    """Step 1: Full-file TS→MKV encode + EPG + YAML + logo + audio check + ASS extraction."""
    path = Path(item['path'])
    destination = Path(item['destination'])
    workingPath = Path(item['path'])
    metadata = destination / '_metadata'

    presetName = item['encoder']['preset']
    cropdetect = item['encoder'].get('cropdetect')
    fixAudio = item['encoder'].get('fixaudio', False)

    # 1. EPG dump from original TS
    metadata.mkdir(parents=True, exist_ok=True)
    epgPath = metadata / workingPath.with_suffix('.epg').name
    with (progress.status("Extracting EPG") if progress else contextlib.nullcontext()):
        EPG.Dump(workingPath, epgPath, quiet=quiet)

    # 2. Audio check on original TS
    ffmpeg_path = 'ffmpeg'
    ffprobe_path = 'ffprobe'
    result = subprocess.run(
        [ffprobe_path, '-v', 'error', '-select_streams', 'a',
         '-show_entries', 'stream=index', '-of', 'csv=p=0', str(workingPath)],
        capture_output=True, text=True, check=True)
    audio_global_indices = list(dict.fromkeys([line.strip() for line in result.stdout.strip().split('\n') if line.strip()]))

    with (progress.status("Checking audio") if progress else contextlib.nullcontext()):
        for audio_pos, global_idx in enumerate(audio_global_indices):
            decode_result = subprocess.run(
                [ffmpeg_path, '-v', 'error', '-err_detect', 'aggressive',
                 '-i', str(workingPath), '-map', f'0:a:{audio_pos}',
                 '-t', '2', '-f', 'null', '-'],
                capture_output=True, text=True)
            error_lines = [line for line in decode_result.stderr.strip().split('\n')
                           if 'channel element' in line and 'is not allocated' in line]
            if error_lines:
                logger.warning(f'Audio stream #{global_idx} has decode errors:')
                for line in error_lines:
                    logger.warning(f'  {line}')
                item['encoder']['fixaudio'] = True
                fixAudio = True

    # 3. Probe original TS for video params
    probe_data = run_json(cli_config.tscutter('probe', '--input', str(workingPath)))
    if probe_data is None:
        raise RuntimeError('tscutter probe failed')

    # 4. Generate program info YAML
    epg = EPG(epgPath, probe_data['serviceId'], epgStation.GetChannels())
    yamlPath = destination / workingPath.with_suffix('.yaml').name
    epg.OutputDesc(yamlPath)

    if progress:
        progress.clear_parent_desc()

    # 6. Encode TS → MKV (embed ASS from TS) + fix ASS background + remux
    outFile = destination / workingPath.with_suffix('.mkv').name
    tmpAss = metadata / f'{workingPath.stem}.ass'
    from .input_file import InputFile
    inputFile = InputFile(workingPath)
    encode_cmd = inputFile.EncodeTsCmd(
        str(workingPath), str(outFile),
        presets[presetName], encoder,
        fixAudio=fixAudio)
    if progress is not None:
        info = inputFile.GetInfo()
        progress.add_task("ffmpeg_encode", info.duration, "Encoding", unit="s")
    run_long(encode_cmd, progress=progress)
    if progress is not None:
        progress.done("ffmpeg_encode")
    # Extract ASS from MKV, fix background + end time, remux
    has_subs = subprocess.run(
        [inputFile.ffprobe, '-v', 'error', '-select_streams', 's',
         '-show_entries', 'stream=index', '-of', 'csv=p=0', str(outFile)],
        capture_output=True, text=True)
    if has_subs.stdout.strip():
        subprocess.run([inputFile.ffmpeg, '-y', '-i', str(outFile),
                       '-map', '0:s:0', str(tmpAss)], capture_output=True, check=True)
        dur = inputFile.GetInfo().duration
        content = tmpAss.read_text(encoding='utf-8')
        content = content.replace(
            ',&H0,&H0,0,0,0,0,100,100,0,0,4,',
            ',&H0,&H80000000,0,0,0,0,100,100,0,0,4,')
        fixed_lines = []
        for line in content.split('\n'):
            if line.startswith('Dialogue:'):
                parts = line.split(',')
                if len(parts) >= 3:
                    try:
                        h, m, s = parts[2].split(':')
                        end_sec = int(h) * 3600 + int(m) * 60 + float(s)
                        if end_sec > dur:
                            h2 = int(dur // 3600)
                            m2 = int((dur % 3600) // 60)
                            s2 = dur % 60
                            parts[2] = f'{h2}:{m2:02d}:{s2:05.2f}'
                            line = ','.join(parts)
                    except (ValueError, IndexError):
                        pass
            fixed_lines.append(line)
        content = '\n'.join(fixed_lines)
        tmpAss.write_text(content, encoding='utf-8')
        tempMkv = outFile.with_suffix('.tmp.mkv')
        subprocess.run([inputFile.ffmpeg, '-y',
                       '-i', str(outFile), '-i', str(tmpAss),
                       '-map', '0:v', '-map', '0:a', '-c', 'copy',
                       '-map', '1:s', '-c:s', 'ass',
                       '-metadata:s:s:0', 'language=jpn',
                       '-disposition:s:0', 'default',
                       '-map_metadata', '0',
                       str(tempMkv)], capture_output=True, check=True)
        tempMkv.replace(outFile)
        tmpAss.unlink()


def Index(item: dict[str, Any], epgStation: EPGStation, quiet: bool, progress: SubprocessProgress | None = None):
    """Step 2: tscutter index on encoded MKV → .ptsmap + logo extraction."""
    destination = Path(item['destination'])
    workingPath = destination / Path(item['path']).with_suffix('.mkv').name
    metadata = destination / '_metadata'
    indexPath = metadata / workingPath.with_suffix('.ptsmap').name

    minSilenceLen = item.get('cutter', {}).get('minSilenceLen', 800)
    silenceThresh = item.get('cutter', {}).get('silenceThresh', -80)
    splitPosShift = item.get('cutter', {}).get('splitPosShift', 1)

    run_pipe(cli_config.tscutter(
        *_pq(quiet), 'index',
        '--input', str(workingPath),
        '--output', str(indexPath),
        '--length', str(minSilenceLen),
        '--threshold', str(silenceThresh),
        '--shift', str(splitPosShift),
    ), progress=progress)

    # Logo extraction (moved from encode — reuses this ptsmap)
    probe_data = run_json(cli_config.tscutter('probe', '--input', str(workingPath)))
    if probe_data is None:
        raise RuntimeError('tscutter probe failed in index')
    epgPath = metadata / Path(item['path']).with_suffix('.epg').name
    epg = EPG(epgPath, probe_data['serviceId'], epgStation.GetChannels())
    logoPath = (Path(item['path']).parent / '_tstriage' / f'{epg.Channel()}_{probe_data["width"]}x{probe_data["height"]}').with_suffix('.png')
    if not logoPath.exists():
        run_pipe(cli_config.tsmarker(
            *_pq(quiet), 'extract-logo',
            '--input', str(workingPath),
            '--index', str(indexPath),
            '--output', str(logoPath),
            '--max-time', '999999',
        ), progress=progress)


def Mark(item: dict[str, Any], epgStation: EPGStation, quiet: bool, progress: SubprocessProgress | None = None):
    """Step 3: tsmarker mark + ensemble + EDL generation."""
    destination = Path(item['destination'])
    path = Path(item['path'])
    workingPath = destination / path.with_suffix('.mkv').name
    metadata = destination / '_metadata'

    indexPath = metadata / workingPath.with_suffix('.ptsmap').name
    markerPath = metadata / workingPath.with_suffix('.markermap').name
    edlPath = destination / workingPath.with_suffix('.edl').name

    if markerPath.exists() and indexPath.stat().st_mtime > markerPath.stat().st_mtime:
        logger.warning(f'removing {markerPath} ...')
        markerPath.unlink()

    # Probe original TS for serviceId/channel (MKV has no TS program metadata)
    originalTS = Path(item['path'])
    probe_data = run_json(cli_config.tscutter('probe', '--input', str(originalTS)))
    if probe_data is None:
        raise RuntimeError('tscutter probe failed in mark task')
    epgPath = metadata / workingPath.with_suffix('.epg').name
    epg = EPG(epgPath, probe_data['serviceId'], epgStation.GetChannels())
    logoPath = (originalTS.parent / '_tstriage' / f'{epg.Channel()}_{probe_data["width"]}x{probe_data["height"]}').with_suffix('.png')

    run_pipe(cli_config.tsmarker(
        *_pq(quiet), 'mark',
        '--method', 'subtitles', '--method', 'clipinfo', '--method', 'logo', '--method', 'speech',
        '--input', str(workingPath),
        '--index', str(indexPath),
        '--marker', str(markerPath),
        '--logo', str(logoPath),
        '--edl', str(edlPath),
    ), progress=progress)

    noEnsemble = item.get('marker', {}).get('noEnsemble', False)
    outputFolder = Path(item['destination'])
    if not noEnsemble:
        searchFolder = outputFolder.parent.parent
        datasetCsv = Path(searchFolder.stem).with_suffix('.csv')

        ds_result = run(cli_config.tsmarker(
            *_q(quiet), 'ensemble-dataset',
            '--input', str(searchFolder),
            '--output', str(datasetCsv),
        ), capture_stderr=True)

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

    # Regenerate EDL with ensemble prediction
    run(cli_config.tsmarker(
        *_q(quiet), 'generate-edl',
        '--marker', str(markerPath),
        '--index', str(indexPath),
        '--output', str(edlPath),
    ))


def Cut(item: dict[str, str], outputFolder: Path, quiet: bool, progress: SubprocessProgress | None = None):
    """Step 4: Physical split into clips for manual review."""
    destination = Path(item['destination'])
    workingPath = destination / Path(item['path']).with_suffix('.mkv').name
    metadata = destination / '_metadata'

    indexPath = metadata / workingPath.with_suffix('.ptsmap').name
    markerPath = metadata / workingPath.with_suffix('.markermap').name

    run_pipe(cli_config.tsmarker(
        *_pq(quiet), 'cut',
        '--input', str(workingPath),
        '--index', str(indexPath),
        '--marker', str(markerPath),
        '--output', str(outputFolder),
    ), progress=progress)


def Confirm(item: dict[str, str], outputFolder: Path):
    """Step 5: Groundtruth from manual review + regenerate EDL."""
    destination = Path(item['destination'])
    workingPath = destination / Path(item['path']).with_suffix('.mkv').name
    metadata = destination / '_metadata'

    markerPath = metadata / workingPath.with_suffix('.markermap').name
    indexPath = metadata / workingPath.with_suffix('.ptsmap').name
    edlPath = destination / workingPath.with_suffix('.edl').name

    run(cli_config.tsmarker(
        'groundtruth',
        '--input', str(workingPath),
        '--index', str(indexPath),
        '--marker', str(markerPath),
        '--clips', str(outputFolder),
        '--edl', str(edlPath),
    ))
    return False


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
