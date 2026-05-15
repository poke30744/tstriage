# EPG YAML → Kodi NFO Metadata

Status: research complete, not yet implemented.

## Goal

Generate Kodi-compatible `.nfo` files from EPG YAML so Kodi displays program information
(title, plot, genre, aired date, runtime, studio) when browsing and playing recorded videos.

## Key Decisions

### Content type: Movies, not TV Shows

Kodi's TV show scanner requires `SxxExx` in filenames — Japanese TS recording filenames
(timestamp-based) don't match. TV show mode is non-negotiable without renaming files.

**Solution:** Use "Movies" content type with "Local information only" scraper.
Each recording is a standalone library entry. Season/episode info goes in `<title>`.

### Format: Sidecar NFO, not embedded

| Approach | Kodi support | Requires library | Notes |
|---|---|---|---|
| **Sidecar `.nfo`** | Full (title, plot, genre, etc.) | Yes | Recommended |
| MKV Matroska tags | TITLE only (others ignored) | Yes | Useless for our needs |
| MKV attachment (`kodi-metadata`) | Full (same as sidecar) | Yes | Requires remux, no benefit over sidecar |
| MP4 atoms (`©nam`, `©des`, etc.) | Good (title, plot, genre) | Yes | ASS subtitles not supported in MP4 |

### Kodi settings

1. Settings → Media → Videos → **"Use video tags"** enabled
2. Add source → **"Movies"** → **"Local information only"** scraper
3. Individual NFO refresh not supported with local scraper — change content type to re-scan

Kodi versions: same behavior confirmed in v21.3 (Omega).

## EPG YAML → NFO Field Mapping

```
YAML field           NFO tag         Notes
──────────────────────────────────────────────────
name          →      <title>         Program title
description   →      <plot>          Merged with extended fields below
extended.*    →      <plot>          Formatted into sections (cast, staff, music, etc.)
startAt       →      <premiered>     Converted to yyyy-mm-dd
duration      →      <runtime>       Converted to minutes
genres[0].lv1 →      <genre>         Mapped via event.yml (e.g. 3 → "ドラマ")
serviceId_desc →     <studio>        Channel name
```

## NFO XML Template (Movie format)

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
    <title>Program Title</title>
    <plot>Description text with formatted sections for cast, staff, music, etc.</plot>
    <premiered>2026-05-02</premiered>
    <runtime>40</runtime>
    <genre>ドラマ</genre>
    <studio>テレ東</studio>
</movie>
```

NFO file must have the same stem as the MKV file, placed in the same directory.

## YAML Fields NOT in NFO (and why)

| YAML field | Used by | Why excluded from NFO |
|---|---|---|
| `serviceId` (raw int) | ensemble, speech | ML feature / channel lookup, not display |
| `startAt` (ms timestamp) | ensemble, speech | ML feature (start_hour, start_weekday), not display |
| `duration` (ms) | ensemble | ML feature, display uses `duration_desc` |
| `genres` (lv1/lv2/un1/un2) | ensemble | ML features, display uses lv1→genre name |
| `eventId`, `networkId` | — | Not used by tsmarker at all |
| `audios`, `video` | — | Codec info, not used by tsmarker |

Bottom line: NFO is a display artifact for Kodi. The YAML file remains the
authoritative data source for the tsmarker pipeline. Both coexist — NFO does
NOT replace YAML.

## Which tsmarker methods use YAML

| Method | YAML fields used | Purpose |
|---|---|---|
| speech | name, description, extended, serviceId_desc, startAt, duration_desc, genres | LLM prompt context |
| ensemble | serviceId, startAt, duration, genres[0].lv1/lv2/un1/un2 | ML feature vector |
| subtitles | — | Subtitle-only detection |
| logo | — | Logo-only detection |
| clipinfo | — | Clip metadata |

Auto-method priority: `_groundtruth > _ensemble > speech > logo > subtitles`

## Test NFO

Tested working on Kodi 21.3 (English UI):

- Directory: `C:\Users\xiaoju\Desktop\TestData\Destination\ドラマ\孤独のグルメ\`
- Source added as "Movies" with "Local information only" scraper
- NFO displays title, plot, aired date, runtime, genre, studio in Kodi info panel

## Implementation Notes

- NFO generation belongs in the encode task (`tstriage/tasks.py`), alongside YAML generation
- `event.yml` genre mapping already exists — reuse for `<genre>` tag
- `extended` dict should be formatted with section labels for readability in plot
- Keep YAML generation unchanged — tsmarker still needs it
- NFO filename must match MKV filename stem
