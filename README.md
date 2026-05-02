# tstriage
Batch runner to process MPEG2-TS files

## Features

- Batch processing pipeline for MPEG2-TS files recorded from TV broadcasts
- Integration with EPGStation for metadata extraction
- Automatic categorization based on EPG keywords
- Commercial detection and removal using multiple analysis methods
- Video encoding with configurable presets
- **Audio processing**: Support for dual mono audio tracks (デュアルモノ) with automatic channel splitting when `componentType=2` is detected in YAML metadata files

## Quick Start

1. Install dependencies: `uv pip install -e .`
2. Configure `tstriage.config.yml`
3. Run the pipeline: `tstriage --config tstriage.config.yml --task categorize list analyze mark cut encode confirm cleanup`

## Dependencies

### Runtime

| Tool | Version | Install |
|---|---|---|
| Python | ≥3.8 | — |
| ffmpeg / ffprobe | any recent | `choco install ffmpeg-full` or [gyan.dev](https://www.gyan.dev/ffmpeg/builds/) |
| Caption2AssC | — | place in `C:\Software\Caption2Ass\` |
| mirakurun (epgdump) | **3.9.0-rc.4** | `npm install -g mirakurun@3.9.0-rc.4 --omit=dev --ignore-scripts` |
| Node.js | **18.x** (for mirakurun) | [nvm-windows](https://github.com/coreybutler/nvm-windows): `nvm install 18 && nvm use 18` |
| EPGStation | any | HTTP accessible (config.yml: `EPGStation: http://...`) |

### Python (via uv)

```
uv pip install -e .
```

### Verified Versions (2026-05-02)

```
Python    3.9.8
ffmpeg    8.1 (choco ffmpeg-full, gyan.dev build)
Node.js   18.20.8 (nvm-windows)
mirakurun 3.9.0-rc.4
uv        0.11.6
```

> mirakurun 4.x beta does not support Windows. `3.9.0-rc.4` is the last version that supports Windows and matches the EPGStation Docker image. `--ignore-scripts` skips the Windows service registration (only the epgdump CLI is needed).

## Per-Folder Settings (`tstriage.json`)

Each destination folder can have a `tstriage.json` to override default behavior. Default:

```json
{
  "marker": {
    "noEnsemble": true
  },
  "encoder": {
    "preset": "drama"
  }
}
```

### Encoder options

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `preset` | string | `"drama"` | Encode preset name, matches a key under `Presets` in `tstriage.config.yml` |
| `bygroup` | bool | `false` | When `true`, each merged clip group is encoded to a separate MP4 instead of merging all clips into one |
| `split` | int | `1` | Split program clips into N roughly equal-duration groups, outputting N MP4 files |
| `cropdetect` | bool | — | Detect and crop still areas (logo/pillarbox removal) |
| `fixaudio` | bool | — | Apply `aresample=async=1` audio fix (set automatically by analyze phase if decode errors detected) |
| `nostrip` | bool | — | Skip the intermediate strip step (encode directly from source) |

When `bygroup` or `split` results in multiple output files, they are named `<stem>_0.mp4`, `<stem>_1.mp4`, etc.

## Documentation

- [CLAUDE.md](CLAUDE.md) - Development guide and architecture overview
- [AUDIO_PROCESSING.md](AUDIO_PROCESSING.md) - Detailed audio processing documentation
