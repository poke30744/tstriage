# Kodi EDL 行为分析：CUT vs COMM_BREAK

2026-05-16 研究记录。

## 背景

tstriage 生成的 EDL 文件使用 action type 3（COMM_BREAK / 广告跳过）。用户在 Kodi 中播放时，发现每个广告段的结尾处会短暂显示几帧广告画面，然后才进入正片。而用 `ffplay -ss <time>` 直接 seek 到同样时间戳则不会出现多余帧。将 EDL type 从 3 改为 0 后效果显著改善。

本文分析 PTS 精度、Kodi 源码机制，解释两者差异的根因。

## 测试文件

- 视频：`銀河英雄伝説 Die Neue These 第四十四話.mkv`（1806.617s）
- EDL 第一条：`0.000  21.268  3`（跳过片头 21.268 秒广告）

## EDL 格式

MPlayer/Kodi EDL 格式：

```
[start] [end] [action]
```

Action type：

| Type | 名称 | 行为 |
|------|------|------|
| 0 | CUT | 永久移除片段，调整总时长，不可回看 |
| 1 | MUTE | 静音，视频继续播放 |
| 2 | SCENE | 场景标记点（类似章节） |
| 3 | COMM_BREAK | 自动跳过一次，允许 seek 回看 |

## 关键帧分析

### 方法论

使用 `ffprobe -show_packets` 提取视频流的所有 packet PTS 和 keyframe 标志（`flags=K__`），配合 `ffmpeg -filter_complex showinfo` 获取每帧的 `mean` 值（YUV 各通道均值）来区分广告画面和正片画面。

### 21.268s 切点附近的关键帧分布

| PTS | 帧类型 | flags | mean (YUV) | 内容 |
|-----|--------|-------|------------|------|
| 19.966 | I | K__ | — | 广告 |
| 21.200 | B | — | [163 149 101] | 广告 |
| 21.234 | P | — | [163 149 101] | 广告 |
| **21.267** | **I** | **K__** | **[88 114 134]** | **正片** |
| 21.300 | B | — | [88 114 134] | 正片 |

关键发现：

1. **21.267 是关键帧（I-frame + `flags=K__`）**，且画面是正片内容（mean 值与广告帧显著不同）
2. 这个关键帧距离 EDL 切点仅 **1ms**（21.267 vs 21.268）
3. ffmpeg `-ss 21.268` 可以正确 seek 到这个关键帧

### 验证：ffmpeg 的精确 seek

```bash
# 输入 seek（-ss 在 -i 前）— 类似 Kodi 的行为
ffmpeg -ss 21.268 -copyts -i file.mkv -vframes 5 -filter_complex showinfo
```

结果：
- 解码了 I-frame（PTS=21.267）但因为 PTS < 目标 21.268，**丢弃**
- 第一个输出帧：PTS=21.300（B-frame），正片内容
- **无多余帧**

这证明了 PTS 是准确的，21.267 关键帧也是有效的。问题不在数据，而在 Kodi 的机制。

## Kodi 源码分析

基于 Kodi master 分支 `VideoPlayer.cpp` 和 `Edl.cpp`。

### Type 0 (CUT)：双层保护

**第一层 — CheckSceneSkip（包级丢弃）：**

```cpp
// VideoPlayer.cpp — ProcessVideoData / ProcessAudioData
bool CVideoPlayer::CheckSceneSkip(const CCurrentStream& current)
{
  const auto hasEdit = m_Edl.InEdit(current.dts + m_offset_pts);
  return hasEdit && hasEdit.value()->action == EDL::Action::CUT;
  //                                              ^^^^^^^^^^^^
  //                                    只有 CUT 会触发丢帧！
}

// 在渲染管线中
if (CheckSceneSkip(m_CurrentVideo))
    drop = true;  // 帧被丢弃，不进入渲染
```

**第二层 — CheckAutoSceneSkip（Seek 跳转）：**

```cpp
// 同样触发 seek，accurate=true
QueueAutoSceneSkip(seekTime);  // mode.accurate = true
```

CUT 的两层保护意味着：即使在 seek 触发之前有任何帧被解码，它们也会在 `CheckSceneSkip` 阶段被丢弃。

### Type 3 (COMM_BREAK)：只有 Seek，无丢帧

```cpp
// CheckSceneSkip 对 COMM_BREAK 返回 false
// → 帧不会被丢弃，正常进入渲染管线！

// 只有 CheckAutoSceneSkip 做 seek 跳转
QueueAutoSceneSkip(seekTime);  // 同一套 seek 机制
```

源码注释解释了为什么 COMM_BREAK 不做包级丢弃：

> *"Users are allowed to jump back into EDL commercial breaks"*

COMM_BREAK 设计意图是允许用户手动 seek 回去看被跳过的广告，所以不能在解码器层面丢弃帧。

### CUT 的特有预处理（Edl.cpp）

```cpp
// Edl.cpp — AddEdit()
if (edit.action == Action::COMM_BREAK)
{
    // 自动添加 autowait（开头等待）和 autowind（结尾回卷）
    edit.start += m_iEdlCommBreakAutowait;   // 默认 0s
    edit.end   -= m_iEdlCommBreakAutowind;   // 默认 0s
}

// MergeShortCommBreaks() — 仅对 COMM_BREAK 生效
// 1. 移除 < 5s 的前导 COMM_BREAK
// 2. 合并相邻的 COMM_BREAK（如果间距和总长低于阈值）
// 3. 如果第一个 COMM_BREAK 起于 m_iEdlMaxStartGap 内，扩展回时间 0
// 4. 移除长度 < m_iEdlMinCommBreakLength 的 COMM_BREAK（起始处除外）
```

CUT 不经过任何预处理，边界完全按 EDL 文件中的原始值。

## 为什么 Type 0 比 Type 3 精准

```
Type 3 (COMM_BREAK) 播放时序：
─────────────────────────────────────────────────
 t=0  解码器输出首帧（广告）→ CheckSceneSkip: NO → 渲染显示 ← 多余帧！
 t=1  解码器输出下一帧（广告）→ CheckSceneSkip: NO → 渲染显示 ← 多余帧！
 t=2  CheckAutoSceneSkip 检测到进入广告
 t=3  QueueAutoSceneSkip → seek 到 21.268
 t=4  seek 完成，从 21.267 I-frame 恢复 → 正片开始
─────────────────────────────────────────────────
      ↑ 2~5 帧广告在 seek 完成前已被显示


Type 0 (CUT) 播放时序：
─────────────────────────────────────────────────
 t=0  解码器输出首帧 → CheckSceneSkip: YES (CUT) → drop
 t=1  解码器输出下一帧 → CheckSceneSkip: YES (CUT) → drop
 ...  持续丢弃 ...
 t=N  CheckAutoSceneSkip → seek 到 21.268
 ...  继续丢弃（第二层保护仍在工作直到离开 CUT 区间）
 t=M  帧 PTS > 21.268 → CheckSceneSkip: NO → 开始显示正片
─────────────────────────────────────────────────
      ↑ 从未显示过广告帧
```

**核心差异不是 seek 精度——两者用的都是 `accurate=true` 的同一套 seek 机制。差异在于 seek 触发之前和解码器恢复之后的帧处理方式。**

## 对 tstriage 的影响

当前 tstriage 输出 type 3（COMM_BREAK）。改为 type 0（CUT）可以消除 Kodi 中的多余帧闪现。

权衡：
- **Type 0**：干净跳转，画面无缝。但用户无法 seek 回看广告段（通常不是需求）
- **Type 3**：允许 seek 回看。但会在跳转时有几帧广告闪现

如果 tstriage 的目标场景是精确的 CM 切除，type 0 是更好的选择。广告误检可以通过提高检测精度来解决，而不需要依赖"允许用户回看"这个逃生口。
