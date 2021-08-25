import sys, shutil, time
from pathlib import Path
import logging
from tqdm import tqdm
from tsutils.common import CopyPart
from tsmarker.common import LoadExistingData

logger = logging.getLogger('tstriage.common')

class WindowsInhibitor:
    '''Prevent OS sleep/hibernate in windows
    API documentation:
    https://msdn.microsoft.com/en-us/library/windows/desktop/aa373208(v=vs.85).aspx'''
    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001
    @staticmethod
    def inhibit():
        import ctypes
        logger.info('Preventing Windows from going to sleep')
        ctypes.windll.kernel32.SetThreadExecutionState(WindowsInhibitor.ES_CONTINUOUS | WindowsInhibitor.ES_SYSTEM_REQUIRED)
    @staticmethod
    def uninhibit():
        import ctypes
        logger.info('Allowing Windows to go to sleep')
        ctypes.windll.kernel32.SetThreadExecutionState(WindowsInhibitor.ES_CONTINUOUS)
    
def CopyWithProgress(srcPath, dstPath, force=False, epgStation=None):
    srcPath, dstPath = Path(srcPath), Path(dstPath)
    if not force and dstPath.is_file() and srcPath.stat().st_size == dstPath.stat().st_size and round(srcPath.stat().st_mtime) == round(dstPath.stat().st_mtime):
        logger.info(f'Skipped copying {srcPath.name}')
        return
    else:
        logger.info(f'Copying {srcPath.name} ...')
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

def ExtractProgram(videoPath, indexPath, markerPath, byGroup):
    ptsMap, markerMap = LoadExistingData(indexPath, markerPath)
    # split by _groundtruth or _ensemble
    if '_groundtruth' in list(markerMap.values())[0]:
        clips = [ eval(k) for k, v in markerMap.items() if v['_groundtruth'] == 1.0 ]
        logger.info('Encoding by _groundtruth ...')
    elif '_ensemble' in list(markerMap.values())[0]:
        clips = [ eval(k) for k, v in markerMap.items() if v['_ensemble'] == 1.0 ]
        logger.info('Encoding by _ensemble ...')
    elif 'subtitles' in list(markerMap.values())[0] and list(markerMap.values())[0]['subtitles'] != 0.5:
        clips = [ eval(k) for k, v in markerMap.items() if v['subtitles'] == 1.0 ]
        logger.info('Encoding by subtitles ...')
    else:
        clips = [ eval(k) for k, v in markerMap.items() if v['logo'] > 0.5 ]
        logger.info('Encoding by logo ...')
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
    programTsList = []
    for i in range(len(mergedClips)):
        clip = mergedClips[i]
        start, end = ptsMap[str(clip[0])]['next_start_pos'], ptsMap[str(clip[1])]['prev_end_pos']
        if byGroup:
            programTsPath = videoPath.with_name(videoPath.name.replace('.ts', f'_prog_{i+1}.ts'))
        else:
            programTsPath = videoPath.with_name(videoPath.name.replace('.ts', f'_prog.ts'))
        CopyPart(videoPath, programTsPath, start, end, mode='ab')
        programTsList.append(programTsPath)
    return sorted(list(set(programTsList)))