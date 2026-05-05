# tstriage + tscutter + tsmarker 现代化 CLI 设计方案

## 背景

三个工具组成一条处理流水线：

- **tscutter** — TS 分析（静音检测、场景切换、PTS 映射表）
- **tsmarker** — CM 标记（字幕、台标、片段信息、语音识别、集成学习、切割）
- **tstriage** — 流水线编排器（通过 subprocess 调用 tscutter/tsmarker）

### 当前问题

1. tscutter/tsmarker 已有 tqdm 进度条，但 tstriage 通过 `capture_output=True` 把它们全部隐藏
2. tstriage 将 ffmpeg stderr 重定向到 `encode.log` / `strip.log` 文件——编码进度完全不可见
3. 三个工具的 `--quiet` 行为不一致（有的不生效，有的硬编码 DEBUG）
4. tscutter 硬编码 `logging.DEBUG`——始终输出大量噪音
5. 无用 logger、废弃 API、语法错误、误导性 help text
6. 长耗时操作用户看不到任何进度反馈，像卡死一样

### 实测数据

在 TestData 上运行得到的关键耗时分布：

| 阶段 | 工具 | 耗时 | 当前反馈 |
|------|------|------|----------|
| analyze | tscutter | **3m51s** | 一行 `Analyzing to split ...` |
| prepare-subtitles | tsmarker | **3m33s** | 一行 `Extracting subtitles ...` |
| ffmpeg encode | ffmpeg | **3m9s** | 一行 `Encoding ...mkv ...`，ffmpeg 进度写入了 log 文件 |

这三段占总运行时间的 95%，但用户看到的是三行日志 + 数分钟的沉默。

---

## 核心原则

**stdout = 数据（JSON），stderr = 呈现（进度、日志）**

这是 Unix 惯例。tscutter/tsmarker 已经遵循——JSON 结果写 stdout，tqdm 写 stderr。tstriage 只需要停止破坏它。

---

## 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                      tstriage                            │
│  Rich Progress (统一面板)                                │
│  ├─ 文件级进度条                                         │
│  ├─ 读取子进程 PROGRESS 行 → 更新对应 task                │
│  └─ 透传子进程 stderr 中的非 PROGRESS 行（错误信息）       │
│                                                          │
│  subprocess.Popen(stderr=PIPE)                           │
│         │                                                │
│         ▼                                                │
│  ┌──────────────┐  ┌──────────────┐                      │
│  │  tscutter    │  │  tsmarker    │                      │
│  │  --progress  │  │  --progress  │                      │
│  │              │  │              │                      │
│  │  输出 JSON   │  │  输出 JSON   │                      │
│  │  进度行到    │  │  进度行到    │                      │
│  │  stderr      │  │  stderr      │                      │
│  └──────────────┘  └──────────────┘                      │
│                                                          │
│  独立运行时（无 --progress）：                              │
│  各自使用 Rich Progress 直接渲染进度条                     │
└─────────────────────────────────────────────────────────┘
```

三种运行模式：

```
本地终端 (TTY)     → Rich 进度条 + 颜色 + 统一面板   (Level 3 完整体验)
本地 --quiet       → WARNING 级别，无进度条           (用户显式)
Jenkins (非 TTY)   → 纯文本日志，无 ANSI              (自动检测)
Jenkins --quiet    → WARNING，更安静                   (批量/夜间)
Jenkins --verbose  → DEBUG 纯文本（排查用）           (CI 失败时)
```

---

## Phase 1: 建立 PROGRESS 协议

这是 Level 3 的核心——让父进程和子进程之间能传递进度信息。

### 1a. 协议格式

子进程通过 stderr 输出 JSON 行，以 `PROGRESS:` 前缀标识：

```
PROGRESS:{"task":"analyze.cut_position","n":35,"total":50,"desc":"查找切点"}
PROGRESS:{"task":"analyze.extract_streams","n":120.5,"total":2405.0,"unit":"s","desc":"提取音频流"}
PROGRESS:{"task":"analyze.cut_position","status":"done"}
```

JSON 字段：

| 字段 | 类型 | 必须 | 说明 |
|------|------|------|------|
| `task` | string | 是 | 唯一任务标识，用于父进程关联 Rich task |
| `desc` | string | 首次必须 | 任务描述，首次出现时用于 `progress.add_task()` |
| `n` | number | 进度更新时 | 当前进度值 |
| `total` | number | 首次必须 | 总进度值 |
| `unit` | string | 否 | 单位（默认 `"it"`） |
| `status` | string | 否 | `"done"` 表示任务完成，父进程隐藏该行 |

线程安全：`sys.stderr.write()` + `sys.stderr.flush()` 在 Python 中是线程安全的（单次 write 调用原子）。每行 JSON 不含换行以外的换行符，保证父进程按行解析不会断行。

### 1b. 共享工具类（tscutter/tsmarker 各自复制一份）

```python
# _progress.py (~25 lines)
import json, sys

class Progress:
    """Rich Progress 封装 — 独立运行时直接渲染，--progress 模式输出 JSON 行"""

    def __init__(self, use_protocol: bool = False):
        self.use_protocol = use_protocol
        if not use_protocol:
            from rich.progress import Progress as RichProgress
            self._rich = RichProgress()
            self._rich.__enter__()
        self._tasks: dict[str, int] = {}  # task_id → Rich task_id

    def add_task(self, task_id: str, total: float, desc: str, unit: str = "it"):
        if self.use_protocol:
            self._emit({"task": task_id, "total": total, "desc": desc, "unit": unit})
        else:
            self._tasks[task_id] = self._rich.add_task(desc, total=total)

    def update(self, task_id: str, n: float):
        if self.use_protocol:
            self._emit({"task": task_id, "n": n})
        else:
            self._rich.update(self._tasks[task_id], completed=n)

    def done(self, task_id: str):
        if self.use_protocol:
            self._emit({"task": task_id, "status": "done"})
        else:
            self._rich.update(self._tasks[task_id], visible=False)

    def close(self):
        if not self.use_protocol:
            self._rich.__exit__(None, None, None)

    def _emit(self, data: dict):
        sys.stderr.write(f"PROGRESS:{json.dumps(data)}\n")
        sys.stderr.flush()
```

### 1c. 父进程读取器（tstriage）

```python
# tstriage/progress_reader.py (~70 lines)
import json, sys, time, logging
from rich.progress import Progress

logger = logging.getLogger('tstriage.progress')

class SubprocessProgress:
    """读取子进程 stderr 中的 PROGRESS 行，TTY 下更新 Rich Progress，非 TTY 下定期输出纯文本行"""

    def __init__(self, progress: Progress | None = None, ctx: str = ""):
        self.progress = progress          # None 表示 Jenkins 模式
        self.ctx = ctx                     # 上下文前缀，如 "video1.m2ts"
        self._tasks: dict[str, dict] = {}  # task_id → {rich_id, desc, last_log_time, last_log_n}
        self._is_tty = sys.stderr.isatty()

    def feed(self, line: str):
        if line.startswith("PROGRESS:"):
            try:
                data = json.loads(line[len("PROGRESS:"):])
            except json.JSONDecodeError:
                return
            tid = data["task"]
            if tid not in self._tasks:
                self._tasks[tid] = self._register(tid, data)
            self._update(tid, data)
        else:
            sys.stderr.write(line)

    def _register(self, tid: str, data: dict) -> dict:
        info = {"desc": data.get("desc", tid), "total": data["total"],
                "last_log_time": 0.0, "last_log_n": -1}
        if self.progress is not None:
            info["rich_id"] = self.progress.add_task(
                info["desc"], total=info["total"], unit=data.get("unit", "it"))
        else:
            # Jenkins 模式：首次注册时打一行
            logger.info(f'[{self.ctx}] {info["desc"]}: 0/{info["total"]}')
        return info

    def _update(self, tid: str, data: dict):
        info = self._tasks[tid]
        if data.get("status") == "done":
            if self.progress is not None:
                self.progress.update(info["rich_id"], completed=info["total"], visible=False)
            else:
                logger.info(f'[{self.ctx}] {info["desc"]}: 完成')
            return

        n = data["n"]
        if self.progress is not None:
            self.progress.update(info["rich_id"], completed=n)
        else:
            # Jenkins: 只在进度变化 >= 10% 或距上次日志 >= 30s 时输出
            pct = n / info["total"] * 100
            prev_pct = info["last_log_n"] / info["total"] * 100
            elapsed = time.time() - info["last_log_time"]
            if (pct - prev_pct >= 10) or (elapsed >= 30):
                logger.info(f'[{self.ctx}] {info["desc"]}: {n}/{info["total"]} ({pct:.0f}%)')
                info["last_log_time"] = time.time()
                info["last_log_n"] = n
```

这样就实现了双模式：

- **TTY**: `progress` 不为 None → 更新 Rich 动画进度条
- **Jenkins (非 TTY)**: `progress` 为 None → 定期输出纯文本进度行，抑制频率避免刷屏

---

## Phase 2: tstriage 改造

### 2a. subprocess_utils.py — 分离 stdout 和 stderr

当前：
```python
result = subprocess.run(cmd, capture_output=True, text=True)
```

改为：调用侧改用 `subprocess.Popen` 并读取 stderr 中的 PROGRESS 行。提供一个高层封装：

```python
def run_pipe(cmd: list[str], progress: SubprocessProgress | None = None):
    """执行命令，stdout 用于捕获 JSON 结果，stderr 用于 progress/透传"""
    logger.debug(f'Running: {" ".join(cmd)}')
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        logger.error(f'Command not found: {cmd[0]}')
        sys.exit(1)

    # 读取 stderr（在后台线程中，避免阻塞）
    stderr_lines = []
    def _read_stderr():
        for line in proc.stderr:
            if progress is not None:
                progress.feed(line.rstrip('\n'))
            else:
                sys.stderr.write(line)
    import threading
    t = threading.Thread(target=_read_stderr, daemon=True)
    t.start()

    stdout = proc.stdout.read()
    proc.wait()
    t.join(timeout=1)
    if proc.returncode != 0:
        logger.error(f'Command failed (exit {proc.returncode}): {" ".join(cmd)}')
        raise RuntimeError(f'Command failed: {cmd[0]} exited with {proc.returncode}')
    return stdout
```

### 2b. pipeline.py — 移除 ffmpeg stderr 重定向

当前：
```python
stderr=open('encode.log', 'w')
stderr=open('strip.log', 'w')
```

改为 `stderr=None`——ffmpeg 进度直接显示在终端。TTY 下自动 `\r` 回刷进度行，非 TTY 下输出静态定期行。

### 2c. pipeline.py — _get_program_clips 使用统一封装

当前直接调 `subprocess.run()`。改为使用 `subprocess_utils.run_json()`。

### 2d. 删除冗余 returncode 检查

`tasks.py:38-39`——`subprocess_utils.run()` 已经 raise，这两行是死代码。

### 2e. epg.py — 不要吞掉 stderr

`EPG.Dump()` quiet 模式下 `stderr=subprocess.DEVNULL`。至少保留 stderr，让错误可见。

### 2f. runner.py — Rich 统一面板

每个 task 方法使用 Rich Progress 面板，包裹子进程调用：

```python
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.console import Console
from rich.logging import RichHandler

console = Console()
logging.basicConfig(
    level=log_level,
    format='%(message)s',
    handlers=[RichHandler(console=console, rich_tracebacks=True)]
)

def Analyze(self):
    items = list(self.nas.ActionItems('.toanalyze'))
    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), BarColumn(),
        console=console, transient=False
    ) as progress:
        sub_progress = SubprocessProgress(progress)
        file_task = progress.add_task("Analyze", total=len(items))
        for path in items:
            item = self.LoadActionItem(path)
            progress.update(file_task, description=f"Analyze: {path.name}")
            run_pipe(cli_config.tscutter('--progress', 'analyze', ...), progress=sub_progress)
            progress.advance(file_task)
```

### 2g. 日志措辞修正

| 当前 | 改为 |
|------|------|
| `Analyzing to split ...` | `Analyze: {path.name}` |
| `Extracting EPG ...` | 删除（子进程 stderr 已有输出） |
| `Extracting subtitles ...` | `Prepare subtitles: {path.name}` |
| `Marking ...` | `Mark: {path.name}` |
| `Cutting ...` | `Cut: {path.name}` |
| `Extracted Program length: 1199.9987999999998` | `Duration: 1200.0s (20m0s)` |
| `Will be encoded into 1 files` | `Encoding 1 file` |
| `Trying to fix issues in subtitles ...` | `Normalize subtitles: {path.name}` |
| `Audio stream 1 (position 0)` | `Audio stream #1` |
| `logger.warn(...)` | `logger.warning(...)` |
| `--quiet` help: `disable progress bar` | `suppress non-error output` |

### 2h. 新增 --verbose 标志

```
-q / --quiet   : WARNING（安静，Jenkins/cron）
默认            : INFO（正常）
-v / --verbose : DEBUG（排查问题）
```

---

## Phase 3: tscutter 改造

### 3a. 新增 --progress 标志

```python
parser.add_argument('--progress', action='store_true',
                    help='output PROGRESS JSON lines to stderr (for pipeline orchestration)')
```

### 3b. 替换全部 tqdm 为 Rich Progress / ProgressReporter

tscutter 现有 6 处 tqdm 调用，改为使用 `Progress` 工具类（Phase 1b）：

| 位置 | 当前 | 改为 |
|------|------|------|
| `analyze.py:56` | `tqdm(intervals, desc='Looking for cut position')` | `progress.add_task("cut_position", ...)` + `progress.update(...)` |
| `ffmpeg.py:91` | `tqdm(total=to-ss, unit='secs')` | `progress.add_task("extract_streams", ...)` |
| `ffmpeg.py:126` | `tqdm(total=to-ss, unit='secs')` | `progress.add_task("extract_props", ...)` |
| `common.py:88` | `tqdm(clips, desc='splitting files')` | `progress.add_task("split_files", ...)` |
| `common.py:110` | `tqdm(total=totalSize, ...)` | `progress.add_task("copy_bytes", ...)` |

### 3c. 修复日志级别

当前硬编码 DEBUG：
```python
logging.basicConfig(level=logging.DEBUG, ...)
```

改为：
```python
log_level = logging.WARNING if args.quiet else logging.INFO
logging.basicConfig(level=log_level, format='%(message)s',
                    handlers=[RichHandler(console=console, rich_tracebacks=True)])
```

### 3d. 修复 --quiet 不生效

当前多处在 `--quiet` 时 tqdm 仍然渲染。改为使用 `Progress` 工具类后，`--quiet` 由 `console.quiet` 或 log level 自动控制。

### 3e. 修复废弃 API

`audio.py:22`: `logger.warn()` → `logger.warning()`

### 3f. 移除 standalone basicConfig

`audio.py:43`、`analyze.py:164`——只在 `__main__` 块中，不应污染模块。改为 INFO 级别。

---

## Phase 4: tsmarker 改造

### 4a. 新增 --progress 标志

同 Phase 3a。

### 4b. 替换全部 tqdm 为 Rich Progress / ProgressReporter

tsmarker 现有 10 处 tqdm 调用：

| 位置 | 当前 | 改为 |
|------|------|------|
| `clip_utils.py:31` | `tqdm(total=total_size, unit='B')` | `progress.add_task("copy", ...)` |
| `clip_utils.py:39` | `tqdm(clips, desc='Extracting clips')` | `progress.add_task("extract_clips", ...)` |
| `clipinfo.py:11` | `tqdm(..., desc='Marking clip info')` | `progress.add_task("clipinfo", ...)` |
| `ensemble.py:86` | `tqdm(..., desc='Searching *.mp4')` | `progress.add_task("search_mp4", ...)` |
| `ensemble.py:101` | `tqdm(..., desc='loading features')` | `progress.add_task("load_features", ...)` |
| `ensemble.py:139` | `tqdm(..., desc='Training')` | `progress.add_task("training", ...)` |
| `logo.py:23` | `tqdm(clips, desc='Detecting logo')` | `progress.add_task("detect_logo", ...)` |
| `pipeline.py:98` | `tqdm(total=info.duration, ...)` | `progress.add_task("ffmpeg_logo", ...)` |
| `text_extractor.py:80` | `tqdm(range(len(clips)))` | `progress.add_task("speech_to_text", ...)` |

### 4c. 修复日志级别

tsmarker 的 `marker.py` 已经正确使用 `WARNING if args.quiet else INFO`。但 `--quiet` 未传递到所有 tqdm 调用点——Phase 4b 统一解决。

### 4d. 移除 standalone basicConfig

`pipeline.py:187`、`ensemble.py:191`、`subtitles.py:96`——只在 `__main__` 的，移除它们。

### 4e. `print()` 错误改为 `sys.exit()`

`pipeline.py:20,34`——裸 `print(e)` 应改为 `logger.error()`，或至少 `print(..., file=sys.stderr)`。

---

## Phase 5: 清理死代码

- `tstriage/tee.py:3` — 未使用的 logger
- `tstriage/epg.py:8` — 未使用的 logger
- `tstriage/epgstation.py:5` — 未使用的 logger
- `tstriage/tasks.py:38-39` — 死代码（returncode 检查）
- `tstriage/CLAUDE.md` — 删除对已删除的 `common.py` / `CopyWithProgress` 的引用

---

## 最终效果

### 本地终端（TTY）

```
╭─ Pipeline ──────────────────────────────────────────────╮
│ ✅ Categorize                    2 files                │
│ ⏳ Analyze                       1/2 files              │
│    ├─ video1.m2ts                                       │
│    │   ├─ 查找切点               ████████░░  80% 40/50  │
│    │   └─ 提取音频流             ██████░░░░  60% 90/150s│
│    └─ video2.m2ts  (等待中)                             │
│ ⏸ Encode                        0/1 files (等待中)     │
╰─────────────────────────────────────────────────────────╯
```

### Jenkins（非 TTY）—— 纯文本定期进度行

非 TTY 下 Rich Progress 不渲染。`SubprocessProgress` 退化为定期日志输出：

```
2026-05-04 20:15:47 - INFO - Analyze start: video1.m2ts
2026-05-04 20:15:47 - INFO - [video1.m2ts] 查找切点: 0/50 (0%)
2026-05-04 20:17:00 - INFO - [video1.m2ts] 查找切点: 20/50 (40%)
2026-05-04 20:18:30 - INFO - [video1.m2ts] 查找切点: 40/50 (80%)
2026-05-04 20:19:38 - INFO - [video1.m2ts] 查找切点: 完成
2026-05-04 20:19:38 - INFO - [video1.m2ts] 提取音频流: 0/2405s (0%)
2026-05-04 20:20:30 - INFO - [video1.m2ts] 提取音频流: 800/2405s (33%)
2026-05-04 20:21:30 - INFO - [video1.m2ts] 提取音频流: 1600/2405s (67%)
2026-05-04 20:22:38 - INFO - [video1.m2ts] 提取音频流: 完成
2026-05-04 20:23:13 - INFO - Analyze done: video1.m2ts (3m51s)
2026-05-04 20:23:41 - INFO - Encode start: video1.mkv
frame= 5620 fps=134 time=00:03:07.62 bitrate=3263.9kbits/s speed=4.49x  ← ffmpeg 自动输出的非 TTY 行
2026-05-04 20:26:50 - INFO - Encode done: video1.mkv (3m9s)
```

输出频率控制：
- **进度变化 ≥ 10%** 时输出一行（避免刷屏）
- **距上次输出 ≥ 30 秒**时强制输出一行（防止长时间无反馈看起来像卡死）
- 每个子操作开始和完成各打一行

TTY 自动检测，无需配置。`sys.stderr.isatty()` 为 False 时自动进入此模式。

### Jenkins 插件配置

所需插件：

| 插件 | 用途 | 状态 |
|------|------|------|
| `ansicolor` | 渲染 ANSI 颜色码（Rich 的彩色输出依赖它） | 已安装 |
| `timestamper` | 每行日志加时间戳 | 已安装 1.26 |
| `pipeline-stage-view` | Stage 时间线图（Jenkins 内置） | 已安装 2.34 |

**全部就绪**，无需额外安装。

**Jenkinsfile 配置**：

```groovy
pipeline {
    agent any
    options {
        ansiColor('xterm')   // 启用 ANSI 颜色渲染
        timestamps()          // 每行日志加时间戳
    }
    stages {
        stage('Analyze')  { steps { sh 'uv run python -m tstriage.runner --task analyze' } }
        stage('Mark')     { steps { sh 'uv run python -m tstriage.runner --task mark' } }
        stage('Cut')      { steps { sh 'uv run python -m tstriage.runner --task cut' } }
        stage('Encode')   { steps { sh 'uv run python -m tstriage.runner --task encode' } }
        stage('Confirm')  { steps { sh 'uv run python -m tstriage.runner --task confirm' } }
    }
}
```

**效果**：
- **Stage View**（Jenkins Classic UI 内置）显示每个 task 的耗时条
- **ansiColor** 让非 TTY 下的纯文本进度行也能带颜色渲染
- **Timestamper** 给每行日志加时间戳，Jenkins 控制台可搜索 `[video1.m2ts]` 追踪单个文件的所有进度

注意：Jenkins console 是 line-based 的，`\r` 回刷的多行动画进度条（Docker 风格面板）无法正常渲染。所以 TTY 模式用 Rich 动画面板，非 TTY 模式走纯文本定期进度行——两种模式自动切换。

---

## 不改的东西

- **保留 argparse**。换成 click/typer 无 UX 价值，只增风险。
- **不破坏向后兼容**。tscutter/tsmarker 不加 `--progress` 时行为不变（Rich 直接渲染）。

## 依赖变更

- **tstriage**: `pyproject.toml` 添加 `rich`
- **tscutter**: `pyproject.toml` 添加 `rich`，移除 `tqdm`
- **tsmarker**: `pyproject.toml` 添加 `rich`，移除 `tqdm`

## 工作量估算

| Phase | 内容 | 改动量 |
|-------|------|--------|
| Phase 1 | PROGRESS 协议 + 共享工具类 | ~80 行（新文件） |
| Phase 2 | tstriage 改造 | ~100 行 |
| Phase 3 | tscutter 改造 | ~80 行（6 处 tqdm → Rich） |
| Phase 4 | tsmarker 改造 | ~120 行（10 处 tqdm → Rich） |
| Phase 5 | 死代码清理 | ~20 行（删除） |
| **合计** | | **~400 行** |

## 验证

1. 本地运行 `tstriage --task analyze`——验证 Rich 统一面板显示，子进程进度条协调更新
2. 本地运行 `tscutter analyze --input test.m2ts`——验证独立运行时 Rich 进度条正常
3. 本地运行 `tstriage --quiet --task analyze`——验证进度条和 INFO 日志被抑制
4. 本地运行 `tstriage --verbose --task analyze`——验证 DEBUG 日志出现
5. 模拟 Jenkins 环境运行（`TERM=dumb` 或管道）——验证无 ANSI 乱码，纯文本 start/done 日志
6. Jenkins 中实际运行——验证 TTY 自动检测

---

## 实施状态（2026-05-05，2026-05-05 最终更新）

### 已完成

#### Phase 1: PROGRESS 协议 + 共享工具类 ✅

| 文件 | 改动 |
|------|------|
| `tstriage/_progress.py` | **新增**：`SubprocessProgress`（父进程读取器）+ `Progress`（子进程包装器）|
| `tscutter/_progress.py` | **新增**：`Progress` 包装器（子进程侧）|
| `tsmarker/_progress.py` | **新增**：`Progress` 包装器（子进程侧）|

协议格式：`PROGRESS:{"task":"...","n":N,"total":T,"desc":"...","unit":"s|B"}` 通过 stderr 传递。

#### Phase 2: tstriage 改造 ✅

| 文件 | 改动 |
|------|------|
| `subprocess_utils.py` | `capture_output`→`stdout=PIPE,stderr=None`；新增 `run_pipe()` 支持双线程 I/O + `poll()` 循环保证 Rich Live 刷新；`_clean_env()` 消除 `uv run` 警告 |
| `pipeline.py` | ffmpeg stderr→DEVNULL；strip stderr→DEVNULL；`_get_program_clips` 改用 `run_json()`；Encode 进度通过 `Tee.pump(on_chunk=...)` 字节流驱动；日志措辞修正 |
| `runner.py` | `warn()`→`warning()`；`--quiet` help 修正；新增 `--verbose`；`RichHandler` + `RichProgress` 多级面板；Ctrl+C 恢复 action file 原名；`refresh_per_second=10` |
| `tasks.py` | 删除死 returncode 检查；`with progress.status(...)` 语法用于子步骤；`run_pipe()` + `--progress` 主力路径 |
| `nas.py` | `tqdm` → `rich.progress.track` |
| `epg.py` | stderr+stdout→DEVNULL 抑制 epgdump 噪音 |
| `epgstation.py` | 移除未使用的 logger |
| `tee.py` | 移除未使用的 logger；`pump()` 新增 `on_chunk` 回调 |
| `pyproject.toml` | `tqdm` → `rich` |

#### Phase 3: tscutter Rich 集成 ✅

| 文件 | 改动 |
|------|------|
| `analyze.py` | `tqdm`→`Progress`；新增 `--progress` flag；`DEBUG`→`INFO/WARNING`；`RichHandler`；`FindSplitPosition` 传递 `progress` |
| `ffmpeg.py` | 3 处 `tqdm`→`Progress`（`ExtractStream`/`ExtractFrameProps`）|
| `audio.py` | `warn()`→`warning()`；`quiet`→`progress`；移除 standalone `basicConfig` |
| `common.py` | 2 处 `tqdm`→`Progress`（`SplitVideo` 字节流 + MB unit / `ExtractClipPipe`）|
| `pyproject.toml` | `tqdm` → `rich` |

#### Phase 4: tsmarker Rich 集成 ✅

| 文件 | 改动 |
|------|------|
| `marker.py` | 新增 `--progress` flag；`RichHandler`；`progress` 传递到所有 MarkAll 方法 |
| `clip_utils.py` | 2 处 `tqdm`→`Progress` |
| `clipinfo.py` | `tqdm`→`Progress` |
| `logo.py` | `tqdm`→`Progress` |
| `pipeline.py` | `tqdm`→`Progress`；`print(e)`→`logger.error()` |
| `ensemble.py` | 3 处 `tqdm`→`Progress` |
| `text_extractor.py` | `tqdm`→`Progress` |
| `dataset.py` | 移除未使用的 `tqdm` import |
| `subtitles.py` | `tqdm`→`Progress`；新增 `progress` 参数 |
| `speech/MarkerMap.py` | `quiet`→`progress`；传递到 `llm_client.classify_batch` |
| `speech/llm_client.py` | `classify_batch`/`_classify_iterative` 新增 `progress` 参数 |
| `common.py` | `Cut()` 方法 `quiet`→`progress` |
| `pyproject.toml` | 新增 `rich`；`tscutter` 改为本地源 `{ path = "../tscutter", editable = true }` |

#### Phase 5: 死代码清理 ✅

- `tee.py`/`epg.py`/`epgstation.py` — 移除未使用的 `logging` import + `logger`
- `tasks.py` — 删除死 returncode 检查
- `CLAUDE.md` — 删除已删除 `common.py`/`CopyWithProgress` 的引用
- 三个 repo 全部清除 `tqdm` 引用（0 处残留）

#### Jenkins / CI 支持 ✅

| 特性 | 实现 |
|------|------|
| TTY 自动检测 | `SubprocessProgress.__init__` 检查 `sys.stderr.isatty()`，非 TTY 丢弃 RichProgress 引用退化为纯文本 |
| 定期进度日志 | 每分钟一次 `[filename] Task: n/total (pct%)`，首行带文件名后续缩进省略 |
| ANSI 颜色 | 已装 `ansicolor` 插件，Jenkinsfile `ansiColor('xterm')` |
| 时间戳 | 已装 `timestamper`，`timestamps()` |
| Stage 视图 | 已装 `pipeline-stage-view`，Jenkinsfile 按 task 拆 `stage` |

#### 多级进度显示 ✅

TTY 模式：
```
⠋ Analyze: filename ━━━━━━━━━━━━━━ 0:03:51 0:05:42
   Extracting EPG
   Extracting streams ━━━━━━━━━━━━ 0:00:45 0:01:30
   Finding cut positions ━━━━━━━━ 0:02:10 0:01:15
```

非 TTY 模式（Jenkins）：
```
[filename] Extracting streams: 0/2405.0
  Extracting streams: 1200.0/2405.0 (50%)
  Extracting streams: done
  Finding cut positions: 0/46.0
  Finding cut positions: 23.0/46.0 (50%)
```

### 实测统计

| 指标 | 数值 |
|------|------|
| 涉及 repo | 3 |
| 修改文件 | 35 |
| 新增文件 | 3（`_progress.py` × 3）|
| 新增代码行 | ~450 行 |
| 删除代码行 | ~110 行 |
| 移除的 tqdm 调用 | 16 处（tscutter 6 + tsmarker 10）|
| 新增 Rich 进度任务 | 16 处 |
| 流式/离散进度区分 | `unit="B"|"s"` → 时间列 + bar；无 unit → 纯 bar |
| 进度条列 | `Spinner, Text(ellipsis), Bar, TimeElapsed, TimeRemaining` |

### 后续迭代

- **tsmarker 独立运行时 tqdm→Rich 测试**：tsmarker 在 Jenkins 上通过 `uv run --directory` 调用，本地源已配置正确
