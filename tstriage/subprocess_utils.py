import json, logging, os, subprocess, sys, threading, time
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
    """Execute command, feeding PROGRESS lines from stderr to the progress instance.
    Main thread stays free so Rich Live display can refresh.
    """
    logger.debug(f'Running: {" ".join(cmd)}')
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=_clean_env())
    except FileNotFoundError:
        logger.error(f'Command not found: {cmd[0]}')
        sys.exit(1)

    stdout_chunks: list[str] = []
    stderr_buffer: list[str] = []

    def _read_stdout():
        try:
            stdout_chunks.append(proc.stdout.read())
        except ValueError:
            pass

    def _read_stderr():
        try:
            for line in proc.stderr:
                if progress is not None:
                    progress.feed(line.rstrip('\n'))
                else:
                    sys.stderr.write(line)
                    stderr_buffer.append(line)
        except ValueError:
            pass

    t_out = threading.Thread(target=_read_stdout, daemon=True)
    t_err = threading.Thread(target=_read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    try:
        while proc.poll() is None:
            time.sleep(0.1)  # keep main thread free for Rich Live refresh
    except KeyboardInterrupt:
        logger.info('Interrupted, terminating subprocess ...')
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        raise
    finally:
        t_out.join(timeout=2)
        t_err.join(timeout=2)

    if proc.returncode != 0:
        logger.error(f'Command failed (exit {proc.returncode}): {" ".join(cmd)}')
        if progress is not None:
            progress.flush_stderr()
        if stderr_buffer:
            logger.error(''.join(stderr_buffer).rstrip())
        raise RuntimeError(f'Command failed: {cmd[0]} exited with {proc.returncode}')
    return ''.join(stdout_chunks)
