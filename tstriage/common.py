from os import unlink
import shutil
from pathlib import Path
import logging
from tqdm import tqdm
from tscutter.common import CopyPart
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
    
    def __enter__(self):
        WindowsInhibitor.inhibit()
    
    def __exit__(self, exc_type, exc_value, traceback):
        WindowsInhibitor.uninhibit()

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

def ExtractProgramList(markerMap, byGroup):
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
    # generate program list
    programList = [ [clip] for clip in mergedClips ] if byGroup else [ mergedClips ]
    return programList

def ExtractProgram(videoPath, clips, ptsMap, outputPath, quiet=True):
    totalSize = 0
    for clip in clips:
        start, end = ptsMap[str(clip[0])]['next_start_pos'], ptsMap[str(clip[1])]['prev_end_pos']
        totalSize += end - start
    if Path(outputPath).exists():
        unlink(outputPath)
    with tqdm(total=totalSize, unit='B', unit_scale=True, unit_divisor=1024, disable=quiet) as pbar:
        for clip in clips:
            start, end = ptsMap[str(clip[0])]['next_start_pos'], ptsMap[str(clip[1])]['prev_end_pos']
            CopyPart(videoPath, outputPath, start, end, mode='ab', pbar=pbar)

def GetClipsDuration(clips):
    duration = 0
    for clip in clips:
        duration += clip[1] - clip[0]
    return duration

def ExtractPrograms(videoPath, indexPath, markerPath, byGroup, splitNum):
    ptsMap, markerMap = LoadExistingData(indexPath, markerPath)
    programClipsList = ExtractProgramList(markerMap, byGroup)
    programTsList = []
    if byGroup:
        for i in range(len(programClipsList)):
            clips = programClipsList[i]
            outputPath = videoPath.with_name(videoPath.name.replace('.ts', f'_prog_{i+1}.ts'))
            logger.info(f'Extracting "{outputPath.name}" ...')
            ExtractProgram(videoPath, clips, ptsMap, outputPath, quiet=False)
            programTsList.append(outputPath)
    elif splitNum > 1:
        programsDuration = GetClipsDuration(programClipsList[0])
        for i in range(splitNum):
            clips = []
            while programClipsList[0] != []:
                clips.append(programClipsList[0].pop(0))
                if 0.95 < GetClipsDuration(clips) / programsDuration * splitNum < 1.05:
                    break
            outputPath = videoPath.with_name(videoPath.name.replace('.ts', f'_prog_{i+1}.ts'))
            logger.info(f'Extracting "{outputPath.name}" ...')
            ExtractProgram(videoPath, clips, ptsMap, outputPath, quiet=False)
            programTsList.append(outputPath)
    else:
        clips = programClipsList[0]
        outputPath = videoPath.with_name(videoPath.name.replace('.ts', f'_prog.ts'))
        logger.info(f'Extracting "{outputPath.name}" ...')
        ExtractProgram(videoPath, clips, ptsMap, outputPath, quiet=False)
        programTsList.append(outputPath)
    return programTsList