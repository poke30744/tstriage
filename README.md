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
