import logging, subprocess, tempfile
from pathlib import Path
import pysubs2
import yaml
from tscutter import ffmpeg
from tscutter.common import GetShortPath
from tsmarker.pipeline import PtsMap, ExtractLogoPipeline, CropDetectPipeline
import tsmarker.common

logger = logging.getLogger('tstriage.pipeline')

class InputFile(ffmpeg.InputFile):
    def StripTsCmd(self, inFile, outFile, audioLanguages=['jpn'], fixAudio=False, noMap=False, audio_config=None):
        args = [
            self.ffmpeg, '-hide_banner', '-y',
            '-i', GetShortPath(inFile),
            '-c:v', 'copy'
        ]
        if fixAudio:
            args += [ 
                '-af',  'aresample=async=1',
                '-c:a', 'aac', '-ar', '48000', '-ac', '2'
            ]
        else:
            args += [ '-c:a', 'copy' ]
        if not noMap:
            args += [ '-map', '0:v', '-map', '0:a', '-ignore_unknown' ]
            for i in range(len(audioLanguages)):
                args += [ f'-metadata:s:a:{i}', f'language={audioLanguages[i]}' ]
        args += [ '-f', 'mpegts', outFile ]
        return args

    def EncodeTsCmd(self, inPath, outPath, preset, encoder, crop=None, audio_config=None, audioLanguages=['jpn']):
        videoFilter = preset.get('videoFilter') or ''
        if crop:
            filters = videoFilter.split(',') if videoFilter else []
            w, h, x, y, dar, sar = crop['w'], crop['h'], crop['x'], crop['y'], crop['dar'], crop['sar']
            cropStr = f'crop={w}:{h}:{x}:{y},setdar=dar={dar[0]}/{dar[1]}'
            filters.insert(0, cropStr)
            if 'scale=' in filters[-1]:
                scale = filters.pop().replace('scale=', '').split(':')
                scale_h = int(scale[1])
                scale_w = round(scale_h * dar[0] * sar[1] / dar[1] / sar[0])
                filters.append(f'scale={scale_w}:{scale_h}')
            else:
                scale_h = round(h / dar[1] / sar[0]) * dar[1] * sar[0]
                scale_w = round(scale_h * dar[0] * sar[1] / dar[1] / sar[0])
                filters.append(f'scale={scale_w}:{scale_h}')
            videoFilter = ','.join(filters)
        if '_nvenc' in encoder:
            # Pure CRF-like mode, similar to x264's -crf experience
            # Uses only crf for quality control, ignores bitrate and maxRate from config
            # NVENC CQ requires higher values than x264 CRF for similar file sizes
            # Based on testing with preset p7: cq 31 ≈ x264 crf 27, so offset is +4
            nvenc_cq = preset['crf'] + 4
            videoCodec = [
                '-c:v', encoder,
                '-rc:v', 'vbr',          # Use vbr mode (vbr_hq is deprecated)
                '-cq:v', str(nvenc_cq),  # Constant quality parameter, adjusted for NVENC
                '-b:v', '0',             # Must be set to 0 to enable true constant quality mode
                '-profile:v', 'high',
                '-preset', 'p7',         # Use p7 preset (highest quality)
                '-tune', 'hq'            # High quality tuning
                # No -maxrate and -bufsize, fully controlled by -cq:v
            ]
        else:
            videoCodec = [ '-c:v', encoder, '-crf', str(preset['crf']) ]
        args = [
            self.ffmpeg, '-hide_banner', '-y',
            '-i', GetShortPath(inPath)
        ]
        if len(videoFilter) > 0:
            args += [ '-vf', videoFilter ]
        args += videoCodec + [
            #https://stackoverflow.com/questions/49686244/ffmpeg-too-many-packets-buffered-for-output-stream-01
            #'-max_muxing_queue_size', '1024',
        ]
        # Audio encoding logic
        has_dual_mono = False
        sampling_rate = 'N/A'
        audio_langs = []
        if audio_config:
            for audio in audio_config:
                if audio.get('componentType') == 2:
                    has_dual_mono = True
                    sampling_rate = audio.get('samplingRate', 'N/A')
                    audio_langs = audio.get('langs', [])
                    logger.info(f'Encoding: Dual mono audio detected, componentType=2, sampling rate: {sampling_rate}Hz, languages: {audio_langs}')
                    break

        if has_dual_mono:
            # Dual mono: split stereo into two mono channels and encode to AAC
            args += ['-filter_complex', '[0:a]channelsplit=channel_layout=stereo[left][right]']
            args += ['-map', '0:v', '-map', '[left]', '-map', '[right]']
            # Encode to mono AAC, keep 48kHz sampling rate
            args += ['-c:a', 'aac', '-ar', '48000', '-ac', '1', '-b:a', '128k']
            # Set language metadata - use languages from audio config if available
            for i in range(2):
                if i < len(audio_langs):
                    lang = audio_langs[i]
                elif i < len(audioLanguages):
                    lang = audioLanguages[i]
                else:
                    lang = 'jpn'
                args += [f'-metadata:s:a:{i}', f'language={lang}']
            args += ['-bsf:a', 'aac_adtstoasc']
        else:
            # Default behavior: copy audio
            args += [ '-c:a', 'copy', '-bsf:a', 'aac_adtstoasc' ]
            #args += ['-c:a', 'libvorbis', '-ar', '48000', '-b:a', '192k', '-ac', '2']
            args += [ '-map', '0:v', '-map', '0:a', '-ignore_unknown' ]
        args += [ outPath ]
        return args

class Tee(object):
    def __init__(self, outPipes: list, couldBeBroken: list=[], pbar=None):
        self.outPipes = outPipes
        self.couldBeBroken = couldBeBroken
        self.pbar = pbar

    def write(self, data):
        brokenPipes = []
        for pipe in self.outPipes:
            if not pipe in brokenPipes:
                try:
                    pipe.write(data)
                except (BrokenPipeError, OSError):
                    if pipe in self.couldBeBroken:
                        brokenPipes.append(pipe)
                    else:
                        raise
        if self.pbar is not None:
            self.pbar.update(len(data))
    
    def close(self):
        for pipe in self.outPipes:
            pipe.close()

class MarkerMap(tsmarker.common.MarkerMap):
    def GetProgramClips(self) -> list:
        if '_groundtruth' in self.Properties():
            clips = [ clip for clip in self.Clips() if self.Value(clip, '_groundtruth') == 1.0 ]
            logger.info('Use _groundtruth to retrieve program clips ...')
        elif '_ensemble' in self.Properties():
            clips = [ clip for clip in self.Clips() if self.Value(clip, '_ensemble') == 1.0 ]
            logger.info('Use _ensemble to retrieve program clips ...')
        else:
            clips = [ clip for clip in self.Clips() if self.Value(clip, 'subtitles') == 1.0 ]
            logger.info('Use subtitles to retrieve program clips ...')
        return clips
    
    @staticmethod
    def MergeNeighbors(clips: list) -> list:
         # merge neighbor clips
        mergedClips = []
        for clip in clips:
            if mergedClips == []:
                mergedClips.append(clip)
            else:
                previousClip = mergedClips.pop()
                if previousClip[1] == clip[0]:
                    mergedClips.append((previousClip[0], clip[1]))
                else:
                    mergedClips.append(previousClip)
                    mergedClips.append(clip)
        return mergedClips

    @staticmethod
    def GetClipsDuration(clips):
        duration = 0
        for clip in clips:
            duration += clip[1] - clip[0]
        return duration

    @staticmethod
    def SplitClips(programClips: list, num: int) -> list[list]:
        splittedClips = []
        programsDuration = MarkerMap.GetClipsDuration(programClips)
        meanDuration = programsDuration / num
        for i in range(num):
            clips = []
            minDistance = meanDuration
            while programClips != []:                
                clips.append(programClips.pop(0))
                distance = abs(MarkerMap.GetClipsDuration(clips) - meanDuration)
                if distance >= minDistance:
                    programClips.insert(0, clips.pop())
                    break
                else:
                    minDistance = distance
            splittedClips.append(clips)
        splittedClips[-1] += programClips
        return splittedClips

def EncodePipeline(inFile: Path, ptsMap: PtsMap, markerMap: MarkerMap, outFile: Path, outSubtitles: Path, byGroup: bool, splitNum: int, preset: dict, cropdetect: bool, encoder: str, fixAudio: bool, noStrip: bool, quiet=False):
    # Load audio configuration from YAML file
    audio_config = None
    yaml_path = outFile.parent / inFile.with_suffix('.yaml').name
    if yaml_path.exists():
        try:
            with open(yaml_path, 'r', encoding='utf-8') as f:
                metadata = yaml.safe_load(f)
                if 'audios' in metadata:
                    audio_config = metadata['audios']
                    if not isinstance(audio_config, list):
                        logger.warning(f'audios field is not a list in {yaml_path.name}, ignoring audio config')
                        audio_config = None
                    else:
                        logger.info(f'Loaded audio config from {yaml_path.name}')
                        # Log audio sampling mode information
                        for i, audio in enumerate(audio_config):
                            if not isinstance(audio, dict):
                                logger.warning(f'Audio track {i} is not a dictionary, skipping')
                                continue
                            component_type = audio.get('componentType')
                            sampling_rate = audio.get('samplingRate', 'N/A')
                            langs = audio.get('langs', [])
                            if component_type == 2:
                                logger.info(f'Audio track {i}: Dual mono (componentType=2), sampling rate: {sampling_rate}Hz, languages: {langs}')
                            elif component_type == 3:
                                logger.info(f'Audio track {i}: Normal stereo (componentType=3), sampling rate: {sampling_rate}Hz, languages: {langs}')
                            else:
                                logger.info(f'Audio track {i}: Unknown type (componentType={component_type}), sampling rate: {sampling_rate}Hz, languages: {langs}')
                else:
                    logger.info(f'No audios field in {yaml_path.name}')
        except Exception as e:
            logger.warning(f'Failed to load audio config from {yaml_path}: {e}')
    else:
        logger.info(f'YAML file not found: {yaml_path}')


    programClips = markerMap.GetProgramClips()
    if splitNum > 1:
        programClipsList = [ MarkerMap.MergeNeighbors(clips) for clips in MarkerMap.SplitClips(programClips, splitNum) ]
    elif byGroup:
        programClipsList = [ [clip] for clip in MarkerMap.MergeNeighbors(programClips) ]
    else:
        programClipsList = [ MarkerMap.MergeNeighbors(programClips) ]
    programLength = 0
    for clips in programClipsList:
        for clip in clips:
            programLength += clip[1] - clip[0]
    logger.info(f'Extracted Program length: {programLength}')
    logger.info(f'Will be encoded into {len(programClipsList)} files')
    cropInfo = None
    if cropdetect:
        with tempfile.TemporaryDirectory(prefix='EncodePipeline_') as tmpFolder:
            logoPath = Path(tmpFolder) / (inFile.stem + '_logo.png')
            ExtractLogoPipeline(inFile, ptsMap, logoPath, maxTimeToExtract=10, removeBoarder=False, quiet=quiet)
            cropInfo = CropDetectPipeline(logoPath)
            if cropInfo is not None:
                # double check if cropping is really needed
                videoInfo = InputFile(inFile).GetInfo()
                sar = videoInfo.sar
                w, h = cropInfo['w'], cropInfo['h']
                availableDARs: list[tuple[int, int]] = [(16, 9), (4, 3), (1,1)]
                darFound: tuple[int, int] = (999, 999)
                for dar in availableDARs:
                    if 0.95 < w * sar[0] / (h * sar[1]) / (dar[0] / dar[1]) < 1.05:
                        darFound = dar
                        break
                zoomRate = w * h / (videoInfo.width * videoInfo.height)
                if darFound[0] != 999 and zoomRate < 0.9:
                    cropInfo['dar'], cropInfo['sar'] = darFound, videoInfo.sar
                else:
                    cropInfo = None
    inputFile = InputFile(inFile)
    for i in range(len(programClipsList)):
        with open('encode.log', 'w') as encodeLogs, open('strip.log', 'w') as stripLogs:
            # encode
            if len(programClipsList) > 1:
                currentOutFile = outFile.parent / f'{outFile.stem}_{i}.mp4'
            else:
                currentOutFile = outFile
            logger.info(f'Encoding {currentOutFile.name} ...')
            if currentOutFile.exists():
                currentOutFile.unlink()
            currentOutFile.touch()
            encodeTsP = subprocess.Popen(inputFile.EncodeTsCmd('-', GetShortPath(currentOutFile), preset, encoder, cropInfo, audio_config, ['jpn']), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=encodeLogs)
            with encodeTsP:
                # subtitles
                startupinfo = subprocess.STARTUPINFO(wShowWindow=6, dwFlags=subprocess.STARTF_USESHOWWINDOW) if hasattr(subprocess, 'STARTUPINFO') else None
                # CREATE_NO_WINDOW (instead of CREATE_NEW_CONSOLE) keeps the process
                # in the same console so it receives Ctrl+C together with the parent.
                creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
                subtitlesP = subprocess.Popen(
                    f'Caption2AssC.cmd - "{outSubtitles / currentOutFile.with_suffix("").name}"',
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    startupinfo=startupinfo,
                    creationflags=creationflags,
                    shell=True)
                if not noStrip:
                    # strip
                    stripTsP = subprocess.Popen(inputFile.StripTsCmd('-', '-', audioLanguages=['jpn'], fixAudio=fixAudio, audio_config=audio_config), stdin=subprocess.PIPE, stdout=encodeTsP.stdin, stderr=stripLogs)
                    with stripTsP, subtitlesP:
                        # extract (data pump)
                        teeFile = Tee(outPipes=[stripTsP.stdin, subtitlesP.stdin], couldBeBroken=[subtitlesP.stdin])
                        clips = programClipsList[i]
                        ptsMap.ExtractClipsPipe(inFile, clips, teeFile, quiet=quiet)
                else:
                    with subtitlesP:
                        # extract (data pump)
                        teeFile = Tee(outPipes=[encodeTsP.stdin, subtitlesP.stdin], couldBeBroken=[subtitlesP.stdin])
                        clips = programClipsList[i]
                        ptsMap.ExtractClipsPipe(inFile, clips, teeFile, quiet=quiet)
                    
            logger.info('Trying to fix issues in subtitles ...')
            subtitlesBasePath = Path(f'{outSubtitles / currentOutFile.with_suffix("").name}')
            for suffix in ('.ass', '.srt'):
                subPath = subtitlesBasePath.with_suffix(suffix)
                if subPath.exists():
                    subtitles = pysubs2.load(str(subPath), encoding='utf-8')
                    subtitles.save(str(subPath))