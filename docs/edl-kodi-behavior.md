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

## 已知坑：源主机名解析到 IPv6 时，EDL 会被静默跳过

2026-09-23 记录。起因是一台 Google TV Streamer（Kodi 21.2，Android TV 14）的 EDL "突然不加载"，
而同一批文件、同一个 SMB 共享在 macOS 的 Kodi 上一切正常。

### 症状

完全没有反应：不跳广告；即使打开 debug 日志，**日志里也找不到任何一行 EDL 相关内容**。
Kodi 依然能在视频旁边列出 `.edl`，甚至能通过它自己的 VFS 把文件读出来（对 `.edl` 调
`Files.PrepareDownload` 拿到 `/vfs/...` 再 GET，能取到完整内容），说明文件访问、权限、编码、命名
都没有问题——**是 EDL 那段代码根本没有执行**。

### 根因（Kodi 21 "Omega" 源码）

```cpp
// Edl.cpp:46
bool CEdl::ReadEditDecisionLists(const CFileItem& fileItem, const float fFramesPerSecond)
{
  const std::string& strMovie = fileItem.GetDynPath();
  if ((URIUtils::IsHD(strMovie) || URIUtils::IsOnLAN(strMovie, LanCheckMode::ANY_PRIVATE_SUBNET)) &&
      !URIUtils::IsInternetStream(strMovie))
  {
      CLog::Log(LOGDEBUG, "{} - Checking for edit decision lists (EDL) ...");
      // ReadVideoReDo / ReadEdl / ReadComskip / ReadBeyondTV
  }
  else
  {
      bFound = ReadPvr(fileItem);   // ← 落到这里，连 .edl 都不会去找
  }
```

SMB 源的情况下，全看 `IsOnLAN()`：

```cpp
// URIUtils.cpp:691
bool URIUtils::IsHostOnLAN(const std::string& host, LanCheckMode lanCheckMode)
{
  // 不带点的主机名当作 NetBIOS 名，直接算本机
  if (host.find('.') == std::string::npos)
    return true;                                  // "acepc-gk3" 走这条捷径

  uint32_t address = ntohl(inet_addr(host.c_str()));
  if (address == INADDR_NONE)
  {
    std::string ip;
    if (CDNSNameCache::Lookup(host, ip))          // "acepc-gk3.local" 在这里被解析
      address = ntohl(inet_addr(ip.c_str()));     // ← 拿 IPv6 字符串喂 inet_addr() = INADDR_NONE
  }

  if (address != INADDR_NONE) { /* 192.168/16、10/8、172.16/12 → return true */ }
  return false;
}
```

而 `CDNSNameCache::Lookup()` 解析时不限地址族，**只取第一条结果**：

```cpp
// DNSNameCache.cpp:39
hints.ai_family = AF_UNSPEC;
hints.ai_socktype = SOCK_STREAM;
if (getaddrinfo(strHostName.c_str(), nullptr, &hints, &res) == 0)
{
  strIpAddress = CNetworkBase::GetIpStr(res->ai_addr);   // 第一条，不管 IPv4 还是 IPv6
  ...
}
```

所以只要 `getaddrinfo()` 第一条返回 AAAA（网络里有全局 IPv6 时很常见，RFC 6724 地址选择优先
IPv6），`inet_addr()` 就解析不了，`address` 停在 `INADDR_NONE`，私有网段判断整段被跳过，
`IsHostOnLAN()` 返回 false → **旁挂的 .edl 永远不会被看一眼**，而且全程静默、没有任何日志。

触发条件：源里用的是**带点**的主机名（`smb://<host>.local:445/...`），且该名字有 AAAA 记录。
`acepc-gk3.local` 目前有 4 条 AAAA（`240f:37:fd39:1:...` 公网 + `fdda:1806:...` ULA）和 1 条 A
（192.168.1.116）；macOS 那台拿到的是 A 记录，Google TV 拿到的是 AAAA。

### 修法

1. **`advancedsettings.xml` 里加 `<hosts>`（首选）**——往 Kodi 的 DNS 缓存里塞静态映射；
   `CDNSNameCache::Lookup()` 是先查这个缓存、再调 `getaddrinfo()` 的（`DNSNameCache.cpp:57`），
   IPv6 那条路根本走不到。名字与路径里的主机名是**大小写敏感**的精确匹配（`==`，
   `DNSNameCache.cpp:88`），改完要重启 Kodi。源路径和媒体库完全不受影响。

   ```xml
   <advancedsettings>
     <hosts>
       <entry name="acepc-gk3.local">192.168.1.116</entry>
     </hosts>
   </advancedsettings>
   ```

2. **源里改用 IP 或不带点的名字**——`smb://192.168.1.116/Seagate 8T/` 按字面 IPv4 解析成功；
   `smb://acepc-gk3/Seagate 8T/`（无点）命中开头的 `return true`。省事，但路径字符串变了，
   共享 MySQL 媒体库里的条目会对不上。

3. **让服务器不发布 AAAA**——比如 NAS 上 `avahi-daemon.conf` 里 `use-ipv6=no`。一次修好所有
   客户端，但要动服务器。

### 这类故障的排查手法

- 打开 debug 日志，找 `"CEdl::ReadEditDecisionLists - Checking for edit decision lists (EDL) on
  local drive or remote share for: ..."` 这一行。**它完全不出现 = EDL 压根没被考虑**，而不是解析
  失败。（不开 debug 日志时这行本来就不存在，所以"日志里没有 EDL"什么都证明不了。）
- Kodi 的 web 服务可以证明文件层没问题：对 `.edl` 调 `Files.PrepareDownload`，再 GET 返回的
  `/vfs/...`。（顺带：`special://` 路径会被 401 拒绝。）
- 播放带 action-0（CUT）EDL 的文件时读 `Player.GetProperties(["totaltime"])` 是很快的行为验证：
  切片生效时，报出来的总时长会比容器时长明显短。

## 对 tstriage 的影响

当前 tstriage 输出 type 3（COMM_BREAK）。改为 type 0（CUT）可以消除 Kodi 中的多余帧闪现。

权衡：
- **Type 0**：干净跳转，画面无缝。但用户无法 seek 回看广告段（通常不是需求）
- **Type 3**：允许 seek 回看。但会在跳转时有几帧广告闪现

如果 tstriage 的目标场景是精确的 CM 切除，type 0 是更好的选择。广告误检可以通过提高检测精度来解决，而不需要依赖"允许用户回看"这个逃生口。
