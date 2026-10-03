# Ambient Video Automation

A local web app that automates the BMPCC 4K + Zoom H2N ambient video workflow. Run it, point it at your folders, and it handles matching, syncing, ordering, and exporting — with optional DaVinci Resolve integration.

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

This launches the app at `http://localhost:8765` and opens your browser. Everything happens through the UI — no command line needed after this.

## What it does

1. **Match** — Extracts scratch audio from each BMPCC video and cross-correlates it against your Zoom H2N WAV files to find which audio goes with which video. Also calculates the precise sync offset.
2. **Select & Order** — Shows thumbnails from each scene in a visual interface. Click to add to your timeline, drag to reorder, set per-scene trim points.
3. **Sync & Export** — For each scene: replaces the BMPCC scratch audio with the H2N recording (using the auto-detected offset), applies trims, exports numbered clips.
4. **Push to Resolve** (optional) — Creates a DaVinci Resolve project with all clips on a timeline.

## Setup

```bash
# Requirements: Python 3.10+, ffmpeg
pip install -r requirements.txt
```

**requirements.txt** installs: `numpy`, `scipy`, `tqdm`

## Workflow

### Step 1: Match

Point the tool at your two folders:

```bash
python main.py match --video ./BMPCC_clips/ --audio ./H2N_recordings/
```

This will:
- Find all video files (mp4/mov/mkv/mxf) and audio files (wav/flac)
- Extract scratch audio from each video
- Cross-correlate to find the best audio match for each video
- Extract a thumbnail from each video
- Save everything to `project.json`

### Step 2: Rename (optional)

```bash
# Preview what would be renamed
python main.py rename --dry-run

# Do the rename
python main.py rename

# Undo if needed
python main.py rename --undo rename_log.json
```

### Step 3: Select scenes visually

```bash
python main.py select
```

This opens an HTML page in your browser showing thumbnails of all matched scenes. Drag scenes from "Available" to "Timeline" to build your sequence. Set per-scene trim points. Click "Export Selection" to get a JSON string.

### Step 4: Sync and export

```bash
# Paste the JSON from the selector
python main.py sync --selection '{"selection":[3,1,5,2],"selection_trims":{"3":{"start":2,"end":1}}}'

# Or save the JSON to a file first
python main.py sync --selection-file selection.json

# Specify output directory
python main.py sync --selection-file selection.json --output ./for_davinci/
```

Output clips are named `01_scene_03.mp4`, `02_scene_01.mp4`, etc. — numbered in your chosen order, ready to import into DaVinci Resolve.

## Configuration

Edit `config.py` to adjust:

- **Matching sensitivity** — `MATCH_MIN_CONFIDENCE` (lower = accept weaker matches)
- **Matching speed** — `MATCH_MAX_DURATION_SEC` (uses first N seconds of audio; 60s is usually enough)
- **Thumbnail appearance** — `THUMBNAIL_TIME_SEC`, `THUMBNAIL_WIDTH`
- **Output quality** — `OUTPUT_AUDIO_CODEC` (default: PCM 24-bit to match H2N), `REENCODE_CRF`
- **Naming pattern** — `RENAME_PATTERN`

### Step 5: Push to DaVinci Resolve (optional)

Instead of importing clips manually, push them straight into a Resolve project:

```bash
# Make sure DaVinci Resolve Studio is running, then:
python main.py resolve --clips ./synced_output/

# Custom project name and frame rate
python main.py resolve --clips ./synced_output/ --name "Forest Ambience" --fps 23.976

# List existing Resolve projects
python main.py resolve --list
```

This creates a new project in Resolve, imports all synced clips into the Media Pool, and builds a timeline with clips in your selected order. Switch to Resolve and start color grading.

**Requirements for Resolve integration:**
- DaVinci Resolve **Studio** (paid version — the free version doesn't support external scripting)
- Resolve must be running with external scripting enabled: Preferences > System > General > External scripting using: **Local**
- Set `PYTHONPATH` to include Resolve's scripting modules (the tool tries to find it automatically):
  - macOS: `export PYTHONPATH="/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/"`
  - Linux: `export PYTHONPATH="/opt/resolve/Developer/Scripting/Modules/"`
  - Windows: `set PYTHONPATH="C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules\"`

## Tips

- **Matching accuracy**: The tool uses the first 60 seconds of audio by default. If your scenes start with silence, increase `MATCH_MAX_DURATION_SEC` in config.py.
- **Unmatched files**: After matching, the tool reports any video or audio files it couldn't pair. Check these manually.
- **Re-running**: Each step reads from and writes back to `project.json`. You can re-run any step without losing earlier work.
- **Audio offset**: The sync step uses the auto-detected offset. If a particular scene sounds off, you can manually adjust the offset in `project.json`.
- **4K performance**: Video is stream-copied (no re-encoding) when possible, so syncing is fast even with 4K footage. Re-encoding only happens when trimming requires frame-precise cuts.
