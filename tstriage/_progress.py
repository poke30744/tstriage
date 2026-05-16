"""Progress utilities shared between tstriage, tscutter, tsmarker.

Two modes:
  standalone: Rich Progress renders directly in terminal
  subprocess: --progress flag outputs PROGRESS JSON lines to stderr

Parent side (tstriage): SubprocessProgress reads PROGRESS lines and updates Rich.
Child side (tscutter/tsmarker): Progress wrapper auto-detects mode.
"""

import json, sys, time, logging
from rich.progress import Progress as RichProgress, TaskID, ProgressColumn
from rich.text import Text
from rich import filesize as _filesize

logger = logging.getLogger('tstriage.progress')


def _parse_ffmpeg_time(time_str: str) -> float | None:
    """Parse HH:MM:SS[.fraction] → seconds."""
    parts = time_str.split(':')
    if len(parts) == 3:
        try:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except ValueError:
            return None
    return None


class _UnitColumn(ProgressColumn):
    """Progress column that formats completed/total according to unit."""

    def render(self, task):
        total = task.total or 0
        completed = task.completed or 0
        unit = task.fields.get("unit", "it")
        if unit == "B":
            text = f"{_filesize.decimal(completed)}/{_filesize.decimal(total)}"
        elif total == int(total):
            text = f"{int(completed)}/{int(total)}"
        else:
            text = f"{completed:.1f}/{total:.1f}"
        if unit not in ("it", "B"):
            text += f" {unit}"
        return Text(text, style="progress.percentage")


class SubprocessProgress:
    """Reads PROGRESS lines from subprocess stderr, updates Rich Progress panel.

    TTY mode: updates animated Rich progress bars.
    Non-TTY mode (Jenkins): emits periodic plain-text progress lines.
    """

    def __init__(self, progress: RichProgress | None = None, ctx: str = ""):
        self.ctx = ctx
        self._tasks: dict[str, dict] = {}
        self._is_tty = sys.stderr.isatty()
        self._stderr: list[str] = []
        self._status_task = None
        self._last_log_ctx = ""
        # In non-TTY mode (Jenkins), use text fallback even if RichProgress is passed
        self.progress = progress if self._is_tty else None

    def _log(self, msg: str):
        """Emit a log line, omitting [ctx] if same as previous line."""
        if self.ctx == self._last_log_ctx:
            logger.info(f'  {msg}')
        else:
            logger.info(f'[{self.ctx}] {msg}')
            self._last_log_ctx = self.ctx

    def add_task(self, task_id: str, total: float, desc: str, unit: str = "it"):
        if self.progress is not None:
            self._tasks[task_id] = {"rich_id": self.progress.add_task(desc, total=total, unit=unit),
                                     "desc": desc, "total": total, "last_log_time": 0.0, "last_log_n": -1}
        else:
            self._tasks[task_id] = {"desc": desc, "total": total, "last_log_time": 0.0, "last_log_n": -1}
            self._log(f'{desc}: 0/{total:.1f}')

    def update(self, task_id: str, n: float):
        info = self._tasks.get(task_id)
        if info is None:
            return
        if self.progress is not None:
            self.progress.update(info["rich_id"], completed=n)
        else:
            elapsed = time.time() - info["last_log_time"]
            if elapsed >= 60:
                pct = n / info["total"] * 100
                self._log(f'{info["desc"]}: {n:.1f}/{info["total"]:.1f} ({pct:.0f}%)')
                info["last_log_time"] = time.time()
                info["last_log_n"] = n

    def done(self, task_id: str):
        info = self._tasks.get(task_id)
        if info is None:
            return
        if self.progress is not None:
            self.progress.update(info["rich_id"], visible=False)
        else:
            self._log(f'{info["desc"]}: done')

    def set_parent_desc(self, desc: str):
        if self.progress is not None:
            if self._status_task is None:
                self._status_task = self.progress.add_task(desc, total=None)
            else:
                self.progress.update(self._status_task, description=desc)

    def clear_parent_desc(self):
        if self.progress is not None and self._status_task is not None:
            self.progress.update(self._status_task, visible=False)
            self._status_task = None

    def status(self, desc: str):
        return _StatusContext(self, desc)

    def feed(self, line: str):
        if not line.startswith("PROGRESS:"):
            self._stderr.append(line)
            return
        try:
            data = json.loads(line[len("PROGRESS:"):])
        except json.JSONDecodeError:
            return
        tid = data["task"]
        if tid not in self._tasks:
            self._tasks[tid] = self._register(tid, data)
        self._update(tid, data)

    def feed_ffmpeg(self, line: str):
        """Parse ffmpeg -progress output or legacy status line for time-based progress."""
        tid = "ffmpeg_encode"
        if tid not in self._tasks:
            return
        # -progress format: out_time=HH:MM:SS.xxxxxx
        if line.startswith("out_time="):
            time_str = line.split('=', 1)[1]
            parsed = _parse_ffmpeg_time(time_str)
            if parsed is not None:
                self.update(tid, parsed)
            return
        # Legacy format: frame=... time=HH:MM:SS.MS ...
        if line.startswith("frame="):
            for part in line.split():
                if part.startswith("time="):
                    time_str = part[5:]
                    parsed = _parse_ffmpeg_time(time_str)
                    if parsed is not None:
                        self.update(tid, parsed)
                    return

    def flush_stderr(self):
        """Output collected stderr lines (call on command failure)."""
        for line in self._stderr:
            sys.stderr.write(line + '\n')
        self._stderr.clear()

    def _register(self, tid: str, data: dict) -> dict:
        info = {
            "desc": data.get("desc", tid),
            "total": data["total"],
            "last_log_time": 0.0,
            "last_log_n": -1,
        }
        if self.progress is not None:
            info["rich_id"] = self.progress.add_task(
                info["desc"], total=info["total"], unit=data.get("unit", "it"))
        else:
            self._log(f'{info["desc"]}: 0/{info["total"]:.1f}')
        return info

    def _update(self, tid: str, data: dict):
        info = self._tasks[tid]
        if data.get("status") == "done":
            if self.progress is not None:
                self.progress.update(info["rich_id"], completed=info["total"],
                                     visible=False)
            else:
                self._log(f'{info["desc"]}: done')
            return
        if "n" not in data:
            return
        n = data["n"]
        if self.progress is not None:
            self.progress.update(info["rich_id"], completed=n)
        else:
            elapsed = time.time() - info["last_log_time"]
            if elapsed >= 60:
                pct = n / info["total"] * 100
                self._log(f'{info["desc"]}: {n:.1f}/{info["total"]:.1f} ({pct:.0f}%)')
                info["last_log_time"] = time.time()
                info["last_log_n"] = n


class Progress:
    """Progress bar wrapper used by tscutter/tsmarker.

    use_protocol=True  → emit PROGRESS JSON lines to stderr
    use_protocol=False → render Rich Progress directly
    """

    def __init__(self, use_protocol: bool = False):
        self.use_protocol = use_protocol
        self._rich: RichProgress | None = None
        self._tasks: dict[str, int] = {}
        if not use_protocol:
            self._rich = RichProgress().__enter__()

    def add_task(self, task_id: str, total: float, desc: str, unit: str = "it"):
        if self.use_protocol:
            self._emit({"task": task_id, "total": total, "desc": desc, "unit": unit})
        elif self._rich is not None:
            self._tasks[task_id] = self._rich.add_task(desc, total=total)

    def update(self, task_id: str, n: float):
        if self.use_protocol:
            self._emit({"task": task_id, "n": n})
        elif self._rich is not None:
            self._rich.update(self._tasks[task_id], completed=n)

    def done(self, task_id: str):
        if self.use_protocol:
            self._emit({"task": task_id, "status": "done"})
        elif self._rich is not None:
            self._rich.update(self._tasks[task_id], visible=False)

    def close(self):
        if self._rich is not None:
            self._rich.__exit__(None, None, None)

    def _emit(self, data: dict):
        sys.stderr.write(f"PROGRESS:{json.dumps(data)}\n")
        sys.stderr.flush()


class _StatusContext:
    def __init__(self, parent: SubprocessProgress, desc: str):
        self._parent = parent
        self._desc = desc

    def __enter__(self):
        self._parent.set_parent_desc(self._desc)

    def __exit__(self, *args):
        self._parent.clear_parent_desc()
