import logging, subprocess, argparse, os, tempfile
from pathlib import Path
import pysubs2
from tscutter import ffmpeg
from tsmarker.pipeline import PtsMap, ExtractLogoPipeline, CropDetectPipeline
import tsmarker.common

logger = logging.getLogger('tstriage.pipeline')

presets = {
    'drama': {
        'videoFilter': 'bwdif=0',
        'bitrate': '2500k',
        'maxRate': '5000k',
        'crf': '19',
    },
    'drama720p': {
        'videoFilter': 'bwdif=0,scale=1280:720',
        'bitrate': '1500k',
        'maxRate': '3000k',
        'crf': '19',
    },
    'anime': {
        'videoFilter': 'pullup,fps=24000/1001',
        'bitrate': '2500k',
        'maxRate': '5000k',
        'crf': '19',
    },
    'anime720p': {
        'videoFilter': 'pullup,fps=24000/1001,scale=1280:720',
        'bitrate': '1500k',
        'maxRate': '3000k',
        'crf': '19',
    },
    'anime480p': {
        'videoFilter': 'pullup,fps=24000/1001,scale=852:480',
        'bitrate': '750k',
        'maxRate': '1500k',
        'crf': '19',
    },
    'bluedvd': {
        'videoFilter': '',
        'bitrate': '2500k',
        'maxRate': '5000k',
        'crf': '19',
    },
}

class InputFile(ffmpeg.InputFile):
    def StripTsCmd(self, inFile, outFile, audioLanguages=['jpn'], fixAudio=False, noMap=False):
        args = [
            self.ffmpeg, '-hide_banner', '-y',
            '-i', inFile,
            '-c:v', 'copy'
        ]
        if fixAudio:
            args += [ 
                '-af',  'aresample=async=1',
                '-c:a', 'aac'
            ]
        else:
            args += [ '-c:a', 'copy' ]
        if not noMap:
            args += [ '-map', '0:v', '-map', '0:a', '-ignore_unknown' ]
            for i in range(len(audioLanguages)):
                args += [ f'-metadata:s:a:{i}', f'language={audioLanguages[i]}' ]
        args += [ '-f', 'mpegts', outFile ]
        return args

    def EncodeTsCmd(self, inPath, outPath, preset, encoder, crop=None):
        preset = presets[preset]
        videoFilter = preset['videoFilter']
        if crop:    
            filters = preset['videoFilter'].split(',')
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
            videoCodec = [ '-c:v', encoder, '-rc:v', 'vbr_hq', '-cq:v', preset['crf'], '-b:v', preset['bitrate'], '-maxrate:v', preset['maxRate'], '-profile:v', 'high' ]
        elif '_videotoolbox' in encoder:
            videoCodec = [ '-c:v', encoder, '-b:v', preset['bitrate'], '-maxrate:v',  preset['maxRate'] ]
        else:
            videoCodec = [ '-c:v', encoder, '-crf', preset['crf'] ]
        args = [
            self.ffmpeg, '-hide_banner', '-y',
            '-i', inPath
        ]
        if len(videoFilter) > 0:
            args += [ '-vf', videoFilter ]
        args += videoCodec + [
            #https://stackoverflow.com/questions/49686244/ffmpeg-too-many-packets-buffered-for-output-stream-01
            #'-max_muxing_queue_size', '1024',
        ]
        # TODO: support opt-in encoding audio
        args += [ '-c:a', 'copy', '-bsf:a', 'aac_adtstoasc' ]
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

    def GetClipsDuration(clips):
        duration = 0
        for clip in clips:
            duration += clip[1] - clip[0]
        return duration

    def SplitClips(programClips: list, num: int) -> list[list]:
        splittedClips = []
        programsDuration = MarkerMap.GetClipsDuration(programClips)
        for i in range(num):
            clips = []
            while programClips != []:
                clips.append(programClips.pop(0))
                if 0.95 < MarkerMap.GetClipsDuration(clips) / programsDuration * num < 1.05:
                    break
            clips += programClips
            splittedClips.append(clips)
        return splittedClips

def EncodePipeline(inFile: Path, ptsMap: PtsMap, markerMap: MarkerMap, outFile: Path, byGroup: bool, splitNum: int, preset: str, cropdetect: bool, encoder: str):
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
            ExtractLogoPipeline(inFile, ptsMap, logoPath, maxTimeToExtract=10, removeBoarder=False)
            cropInfo = CropDetectPipeline(logoPath)
            if cropInfo is not None:
                # double check if cropping is really needed
                videoInfo = InputFile(inFile).GetInfo()
                sar = videoInfo['sar']
                w, h = cropInfo['w'], cropInfo['h']
                for dar in ((16, 9), (4, 3), (1,1), (999, 999)):
                    if 0.95 < w * sar[0] / (h * sar[1]) / (dar[0] / dar[1]) < 1.05:
                        break
                zoomRate = w * h / (videoInfo['width'] * videoInfo['height'])
                if dar[0] != 999 and zoomRate < 0.9:
                    cropInfo['dar'], cropInfo['sar'] = dar, videoInfo['sar']
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
            encodeTsP = subprocess.Popen(inputFile.EncodeTsCmd('-', currentOutFile, preset, encoder, cropInfo), stdin=subprocess.PIPE, stderr=encodeLogs)
            with encodeTsP :
                # strip
                stripTsP = subprocess.Popen(inputFile.StripTsCmd('-', '-'), stdin=subprocess.PIPE, stdout=encodeTsP.stdin, stderr=stripLogs)
                # subtitles
                startupinfo = subprocess.STARTUPINFO(wShowWindow=6, dwFlags=subprocess.STARTF_USESHOWWINDOW) if hasattr(subprocess, 'STARTUPINFO') else None
                creationflags = subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, 'CREATE_NEW_CONSOLE') else 0
                subtitlesP = subprocess.Popen(    
                    f'Captain2AssC.cmd - "{currentOutFile.with_suffix("")}"',
                    stdin=subprocess.PIPE,
                    startupinfo=startupinfo,
                    creationflags=creationflags,
                    shell=True)
                with stripTsP, subtitlesP:
                    # extract (data pump)
                    teeFile = Tee(outPipes=[stripTsP.stdin, subtitlesP.stdin], couldBeBroken=[subtitlesP.stdin])
                    clips = programClipsList[i]
                    ptsMap.ExtractClipsPipe(inFile, clips, teeFile, quiet=False)
            logger.info('Trying to fix issues in subtitles ...')
            for suffix in ('.ass', '.srt'):
                subPath = currentOutFile.with_suffix(suffix)
                if subPath.exists():
                    subtitles = pysubs2.load(str(subPath), encoding='utf-8')
                    subtitles.save(subPath)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process TS files in pipeline')
    subparsers = parser.add_subparsers(required=True, title='subcommands', dest='command')

    subparser = subparsers.add_parser('encode', help='encode marked mpegts file')
    subparser.add_argument('--input', '-i', required=True, help='input mpegts path')
    subparser.add_argument('--bygroup', action='store_true', help='extract into groups')
    subparser.add_argument('--preset', default='drama', help='encoder preset string')
    subparser.add_argument('--cropdetect', '-c', action='store_true', help='detect and crop still area')
    subparser.add_argument('--encoder', default='nvenc_h264', help='FFmpeg encoder name')
    subparser.add_argument('--notag', action='store_true', help="don't add tag to output filename")

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    os.environ['PATH'] = f'{os.environ["PATH"]};C:\\Software\\Caption2Ass'

    if args.command == 'encode':
        inFile = Path(args.input)
        ptsMap = PtsMap(inFile.parent / '_metadata' / (inFile.stem + '.ptsmap'))
        markerMap = MarkerMap(inFile.parent / '_metadata' / (inFile.stem + '.markermap'), ptsMap)
        outputPath = inFile.with_suffix('.mp4') if args.notag else inFile.parent / f'{inFile.stem}_({args.preset}_{args.encoder}_crf{presets[args.preset]["crf"]}).mp4'
        EncodePipeline(
            inFile=inFile,
            ptsMap=ptsMap,
            markerMap=markerMap,
            outFile=outputPath,
            byGroup=args.bygroup,
            splitNum=1,
            preset=args.preset,
            cropdetect=args.cropdetect,
            encoder=args.encoder)