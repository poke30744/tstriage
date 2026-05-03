---
name: subtitle-lessons
description: Lessons learned from attempting to fix ffmpeg subtitle timestamp gaps
type: project
originSessionId: 558e22b3-725c-4c06-9403-eaf6fa546c8b
---
## Root Cause of Subtitle Gap Issue

`ExtractClipsPipe` concatenates raw TS bytes without adjusting PTS. When clips
are concatenated (commercials removed), subtitle PTS values retain original
timestamps, causing time gaps in ffmpeg-extracted ASS subtitles that were not
present with Caption2Ass (which detected PCR gaps automatically).

## What Works: Clip-Boundary Adjustment

**Approach**: Use PtsMap clip boundaries (video PTS domain, same as ffmpeg output)
to compute gap corrections.

**Algorithm**:
```python
startPts = clips[0][0]
# Build per-clip table: (ffmpeg_start, ffmpeg_end, correction)
correction = 0.0
for i, (c_start, c_end) in enumerate(clips):
    ff_start = c_start - startPts   # ffmpeg-normalized range
    ff_end = c_end - startPts
    if i > 0:
        correction -= clips[i][0] - clips[i-1][1]  # gap size
    clip_ranges.append((ff_start, ff_end, correction))

# For each subtitle event:
for event in subs.events:
    start_sec = event.start / 1000.0
    for ff_start, ff_end, corr in clip_ranges:
        if ff_start - 0.001 <= start_sec <= ff_end + 0.001:
            event.start += int(round(corr * 1000))
            event.end += int(round(corr * 1000))
            break
```

**Why it works**: Both clip boundaries (from PtsMap) and ffmpeg subtitle
timestamps use the same video PTS clock. ffmpeg normalizes to start from 0
(≈ clips[0][0]). Gap positions are exact — no PCR/PTS clock mismatch.

**Verified result**: Clip boundaries are continuous (0.000s gaps), no cumulative
drift within the gap correction itself.

## What Does NOT Work

1. **PCR-based gap detection** (replicating Caption2Ass's correctTS mechanism):
   PCR and PTS use the same 90kHz clock in MPEG-TS, but when reading from
   ExtractClipsPipe's concatenated stream, the first PCR and first video PTS
   are offset by an unpredictable amount (0.5–0.7s for Japanese ISDB-T).
   This creates a constant offset between PCR-based gap positions and
   PTS-based ffmpeg timestamps.

2. **`-copyts` with ffmpeg pipe input**: ffmpeg normalizes timestamps
   regardless when reading from a pipe. `-copyts` doesn't help.

3. **Measuring PCR-PTS offset from TS file head**: The first PTS found in a
   1MB block at clip start may come from audio/other streams, not video,
   making the measurement inaccurate by ~0.15s.

4. **Comparing subtitle files by nearest-timestamp**: Old Caption2Ass splits
   each caption into 4-10 Dialogue lines (one per screen position), while
   ffmpeg combines them into 1-2 lines. Nearest-timestamp matching produces
   misleading "drift" because different events are matched.

## Key Implementation Choices

1. **Use pysubs2** for ASS manipulation, not regex. Cleaner and safer.
2. **Use clip boundaries from PtsMap** for gap positions — they're in the
   same PTS domain as ffmpeg output.
3. **Match subtitles by clip membership**, not by cumulative duration
   comparison. The ffmpeg timestamp ranges of clips include the gap, so the
   cumulative duration approach gives wrong positions for gaps after the first.
4. **Transparency fix via pysubs2 Style.backcolor**, not regex:
   ```python
   for style in subs.styles.values():
       if style.backcolor == pysubs2.Color(0, 0, 0, 0):
           style.backcolor = pysubs2.Color(0, 0, 0, 128)
   ```

## Known Unresolved Limitations

1. **ffmpeg/libaribcaption extracts ~54% fewer subtitle events** than
   Caption2Ass (2405 vs 1098 in one test). Not fixable in post-processing.
2. **~0.15–0.7s constant timing offset** between ffmpeg and Caption2Ass
   due to different subtitle PTS detection in the two engines.
3. **No base offset alignment**: Neither PCR-based nor PTS-based base
   shift could reliably align ffmpeg timestamps with Caption2Ass's time
   base. The PCR-PTS offset measurement is unreliable.

## Code Changes Made

- `tstriage/pipeline.py`: Added `_AdjustAssTimestamps`, `SubtitlesPipeline`,
  `subtitles` subcommand. Uses pysubs2 for ASS manipulation.
- `tstriage/tasks.py`: Fixed `PrepareSubtitles` import from
  `tsmarker.speech.text_extractor` → `tsmarker.speech.MarkerMap`.

## How to Test

```bash
# Regenerate subtitles for a .toencode file:
uv run python -m tstriage.pipeline subtitles \
  -i "//acepc-gk3/BUFFALO_Exp/recorded/_tstriage/xxx.toencode" \
  --dest-base "//acepc-gk3/Seagate 8T/categorized_mp4"
```

## How to Revert to Pre-April-29 Versions

```bash
uv tool install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple \
  --with tscutter==0.1.91 --with tsmarker==0.1.117 \
  tstriage==<VERSION>
```

Jenkins at `nucbox3.local:8080` has build history for exact version matching.
