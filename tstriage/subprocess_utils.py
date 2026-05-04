import json, logging, subprocess, sys
from typing import Optional

logger = logging.getLogger('tstriage.subprocess_utils')


def run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    logger.debug(f'Running: {" ".join(cmd)}')
    try:
        result = subprocess.run(cmd, capture_output=capture, text=True)
    except FileNotFoundError:
        logger.error(f'Command not found: {cmd[0]}')
        logger.error('Ensure the CLI tool is installed and in PATH, or configure "Cli" in config.yml')
        sys.exit(1)
    if result.returncode != 0:
        logger.error(f'Command failed (exit {result.returncode}): {" ".join(cmd)}')
        if result.stderr:
            logger.error(result.stderr.strip())
        if result.stdout:
            logger.error(result.stdout.strip())
        raise RuntimeError(f'Command failed: {cmd[0]} exited with {result.returncode}')
    return result


def run_json(cmd: list[str]) -> Optional[dict]:
    result = run(cmd)
    if result.returncode != 0:
        logger.error(f'Command failed (exit {result.returncode}): {" ".join(cmd)}')
        logger.error(result.stderr.strip())
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.error(f'Invalid JSON from: {" ".join(cmd)}')
        return None
