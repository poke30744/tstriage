import shutil, logging, subprocess, os
from pathlib import Path
from tqdm import tqdm
from .epgstation import EPGStation

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

def CopyWithProgress2(srcPath: Path, dstPath: Path):
    completedProcess = subprocess.run(['robocopy', '/z', '/copy:DT', '/NJH', '/NJS', '/NDL', srcPath.parent, dstPath.parent, srcPath.name])
    if completedProcess.returncode >= 8:
        completedProcess.check_returncode()
    if srcPath.name != dstPath.name:
        shutil.move(dstPath.parent / srcPath.name, dstPath)

def CopyWithProgress(srcPath: Path, dstPath: Path, force: bool=False, epgStation: EPGStation=None):
    if not srcPath.exists():
        raise FileNotFoundError()
    if not force and dstPath.is_file() and srcPath.stat().st_size == dstPath.stat().st_size and round(srcPath.stat().st_mtime) == round(dstPath.stat().st_mtime):
        logger.info(f'Skipped copying {srcPath.name}')
        return
    else:
        if dstPath.exists():
            logger.warn(f'Removing {dstPath} ...')
        logger.info(f'Copying {srcPath.name} ...')
    Path(dstPath).parent.mkdir(parents=True, exist_ok=True)
    if epgStation is not None:
        if not srcPath.is_relative_to(epgStation.recorded) and not dstPath.is_relative_to(epgStation.recorded):
            epgStation = None # no need to block copying
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
