# tstriage CLI 化改造：完整设计

## 背景

tstriage 目前通过 Python import 直接依赖 `tscutter` 和 `tsmarker` 两个库，导致三者版本绑定、无法独立升级。

目标：将所有 Python import 依赖替换为 subprocess 调用 CLI 工具。三者之间的唯一契约是 **JSON 文件格式**（`.ptsmap`、`.markermap`）和 **CLI stdout 约定**，不再共享任何 Python 代码。

## 解耦原则

核心设计决策：**ptsmap 的读写者是 tscutter。tsmarker 不持有 PtsMap，而是通过 tscutter CLI 获取 clip 信息。**

```
tscututer  ──写入──→ .ptsmap ──读取──→ tstriage (极简 5 行)
                │
                └── tscutter list-clips ──→ tsmarker (stdout JSON)
                └── tscutter select-clips ──→ tsmarker (stdout JSON)
                └── tscutter extract-clips ──→ tsmarker / tstriage (stdout TS)
```

## 整体架构

```
tstriage (Python)
  ├── PtsMap 极简版 (5 行: 读 JSON + Clips())
  ├── MarkerMap 极简版 (~30 行: GetProgramClips/MergeNeighbors/SplitClips)
  ├── EncodePipeline (解除 InputFile 继承, ffmpeg 命令构建)
  ├── subprocess → tscutter analyze
  ├── subprocess → tscutter probe
  ├── subprocess → tscutter list-clips
  ├── subprocess → tscutter select-clips
  ├── subprocess → tscutter extract-clips
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
  └── subprocess → tsmarker cut

tscutter CLI (Python, 自主项目)
  ├── PtsMap (~75 行: 仅有自己用)
  ├── ffmpeg.probe() (~3 行: probe 子命令)
  └── 内部 subprocess → ffmpeg, ffprobe

tsmarker CLI (Python, 自主项目)
  ├── 无 PtsMap (通过 tscutter 获取 clip 信息)
  ├── ffmpeg.probe() (~3 行: mark-clipinfo 用 duration)
  └── 内部 subprocess → ffmpeg, Caption2AssC, tscutter
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

### 1.5 tscutter extract-clips

替代 `PtsMap.ExtractClipsPipe()` 和 `PtsMap.SplitVideo()`

```
tscutter extract-clips --input <ts_path> --index <ptsmap_path>
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
- 用途: Encode 阶段管道 `tscutter extract-clips ... | ffmpeg -i - ...`

**文件模式**（指定 `--output-dir`）:
- 每个 clip 输出独立 `.ts` 文件: `<00:00:00.000>-<00:00:30.500>.ts`

错误处理:

| 条件 | exit code | stderr |
|---|---|---|
| 输入文件不存在 | 1 | 文件路径 |
| ptsmap 不存在 | 2 | `FileNotFoundError: ...` |
| ptsmap JSON 损坏 | 3 | `InvalidIndexFormat: ...` |
| clip 范围超出 | 4 | `ClipOutOfRange: <clip>` |
| stdout SIGPIPE | 0 | 无 |

---

## 二、tsmarker CLI

统一入口: `tsmarker <subcommand> [options]`

共同约定:
- `--quiet` / `-q`: 关闭进度条
- markermap 子命令均为**原地修改**: 读 JSON → 处理 → 写回同一文件
- clips 信息通过 `tscutter list-clips` / `tscutter select-clips` 获取（stdout JSON 解析）
- TS 数据提取通过 `tscutter extract-clips` 管道获取
- 正常退出 code=0，异常 code≠0

### 2.1 tsmarker extract-logo

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
3. 调 `tscutter extract-clips -i <ts> -x <ptsmap> -c '<clip>'` 获取 TS 数据
4. ffmpeg 提取帧 → 均值合成 → Canny 边缘检测
5. 可选去除边框
6. 输出 PNG

### 2.2 tsmarker crop-detect

替代 `tsmarker.pipeline.CropDetectPipeline()`

```
tsmarker crop-detect --input <logo_png> [--threshold <float>]
```

stdout: JSON; 无边框时 `null`
```json
{"w": 1440, "h": 1080, "x": 240, "y": 0}
```

### 2.3 tsmarker prepare-subtitles

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

### 2.4 tsmarker mark-subtitles

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

### 2.5 tsmarker mark-clipinfo

```
tsmarker mark-clipinfo --video <ts_path> --index <ptsmap_path>
                       --marker <markermap_path> [--quiet]
```

行为:
1. 调 `tscutter list-clips -x <ptsmap>` 获取 clip 列表
2. 调 `tscutter probe -i <ts>` 获取 duration
3. 计算每个 clip 的 position, duration, duration_prev, duration_next
4. 写回 markermap

### 2.6 tsmarker mark-logo

```
tsmarker mark-logo --video <ts_path> --index <ptsmap_path>
                   --marker <markermap_path> --logo <logo_png>
                   [--quiet]
```

行为:
1. 调 `tscutter list-clips -x <ptsmap>` 获取 clip 列表
2. 对每个 clip 调 `tscutter extract-clips` 管道获取 TS 数据 → 均值画面 → Canny 边缘
3. 与台标图做 AND overlap → logo score (0.0~1.0)
4. score ≤ 0.5 时用全长重试一次
5. 写回 markermap

### 2.7 tsmarker mark-speech

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

### 2.8 tsmarker ensemble-dataset

```
tsmarker ensemble-dataset --input <search_folder> --output <csv_path>
                          [--no-normalize] [--quiet]
```

不需要 tscutter——tsmarker 自己遍历 `.mp4` + `.yaml` + `.markermap` 文件。

### 2.9 tsmarker ensemble-train

```
tsmarker ensemble-train --input <csv_path> --output <model_path>
                        [--random-state <int>] [--test-size <float>]
                        [--quiet]
```

### 2.10 tsmarker ensemble-predict

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

### 2.11 tsmarker groundtruth

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

### 2.12 tsmarker cut

```
tsmarker cut --video <ts_path> --index <ptsmap_path>
             --marker <markermap_path> --by <method>
             --output <folder> [--quiet]
```

行为:
1. 调 `tscutter extract-clips --output-dir <tmp>` 拆分 TS
2. 读 markermap 根据 `--by` 字段分类
3. <0.5 → `output/CM/`，≥0.5 → `output/`
4. 文件名加 `.L` / `.S` 后缀

---

## 三、最终重复代码清单

### 实际重复（需各存在一份）

| 代码 | 在哪里 | 行数 | 说明 |
|---|---|---|---|
| `ffmpeg.probe(path, show_programs=None)` | tscutter + tsmarker | 各 ~3 | probe 子命令 / mark-clipinfo 用 duration |
| `ClipToFilename` | tscutter + tsmarker | 各 1 | `'{:08.3f}-{:08.3f}.ts'.format(...)` |
| 文件读+写管道 (Caption2AssC) | tsmarker | 4 | `open().read()` 替代 CopyPartPipe |
| **总计** | | **~8 行** | |

### 不重复（各自独有）

| 代码 | 属于 | 说明 |
|---|---|---|
| `PtsMap` 完整版 (~75 行) | tscutter | ptsmap 唯一读写者 |
| `PtsMap` 极简版 (5 行) | tstriage | `json.load` + `Clips()` |
| `MarkerMap` 极简版 (~30 行) | tstriage | 只读 JSON + 业务判断 |
| `DetectSilence` / `MergeIntervals` / `FindSplitPosition` / `GeneratePtsMap` | tscutter | analyze 领域逻辑 |
| 所有 MarkerMap 子类 (subtitles/clipinfo/logo/speech/ensemble/groundtruth) | tsmarker | 标记领域逻辑 |
| `ExtractLogoPipeline` / `CropDetectPipeline` | tsmarker | 台标领域逻辑 |
| `PrepareSubtitles` / `ExtractAudioText` | tsmarker | 字幕+STT |
| `EncodePipeline` / `StripTsCmd` / `EncodeTsCmd` | tstriage | 编码领域逻辑 |
| `EPG` 类 | tstriage | EPG 解析（已是 subprocess） |

### 已消除

| 原函数 | 消除方式 |
|---|---|
| `CheckExtenralCommand` | 替换为 `shutil.which()`（标准库） |
| `CopyPartPipe` | tsmarker 内联 4 行文件读取 |
| `GetShortPath` | 即将删除的 hack，无需复制 |
| `FormatTimestamp` | 仅 tscutter 使用，1 份 |

---

## 四、ffmpeg/ffprobe 封装建议

| 场景 | 推荐方式 | 理由 |
|---|---|---|
| ffprobe 查询 (`GetInfo`) | ✅ `ffmpeg-python` 的 `probe()` | 50 行 → 3 行，收益最大 |
| 简单 ffmpeg 提取 | ✅ `ffmpeg-python` | 语法更干净 |
| 复杂管道 (stdin 喂数据) | ❌ `subprocess.Popen` | ffmpeg-python 有已知死锁风险 |
| tstriage 命令行构建 | ❌ 手写 `list[str]` | 需要精确控制参数做多进程编排 |

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
| `Cut()` | `tsmarker.common.MarkerMap(...).Cut(...)` | `tsmarker cut --video <ts> -x <ptsmap> -m <markermap> --by <method> -o <dir>` |
| `Confirm()` | `groundtruth.MarkerMap(...)` | `tsmarker groundtruth -m <markermap> -x <ptsmap> -c <clips_folder>` |
| `Encode()` | `PtsMap.ExtractClipsPipe()` | `tscutter extract-clips -i <ts> -x <ptsmap> -c '<json>'` (stdout 管道) |
| `Encode()` | `CropDetectPipeline(...)` | `tsmarker crop-detect -i <logo.png>` |
| `Encode()` | `ExtractLogoPipeline(...)` | `tsmarker extract-logo -i <ts> -x <ptsmap> -o <logo.png> --max-time 10 --no-remove-border` |

---

## 六、实现顺序

### Phase 1: tstriage 内联准备
1. 替换 `CheckExtenralCommand` → `shutil.which()`
2. 创建本地 `PtsMap` 极简封装 (~5 行)
3. 创建本地 `MarkerMap` 极简封装 (~30 行)
4. 重构 `pipeline.py`: 解除 `InputFile` 对 `tscutter.ffmpeg.InputFile` 的继承

### Phase 2: tscutter CLI
5. 添加 `--shift` 参数到现有 analyze 子命令
6. 新增 `probe` 子命令 (内部用 `ffmpeg.probe()`)
7. 新增 `list-clips` 子命令
8. 新增 `select-clips` 子命令
9. 新增 `extract-clips` 子命令

### Phase 3: tsmarker CLI
10. 统一 CLI 入口框架
11. 已有 CLI 的: extract-logo, crop-detect, ensemble-dataset/train/predict（统一到子命令，移除 PtsMap 导入改为调 tscutter）
12. 新增 CLI 的: prepare-subtitles, mark-subtitles, mark-clipinfo, mark-logo, mark-speech, groundtruth, cut

### Phase 4: tstriage 切换
13. 逐函数切换为 subprocess CLI 调用
14. 移除 `pyproject.toml` 中的 tscutter/tsmarker 依赖
15. 运行测试

---

## 七、验证方法

1. 每个 CLI 子命令的单元测试
2. tstriage 现有测试 (`tests/`) 在迁移后全部通过
3. 端到端测试: 取一个样本 TS，完整跑通所有 task
4. 对比迁移前后 `.ptsmap` 和 `.markermap` 内容一致
