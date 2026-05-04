import json, logging, subprocess, tempfile
from pathlib import Path
import pysubs2
import yaml
from . import cli_config
from .input_file import InputFile
from .tee import Tee

logger = logging.getLogger('tstriage.pipeline')


def _load_audio(yaml_path: Path) -> list[dict] | None:
    if not yaml_path.exists():
        logger.info(f'YAML file not found: {yaml_path}')
        return None
    try:
        with open(yaml_path, 'r', encoding='utf-8') as f:
            metadata = yaml.safe_load(f)
            audios = metadata.get('audios', [])
    except Exception as e:
        logger.warning(f'Failed to load audio config from {yaml_path}: {e}')
        return None

    if not isinstance(audios, list):
        logger.warning(f'audios field is not a list in {yaml_path.name}, ignoring')
        return None

    logger.info(f'Loaded audio config from {yaml_path.name}')
    for i, a in enumerate(audios):
        if not isinstance(a, dict):
            continue
        ct = a.get('componentType')
        label = {2: 'Dual mono', 3: 'Normal stereo'}.get(ct, f'Unknown ({ct})')
        logger.info(f'Audio track {i}: {label}, {a.get("samplingRate", "?")}Hz, {a.get("langs", [])}')
    return audios


def _get_program_clips(ptsmap_path: Path, markermap_path: Path, split_num: int, by_group: bool, quiet: bool) -> list[list]:
    cmd = cli_config.tsmarker('get-program-clips',
                              '--marker', str(markermap_path),
                              '--index', str(ptsmap_path))
    if split_num > 1:
        cmd += ['--split', str(split_num)]
    elif by_group:
        cmd += ['--by-group']
    if quiet:
        cmd += ['--quiet']

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'tsmarker get-program-clips failed: {result.stderr}')
    data = json.loads(result.stdout)
    logger.info(f'Using method: {data["by_method"]}')
    return data['groups']


def _detect_crop(inFile: Path, ptsmap_path: Path, quiet: bool) -> dict | None:
    with tempfile.TemporaryDirectory(prefix='EncodePipeline_') as td:
        logo_path = Path(td) / (inFile.stem + '_logo.png')
        qflag = ['--quiet'] if quiet else []
        subprocess.run(cli_config.tsmarker('extract-logo',
                                           '--input', str(inFile),
                                           '--index', str(ptsmap_path),
                                           '--output', str(logo_path),
                                           '--max-time', '10',
                                           '--no-remove-border') + qflag, check=True)
        result = subprocess.run(cli_config.tsmarker('crop-detect', '--input', str(logo_path)),
                                capture_output=True, text=True, check=True)
        if not result.stdout.strip() or result.stdout.strip() == 'null':
            return None

        crop = json.loads(result.stdout)
        info = InputFile(inFile).GetInfo()

        w, h = crop['w'], crop['h']
        dar_found = None
        for dar in [(16, 9), (4, 3), (1, 1)]:
            ratio = w * info.sar[0] / (h * info.sar[1]) / (dar[0] / dar[1])
            if 0.95 < ratio < 1.05:
                dar_found = dar
                break

        if dar_found and w * h / (info.width * info.height) < 0.9:
            crop['dar'], crop['sar'] = dar_found, info.sar
            return crop
        return None


def _start_subtitles_process(out_subtitles: Path, out_file: Path):
    si = subprocess.STARTUPINFO(wShowWindow=6, dwFlags=subprocess.STARTF_USESHOWWINDOW) if hasattr(subprocess, 'STARTUPINFO') else None
    cf = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
    return subprocess.Popen(
        f'Caption2AssC.cmd - "{out_subtitles / out_file.with_suffix("").name}"',
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        startupinfo=si, creationflags=cf, shell=True)


def EncodePipeline(inFile: Path, ptsmap_path: Path, markermap_path: Path, outFile: Path, outSubtitles: Path,
                   byGroup: bool, splitNum: int, preset: dict, cropdetect: bool, encoder: str,
                   fixAudio: bool, noStrip: bool, quiet=False):

    audio_config = _load_audio(outFile.parent / inFile.with_suffix('.yaml').name)

    groups = _get_program_clips(ptsmap_path, markermap_path, splitNum, byGroup, quiet)
    total = sum(clip[1] - clip[0] for group in groups for clip in group)
    logger.info(f'Extracted Program length: {total}')
    logger.info(f'Will be encoded into {len(groups)} files')

    crop = _detect_crop(inFile, ptsmap_path, quiet) if cropdetect else None
    inputFile = InputFile(inFile)

    for i, clips in enumerate(groups):
        currentOut = outFile if len(groups) == 1 else outFile.parent / f'{outFile.stem}_{i}.mkv'
        logger.info(f'Encoding {currentOut.name} ...')
        currentOut.unlink(missing_ok=True)
        currentOut.touch()

        encode_cmd = inputFile.EncodeTsCmd('-', str(currentOut), preset, encoder, crop, audio_config, ['jpn'])
        encodeP = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=open('encode.log', 'w'))

        with encodeP:
            subsP = _start_subtitles_process(outSubtitles, currentOut)

            extract_cmd = cli_config.tsmarker('extract-clips',
                                              '--input', str(inFile),
                                              '--index', str(ptsmap_path),
                                              '--clips', json.dumps(clips))
            if quiet:
                extract_cmd.append('--quiet')

            if noStrip:
                extractP = subprocess.Popen(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                with subsP, extractP:
                    Tee(encodeP.stdin, subsP.stdin, broken_ok=(subsP.stdin,)).pump(extractP.stdout)
            else:
                strip_cmd = inputFile.StripTsCmd('-', '-', ['jpn'], fixAudio=fixAudio, audio_config=audio_config)
                stripP = subprocess.Popen(strip_cmd, stdin=subprocess.PIPE, stdout=encodeP.stdin,
                                          stderr=open('strip.log', 'w'))
                extractP = subprocess.Popen(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                with stripP, subsP, extractP:
                    Tee(stripP.stdin, subsP.stdin, broken_ok=(subsP.stdin,)).pump(extractP.stdout)

        logger.info('Trying to fix issues in subtitles ...')
        base = outSubtitles / currentOut.with_suffix('').name
        for suffix in ('.ass', '.srt'):
            sp = base.with_suffix(suffix)
            if sp.exists():
                pysubs2.load(str(sp), encoding='utf-8').save(str(sp))
