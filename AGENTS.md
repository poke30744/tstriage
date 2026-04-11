# tstriage - Agent Instructions

## Project Overview
Python package for batch processing MPEG2-TS files. Processes recorded TV streams from network storage, categorizes, cuts, encodes, and moves to destination.

## Key Commands
- **Install with uv**: `uv pip install -e .` (editable install)
- **Test**: `pytest tests/` (pytest configured in .vscode/settings.json)
- **Build with uv**: `uv build`
- **Run**: `tstriage` (console script from `tstriage.runner:main`)

## Configuration
- Main config: `tstriage.config.yml` (required for execution)
- Package data includes: `channels.yml`, `event.yml`
- Config paths reference external tools: TsSplitter, Caption2Ass

## Architecture Notes
- Entry point: `tstriage.runner:main`
- Core modules: `tasks.py` (Analyze, Mark, Cut, Encode, Confirm, Cleanup), `epgstation.py`, `nas.py`, `pipeline.py`
- External dependencies: `tscutter`, `tsmarker`, `PyYAML`, `psutil`
- Python 3.9+ required

## Development
- No lint/format config found; follow PEP 8
- Tests in `tests/` directory
- Jenkins CI pipeline builds and publishes to Test PyPI using `uv publish`
- Version format: `0.1.{BUILD_NUMBER}` from env var

## Operational Notes
- Processes files from network storage (`\\acepc-gk3\BUFFALO_Exp\recorded`)
- Outputs to categorized MP4 files (`\\acepc-gk3\Seagate 8T\categorized_mp4`)
- Uses cache directory from config (`Cache: ~/Desktop/workingFolder2`)
- Integrates with EPGStation API for metadata
- Supports multiple encoding presets (drama, anime, etc.) with NVIDIA NVENC