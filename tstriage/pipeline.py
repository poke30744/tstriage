import logging, subprocess, argparse, os, tempfile, re, io
from pathlib import Path
from threading import Thread
from tqdm import tqdm
import numpy as np
from PIL import Image
from tscutter import common
from tscutter.common import ClipToFilename
from tsmarker.logo import drawEdges, cv2imread, cv2imwrite
from tsmarker.common import MarkerMap
from .encode import presets
from .common import ExtractProgramList

logger = logging.getLogger('tstriage.pipeline')

def StripTsCmd(inFile, outFile, audioLanguages=['jpn'], fixAudio=False, noMap=False):
    args = [
        'ffmpeg', '-hide_banner', '-y',
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

def EncodeTsCmd(inPath, outPath, preset, encoder, crop={}):
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
        'ffmpeg', '-hide_banner', '-y',
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

def ExtractAreaCmd(inFile, folder, crop=None, ss=None, to=None, fps='1/1'):
    args = [ 'ffmpeg', '-hide_banner' ]
    if ss is not None and to is not None:
        args += [ '-ss', str(ss), '-to', str(to) ]
    args += [ '-i', inFile ]
    vFilters = []
    if crop is not None:
        vFilters += [ f'crop={crop["w"]}:{crop["h"]}:{crop["x"]}:{crop["y"]}' ]
    if fps is not None:
        vFilters += [ f'fps={fps}' ]
    if vFilters:
        args += [ '-filter:v', ','.join(vFilters) ]
    args += [ f'{folder}/out%8d.bmp' ]
    return args


class Tee(object):
    def __init__(self, outPipes, pbar=None):
        self.outPipes = outPipes
        self.pbar = pbar

    def write(self, data):
        for pipe in self.outPipes:
            pipe.write(data)
        if self.pbar is not None:
            self.pbar.update(len(data))
    
    def close(self):
        for pipe in self.outPipes:
            pipe.close()

class PtsMap(common.PtsMap):
    def ExtractMeanImagePipe(self, inFile: Path, clip: tuple[float], logoPath: Path, quiet: bool=False):
        with tempfile.TemporaryDirectory(prefix='LogoPipeline_') as tmpFolder:
            with subprocess.Popen(ExtractAreaCmd('-', tmpFolder), stdin=subprocess.PIPE, stderr=subprocess.PIPE) as extractAreaP:
                thread = Thread(target=PtsMap.ExtractClipPipe, args=(self, inFile, clip, extractAreaP.stdin, quiet))
                thread.start()

                class LogoGenerator:
                    def __init__(self):
                        self.picSum = None
                        self.count = 0
                    def Callback(self):
                        for path in Path(tmpFolder).glob('*.bmp'):
                            image = np.array(Image.open(path)).astype(np.float32)
                            self.picSum = image if self.picSum is None else (self.picSum + image)
                            self.count += 1
                            os.unlink(path)
                    def Save(self, path):
                        Image.fromarray((self.picSum/self.count).astype(np.uint8)).save(str(path))
                        
                logoGenerator = LogoGenerator()
                info = HandleFFmpegLog(lines=io.TextIOWrapper(extractAreaP.stderr, errors='ignore'), callback=logoGenerator.Callback)                 
                if logoGenerator.count > 0:
                    logoGenerator.Save(logoPath)
                else:
                    Image.new("RGB", (info['width'], info['height']), (0, 0, 0)).save(str(logoPath))

                thread.join()

def EncodePipeline(inFile, ptsMap: PtsMap, markerMap: MarkerMap, byGroup, preset, encoder):
    programClipsList = ExtractProgramList(markerMap, byGroup)
    for i in range(len(programClipsList)):
        with open('encode.log', 'w') as encodeLogs, open('strip.log', 'w') as stripLogs:
            # encode
            outFile = inFile.with_stem(f'{inFile.stem}_{i}').with_suffix('.mp4')
            logger.info(f'Encoding {outFile.name} ...')
            encodeTsP = subprocess.Popen(EncodeTsCmd('-', outFile, preset, encoder), stdin=subprocess.PIPE, stderr=encodeLogs)
            with encodeTsP :
                # strip
                stripTsP = subprocess.Popen(StripTsCmd('-', '-'), stdin=subprocess.PIPE, stdout=encodeTsP.stdin, stderr=stripLogs)
                # subtitles
                startupinfo = subprocess.STARTUPINFO(wShowWindow=6, dwFlags=subprocess.STARTF_USESHOWWINDOW) if hasattr(subprocess, 'STARTUPINFO') else None
                creationflags = subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, 'CREATE_NEW_CONSOLE') else 0
                subtitlesP = subprocess.Popen(    
                    ['Captain2AssC.cmd', '-', outFile.with_suffix('') ],
                    stdin=subprocess.PIPE,
                    startupinfo=startupinfo,
                    creationflags=creationflags,
                    shell=True)
                with stripTsP, subtitlesP:
                    # extract (data pump)
                    teeFile = Tee(outPipes=[stripTsP.stdin, subtitlesP.stdin])
                    clips = programClipsList[i]
                    ptsMap.ExtractClipsPipe(inFile, clips, teeFile, quiet=False)

def ReadFFmpegInfo(lines):
    soundTracks = 0
    duration = None
    for line in lines:
        if 'Duration' in line:
            durationFields = line.split(',')[0].replace('Duration:', '').strip().split(':')
            if durationFields[0] != 'N/A':
                duration = float(durationFields[0]) * 3600 + float(durationFields[1]) * 60  + float(durationFields[2])
        if 'Stream #' in line:
            if 'Video:' in line:
                for item in re.findall(r'\d+x\d+', line):
                    sizeFields = item.split('x')
                    if sizeFields[0] != '0' and sizeFields[1] != '0':
                        width, height = int(sizeFields[0]), int(sizeFields[1])
                        break
                for item in line.split(','):
                    if ' fps' in item:
                        fps = float(item.replace(' fps', ''))
                        break
                sar = line.split('SAR ')[1].split(' ')[0].split(':')
                sar = int(sar[0]), int(sar[1])
                dar = line.split('DAR ')[1].split(' ')[0].split(']')[0].split(':')
                dar = int(dar[0]), int(dar[1])
                sar = sar
                dar = dar
            elif 'Audio:' in line and 'Hz,' in line:
                soundTracks += 1
        if line.startswith('Output') or 'time=' in line:
            break
    return {
        'duration': duration, 
        'width': width,
        'height': height,
        'fps': fps,
        'sar': sar,
        'dar': dar,
        'soundTracks': soundTracks
    }

def HandleFFmpegProgress(lines, pbar=None, callback=None):
    for line in lines:
        if 'time=' in line:
            for item in line.split(' '):
                if item.startswith('time='):
                    timeFields = item.replace('time=', '').split(':')
                    time = float(timeFields[0]) * 3600 + float(timeFields[1]) * 60  + float(timeFields[2])
                    if pbar is not None:
                        pbar.update(time - pbar.n)
                    if callback is not None:
                        callback()
    if pbar is not None:
        pbar.update(pbar.total - pbar.n)
    if callback is not None:
        callback()

def HandleFFmpegLog(lines, pbar=None, callback=None):
    info = ReadFFmpegInfo(lines)
    if str(pbar) == 'auto':
        if info['duration'] is not None:
            with tqdm(total=info['duration'], unit='SECONDs', unit_scale=True) as pbar:
                HandleFFmpegProgress(lines, pbar, callback)
        else:
            HandleFFmpegProgress(lines, None, callback)
    else:
        HandleFFmpegProgress(lines, pbar, callback)
    return info

def ExtractLogoPipeline(inFile: Path, ptsMap: PtsMap, outFile: Path, maxTimeToExtract=120, removeBoarder: bool=True, quiet: bool=False) -> None:
    # calculate the logo of the entire video
    selectedClips, selectedLen = ptsMap.SelectClips()
    if selectedLen == 0:
        selectedClips, selectedLen = ptsMap.SelectClips(lengthLimit=15)
    if selectedLen == 0:
        selectedClips, selectedLen = ptsMap.SelectClips(lengthLimit=0)
    with tempfile.TemporaryDirectory(prefix='ExtractLogoPipeline_') as tmpFolder:
        videoLogo = None
        clip = selectedClips[0]
        clipLen = clip[1] - clip[0]
        logoPath = tmpFolder / Path(ClipToFilename(clip)).with_suffix('.png')
        # shorten clip to less than maxTimeToExtract seconds
        if clip[1] - clip[0] > maxTimeToExtract:
            padding = (clip[1] - clip[0] - maxTimeToExtract) / 2
            clip = (padding + clip[0], padding + clip[0] + maxTimeToExtract)
        logger.info(f'Extracting logo from {inFile.name}: {clip} ...')
        ptsMap.ExtractMeanImagePipe(inFile, clip, logoPath, quiet)
        img = cv2imread(str(logoPath)) * clipLen
        videoLogo = img if videoLogo is None else (videoLogo + img)
        videoLogo /= selectedLen
        logoPath = Path(tmpFolder) / (inFile.stem + '_logo.png')
        cv2imwrite(str(logoPath), videoLogo)
        drawEdges(logoPath, outputPath=outFile, removeBoarder=removeBoarder)

def CropDetectPipeline(videoEdgePath, threshold=0.3):
    videoEdges = np.array(Image.open(videoEdgePath))
    xAxis = videoEdges.mean(axis=0)
    yAxis = videoEdges.mean(axis=1)
    try:
        x1, x2 = np.argwhere(xAxis > 255 * threshold).flatten()
        y1, y2 = np.argwhere(yAxis > 255 * threshold).flatten()
        w = x2 - x1 + 1
        h = y2 - y1 + 1
        return { 'w': w, 'h': h, 'x': x1, 'y': y1 }
    except ValueError:
        return None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process TS files in pipeline')
    subparsers = parser.add_subparsers(required=True, title='subcommands', dest='command')

    subparser = subparsers.add_parser('encode', help='encode marked mpegts file')
    subparser.add_argument('--input', '-i', required=True, help='input mpegts path')
    subparser.add_argument('--bygroup', action='store_true', help='extract into groups')
    subparser.add_argument('--preset', default='drama', help='encoder preset string')
    subparser.add_argument('--encoder', default='nvenc_h264', help='FFmpeg encoder name')

    subparser = subparsers.add_parser('logo', help='extract logo from mpegts file')
    subparser.add_argument('--input', '-i', required=True, help='input mpegts path')

    subparser = subparsers.add_parser('cropdetect', help='detect crop parameters for mpegts file')
    subparser.add_argument('--input', '-i', required=True, help='input mpegts path')

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    os.environ['PATH'] = f'{os.environ["PATH"]};C:\\Software\\Caption2Ass'

    if args.command == 'encode':
        inFile = Path(args.input)
        indexPath = inFile.parent / '_metadata' / (inFile.stem + '.ptsmap')
        markerPath = inFile.parent / '_metadata' / (inFile.stem + '.markermap')
        ptsMap = PtsMap(indexPath)
        markerMap = MarkerMap(markerPath, ptsMap)
        EncodePipeline(inFile, ptsMap=ptsMap, markerMap=markerMap, byGroup=args.bygroup, preset=args.preset, encoder=args.encoder)
    elif args.command == 'logo':
        inFile = Path(args.input)
        outFile = inFile.with_suffix('.logo.png')
        indexPath = inFile.parent / '_metadata' / (inFile.stem + '.ptsmap')
        ExtractLogoPipeline(inFile, PtsMap(indexPath), outFile)
    elif args.command == 'cropdetect':
        cropInfo = CropDetectPipeline(Path(args.input))
        print(cropInfo)