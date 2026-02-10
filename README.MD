<img width="1052" height="457" alt="Image" src="https://github.com/user-attachments/assets/31535288-9a6c-472a-bbb7-2791e350f19b" />

# MixSplitR v7.1
### Mix Archival Tool by KJD


MixSplitR helps you record and organize digitized vinyl, CD, and mixes into a clean music library with split tracks, metadata, artwork, and structured folders.

It supports:
- Direct system audio capture 
- Individual tracks
- Long recordings/mixes that need splitting


## Turn this:

<img width="2408" height="1644" alt="Image" src="https://github.com/user-attachments/assets/0a7f305b-87d1-47ef-94a1-d9210ec6092f" />

## Into this!


<img width="2688" height="943" alt="Image" src="https://github.com/user-attachments/assets/5a8af601-7243-4572-987e-f3b20d59142f" />

---
# [Download](https://github.com/chefkjd/MixSplitR/releases)




## v7.1 Highlights

### AcoustID support!
- Use acoustID to replace the need for ACRcloud or combine both for an expansive catalog of music identification 

### End-to-End Workflow (Now Includes Recording)
- Built-in system audio recording on Windows/macOS
- Record, split, identify, review, and export in one tool
- Makes MixSplitR a full capture-to-library workflow

### Album Search + Normalization + Deep Scan
- Album search toggle for release-focused matching/grouping
- Artist normalization toggle for cleaner folders and filenames
  - Collab/feat credits can be moved into title metadata
- Auto Deep Scan mode for recursive subfolder discovery

### Session History (Beta) Expansion
- Session-first history workflow
- Compare two sessions
- Reorganize files from session metadata
- Rollback with dry-run preview before delete
- Import/export session records (`.json`) (drag-drop path supported)
- Safe session apply to a target folder (no overwrite/no delete)
- Delete session records with safety checks (history JSON only)
- Rollback apply writes a new rollback session record for audit trail

### Auditable Processing with Rollback
- Session manifests capture processing details and outputs
- History is designed for audit/review, not just quick reruns
- Rollback includes preview-first safety before apply

### Unsaved Preview Workflow
- `Finish Unsaved Preview` from main menu
- `Clear Unsaved Preview Data` option
- Exit flow that lets you keep or clear unsaved preview work

### In-Tool Track Preview and Editing
- Preview track audio directly in the editor
- Edit track metadata before file creation
- Convert unidentified tracks to identified inside the app
- MusicBrainz search assist to label individual tracks quickly
- Import tracklists/CUE data to speed up labeling
- Supports simple tracklist formats (e.g., `MM:SS Artist - Title`, `HH:MM:SS Artist - Title (Album)`)

### Multi-Format Export + Splitter Input Support
- Multi-file-type export options (lossless + lossy profiles)
- Waveform splitter supports common audio formats and converts internally for waveform playback

### Portable Startup Scan Mode
- New first-run option and Settings toggle:
  - `Portable Startup Scan (ON/OFF)`
- ON: startup scans audio next to app/script
- OFF: startup uses your Music folder

### Expanded Identification Modes and Sources
- `ACRCloud + MusicBrainz`
- `MusicBrainz only` mode for users who do not want paid API dependency
- AcoustID support for MusicBrainz fingerprint matching
- Shazam integration as an additional identification path
- `Dual Mode` (runs multiple backends, picks best-confidence result)
- Better coverage for difficult/underground tracks via multi-source enrichment

### Fingerprint Accuracy Tuning
- Configurable fingerprint sample window (`8-45s`, default `12s`)
- Applied to ACRCloud, Dual mode, and Shazam sampling paths
- Lets you trade speed for better hit rate on difficult tracks

### Improved Settings and Directory Controls
- Album search toggle
- Artist normalization toggle (collab credits moved to title)
- Auto deep scan toggle
- Portable startup scan toggle
- Directory settings for:
  - Output folder
  - Recording folder
  - Session History folder

### One-Click / Direct Mode Improvements
- Direct Mode now asks for output format before export (instead of silently forcing one format)
- Selected output format is saved in session/manifest config snapshots

### Better Data Handling and Storage Defaults
- Config, cache, and session data are kept in a dedicated app-data folder
  - Windows: `%APPDATA%\\MixSplitR`
  - macOS: `~/Library/Application Support/MixSplitR`
  - Linux: `~/.local/share/MixSplitR`
- Music output defaults to a nested library under your Music folder:
  - `~/Music/MixSplitR Library` (or platform equivalent)

---

## Core Features

- Automatic split detection for mixes with silence gaps
- Manual visual splitter for seamless DJ mixes
- Assisted splitting (auto-detect + manual review)
- Interactive Track Editor:
  - Audio preview
  - Manual metadata correction
  - Convert unidentified tracks to identified
  - MusicBrainz text-search assist for difficult IDs
  - Tracklist/CUE import for faster bulk labeling
  - Simple timestamp format import support (`MM:SS`, `HH:MM:SS`, optional album text)
  - Filter views (missing genre/BPM, identified/unidentified)
- Preview/Apply workflow:
  - Full Preview (cached chunks + playback/edit support)
  - Light Preview (lower disk usage, re-splits on apply)
- Metadata enrichment from multiple sources (MusicBrainz, iTunes, Deezer, Last.fm)
- Configurable fingerprint sample size (`8-45s`) for identification tuning
- Local BPM detection fallback via `librosa`
- Artist-folder organization and metadata tagging
- System audio recording flow (Windows/macOS)
- Large-file streaming mode (FFmpeg-based) for memory-heavy files
- Multi-format export targets in apply flow (FLAC, ALAC, WAV, AIFF, MP3, AAC, OGG, OPUS)
- Direct Mode output format selector (same format family as apply flow)


## Manually split or use assisted mode to find the splits for you, leaving only for you to review and edit!
     - Supports CUE sheets in multiple formats!


<img width="1672" height="1258" alt="Image" src="https://github.com/user-attachments/assets/edea7387-a109-484e-a7fb-8c02e93d3648" />

---

## Supported Formats

### Input
`WAV`, `FLAC`, `MP3`, `M4A`, `OGG`, `AAC`, `WMA`, `AIFF`, `OPUS`

### Output (Apply mode)
- FLAC
- ALAC (`.m4a`)
- WAV
- AIFF
- MP3 (`320k`, `256k`, `192k`)
- AAC (`256k`)
- OGG Vorbis (`~500k`, `~320k`)
- OPUS (`256k`)

---

## Requirements

### Identification
- ACRCloud account (if using ACRCloud mode)
- AcoustID key recommended for MusicBrainz-only fingerprinting
- Last.fm API key optional (better genre/tag enrichment)
- On Mac it is required to install blackhole for sound loopback. The program will walk you through the steps if it detects no blackhole.

---

## Quick Start

1. Launch MixSplitR.
2. On first run, choose identification mode.
3. Choose startup scan mode (portable or Music folder).
4. Load audio files/folders (drag-drop supported).
5. Run `Preview Mode` (recommended) or `Direct Mode`.
6. In Preview Mode, choose split mode: Automatic, Manual, or Assisted.
7. Review in Track Editor, then apply.
8. Use Session History (Beta) for compare/restore/import/export/delete workflows.

---

## Config and Data Paths

MixSplitR stores config/cache/session data in platform app-data folders:

- Windows: `%APPDATA%\\MixSplitR`
- macOS: `~/Library/Application Support/MixSplitR`
- Linux: `~/.local/share/MixSplitR`

Typical files/folders:
- `config.json`
- `mixsplitr_cache.json`
- `manifests/`

Default music output path:
- `~/Music/MixSplitR Library` (unless changed in Settings)

---

## [Did I save you some time? Help me fund my large caffeine intake!](https://ko-fi.com/mixsplitr)

## Privacy and Intended Use

- For personal, non-commercial use only
- Keep API keys private (`config.json` contains credentials)
- Do not upload credentials to public repos
- Respect copyright law and only process audio you have legal rights to use

---

## Links

- Releases: <https://github.com/chefkjd/MixSplitR/releases>
- Source: <https://github.com/chefkjd/MixSplitR>
- Issues: <https://github.com/chefkjd/MixSplitR/issues>
- ACRCloud: <https://console.acrcloud.com>
- AcoustID: <https://acoustid.org/api-key>
- Last.fm API: <https://www.last.fm/api/account/create>
