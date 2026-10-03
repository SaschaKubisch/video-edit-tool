# Product Requirements Document — Ambient Video Auto

**Version:** 1.0
**Date:** 2026-10-03
**Owner:** Sascha Kubisch
**Status:** Implemented (v1), Resolve script integration pending

---

## 1. Problem Statement

Ambient/nature videos are shot dual-system: a Blackmagic Pocket Cinema Camera 4K (BMPCC 4K) records video with scratch audio, and a Zoom H2N records high-quality audio. Each shoot yields 20–30 scenes of 1–5 minutes, producing two unrelated sets of files (`IMG_xxxx.MOV` and `ZOOMxxxx.WAV`) with no shared naming or timecode.

Preparing these for editing is slow and error-prone:

- Pairing each video with its audio must be done by hand (comparing durations, listening for claps).
- Files must be renamed so the editor can auto-sync them.
- Choosing which scenes to keep, and in what order, requires scrubbing through every clip.
- Importing into DaVinci Resolve and arranging the timeline is repetitive manual work.

## 2. Goal

Reduce the time from "SD cards on the desk" to "timeline open in DaVinci Resolve with synced audio" from roughly an hour to a few minutes, with zero re-encoding and no loss of the ability to correct mistakes manually in Resolve.

## 3. Users

A single user (the filmmaker) running the tool locally on macOS. No multi-user, auth, or cloud requirements.

## 4. Core User Journey

1. User launches the app (`python main.py`) and a browser tab opens at `localhost:8765`.
2. User picks the video folder and the audio folder via an in-browser directory picker.
3. User clicks **Match**. The tool pairs every `.MOV` with its `.WAV`, computes a sync offset, and extracts a thumbnail per scene.
4. User reviews a thumbnail grid, selects the scenes to keep, and drags them into the desired order. Optional per-scene trim in/out points can be set.
5. User clicks **Copy & Rename Audio Files**. Each matched `.WAV` is copied into the video folder with the same stem as its video (`IMG_2231.MOV` ↔ `IMG_2231.WAV`). Originals are untouched.
6. User opens DaVinci Resolve, imports the video folder, and uses **Auto Sync Audio → Based on Filename** (or runs the provided Resolve script) to get a synced timeline in the chosen order.

## 5. Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| F1 | Discover video (`.mov .mp4 .mkv .avi .mxf`) and audio (`.wav .flac .aiff`) files in two user-chosen folders. | Must |
| F2 | Match each video to exactly one audio file using duration similarity as the primary criterion, solved globally (not greedily). | Must |
| F3 | When multiple audio files have near-identical durations, disambiguate using waveform cross-correlation between the camera scratch track and the candidate recordings (claps/transients). | Must |
| F4 | Compute a sync offset (seconds) per pair and store it. | Must |
| F5 | Extract one thumbnail per video for visual identification. | Must |
| F6 | Browser UI to select scenes and order them by drag-and-drop. | Must |
| F7 | Optional trim in/out per scene. | Should |
| F8 | Copy matched audio files into the video folder, renamed to the video stem. No transcoding of audio or video. | Must |
| F9 | Default output folder is the video folder; user may override. | Must |
| F10 | Persist all state (folders, matches, offsets, selection, order, trims) to `project.json` so the session can be resumed. | Must |
| F11 | Push the ordered scenes into a new DaVinci Resolve project/timeline with video on track 1 and audio on track 2, offset applied. | Should |
| F12 | Work with the **free** edition of DaVinci Resolve (script runnable from Resolve's *Workspace → Scripts* menu). | Should |
| F13 | CLI subcommands for every step so the pipeline can be run without the browser UI. | Could |
| F14 | Undo for any file renames performed by the tool. | Could |

## 6. Non-Functional Requirements

- **No re-encoding.** Video and audio must never be transcoded by this tool; quality is preserved bit-for-bit.
- **Local only.** Runs entirely on the user's machine; no network calls apart from `localhost`.
- **Speed.** Matching 30 × 30 files should finish in well under a minute on a laptop; expensive audio extraction happens only when needed (ambiguous pairs, offset detection) and only on the first 60 s of each file.
- **Transparency.** Console output explains each matching decision (duration diff, correlation scores, swaps) so the user can audit results.
- **Recoverability.** Any mistake must be correctable in Resolve: audio is delivered as a separate file and track, never baked into the video.
- **Minimal dependencies.** Python 3.10+, `numpy`, `scipy`, `tqdm`, plus system `ffmpeg`/`ffprobe`.

## 7. Out of Scope (v1)

- Automatic scene detection within a single long recording.
- Colour grading, LUTs, or any image processing.
- Final render/export; this is left to DaVinci Resolve.
- Subtitle/narration generation.
- Windows/Linux support (likely works, but untested).
- Multi-camera (>1 video source) or multi-recorder (>1 audio source) setups.

## 8. Success Metrics

- 100 % of pairs correctly matched on a typical 26-scene shoot (verified by the user in Resolve).
- End-to-end time from launch to synced Resolve timeline ≤ 5 minutes for 30 scenes.
- Zero transcoded files produced.

## 9. Key Decisions & Rationale

| Decision | Alternative considered | Why |
|----------|------------------------|-----|
| Duration-first matching with Hungarian assignment | Pure audio cross-correlation across all pairs | Cross-correlation between a camera mic and an H2N gave weak scores (8–18 %) and O(n²) extractions; durations are near-identical for paired files and cheap to read. |
| Cross-correlation only as a tiebreaker | Always run correlation | Keeps the common case fast; correlation is decisive precisely when durations can't be. |
| Copy + rename audio instead of muxing into video | ffmpeg merge into a new `.mov` | No re-encode, instant, and keeps audio on a separate track so offsets can be nudged in Resolve. |
| Output defaults to the video folder | Separate `synced_output/` folder | Resolve's *Auto Sync by Filename* needs both files in the same import; one folder = one drag. |
| Resolve script run from inside Resolve | External scripting API | External API is Studio-only; the in-app Scripts menu works on the free edition. |
| Single-file browser UI served by `http.server` | Electron / Tkinter / Streamlit | Zero extra dependencies, trivially inspectable, good enough for one user. |

## 10. Open Items / Future Work

- Ship `resolve_import.py` as a drop-in for `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`.
- Surface the "ambiguous → resolved by correlation" decisions in the UI, not just the console.
- Allow manual re-pairing in the UI (drag an audio onto a different video).
- Optional `.srt` generation for narration subtitles from per-scene labels.
- Package as a double-clickable `.command` / `.app` so Terminal isn't required.
