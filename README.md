# tstriage
Batch runner to process MPEG2-TS files

## Features

- Batch processing pipeline for MPEG2-TS files recorded from TV broadcasts
- Integration with EPGStation for metadata extraction
- Automatic categorization based on EPG keywords
- Commercial detection and removal using multiple analysis methods
- Video encoding with configurable presets (x264, NVENC)
- **Audio processing**: Support for dual mono audio tracks with automatic channel splitting
- **CLI-based architecture**: All tscutter/tsmarker calls are subprocess CLI invocations — zero Python import dependency

## Quick Start

1. Install dependencies: `uv pip install -e .`
2. Configure `tstriage.config.yml`
3. Run the pipeline: `tstriage --config tstriage.config.yml --task categorize list analyze mark cut encode confirm cleanup`

## Configuration

### `tstriage.config.yml`

```yaml
Cli:                    # CLI command paths (optional, defaults to PATH)
  tscutter: uv run --directory C:/repos/tscutter tscutter
  tsmarker: uv run --directory C:/repos/tsmarker tsmarker
Uncategoried: ~/recorded
Destination: ~/categorized
EPGStation: http://localhost:8888
Encoder: h264_nvenc
Presets:
  anime:
    videoFilter: pullup,fps=24000/1001
    crf: 24
Environment:            # Injected as env vars for subprocesses
  OPENAI_API_KEY: sk-...
  OPENAI_API_BASE: https://api.deepseek.com
  OPENAI_MODEL: deepseek-v4-flash
```

### `Cli` section

Defines the commands used to invoke tscutter and tsmarker. If omitted, defaults to `tscutter` and `tsmarker` on PATH.

For local development with editable repos:
```yaml
Cli:
  tscutter: uv run --directory C:/repos/tscutter tscutter
  tsmarker: uv run --directory C:/repos/tsmarker tsmarker
```

### `Environment` section

Key-value pairs injected as environment variables before running any task. Used by tsmarker speech marking (LLM API keys) and other subprocesses.

## Dependencies

### Runtime

| Tool | Purpose |
|---|---|
| Python | ≥3.13 |
| ffmpeg / ffprobe | Video encode, probe, audio check |
| ffmpeg5 | Scene change detection (tscutter analyze) |
| Caption2AssC | Subtitle extraction |
| mirakurun-epgdump | EPG data extraction from TS |
| EPGStation | HTTP metadata source |

### Python

```
uv pip install -e .
```

Key packages: tscutter, tsmarker, ffmpeg-python, PyYAML, psutil, pysubs2.

## Per-Folder Settings (`tstriage.json`)

```json
{
  "marker": {"noEnsemble": true},
  "encoder": {"preset": "drama"}
}
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `preset` | string | `"drama"` | Encode preset name |
| `bygroup` | bool | `false` | Each clip group → separate MKV |
| `split` | int | `1` | Split into N output files |
| `cropdetect` | bool | — | Logo/pillarbox crop detection |
| `fixaudio` | bool | — | Audio resample fix (auto-set by analyze) |
| `nostrip` | bool | — | Skip strip step, encode directly |

## Documentation

- [CLAUDE.md](CLAUDE.md) - Development guide and architecture
- [CLI_DESIGN.md](CLI_DESIGN.md) - CLI decoupling design and progress
- [AUDIO_PROCESSING.md](AUDIO_PROCESSING.md) - Audio processing details
