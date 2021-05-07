import sys, shutil, time
from pathlib import Path
from tqdm import tqdm
from tsutils.common import CopyPart
from tsmarker.common import LoadExistingData

class WindowsInhibitor:
    '''Prevent OS sleep/hibernate in windows
    API documentation:
    https://msdn.microsoft.com/en-us/library/windows/desktop/aa373208(v=vs.85).aspx'''
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    @staticmethod
    def inhibit():
        import ctypes
        print("Preventing Windows from going to sleep", file=sys.stderr)
        ctypes.windll.kernel32.SetThreadExecutionState(WindowsInhibitor.ES_CONTINUOUS | WindowsInhibitor.ES_SYSTEM_REQUIRED)
    @staticmethod
    def uninhibit():
        import ctypes
        print("Allowing Windows to go to sleep", file=sys.stderr)
        ctypes.windll.kernel32.SetThreadExecutionState(WindowsInhibitor.ES_CONTINUOUS)
    
def CopyWithProgress(srcPath, dstPath, force=False, epgStation=None):
    srcPath, dstPath = Path(srcPath), Path(dstPath)
    if not force and dstPath.is_file() and srcPath.stat().st_size == dstPath.stat().st_size and round(srcPath.stat().st_mtime) == round(dstPath.stat().st_mtime):
        print(f'Skipped copying {srcPath.name}', file=sys.stderr)
        return
    else:
        print(f'Copying {srcPath.name} ...', file=sys.stderr)
    Path(dstPath).parent.mkdir(parents=True, exist_ok=True)
    with open(srcPath, 'rb') as rf:
        with open(dstPath, 'wb') as wf:
            with tqdm(total=srcPath.stat().st_size, unit_scale=True, unit='M') as pbar:
                remaining = srcPath.stat().st_size
                while remaining:
                    if epgStation is not None:
                        epgStation.BusyWait()
                    buf = rf.read(1024 * 1024)
                    wf.write(buf)
                    pbar.update(len(buf))
                    remaining -= len(buf)
    shutil.copystat(srcPath, dstPath)

def ExtractProgram(videoPath, indexPath, markerPath):
    ptsMap, markerMap = LoadExistingData(indexPath, markerPath)
    # split by _groundtruth or _ensemble
    if '_groundtruth' in list(markerMap.values())[0]:
        clips = [ eval(k) for k, v in markerMap.items() if v['_groundtruth'] == 1.0 ]
    else:
        clips = [ eval(k) for k, v in markerMap.items() if v['_ensemble'] == 1.0 ]
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
    programTsPath = videoPath.with_name(videoPath.name.replace('.ts', '_prog.ts'))
    if programTsPath.exists():
        programTsPath.unlink()
    for clip in mergedClips:
        start, end = ptsMap[str(clip[0])]['next_start_pos'], ptsMap[str(clip[1])]['prev_end_pos']
        CopyPart(videoPath, programTsPath, start, end, mode='ab')
    return programTsPath