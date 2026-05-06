import json, logging, shutil, subprocess, tempfile
from pathlib import Path
import pysubs2
import yaml
from . import cli_config
from .input_file import InputFile
from .subprocess_utils import run_json
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

    data = run_json(cmd)
    if data is None:
        raise RuntimeError('tsmarker get-program-clips failed')
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
    exe = shutil.which('Caption2AssC.cmd') or shutil.which('Caption2AssC')
    if exe is None:
        raise RuntimeError('Caption2AssC not found in PATH — install Caption2AssC or add it to PATH')
    si = subprocess.STARTUPINFO(wShowWindow=6, dwFlags=subprocess.STARTF_USESHOWWINDOW) if hasattr(subprocess, 'STARTUPINFO') else None
    cf = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
    return subprocess.Popen(
        f'"{exe}" - "{out_subtitles / out_file.with_suffix("").name}"',
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        startupinfo=si, creationflags=cf, shell=True)


def EncodePipeline(inFile: Path, ptsmap_path: Path, markermap_path: Path, outFile: Path, outSubtitles: Path,
                   byGroup: bool, splitNum: int, preset: dict, cropdetect: bool, encoder: str,
                   fixAudio: bool, noStrip: bool, quiet=False, progress=None):

    audio_config = _load_audio(outFile.parent / inFile.with_suffix('.yaml').name)

    groups = _get_program_clips(ptsmap_path, markermap_path, splitNum, byGroup, quiet)
    total = sum(clip[1] - clip[0] for group in groups for clip in group)
    mins = int(total // 60)
    secs = int(total % 60)
    logger.info(f'Duration: {total:.1f}s ({mins}m{secs}s)')
    n = len(groups)
    logger.info(f'Encoding into {n} file{"s" if n > 1 else ""}')

    crop = _detect_crop(inFile, ptsmap_path, quiet) if cropdetect else None
    inputFile = InputFile(inFile)

    for i, clips in enumerate(groups):
        currentOut = outFile if len(groups) == 1 else outFile.parent / f'{outFile.stem}_{i}.mkv'
        currentOut.unlink(missing_ok=True)
        currentOut.touch()

        # Calculate total bytes for progress tracking.
        # extractP outputs concatenated clip byte ranges from the original TS.
        # Tee.pump feeds these bytes to ffmpeg, so byte throughput ≈ encode progress.
        total_bytes = 0
        with open(ptsmap_path) as f:
            ptsmap_data = json.load(f)
        for clip in clips:
            start_pos = ptsmap_data[str(clip[0])]['next_start_pos']
            end_pos = ptsmap_data[str(clip[1])]['prev_end_pos']
            total_bytes += end_pos - start_pos

        encode_tid = "ffmpeg_encode"
        if progress is not None:
            progress.add_task(encode_tid, total_bytes, "Encoding", unit="B")
        bytes_read = 0
        def _on_chunk(n):
            nonlocal bytes_read
            bytes_read += n
            if progress is not None:
                progress.update(encode_tid, bytes_read)

        encode_cmd = inputFile.EncodeTsCmd('-', str(currentOut), preset, encoder, crop, audio_config, ['jpn'])
        encodeP = subprocess.Popen(encode_cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)

        with encodeP:
            subsP = _start_subtitles_process(outSubtitles, currentOut)

            extract_cmd = cli_config.tsmarker('extract-clips',
                                              '--input', str(inFile),
                                              '--index', str(ptsmap_path),
                                              '--clips', json.dumps(clips))
            if quiet:
                extract_cmd.append('--quiet')

            extractP = subprocess.Popen(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            if noStrip:
                try:
                    Tee(encodeP.stdin, subsP.stdin, broken_ok=(subsP.stdin,)).pump(
                        extractP.stdout, buf_size=1024*1024, on_chunk=_on_chunk)
                finally:
                    extractP.wait()
                    subsP.wait()
            else:
                strip_cmd = inputFile.StripTsCmd('-', '-', ['jpn'], fixAudio=fixAudio, audio_config=audio_config)
                stripP = subprocess.Popen(strip_cmd, stdin=subprocess.PIPE, stdout=encodeP.stdin,
                                          stderr=subprocess.DEVNULL)
                try:
                    Tee(stripP.stdin, subsP.stdin, broken_ok=(subsP.stdin,)).pump(
                        extractP.stdout, buf_size=1024*1024, on_chunk=_on_chunk)
                finally:
                    extractP.wait()
                    subsP.wait()
                    stripP.wait()
                if stripP.returncode != 0:
                    raise RuntimeError(f'ffmpeg strip failed (exit {stripP.returncode})')

            if extractP.returncode != 0:
                stderr_text = extractP.stderr.read().decode('utf-8', errors='replace').strip()
                raise RuntimeError(f'tsmarker extract-clips failed (exit {extractP.returncode}): {stderr_text}')
            if subsP.returncode != 0:
                stderr_text = subsP.stderr.read().decode('utf-8', errors='replace').strip()
                logger.error(f'Caption2AssC failed (exit {subsP.returncode}): {stderr_text}')
                raise RuntimeError(f'Caption2AssC failed (exit {subsP.returncode})')

        if encodeP.returncode != 0:
            raise RuntimeError(f'ffmpeg encode failed (exit {encodeP.returncode})')

        if progress is not None:
            progress.done(encode_tid)

        logger.info(f'Normalizing subtitle encoding: {currentOut.name}')
        base = outSubtitles / currentOut.with_suffix('').name
        for suffix in ('.ass', '.srt'):
            sp = base.with_suffix(suffix)
            if sp.exists():
                pysubs2.load(str(sp), encoding='utf-8').save(str(sp))
