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

## Documentation

- [CLAUDE.md](CLAUDE.md) - Development guide and architecture overview
- [AUDIO_PROCESSING.md](AUDIO_PROCESSING.md) - Detailed audio processing documentation
