import shutil, logging
from pathlib import Path
from tqdm import tqdm

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
