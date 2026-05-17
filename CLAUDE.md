# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development

- **Always use `uv run --directory C:\repos\tscutter tscutter ...` and `uv run --directory C:\repos\tsmarker tsmarker ...`** for local development. Never rely on installed packages — the repos at `C:\repos\tscutter` and `C:\repos\tsmarker` are the source of truth.

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
- Run full pipeline: `uv run python -m tstriage.runner --config tstriage.config.yml run categorize list encode index mark cut confirm cleanup`
- Individual tasks can be run as subcommands: `tstriage categorize`, `tstriage list`, etc.

## High-Level Architecture

### Overview
tstriage is a batch processing pipeline for MPEG2-TS files recorded from TV broadcasts. It orchestrates tscutter and tsmarker via subprocess CLI calls — there are **zero Python import dependencies** on either package.

### Core Modules
1. **runner** (`tstriage/runner.py`): Main entry point. Parses CLI arguments, loads YAML configuration, injects environment variables, and executes tasks via the `Runner` class.
2. **tasks** (`tstriage/tasks.py`): Individual processing steps: `Encode` (TS→MKV + EPG/logo/ASS/YAML/audio), `Index` (tscutter index), `Mark` (+EDL), `Cut`, `Confirm` (+EDL), `Cleanup`.
3. **input_file** (`tstriage/input_file.py`): Local `InputFile` class with ffmpeg/ffprobe discovery, `GetInfo()`, `EncodeTsCmd()`.
4. **video_info** (`tstriage/video_info.py`): `VideoInfo` dataclass for probe results.
6. **cli_config** (`tstriage/cli_config.py`): Configurable tscutter/tsmarker command paths, read from config `Cli` section.
7. **subprocess_utils** (`tstriage/subprocess_utils.py`): `run()`, `run_json()`, `run_pipe()`, `run_long()` — subprocess execution. All single-threaded. `run_long()` reads ffmpeg stderr for time-based progress; `run_pipe()` reads PROGRESS lines for count-based progress. No threading, no PIPE deadlock risk.
8. **nas** (`tstriage/nas.py`): File discovery and action item management in `_tstriage` folder.
9. **epgstation** (`tstriage/epgstation.py`): EPGStation API client.
10. **epg** (`tstriage/epg.py`): EPG data extraction via `mirakurun-epgdump` subprocess.
11. **_progress** (`tstriage/_progress.py`): `SubprocessProgress` — reads PROGRESS lines from subprocess stderr, updates Rich panel. TTY/non-TTY dual mode.

### Processing Pipeline
1. **categorize**: Match unprocessed TS files against EPGStation keywords; create `.categorized`
2. **list**: Convert `.categorized` to `.toencode` using `tstriage.json` settings
3. **encode**: TS→MKV encoding + EPG/YAML/logo extraction + audio check + ASS embed with fixed background and end-time truncation (via post-encode extract→fix→remux); create `.toindex`
4. **index**: `tscutter index` on MKV (histogram scene-change) → `.ptsmap`; create `.tomark`
5. **mark**: `tsmarker mark` (subtitles/clipinfo/logo/speech/ensemble) + whisper STT → `.generated.srt` + EDL; create `.tocut`
6. **cut**: `tsmarker cut` on MKV → clips/ folder; create `.toconfirm`
7. **confirm**: `tsmarker groundtruth` from clips/ → regenerate EDL; create `.tocleanup`
8. **cleanup**: Remove temporary cache files

### Key Design Decisions (2026-05-11 Refactor)

- **Logo detection**: NCC (normalized cross-correlation) on raw pixel templates, replacing edge-based AND comparison. Logo stored as full-frame grayscale mean image during encode. Region auto-detected at mark time via edge-density scanning. `maxTimeToExtract` increased to 60s.
- **Scene-change detection**: Histogram chi-squared distance (64-bin grayscale) replaces SAD. Codec-agnostic, no I-frame dependency. Frames extracted at 2fps.
- **CM classification**: `_auto_by_method`: `_groundtruth > _ensemble > speech > logo > subtitles`. Falls back to `logo` when subtitles have no signal (all 0.0 or 0.5).
- **Index on MKV**: tscutter index runs on the encoded MKV for generality. The histogram algorithm works correctly across mpeg2video/h264 codec differences.
- **ASS embedded in MKV**: subtitles embedded with `language=jpn, default` disposition. tsmarker reads ASS from MKV via ffmpeg extraction. No standalone `.ass.original` file needed.
- **EDL format**: Kodi MPlayer EDL (seconds, space-separated, action=3 for CM breaks). Adjacent CM clips are merged into single EDL entries.
- **WAV extraction**: `aresample=async=1` filter removed — it distorted MKV raw AAC timing by ~0.9s compared to TS ADTS AAC.
- **ASS background + end time**: After encode, ASS extracted from MKV, BackColour fixed (`&H0`→`&H80000000`), end times exceeding video duration truncated to actual duration, then remuxed back into MKV with `-map_metadata 0`.
- **Subprocess**: `run_long()` for ffmpeg encode — single-threaded stderr read, parses `time=` for progress. `run_pipe()` for tscutter/tsmarker — single-threaded PROGRESS line reader. No threading, no PIPE deadlock.
- **Confirm simplified**: no longer reports "Re-encoding is needed!", just updates groundtruth and regenerates EDL.

### Configuration
- **`tstriage.config.yml`**: Required runtime config. Supports `$VAR` env var expansion in values.
  - `Cli`: Optional tscutter/tsmarker command paths (defaults to `tscutter`/`tsmarker` on PATH)
  - `Environment`: Key-value pairs injected as env vars before task execution

### External Dependencies
- **tscutter CLI**: analyze, split, probe, list-clips, select-clips (subprocess only, no Python import)
- **tsmarker CLI**: mark, cut, groundtruth, extract-logo, crop-detect, prepare-subtitles, get-program-clips, ensemble-* (subprocess only). `generate-edl` for EDL output.
- **ffmpeg / ffprobe**: Via `shutil.which()` in `input_file.py`, with clear error if not found
- **mirakurun-epgdump**: Called in `epg.py` for EPG data

### Notes for Development
- Python ≥3.13 required
- All tscutter/tsmarker interaction is via subprocess CLI — zero import dependency
- For local dev, set `Cli` in config to `uv run --directory <repo> <cmd>` to use uncommitted source
- Tests are pure unit tests (no sample files needed); use `uv run pytest tests/`

### Lessons Learned (2026-05-04 CLI Refactor)

1. **Subprocess exit codes must be checked — never ignore them.**
   `subprocess_utils.run()` must raise on non-zero exit. Silent failures waste hours.

2. **Don't add defensive `.get()` or try/except to mask symptoms.**
   If a dict key is missing or an operation fails, find the root cause. Protective defaults hide real bugs.

3. **When migrating to CLI, replicate ALL parameters from the old Python API.**
   The old `logo.MarkerMap.MarkAll(videoPath, logoPath=logoPath)` passed a cached logo file. The new CLI didn't pass `--logo`, forcing re-extraction which triggered a race condition on slow filesystems.

4. **Never commit or push without explicit permission.**
   Wait for the user to say "commit" or "push". Use `git stash` for temporary work.

5. **Test locally before claiming something works.**
   Run the full pipeline on sample data and verify output correctness.

6. **When removing a feature, remove ALL related code.**
   Dead code breeds confusion — keep the codebase clean.

7. **`uv run --directory <repo>` creates isolated venvs.**
   If repo A depends on repo B, repo B must be installed from local source (not Test PyPI) via `[tool.uv.sources]` in repo A's pyproject.toml.
