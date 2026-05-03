# ASS 字幕字符跨平台字体兼容性

## 问题背景

ffmpeg（libaribcaption）从 ARIB 广播字幕生成的 ASS 文件，在 Android TV 的 KODI 上部分字符显示为方块（tofu），而 macOS KODI 正常。

根本原因：Android TV 的默认字体（Noto Sans CJK / Roboto）对以下 Unicode 区块覆盖不全：
- **Halfwidth and Fullwidth Forms（U+FF00–U+FFEF）** — 半角假名/标点区段
- **Miscellaneous Symbols（U+2600–U+27FF）** — 杂项符号
- CJK Symbols 区块中的个别字符

macOS 的 Hiragino Sans 对这些区段覆盖完整，因此无此问题。

## 已确认的问题字符

### U+301C WAVE DASH `〜` vs U+FF5E FULLWIDTH TILDE `～`

这是日文字符编码史上最著名的分裂问题：

- **Unicode 标准**：ARIB 波浪线 → U+301C（Apple/macOS 遵循）
- **Microsoft Code Page 932**：对应字符 → U+FF5E（Windows 生态遵循）
- **libaribcaption**：遵循 Unicode 标准，输出 U+301C
- **Android Noto Sans CJK**：部分版本只有 U+FF5E 字形，缺失 U+301C

**症状**：`お〜` 中的 `〜` 在 Android TV KODI 显示为方块。

**修复方向**：`〜`(U+301C) → `～`(U+FF5E)

## 完整风险字符表

### HIGH 风险 — Android TV 大概率缺字形

| 字符 | 码点 | 名称 | 替换建议 |
|------|------|------|----------|
| `｡` | U+FF61 | Halfwidth Ideographic Full Stop | `。` (U+3002) |
| `➡` | U+27A1 | Black Rightwards Arrow | `→` (U+2192) |
| `｢` | U+FF62 | Halfwidth Left Corner Bracket | `「` (U+300C) |
| `｣` | U+FF63 | Halfwidth Right Corner Bracket | `」` (U+300D) |
| `〜` | U+301C | Wave Dash | `～` (U+FF5E) |
| `♬` | U+266C | Beamed Sixteenth Notes | `♪` (U+266A) |
| `〓` | U+3013 | Geta Mark（本意即缺字符号） | `♪` 或 `※` (U+203B) |
| `ｰ` | U+FF70 | Halfwidth Prolonged Sound Mark | `ー` (U+30FC) |
| `⚟` | U+269F | Three Lines Converging Left | 删除或用 `※` |
| `･` | U+FF65 | Halfwidth Katakana Middle Dot | `・` (U+30FB) |
| `⚞` | U+269E | Three Lines Converging Right | 删除或用 `※` |

### MED 风险 — 可能有问题

| 字符 | 码点 | 名称 |
|------|------|------|
| `｟` | U+FF5F | Fullwidth Left White Parenthesis |
| `｠` | U+FF60 | Fullwidth Right White Parenthesis |
| `⁉` | U+2049 | Exclamation Question Mark |

### LOW 风险 — 所有 CJK 字体基本覆盖

全角标点（`！？＜＞`）、全角数字（`０-９`）、CJK 符号（`『』《》々…`）、表意空格（`　`）等。

## 为什么是 libaribcaption 产生的

ARIB STD-B24 字幕标准使用半角假名/标点来节省广播带宽。ARIB 字符集与 Unicode 的映射关系由 libaribcaption 内部查找表决定，该表遵循 Unicode 标准而非 Microsoft 兼容映射。

ffmpeg 无论运行在什么操作系统上，输出结果相同——因为映射表是 libaribcaption 库内置的。

## 实现思路

在 tstriage 的编码流程中，ASS 文件生成后追加一个字符规范化步骤：

```python
# 半角标点 → 全角
"｡" → "。"  # ｡ → 。
"｢" → "「"  # ｢ → 「
"｣" → "」"  # ｣ → 」

# 半角假名 → 全角
"ｰ" → "ー"  # ｰ → ー
"･" → "・"  # ･ → ・

# 历史分裂字符
"〜" → "～"  # 〜 → ～
"〓" → "♪"  # 〓 → ♪

# 杂项符号
"➡" → "→"  # ➡ → →
"♬" → "♪"  # ♬ → ♪
"⚟" → "※"  # ⚟ → ※
"⚞" → "※"  # ⚞ → ※
```

## 参考资料

- [Unicode Technical Report #25: Unicode Support for Mathematics](https://www.unicode.org/reports/tr25/)
- JIS X 0201 / JIS X 0208 与 Unicode 映射差异
- ARIB STD-B24 第一卷第三编 字幕文字编码
- [Noto Sans CJK 字体覆盖](https://github.com/googlefonts/noto-cjk)
