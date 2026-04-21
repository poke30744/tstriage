# Audio Processing in tstriage

This document describes the audio processing capabilities added to support dual mono audio formats.

## Overview

tstriage now supports processing of dual mono audio tracks (デュアルモノ) where the left and right channels contain independent audio content (e.g., main audio in one channel, audio description for visually impaired in the other channel).

## Audio Metadata YAML Files

During the `analyze` phase, `mirakurun-epgdump` generates EPG files containing audio metadata. tstriage extracts the relevant audio information from these EPG files and creates YAML files. These YAML files are located in the same directory as the output MP4 files, named `<ts-filename>.yaml`.

### File Structure
```yaml
audios:
  - componentType: 2  # or 3 for normal stereo
    componentTag: "0x02"
    samplingRate: 48000
    langs: ["jpn", "jpn"]
    # ... other metadata fields
```

### Key Fields
- **`componentType`**: Audio format identifier
  - `2`: Dual mono (independent L/R channels)
  - `3`: Normal stereo (identical L/R channels)
- **`samplingRate`**: Audio sampling rate in Hz
- **`langs`**: Language codes for each channel (array)

## Audio Processing Logic

### Encoding Pipeline
The `EncodePipeline` function in `tstriage/pipeline.py` reads the YAML audio configuration and adjusts audio processing accordingly.

### Dual Mono Processing (`componentType=2`)
When `componentType=2` is detected:

1. **Audio Splitting**: Uses ffmpeg's `channelsplit` filter to separate stereo into two mono channels
2. **Encoding**: Each mono channel is encoded as an independent AAC mono track
3. **Metadata**: Language metadata is set for each track (from `langs` array or default `jpn`)

**FFmpeg command example:**
```bash
ffmpeg -i input.ts -filter_complex "[0:a]channelsplit=channel_layout=stereo[left][right]" \
  -map 0:v -map "[left]" -map "[right]" \
  -c:v <video_codec> \
  -c:a aac -ar 48000 -ac 1 -b:a 128k \
  -metadata:s:a:0 language=jpn -metadata:s:a:1 language=jpn \
  output.mp4
```

### Normal Stereo Processing (`componentType=3`)
Default behavior: audio is copied without modification (`-c:a copy`).

### No Audio Config Available
When YAML file is missing or has no `audios` field, default stereo processing is used.

## Logging

Audio processing information is logged at INFO level:

1. **Configuration loading**: Audio track information (componentType, sampling rate, languages)
2. **Dual mono detection**: When `componentType=2` is detected during encoding
3. **File status**: YAML file presence/absence and parsing results

## Special Cases

### `noStrip` Parameter
When `noStrip=True` is passed to `EncodePipeline`, the audio processing still occurs in the encoding stage (`EncodeTsCmd`). The `StripTsCmd` function only performs basic audio copying or `fixAudio` processing, while dual mono splitting is handled exclusively in the encoding phase.

### Missing or Invalid YAML Files
If the YAML file is missing, corrupt, or lacks the `audios` field:
- Audio configuration defaults to `None`
- Normal stereo processing is used (`-c:a copy`)
- Log messages indicate the absence of audio configuration

## File Locations

- **YAML files**: `<output-directory>/<ts-filename>.yaml` (same directory as MP4 output)
- **Metadata files**: `<output-directory>/_metadata/` (contains `.ptsmap`, `.markermap` files)

## Testing

Test with TS files containing dual mono audio to verify:
1. YAML file generation during `analyze` phase
2. Correct detection of `componentType=2`
3. Proper audio channel splitting during `encode` phase
4. Output MP4 file with two independent mono audio tracks

### Test Command
```bash
tstriage --config tstriage.config.yml --task encode --input <ts-file>
```