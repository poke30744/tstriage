# fixaudio 检测盲区分析与修复设计

## 问题

TS 文件容器层存在 corrupt 音频包时，analyze 任务无法检测到，导致 encode 阶段用 stream copy 产出静音文件。必须手工加 `fixaudio: true` 才能产出正常音频。

## 根因

### 故障链路

```
TS 录制 → 容器层产生 corrupt 音频 PES 包
         (Packet corrupt, PES packet size mismatch)
              │
              ▼
analyze 检测: ffmpeg -v error 直接解码 TS
              ├─ -v error 过滤了 warning 级别的 Packet corrupt
              └─ TS demuxer 有容错，AAC 解码器产出正常音频
                   → 检测通过，不设 fixaudio ✓ (误判)
              │
              ▼
encode 阶段:  -c:a copy (stream copy)
              ├─ corrupt AAC frame 原样进入 MP4
              └─ MP4 中的 AAC 解码: 382 个 "channel element 1.2 is not allocated"
                   → 产出静音 (mean_volume: -81.9 dB)
```

### 三种场景对比 (同一文件实测)

| 场景 | mean_volume | 结果 |
|---|---|---|
| TS 源直接解码 | -29.7 dB | 正常 |
| Stream copy 到 MP4 再解码 | -81.9 dB | 静音 |
| Re-encode (fixaudio) | -29.8 dB | 正常 |

### 为什么 TS 直接解码正常但 stream copy 后异常

TS demuxer 在读取 corrupt PES 包时做了容错处理（跳过损坏数据、timebase 修正等），AAC 解码器拿到的数据是修复过的。但 `-c:a copy` 把原始 AAC bitstream 抽出来写入 MP4，失去了 TS 容器的保护，corrupt 数据直接暴露给 AAC 解码器，导致 `channel element 1.2 is not allocated` 错误。

## 修复设计

### 目标

1. 不误判：偶然的 Packet corrupt（几帧 glitch）不应触发 fixaudio
2. 不漏判：会导致 stream copy 后静音的 corrupt 必须检出
3. 不降性能：正常文件无额外开销

### 方案：两阶段检测

**阶段 1 — 快速筛选**（所有文件都跑，0 额外开销）

改动：`-v error` → `-v warning`，新增匹配 `Packet corrupt` 在指定音频流上

**阶段 2 — 精确确认**（仅可疑流才跑，pipe 方式无临时文件）

用 pipe 模拟 encode 实际行为：`TS → -c:a copy → MP4 pipe → decode`，检查是否有 `channel element` 错误。

### 伪代码

```python
# analyze() 中的音频检测逻辑

for audio_pos, global_idx in enumerate(audio_global_indices):
    # 阶段 1: 用 -v warning 做一次解码检测
    test_cmd = [
        ffmpeg_path, '-v', 'warning', '-err_detect', 'aggressive',
        '-i', str(workingPath), '-map', f'0:a:{audio_pos}',
        '-t', '2', '-f', 'null', '-'
    ]
    result = subprocess.run(test_cmd, capture_output=True, text=True)

    # 匹配 codec 层错误 (原有逻辑)
    has_chan_err = any('channel element' in l and 'is not allocated' in l
                       for l in result.stderr.split('\n'))

    # 匹配容器层 corrupt (新增)
    has_pkt_corrupt = any('Packet corrupt' in l and f'stream = {global_idx}' in l
                          for l in result.stderr.split('\n'))

    if has_chan_err:
        # 原逻辑：AAC 解码器直接报错 → 必定需要 fixaudio
        logger.warning(f'Audio stream {global_idx} (pos {audio_pos}) has decode errors')
        item['encoder']['fixaudio'] = True
    elif has_pkt_corrupt:
        # 阶段 2: 管道确认 stream copy 后是否真有问题
        if _check_stream_copy_decode(ffmpeg_path, workingPath, audio_pos):
            logger.warning(f'Audio stream {global_idx} (pos {audio_pos}) has corrupt'
                           ' TS packets that cause decode errors after stream copy')
            item['encoder']['fixaudio'] = True
        else:
            logger.info(f'Audio stream {global_idx} (pos {audio_pos}) has corrupt'
                        ' TS packets but stream copy is clean, skipping fixaudio')


def _check_stream_copy_decode(ffmpeg_path, input_path, audio_pos):
    """Pipe: TS → -c:a copy → MP4 → decode → 检测 channel element 错误"""
    cmd = (
        f'"{ffmpeg_path}" -v error -i "{input_path}" '
        f'-map 0:a:{audio_pos} -c:a copy -t 2 -f mp4 - | '
        f'"{ffmpeg_path}" -v error -err_detect aggressive -i - -t 2 -f null -'
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return any('channel element' in l and 'is not allocated' in l
               for l in result.stderr.split('\n'))
```

### 设计决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 阶段 1 用 `-v warning` 而非 `-v error` | warning | Packet corrupt 是 warning 级，error 级看不到 |
| 阶段 2 用 pipe 而非临时文件 | pipe | 无磁盘 I/O，无清理负担 |
| 阶段 2 用 `shell=True` | shell=True | 管道 `\|` 需要 shell 解释，ffmpeg 路径已通过参数化保证安全 |
| 匹配用 `stream = {global_idx}` | global_idx | 精确匹配当前音频流的 corrupt 包，避免视频/数据流的误匹配 |
| 只测 2 秒 | 2s | corrupt 包在文件开头就有，2 秒足够捕获 |

### 不采用的替代方案

| 方案 | 问题 |
|---|---|
| 永久开 fixaudio | 浪费 CPU/时间重编码所有音频，大多数文件不需要 |
| 只降低 log level 不验证 | 偶然 corrupt 包（不影响实际播放）也会被误判 |
| stream copy 到临时文件再检查 | 磁盘 I/O 开销，需要管理临时文件路径 |

## 相关文件

- `tstriage/tasks.py:56-85` — Analyze 函数中的音频检测逻辑
- `tstriage/pipeline.py:11-21` — `StripTsCmd` 中 fixAudio 的 encode 行为

## 验证方法

用本次出问题的文件验证：`2026年04月30日07時30分00秒-【連続テレビ小説】風、薫る（２４）第５週「集いし者たち」[解][字].m2ts`

预期：
- 阶段 1 检测到 `Packet corrupt (stream = 1)`
- 阶段 2 管道确认 `channel element is not allocated` → 设置 fixaudio

验证 clean 文件不会误判：取一个无 corrupt 的正常 TS 文件，预期两阶段都不过，不设 fixaudio。
