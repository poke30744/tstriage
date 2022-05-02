import subprocess, argparse, logging
from pathlib import Path
from tscutter.common import TsFileNotFound, InvalidTsFormat
from tscutter.ffmpeg import GetInfo

TRIM_THRESHOLD = 10 * 1024 * 1024
BUF_SIZE = 1024 * 1024

logger = logging.getLogger('tstriage.splitter')

def Split(videoPath):
    videoPath = Path(videoPath).absolute()
    if not videoPath.is_file():
        raise TsFileNotFound(f'"{videoPath.name}" not found!')
    startupinfo = subprocess.STARTUPINFO(wShowWindow=6, dwFlags=subprocess.STARTF_USESHOWWINDOW) if hasattr(subprocess, 'STARTUPINFO') else None
    creationflags = subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, 'CREATE_NEW_CONSOLE') else 0
    pipeObj = subprocess.Popen(
        f'TsSplitter.cmd "{videoPath}"',
        startupinfo=startupinfo,
        creationflags=creationflags,
        shell=True)
    pipeObj.wait()
    splittedTs1 = [ path for path in videoPath.parent.glob(f'*{videoPath.suffix}') if path.stem.startswith(videoPath.stem + '_') ]
    splittedTs2 = [ path for path in splittedTs1 if '_HD' in path.stem or '_CS' in path.stem ]
    splittedTs = sorted(splittedTs2, key=lambda x: x.stem)
    if len(splittedTs) == 0:
        raise InvalidTsFormat(f'"{videoPath.name}" is invalid!')
    return splittedTs

def Trim(videoPath, outputPath=None):
    videoPath = Path(videoPath)
    splittedTs = Split(videoPath)
    while True:
        if splittedTs[0].stat().st_size < TRIM_THRESHOLD:
            splittedTs[0].unlink()
            del splittedTs[0]
        elif splittedTs[-1].stat().st_size < TRIM_THRESHOLD:
            splittedTs[-1].unlink()
            del splittedTs[-1]
        else:
            break
    outputPath = Path(outputPath) if outputPath is not None else Path(str(videoPath).replace(videoPath.suffix, '_trimmed.ts'))
    with outputPath.open('wb') as wf:
        for path in splittedTs:
            with path.open('rb') as rf:
                while True:
                    data = rf.read(BUF_SIZE)
                    if len(data) == 0:
                        break
                    wf.write(data)
            path.unlink()
    if GetInfo(outputPath)['duration'] / GetInfo(videoPath)['duration'] < 0.95:
        # trimmed more than expected
        raise InvalidTsFormat(f'"{videoPath.name}" is invalid!')
    return outputPath

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Trim TS file')
    parser.add_argument('--input', '-i', required=True, help='input mpegts path')
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    Trim(args.input)