import shutil, logging, subprocess, os
from pathlib import Path
from tqdm import tqdm

logger = logging.getLogger('tstriage.common')

def CopyWithProgress2(srcPath: Path, dstPath: Path, quiet=False):
    if os.name == 'nt':
        robocopyCmd = ['robocopy', '/z', '/copy:DT', '/NJH', '/NJS', '/NDL']
        if quiet:
            robocopyCmd += ['/NP']
        completedProcess = subprocess.run(robocopyCmd + [srcPath.parent, dstPath.parent, srcPath.name])
        if completedProcess.returncode >= 8:
            completedProcess.check_returncode()
        if srcPath.name != dstPath.name:
            shutil.move(dstPath.parent / srcPath.name, dstPath)
    else:
        CopyWithProgress(srcPath, dstPath)

def CopyWithProgress(srcPath: Path, dstPath: Path, force: bool=False):
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
                    buf = rf.read(1024 * 1024)
                    wf.write(buf)
                    pbar.update(len(buf))
                    remaining -= len(buf)
    shutil.copystat(srcPath, dstPath)
