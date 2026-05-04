# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Development Commands

### Installation and Setup
- Install dependencies with uv: `uv pip install -e .` (editable install)
- Install development dependencies: `uv pip install -e ".[dev]"`
- Build package: `uv build`
- Publish to Test PyPI: `uv publish --publish-url https://test.pypi.org/legacy/ dist/*` (requires credentials)

### Testing
- Run all tests: `uv run pytest tests/`
- Run a specific test: `uv run pytest tests/test_input_file.py::test_get_info`
- Tests are configured for pytest in `.vscode/settings.json`

### Running the Application
- The entry point is `tstriage.runner:main`, installed as console script `tstriage`.
- **For local development**: Use `uv run python -m tstriage.runner` to run directly from source.
- Run with configuration: `uv run python -m tstriage.runner --config tstriage.config.yml --task categorize list analyze mark cut encode confirm cleanup`
- Individual tasks can be run separately: `--task categorize`, `--task list`, etc.

## High-Level Architecture

### Overview
tstriage is a batch processing pipeline for MPEG2-TS files recorded from TV broadcasts. It orchestrates tscutter and tsmarker via subprocess CLI calls — there are **zero Python import dependencies** on either package.

### Core Modules
1. **runner** (`tstriage/runner.py`): Main entry point. Parses CLI arguments, loads YAML configuration, injects environment variables, and executes tasks via the `Runner` class.
2. **tasks** (`tstriage/tasks.py`): Individual processing steps: `Analyze`, `Mark`, `Cut`, `Encode`, `Confirm`, `Cleanup`. Each function calls tscutter/tsmarker via subprocess.
3. **pipeline** (`tstriage/pipeline.py`): `EncodePipeline` — ffmpeg encoding orchestration with subtitle extraction and TS stripping.
4. **input_file** (`tstriage/input_file.py`): Local `InputFile` class with ffmpeg/ffprobe discovery, `GetInfo()`, `StripTsCmd()`, `EncodeTsCmd()`.
5. **video_info** (`tstriage/video_info.py`): `VideoInfo` dataclass for probe results.
6. **cli_config** (`tstriage/cli_config.py`): Configurable tscutter/tsmarker command paths, read from config `Cli` section.
7. **subprocess_utils** (`tstriage/subprocess_utils.py`): `run()`, `run_json()` helpers with error handling.
8. **nas** (`tstriage/nas.py`): File discovery and action item management in `_tstriage` folder.
9. **epgstation** (`tstriage/epgstation.py`): EPGStation API client.
10. **epg** (`tstriage/epg.py`): EPG data extraction via `mirakurun-epgdump` subprocess.
11. **common** (`tstriage/common.py`): `CopyWithProgress`, `CopyWithProgress2`.

### Processing Pipeline
1. **categorize**: Match unprocessed TS files against EPGStation keywords; create `.categorized`
2. **list**: Convert `.categorized` to `.toanalyze` using `tstriage.json` settings
3. **analyze**: `tscutter analyze` → `tsmarker prepare-subtitles` → `tsmarker extract-logo` → audio check; create `.tomark`
4. **mark**: `tsmarker mark` (subtitles/clipinfo/logo/speech) → `tsmarker ensemble-*`; create `.tocut`
5. **cut**: `tsmarker cut --by auto`; create `.toencode`
6. **encode**: `tsmarker get-program-clips` → `tsmarker extract-clips` → ffmpeg encode; create `.toconfirm`
7. **confirm**: `tsmarker groundtruth`; create `.tocleanup` or `.toencode`
8. **cleanup**: Remove temporary cache files

### Configuration
- **`tstriage.config.yml`**: Required runtime config. Supports `$VAR` env var expansion in values.
  - `Cli`: Optional tscutter/tsmarker command paths (defaults to `tscutter`/`tsmarker` on PATH)
  - `Environment`: Key-value pairs injected as env vars before task execution

### External Dependencies
- **tscutter CLI**: analyze, probe, list-clips, select-clips (subprocess only, no Python import)
- **tsmarker CLI**: mark, cut, groundtruth, extract-clips, extract-logo, crop-detect, prepare-subtitles, get-program-clips, ensemble-* (subprocess only)
- **ffmpeg / ffprobe**: Via `shutil.which()` in `input_file.py`, with clear error if not found
- **Caption2AssC.cmd**: Called directly in `EncodePipeline` for subtitle extraction
- **mirakurun-epgdump**: Called in `epg.py` for EPG data

### Notes for Development
- Python ≥3.13 required
- All tscutter/tsmarker interaction is via subprocess CLI — zero import dependency
- For local dev, set `Cli` in config to `uv run --directory <repo> <cmd>` to use uncommitted source
- Tests are pure unit tests (no sample files needed); use `uv run pytest tests/`
