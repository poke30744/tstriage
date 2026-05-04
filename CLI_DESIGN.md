# tstriage CLI 化改造：完整设计

## 背景

tstriage 目前通过 Python import 直接依赖 `tscutter` 和 `tsmarker` 两个库，导致三者版本绑定、无法独立升级。

目标：将所有 Python import 依赖替换为 subprocess 调用 CLI 工具。三者之间的唯一契约是 **JSON 文件格式**（`.ptsmap`、`.markermap`）和 **CLI stdout 约定**，不再共享任何 Python 代码。

## 解耦原则

核心设计决策：**ptsmap 的读写者是 tscutter。tsmarker 不持有 PtsMap，而是通过 tscutter CLI 获取 clip 信息。tstriage 是纯 shell 编排器，不持有 PtsMap 也不持有 MarkerMap。**

```
tscutter  ──写入──→ .ptsmap (唯一写)
                ├── tscutter list-clips ──→ tsmarker (stdout JSON)
                └── tscutter select-clips ──→ tsmarker (stdout JSON)

tsmarker  ──读取──→ .ptsmap (只读，通过 tscutter CLI)
          ──读写──→ .markermap (唯一读写)
                ├── tsmarker extract-clips ──→ tstriage (stdout TS 管道)
                ├── tsmarker get-program-clips ──→ tstriage (stdout JSON)
                └── tsmarker cut --by auto ──→ 文件系统
```

## 整体架构

```
tstriage (Python, 纯 shell 编排器)
  ├── EncodePipeline (ffmpeg 编码, 自有逻辑)
  ├── EPG 解析 (已是 subprocess)
  ├── subprocess → tscutter analyze
  ├── subprocess → tscutter probe
  ├── subprocess → tsmarker extract-clips
  ├── subprocess → tsmarker extract-logo
  ├── subprocess → tsmarker crop-detect
  ├── subprocess → tsmarker prepare-subtitles
  ├── subprocess → tsmarker mark-subtitles
  ├── subprocess → tsmarker mark-clipinfo
  ├── subprocess → tsmarker mark-logo
  ├── subprocess → tsmarker mark-speech
  ├── subprocess → tsmarker ensemble-dataset
  ├── subprocess → tsmarker ensemble-train
  ├── subprocess → tsmarker ensemble-predict
  ├── subprocess → tsmarker groundtruth
  ├── subprocess → tsmarker get-program-clips
  └── subprocess → tsmarker cut

tscutter CLI (Python, 自主项目)
  ├── analyze: 静音检测 → 场景变换 → 写入 .ptsmap
  ├── probe: ffprobe 查询 → VideoInfo JSON
  ├── list-clips: 读 .ptsmap → clip 列表 JSON
  ├── select-clips: 读 .ptsmap → 候选长片段 JSON
  ├── PtsMap (~25 行: json.load + Clips + SelectClips)
  └── ffmpeg.probe() (~3 行)

tsmarker CLI (Python, 自主项目)
  ├── extract-clips: 读 .ptsmap → 按 byte range 提取 TS (stdout / 文件)
  ├── extract-logo: TS + .ptsmap → 台标边缘图 PNG
  ├── crop-detect: logo PNG → crop 参数 JSON
  ├── prepare-subtitles: TS + .ptsmap → .ass.original + .assgen
  ├── mark-*: 标记 .markermap (subtitles/clipinfo/logo/speech/ensemble)
  ├── ensemble-*: 集成学习训练与预测
  ├── groundtruth: 人工校验 → 标记 _groundtruth
  ├── get-program-clips: .markermap → 节目分组 JSON
  ├── cut: TS + .ptsmap + .markermap → CM/ 分类文件夹
  ├── CopyPart / CopyPartPipe / ClipToFilename (tsmarker 独有, 移自 tscutter)
  └── ffmpeg.probe() (~3 行: mark-clipinfo 用 duration)
```

### 数据契约

- **`.ptsmap`**: `{ "pts_time": { "next_start_pos": int, "prev_end_pos": int, "prev_end_pts": float, ... }, ... }`
- **`.markermap`**: `{ "[start_pts, end_pts]": { "subtitles": float, "logo": float, ... }, ... }`

CLI stdout 均为紧凑 JSON。

---

## 一、tscutter CLI

统一入口: `tscutter <subcommand> [options]`

共同约定:
- `--quiet` / `-q`: 关闭进度条输出（进度输出到 stderr）
- 正常退出 code=0，异常 code≠0
- 数据输出到 stdout（JSON），进度/日志到 stderr

### 1.1 tscutter analyze

替代 `tscutter.analyze.AnalyzeVideo()`

```
tscutter analyze --input <ts_path> --output <ptsmap_path>
                 [--length <ms>] [--threshold <dB>] [--shift <sec>]
                 [--quiet]
```

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|---|---|---|---|---|
| `--input`, `-i` | path | ✅ | — | 输入 TS 文件路径 |
| `--output`, `-o` | path | ✅ | — | 输出 .ptsmap 路径 |
| `--length`, `-l` | int | ❌ | 800 | 最小静音长度 (ms) |
| `--threshold`, `-t` | int | ❌ | -80 | 静音阈值 (dB) |
| `--shift`, `-s` | float | ❌ | 1 | 切分点搜索偏移 (秒) |
| `--quiet`, `-q` | flag | ❌ | false | 不输出进度 |

stdout: 无。结果写入 `--output`。

错误处理:

| 条件 | exit code | stderr |
|---|---|---|
| 输入文件不存在 | 1 | `TsFileNotFound: "<name>" not found!` |
| TS 格式无效 | 2 | `InvalidTsFormat: "<name>" is invalid!` |
| ffmpeg 不在 PATH | 3 | `ProgramNotFound: ffmpeg not found in $PATH!` |
| 处理失败 | 4 | 具体错误详情 |

### 1.2 tscutter probe

替代 `tscutter.ffmpeg.InputFile.GetInfo()`

```
tscutter probe --input <ts_path>
```

内部实现: `ffmpeg.probe(str(path), show_programs=None)`（推荐用 [ffmpeg-python](https://github.com/kkroening/ffmpeg-python) 库，3 行代码替代手写 50 行 ffprobe JSON 解析）。

stdout:
```json
{"duration":3600.5,"width":1920,"height":1080,"fps":29.97,"sar":[1,1],"dar":[16,9],"soundTracks":2,"serviceId":1024}
```

错误处理:

| 条件 | exit code | stderr |
|---|---|---|
| 输入文件不存在 | 1 | `TsFileNotFound: ...` |
| ffprobe 解析失败 | 2 | `InvalidTsFormat: ...` |
| ffprobe 不在 PATH | 3 | `ProgramNotFound: ffprobe not found` |

### 1.3 tscutter list-clips

替代 tsmarker 中所有 `ptsMap.Clips()` 调用。

```
tscutter list-clips --index <ptsmap_path>
```

stdout: JSON 数组（所有 clip 的 [start, end]）
```json
[[0.0, 15.8], [15.8, 120.5], [120.5, 350.2], [350.2, 480.0]]
```

错误处理:

| 条件 | exit code | stderr |
|---|---|---|
| ptsmap 不存在 | 1 | `FileNotFoundError: ...` |
| JSON 损坏 | 2 | `InvalidIndexFormat: ...` |

### 1.4 tscutter select-clips

替代 `PtsMap.SelectClips()`，供 tsmarker extract-logo 选择候选片段。

```
tscutter select-clips --index <ptsmap_path> [--min-length <sec>]
```

stdout: JSON 数组（按长度降序排列的长片段）
```json
[[120.5, 350.2], [350.2, 480.0]]
```

参数:

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|---|---|---|---|---|
| `--index`, `-x` | path | ✅ | — | .ptsmap 文件 |
| `--min-length` | float | ❌ | 150 | 最短片段时长 (秒) |

注：`select_longest_clips` 的领域知识（总长不超过视频一半）保留在 tsmarker 侧——tscutter 只负责提供排序后的候选列表，tsmarker 自行决定取前几个。

---

## 二、tsmarker CLI

统一入口: `tsmarker <subcommand> [options]`

共同约定:
- `--quiet` / `-q`: 关闭进度条
- markermap 子命令均为**原地修改**: 读 JSON → 处理 → 写回同一文件
- clips 信息通过 `tscutter list-clips` / `tscutter select-clips` 获取（stdout JSON 解析）
- TS 数据提取由 tsmarker 自己的 `extract-clips` 子命令完成（读 .ptsmap → 按 byte range 从 TS 读取）
- 正常退出 code=0，异常 code≠0

### 2.1 tsmarker extract-clips

替代 `PtsMap.ExtractClipsPipe()`, `PtsMap.ExtractClipPipe()`, `PtsMap.SplitVideo()`。
将 `CopyPart`, `CopyPartPipe`, `ClipToFilename` 从 tscutter 移入 tsmarker（这些函数的唯一消费者是 tsmarker）。

```
tsmarker extract-clips --input <ts_path> --index <ptsmap_path>
                       --clips <json_array> [--output-dir <dir>]
                       [--quiet]
```

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|---|---|---|---|---|
| `--input`, `-i` | path | ✅ | — | 输入 TS 文件 |
| `--index`, `-x` | path | ✅ | — | .ptsmap 文件 |
| `--clips`, `-c` | JSON | ✅ | — | 片段数组 `'[[0.0,100.5],[200.0,350.8]]'` |
| `--output-dir`, `-o` | path | ❌ | (stdout) | 输出目录; 不指定则输出 TS 字节流到 stdout |
| `--quiet`, `-q` | flag | ❌ | false | 不输出进度 |

**stdout 模式**（不指定 `--output-dir`）:
- clips 对应的 TS 字节流连续输出到 stdout
- 进度条输出到 stderr
- 用途: Encode 阶段管道 `tsmarker extract-clips ... | ffmpeg -i - ...`

**文件模式**（指定 `--output-dir`）:
- 每个 clip 输出独立 `.ts` 文件，使用 `ClipToFilename` 命名

错误处理:

| 条件 | exit code | stderr |
|---|---|---|
| 输入文件不存在 | 1 | 文件路径 |
| ptsmap 不存在 | 2 | `FileNotFoundError: ...` |
| ptsmap JSON 损坏 | 3 | `InvalidIndexFormat: ...` |
| clip 范围超出 | 4 | `ClipOutOfRange: <clip>` |
| stdout SIGPIPE | 0 | 无 |

### 2.2 tsmarker extract-logo

替代 `tsmarker.pipeline.ExtractLogoPipeline()`

```
tsmarker extract-logo --input <ts_path> --index <ptsmap_path>
                      --output <png_path> [--max-time <sec>]
                      [--no-remove-border] [--quiet]
```

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|---|---|---|---|---|
| `--input`, `-i` | path | ✅ | — | 输入 TS 文件 |
| `--index`, `-x` | path | ✅ | — | .ptsmap 文件 |
| `--output`, `-o` | path | ✅ | — | 输出台标边缘图 PNG |
| `--max-time` | float | ❌ | 120 | 最长提取时长 (秒) |
| `--no-remove-border` | flag | ❌ | false | 不消除画幅边缘 |
| `--quiet`, `-q` | flag | ❌ | false | 不输出进度 |

内部流程:
1. 调 `tscutter select-clips -x <ptsmap>` 获取候选片段
2. 选择最长的，截取不超过 `--max-time`
3. 调内部 `extract-clips` 获取 TS 数据
4. ffmpeg 提取帧 → 均值合成 → Canny 边缘检测
5. 可选去除边框
6. 输出 PNG

### 2.3 tsmarker crop-detect

替代 `tsmarker.pipeline.CropDetectPipeline()`

```
tsmarker crop-detect --input <logo_png> [--threshold <float>]
```

stdout: JSON; 无边框时 `null`
```json
{"w": 1440, "h": 1080, "x": 240, "y": 0}
```

### 2.4 tsmarker prepare-subtitles

替代 `PrepareSubtitles()`（定义在 tsmarker.speech.MarkerMap）

```
tsmarker prepare-subtitles --input <ts_path> --index <ptsmap_path> [--quiet]
```

输出文件（放在 ptsmap 同级目录）:
- `<stem>.ass.original` — Caption2AssC 提取的字幕
- `<stem>.assgen` — Google STT 生成的文本 JSON

内部流程:
1. 调 `tscutter list-clips -x <ptsmap>` 获取 clip 列表
2. 整个 TS 文件管道喂给 Caption2AssC（4 行标准文件读取，不需要 CopyPartPipe）
3. 对每个 clip 提取字幕文本
4. 无字幕的 clip → ffmpeg 提取 WAV → Google Speech Recognition
5. 写入 `.assgen`

### 2.5 tsmarker mark-subtitles

```
tsmarker mark-subtitles --video <ts_path> --index <ptsmap_path>
                        --marker <markermap_path>
```

行为:
1. 调 `tscutter list-clips -x <ptsmap>` 获取 clip 列表
2. Caption2AssC 提取字幕
3. 对每个 clip 检测字幕事件与 `[clip[0],clip[1]]` 重叠
4. 有重叠 → `1.0`，无 → `0.0`，无字幕流 → `0.5`
5. 写回 markermap

### 2.6 tsmarker mark-clipinfo

```
tsmarker mark-clipinfo --video <ts_path> --index <ptsmap_path>
                       --marker <markermap_path> [--quiet]
```

行为:
1. 调 `tscutter list-clips -x <ptsmap>` 获取 clip 列表
2. 调 `tscutter probe -i <ts>` 获取 duration
3. 计算每个 clip 的 position, duration, duration_prev, duration_next
4. 写回 markermap

### 2.7 tsmarker mark-logo

```
tsmarker mark-logo --video <ts_path> --index <ptsmap_path>
                   --marker <markermap_path> --logo <logo_png>
                   [--quiet]
```

行为:
1. 调 `tscutter list-clips -x <ptsmap>` 获取 clip 列表
2. 对每个 clip 调内部 `extract-clips` 管道获取 TS 数据 → 均值画面 → Canny 边缘
3. 与台标图做 AND overlap → logo score (0.0~1.0)
4. score ≤ 0.5 时用全长重试一次
5. 写回 markermap

### 2.8 tsmarker mark-speech

```
tsmarker mark-speech --video <ts_path> --index <ptsmap_path>
                     --marker <markermap_path> --api-url <url>
                     [--quiet]
```

行为:
1. 调 `tscutter list-clips -x <ptsmap>` 获取 clip 列表
2. 读取 `.ass.original` / `.assgen`（不存在则自动生成）
3. 提取每个 clip 文本
4. POST JSON 到 `--api-url`
5. 写入 `speech` 字段

### 2.9 tsmarker ensemble-dataset

```
tsmarker ensemble-dataset --input <search_folder> --output <csv_path>
                          [--no-normalize] [--quiet]
```

不需要 tscutter——tsmarker 自己遍历 `.mp4` + `.yaml` + `.markermap` 文件。

### 2.10 tsmarker ensemble-train

```
tsmarker ensemble-train --input <csv_path> --output <model_path>
                        [--random-state <int>] [--test-size <float>]
                        [--quiet]
```

### 2.11 tsmarker ensemble-predict

```
tsmarker ensemble-predict --model <model_path> --index <ptsmap_path>
                          --marker <markermap_path> [--normalize]
                          [--dry-run] [--quiet]
```

行为:
1. 调 `tscutter list-clips -x <ptsmap>` 获取 clip 列表
2. 从 markermap 同级目录读 `.yaml`
3. 用模型预测 `_ensemble`
4. 写回 markermap

### 2.12 tsmarker groundtruth

```
tsmarker groundtruth --marker <markermap_path> --index <ptsmap_path>
                     --clips-folder <folder>
```

行为:
1. 调 `tscutter list-clips -x <ptsmap>` 获取 clip 列表
2. clip 在 `folder/` → `1.0`，在 `folder/CM/` → `0.0`
3. 比较新旧值判断是否需重编码
4. 写回 markermap

stdout: `{"re_encode_needed": true}` 或 `false`

### 2.13 tsmarker get-program-clips

替代 tstriage 中 `MarkerMap.GetProgramClips()` + `MergeNeighbors()` + `SplitClips()` 全部逻辑。

```
tsmarker get-program-clips --marker <markermap_path> --index <ptsmap_path>
                           [--by <method>] [--split <n>]
                           [--by-group] [--quiet]
```

| 参数 | 类型 | 必需 | 默认值 | 说明 |
|---|---|---|---|---|
| `--marker`, `-m` | path | ✅ | — | .markermap 文件 |
| `--index`, `-x` | path | ✅ | — | .ptsmap 文件 |
| `--by`, `-b` | str | ❌ | `auto` | 节目判定字段: `auto` / `_groundtruth` / `_ensemble` / `subtitles` |
| `--split`, `-s` | int | ❌ | 1 | 拆分为 N 组（用于多文件编码） |
| `--by-group` | flag | ❌ | false | 每个 clip 独立成组 |

`--by auto` 优先级: `_groundtruth` > `_ensemble` > `subtitles`。

stdout:
```json
{
  "groups": [[[0.0,120.5],[120.5,350.2]], [[350.2,480.0]]],
  "by_method": "subtitles"
}
```

内部流程:
1. 调 `tscutter list-clips -x <ptsmap>` 获取 clip 列表
2. 读 markermap，按 `--by` 规则过滤出节目 clip
3. `MergeNeighbors`: 合并相邻 clip
4. `--split` > 1 时按均长拆分; `--by-group` 时每 clip 独立
5. 输出分组 JSON

### 2.14 tsmarker cut

```
tsmarker cut --video <ts_path> --index <ptsmap_path>
             --marker <markermap_path> --by <method>
             --output <folder> [--quiet]
```

`--by` 默认值: `auto`（自动选择 `_groundtruth` > `_ensemble` > `subtitles`）。

行为:
1. 调内部 `extract-clips --output-dir <tmp>` 拆分 TS
2. 读 markermap 根据 `--by` 字段分类
3. <0.5 → `output/CM/`，≥0.5 → `output/`
4. 文件名加 `.L` / `.S` 后缀

---

## 三、最终重复代码清单

### 实际重复（需各存在一份）

| 代码 | 在哪里 | 行数 | 说明 |
|---|---|---|---|
| `ffmpeg.probe(path, show_programs=None)` | tscutter + tsmarker | 各 ~3 | probe 子命令 / mark-clipinfo 用 duration |
| 文件读+写管道 (Caption2AssC) | tsmarker | 4 | `open().read()` 替代 CopyPartPipe（✅ 已完成） |
| 异常类 (`TsFileNotFound`, `InvalidTsFormat`) | tscutter + tsmarker | 各 ~3 | 简单单行类 |
| **总计** | | **~13 行** | |

> `ClipToFilename` 已随 `extract-clips` 移入 tsmarker，不再跨库。
> `CopyPart` / `CopyPartPipe` 已随 `extract-clips` 移入 tsmarker，不再跨库。

### 不重复（各自独有）

| 代码 | 属于 | 说明 |
|---|---|---|
| `PtsMap` 完整版 (~75 行) | tscutter | ptsmap 唯一读写者 |
| `DetectSilence` / `MergeIntervals` / `FindSplitPosition` / `GeneratePtsMap` | tscutter | analyze 领域逻辑 |
| 所有 MarkerMap 子类 (subtitles/clipinfo/logo/speech/ensemble/groundtruth) | tsmarker | 标记领域逻辑 |
| `GetProgramClips` / `MergeNeighbors` / `SplitClips` | tsmarker | 节目分组逻辑（移入 `get-program-clips`） |
| `ExtractLogoPipeline` / `CropDetectPipeline` | tsmarker | 台标领域逻辑 |
| `PrepareSubtitles` / `ExtractAudioText` | tsmarker | 字幕+STT |
| `EncodePipeline` / `StripTsCmd` / `EncodeTsCmd` | tstriage | 编码领域逻辑 |
| `EPG` 类 | tstriage | EPG 解析（已是 subprocess） |

### 已消除

| 原函数 | 消除方式 | 状态 |
|---|---|---|
| `CheckExtenralCommand` | 替换为 `shutil.which()`（标准库） | ✅ 完成 (2026-05-02) |
| `CopyPartPipe` | tsmarker 内联 4 行文件读取 | ✅ 完成 (2026-05-02) |
| `GetShortPath` | 已删除的 hack，无需复制 | ✅ 完成 (commit 8ef2a71) |
| `jsonpath_ng` 依赖 | tscutter GetInfo 改用 `ffmpeg.probe()` + 直接 dict 访问 | ✅ 完成 (2026-05-02) |
| `FormatTimestamp` | 仅 tscutter 使用，1 份 | 保留 |

---

## 四、ffmpeg/ffprobe 封装建议

| 场景 | 推荐方式 | 理由 | 状态 |
|---|---|---|---|
| ffprobe 查询 (`GetInfo`) | ✅ `ffmpeg-python` 的 `probe()` | 50 行 → 3 行，收益最大 | ✅ 已实施 |
| 简单 ffmpeg 提取 | ✅ `ffmpeg-python` | 语法更干净 | 待采用 |
| 复杂管道 (stdin 喂数据) | ❌ `subprocess.Popen` | ffmpeg-python 有已知死锁风险 | 保持 |
| tstriage 命令行构建 | ❌ 手写 `list[str]` | 需要精确控制参数做多进程编排 | 保持 |

> 注：tscutter `GetInfo()` 已替换为 `ffmpeg.probe(path, cmd=ffprobe, show_programs=None)` + 直接 dict 访问。
> 同时移除了 `jsonpath-ng` 依赖（tscutter pyproject.toml 中 `jsonpath-ng` → `ffmpeg-python`）。

---

## 五、tasks.py 迁移对照

| 函数 | 当前调用 | CLI 替换 |
|---|---|---|
| `Analyze()` | `AnalyzeVideo(...)` | `tscutter analyze -i <ts> -o <ptsmap> -l <len> -t <thresh> -s <shift>` |
| `Analyze()` | `PrepareSubtitles(...)` | `tsmarker prepare-subtitles -i <ts> -x <ptsmap>` |
| `Analyze()` | `ExtractLogoPipeline(...)` | `tsmarker extract-logo -i <ts> -x <ptsmap> -o <logo.png>` |
| `Analyze()` | audio decode check | **保留** (已是 subprocess ffmpeg) |
| `Mark()` | `subtitles.MarkerMap(...)` | `tsmarker mark-subtitles --video <ts> -x <ptsmap> -m <markermap>` |
| `Mark()` | `clipinfo.MarkerMap(...)` | `tsmarker mark-clipinfo --video <ts> -x <ptsmap> -m <markermap>` |
| `Mark()` | `logo.MarkerMap(...)` | `tsmarker mark-logo --video <ts> -x <ptsmap> -m <markermap> --logo <png>` |
| `Mark()` | `speech.MarkerMap(...)` | `tsmarker mark-speech --video <ts> -x <ptsmap> -m <markermap> --api-url <url>` |
| `Mark()` | `ensemble.CreateDataset(...)` | `tsmarker ensemble-dataset -i <folder> -o <csv>` |
| `Mark()` | `ensemble.LoadDataset()` + `Train()` | `tsmarker ensemble-train -i <csv> -o <model.pkl>` |
| `Mark()` | `ensemble.MarkerMap(...).MarkAll(...)` | `tsmarker ensemble-predict -m <model.pkl> -x <ptsmap> --marker <markermap>` |
| `Cut()` | `tsmarker.common.MarkerMap(...)` | `tsmarker cut --video <ts> -x <ptsmap> -m <markermap> --by auto -o <dir>` |
| `Confirm()` | `groundtruth.MarkerMap(...)` | `tsmarker groundtruth -m <markermap> -x <ptsmap> -c <clips_folder>` |
| `Encode()` | `MarkerMap.GetProgramClips()` + `MergeNeighbors()` + `SplitClips()` | `tsmarker get-program-clips -m <markermap> -x <ptsmap> [--split N] [--by-group]` |
| `Encode()` | `PtsMap.ExtractClipsPipe()` | `tsmarker extract-clips -i <ts> -x <ptsmap> -c '<json>'` (stdout 管道) |
| `Encode()` | `CropDetectPipeline(...)` | `tsmarker crop-detect -i <logo.png>` |
| `Encode()` | `ExtractLogoPipeline(...)` | `tsmarker extract-logo -i <ts> -x <ptsmap> -o <logo.png> --max-time 10 --no-remove-border` |

---

## 六、实现顺序

### Phase 1: 消除 tstriage 的 Python import 依赖（清洁工）
1. ✅ 替换 `CheckExtenralCommand` → `shutil.which()`（tscutter + tstriage 均已替换）
2. ✅ `CopyPartPipe` 替换为 4 行文件读取（tsmarker/subtitles.py）
3. ✅ `jsonpath-ng` → `ffmpeg-python` + 直接 dict 访问（tscutter/ffmpeg.py GetInfo）
4. ✅ 重构 `pipeline.py`: 解除 `class InputFile(ffmpeg.InputFile)` 继承 → 新建 `video_info.py` + `input_file.py`
5. ✅ 新增 `tsmarker get-program-clips` CLI（替代 tstriage MarkerMap 的 4 个方法）
6. ✅ 新增 `tsmarker cut --by auto` 默认值（消除 tstriage 中的 byMethod 选择逻辑）
7. ✅ 从 tstriage 删除 `PtsMap` / `MarkerMap` 导入，切换为 subprocess CLI 调用

### Phase 2: tscutter CLI
8. ✅ 添加 `--shift` 参数到现有 analyze 子命令
9. ✅ 新增 `probe` 子命令 (内部用 `ffmpeg.probe()`)
10. ✅ 新增 `list-clips` 子命令
11. ✅ 新增 `select-clips` 子命令
12. ✅ PtsMap 瘦身: 移除 `ExtractClipsPipe`（tstriage 改用 CLI），移除 `split` CLI 子命令（未被 tstriage 调用）

### Phase 3: tsmarker CLI
13. ✅ 新增 `extract-clips` 子命令（含 `clip_utils.py`)
14. ✅ 新增 `get-program-clips` 子命令
15. ✅ `cut` 子命令: `--by` 默认值改为 `auto`
16. ✅ 统一 CLI 入口框架（extract-logo, crop-detect, ensemble-dataset/train/predict 已添加）
17. ✅ 新增 CLI 的: prepare-subtitles, mark-subtitles, mark-clipinfo, mark-logo, mark-speech, groundtruth
18. ✅ 移除 `merge` CLI 子命令（未实现，未被 tstriage 调用）

### Phase 4: tstriage 切换
19. ✅ 逐函数切换为 subprocess CLI 调用
20. ✅ 删除 `from tscutter/tsmarker import ...` 所有导入
21. ✅ 保留 `pyproject.toml` 中 tscutter/tsmarker 作为 CLI 依赖（非 Python import）
22. ✅ 运行测试（12 单元测试 + 8 端到端 task 全部通过）

---

## 七、验证方法

1. 每个 CLI 子命令的单元测试
2. tstriage 现有测试 (`tests/`) 在迁移后全部通过
3. 端到端测试: 取一个样本 TS，完整跑通所有 task
4. 对比迁移前后 `.ptsmap` 和 `.markermap` 内容一致

---

## 八、实现进度

### 已完成 (2026-05-03)

全部 Phase 1-4 完成，端到端测试通过。

| 仓库 | 变更 | 文件 |
|---|---|---|
| tscutter | 新增 `probe`、`list-clips`、`select-clips` CLI | `analyze.py` |
| tscutter | `analyze` 新增 `--shift` 参数 | `analyze.py` |
| tscutter | 移除 `split` CLI（未被 tstriage 调用） | `analyze.py` |
| tscutter | 移除 `ExtractClipsPipe`（tstriage 改用 CLI） | `common.py` |
| tscutter | 添加 `audioop-lts`、升级 `requires-python >=3.13` | `pyproject.toml` |
| tsmarker | 新增 `extract-clips` 子命令 | `marker.py`, `clip_utils.py` |
| tsmarker | 新增 `get-program-clips` 子命令（含 merge+split 逻辑） | `marker.py`, `common.py` |
| tsmarker | 新增 `prepare-subtitles`、`extract-logo`、`crop-detect` CLI | `marker.py` |
| tsmarker | 新增 `ensemble-dataset/train/predict` CLI | `marker.py` |
| tsmarker | `cut`: `--method` → `--by`，默认值 `auto` | `marker.py` |
| tsmarker | `groundtruth`: stdout 改为 JSON | `marker.py` |
| tsmarker | 移除 `merge` CLI（未实现） | `marker.py` |
| tsmarker | 升级 `requires-python >=3.13` | `pyproject.toml` |
| tstriage | 新建 `video_info.py`、`input_file.py`（脱离 tscutter 继承） | 2 新文件 |
| tstriage | 新建 `cli_config.py`（tscutter/tsmarker 命令路径可配置） | 1 新文件 |
| tstriage | 新建 `subprocess_utils.py`（subprocess 辅助函数） | 1 新文件 |
| tstriage | `tasks.py`: 全部 task 切换为 subprocess CLI 调用 | `tasks.py` |
| tstriage | `pipeline.py`: EncodePipeline 改用 `tsmarker get-program-clips` + `tsmarker extract-clips` | `pipeline.py` |
| tstriage | `epg.py`: 解耦 tscutter `InputFile`，改为 `service_id: int` | `epg.py` |
| tstriage | `runner.py`: 移除 `Path` 段逻辑，新增 `Cli` 段读取 | `runner.py` |
| tstriage | 删除旧集成测试，新增 12 个单元测试 | `tests/` |
| tstriage | 添加 `ffmpeg-python` 直接依赖，升级 `requires-python >=3.13` | `pyproject.toml` |

### 最终结果：tstriage 零 Python import ✅

| 仓库 | Python import 依赖 |
|---|---|
| **tstriage** | `tscutter` ❌ 无, `tsmarker` ❌ 无 |
| **tsmarker** | `tscutter` 保留（PtsMap, SplitVideo 等被内部使用） |
| **tscutter** | 独立 |

残留的跨仓库重复代码：

| 代码 | 位置 | 行数 | 为何必须 |
|---|---|---|---|
| `ffmpeg.probe()` | tscutter + tsmarker | 各 ~3 | probe / clipinfo 用 duration |
| `CopyPart` / `CopyPartPipe` | tscutter + tsmarker (clip_utils) | 各 ~15 | SplitVideo + extract-clips |
| `ClipToFilename` | tscutter + tsmarker (clip_utils) | 各 ~3 | 文件名生成 |
| 异常类 | tscutter + tsmarker | 各 ~3 | TsFileNotFound, InvalidTsFormat |

---

## 九、三项目分工

### tscutter — 切分点分析器

**职责**: 分析 TS 文件的音频/视频特征，确定切分点位置。

| 子命令 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `analyze` | TS 文件 | `.ptsmap` | 静音检测 → 场景变换 → 生成切分点 |
| `probe` | TS 文件 | stdout JSON | ffprobe 查询（duration, resolution, fps, serviceId 等） |
| `list-clips` | `.ptsmap` | stdout JSON | 返回所有 clip 的 `[start, end]` 列表 |
| `select-clips` | `.ptsmap` | stdout JSON | 返回按长度降序排列的候选长片段 |

**持有**: `PtsMap` (~25 行: `json.load` + `Clips()` + `SelectClips()`)
**依赖**: ffmpeg, ffprobe
**不依赖**: tstriage, tsmarker

### tsmarker — 标记与提取

**职责**: 标记每个 clip 的属性（字幕/台标/语音/…），提取 TS 片段和台标。

| 子命令 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `extract-clips` | TS + `.ptsmap` + clips JSON | stdout TS / `.ts` 文件 | 按 byte range 提取 TS 数据 |
| `extract-logo` | TS + `.ptsmap` | PNG | 台标边缘检测图 |
| `crop-detect` | logo PNG | stdout JSON | 检测画幅裁剪参数 |
| `prepare-subtitles` | TS + `.ptsmap` | `.ass.original` + `.assgen` | 字幕提取 + 语音识别 |
| `mark-subtitles` | TS + `.ptsmap` | `.markermap` (原地修改) | 字幕检测标记 |
| `mark-clipinfo` | TS + `.ptsmap` | `.markermap` (原地修改) | clip 位置/时长标记 |
| `mark-logo` | TS + `.ptsmap` + logo PNG | `.markermap` (原地修改) | 台标检测标记 |
| `mark-speech` | TS + `.ptsmap` + API URL | `.markermap` (原地修改) | 语音内容标记 |
| `ensemble-dataset` | 搜索目录 | CSV | 生成集成学习数据集 |
| `ensemble-train` | CSV | model.pkl | 训练集成模型 |
| `ensemble-predict` | model + `.ptsmap` | `.markermap` (原地修改) | 集成预测标记 |
| `groundtruth` | `.markermap` + clips 目录 | `.markermap` (原地修改) | 人工校验标记 |
| `get-program-clips` | `.markermap` + `.ptsmap` | stdout JSON | 返回节目分组（merge + split） |
| `cut` | TS + `.ptsmap` + `.markermap` | CM/ 分类文件夹 | 按标记切分 TS 文件 |

**持有**: `MarkerMap` 完整实现, `CopyPart` / `CopyPartPipe` / `ClipToFilename` (从 tscutter 移入)
**依赖**: ffmpeg, Caption2AssC, tscutter CLI (list-clips, select-clips, probe)
**不依赖**: tstriage

### tstriage — 编排器

**职责**: 读取配置，按序调用 tscutter/tsmarker CLI，执行 ffmpeg 编码。

| 函数 | 说明 |
|---|---|
| `Analyze()` | 调 `tscutter analyze` → `tsmarker prepare-subtitles` → `tsmarker extract-logo` → audio check |
| `Mark()` | 调 `tsmarker mark-subtitles/clipinfo/logo/speech` → `tsmarker ensemble-*` |
| `Cut()` | 调 `tsmarker cut --by auto` |
| `Confirm()` | 调 `tsmarker groundtruth` |
| `Encode()` | 调 `tsmarker get-program-clips` → `tsmarker extract-clips` → ffmpeg 编码 |
| `Cleanup()` | 删除临时缓存文件 |

**持有**: `EncodePipeline` (ffmpeg 编码命令构建), `EPG` 解析
**依赖**: tscutter CLI, tsmarker CLI, ffmpeg
**Python import**: 零（不 import tscutter, 不 import tsmarker）
