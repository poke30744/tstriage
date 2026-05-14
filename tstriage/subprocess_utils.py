import json, logging, os, subprocess, sys
from typing import Optional

logger = logging.getLogger('tstriage.subprocess_utils')


def _clean_env() -> dict:
    env = os.environ.copy()
    env.pop('VIRTUAL_ENV', None)
    return env


def run(cmd: list[str], capture_stderr: bool = False) -> subprocess.CompletedProcess:
    """Execute command. stdout captured for JSON. Set capture_stderr=True when stderr must be inspected."""
    logger.debug(f'Running: {" ".join(cmd)}')
    try:
        stderr = subprocess.PIPE if capture_stderr else None
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=stderr, text=True, env=_clean_env())
    except FileNotFoundError:
        logger.error(f'Command not found: {cmd[0]}')
        logger.error('Ensure the CLI tool is installed and in PATH, or configure "Cli" in config.yml')
        sys.exit(1)
    if result.returncode != 0:
        logger.error(f'Command failed (exit {result.returncode}): {" ".join(cmd)}')
        raise RuntimeError(f'Command failed: {cmd[0]} exited with {result.returncode}')
    return result


def run_json(cmd: list[str]) -> Optional[dict]:
    result = run(cmd)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.error(f'Invalid JSON from: {" ".join(cmd)}')
        return None


def run_pipe(cmd: list[str], progress=None):
    """Execute command, read stderr line-by-line, feed to progress.feed().
    Used for tscutter/tsmarker which output PROGRESS JSON lines terminated by \\n.
    """
    return _run_subprocess(cmd, progress=progress, line_mode=True)


def run_long(cmd: list[str], progress=None):
    """Execute a long-running command (e.g. ffmpeg encode).
    Reads stderr in chunks, splits on \\r/\\n, feeds to progress.feed_ffmpeg().
    """
    return _run_subprocess(cmd, progress=progress, line_mode=False)


def _run_subprocess(cmd: list[str], progress=None, line_mode: bool = True):
    logger.debug(f'Running: {" ".join(cmd)}')
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, encoding='utf-8', errors='replace',
                                env=_clean_env())
    except FileNotFoundError:
        logger.error(f'Command not found: {cmd[0]}')
        sys.exit(1)

    stderr_lines: list[str] = []

    def process_line(line: str):
        if progress is not None:
            if line_mode:
                progress.feed(line)
            else:
                progress.feed_ffmpeg(line)
        else:
            sys.stderr.write(line + '\n')
            stderr_lines.append(line + '\n')

    try:
        if line_mode:
            for line in proc.stderr:
                process_line(line.rstrip('\n'))
        else:
            buf = ''
            while True:
                chunk = proc.stderr.read(4096)
                if not chunk:
                    break
                for ch in chunk:
                    if ch in ('\r', '\n'):
                        if buf:
                            process_line(buf)
                        buf = ''
                    else:
                        buf += ch
            if buf:
                process_line(buf)
    except KeyboardInterrupt:
        logger.info('Interrupted, terminating subprocess ...')
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise

    proc.wait()

    if proc.returncode != 0:
        logger.error(f'Command failed (exit {proc.returncode}): {" ".join(cmd)}')
        if progress is not None and line_mode:
            progress.flush_stderr()
        if stderr_lines:
            logger.error(''.join(stderr_lines).rstrip())
        raise RuntimeError(f'Command failed: {cmd[0]} exited with {proc.returncode}')
