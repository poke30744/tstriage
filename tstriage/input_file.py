import json, logging, shutil
from pathlib import Path
from typing import Optional
import ffmpeg
from .video_info import VideoInfo

logger = logging.getLogger('tstriage.input_file')


class InputFile:
    def __init__(self, path: str | Path) -> None:
        self.ffmpeg = shutil.which('ffmpeg')
        if self.ffmpeg is None:
            raise RuntimeError("ffmpeg not found in $PATH — install ffmpeg or add it to PATH")
        self.ffprobe = shutil.which('ffprobe')
        if self.ffprobe is None:
            raise RuntimeError("ffprobe not found in $PATH — install ffmpeg or add it to PATH")
        self.path = Path(path)

    def GetInfo(self) -> VideoInfo:
        try:
            probeInfo = ffmpeg.probe(str(self.path), cmd=self.ffprobe, show_programs=None)
        except (ffmpeg.Error, json.JSONDecodeError, KeyError):
            raise RuntimeError(f'"{self.path.name}" is invalid!')

        video_stream = next(s for s in probeInfo['streams'] if s.get('codec_type') == 'video')
        audio_streams = [s for s in probeInfo['streams'] if s.get('codec_type') == 'audio']

        num, den = video_stream['avg_frame_rate'].split('/')
        fps = float(num) / float(den)
        return VideoInfo(
            duration = float(video_stream['duration']),
            width = video_stream['width'],
            height = video_stream['height'],
            fps = fps,
            sar = tuple(map(int, video_stream['sample_aspect_ratio'].split(':'))),
            dar = tuple(map(int, video_stream['display_aspect_ratio'].split(':'))),
            soundTracks = len(audio_streams),
            serviceId = next(p['program_id'] for p in probeInfo['programs'] if p['nb_streams'] > 0),
        )

    def StripTsCmd(self, inFile: str | Path, outFile: str | Path, audioLanguages: list[str] = ['jpn'], fixAudio: bool = False, noMap: bool = False, audio_config: Optional[list[dict]] = None) -> list[str]:
        args = [
            self.ffmpeg, '-hide_banner', '-y',
            '-i', str(inFile),
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

    def EncodeTsCmd(self, inPath: str | Path, outPath: str | Path, preset: dict, encoder: str, crop: Optional[dict] = None, audio_config: Optional[list[dict]] = None, audioLanguages: list[str] = ['jpn']) -> list[str]:
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
            nvenc_cq = preset['crf'] + 4
            videoCodec = [
                '-c:v', encoder,
                '-rc:v', 'vbr',
                '-cq:v', str(nvenc_cq),
                '-b:v', '0',
                '-profile:v', 'high',
                '-preset', 'p7',
                '-tune', 'hq'
            ]
        else:
            videoCodec = [ '-c:v', encoder, '-crf', str(preset['crf']) ]
        args = [
            self.ffmpeg, '-hide_banner', '-y',
            '-i', str(inPath)
        ]
        if len(videoFilter) > 0:
            args += [ '-vf', videoFilter ]
        args += videoCodec

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
            args += ['-filter_complex', '[0:a]channelsplit=channel_layout=stereo[left][right]']
            args += ['-map', '0:v', '-map', '[left]', '-map', '[right]']
            args += ['-c:a', 'aac', '-ar', '48000', '-ac', '1', '-b:a', '128k']
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
            args += [ '-c:a', 'copy', '-bsf:a', 'aac_adtstoasc' ]
            args += [ '-map', '0:v', '-map', '0:a', '-ignore_unknown' ]
        args += [ outPath ]
        return args
