# Technical Specification — Ambient Video Auto

**Version:** 1.1 — 2026-10-03
Companion to `PRD.md`. Describes the implemented system as it exists in this folder.

---

## 1. Architecture Overview

```
┌──────────────┐   HTTP (localhost:8765)   ┌───────────────────────────────┐
│  ui.html     │ ◄──────────────────────► │  server.py (ThreadingHTTPServer)│
│  (browser)   │   JSON API + thumbnails   │  ├─ worker threads            │
└──────────────┘                           │  └─ global Project + lock     │
                                           └──────┬──────────┬────────────┘
                                                  │          │
                      ┌───────────────────────────┼──────────┼──────────────────┐
                      ▼                           ▼          ▼                  ▼
               matcher.py                 thumbnails.py   syncer.py   resolve_integration.py
            (ffprobe/ffmpeg,              (ffmpeg)        (shutil)    (DaVinciResolveScript)
             numpy/scipy)
                      │
                      ▼
                 project.py  ──►  project.json  (single source of truth)
                      ▲
                 config.py (constants)
```

Everything runs in one Python process. Long operations (match, sync, resolve) run on daemon threads, one at a time; the UI polls `/api/status`.

## 2. Module Reference

### 2.1 `config.py`
Constants only (unused constants from the old export/re-encode design were removed). Values:

| Name | Value | Purpose |
|------|-------|---------|
| `VIDEO_EXTENSIONS` | `.mp4 .mov .avi .mkv .mxf` | Discovery (case-insensitive) |
| `AUDIO_EXTENSIONS` | `.wav .flac .aiff .aif` | Discovery |
| `MATCH_SAMPLE_RATE` | 16000 | Downsample rate for correlation |
| `MATCH_MAX_DURATION_SEC` | 60 | Only first N seconds extracted for correlation |
| `THUMBNAIL_TIME_SEC` / `_WIDTH` / `_FORMAT` / `_QUALITY` | 3.0 / 480 / jpg / 85 | Frame grab position, size, encoding |
| `RENAME_PATTERN` | `scene_{index:02d}` | Legacy `rename` command only |
| `SELECTOR_PORT` | 8765 | HTTP port |
| `PROJECT_FILE` | `project.json` | Persistence path (relative to CWD) |

### 2.2 `project.py` — data model

```python
@dataclass
class SceneMatch:
    index: int
    video_path: str
    audio_path: str
    video_duration: float
    audio_duration: float
    offset: float          # seconds, t_audio - t_video (see below)
    confidence: float      # 0..1, margin based (see 2.3)
    thumbnail_path: str = ""
    label: str = ""
    trim_start: float = 0.0   # seconds cut from the START
    trim_end: float = 0.0     # seconds cut from the END

@dataclass
class Project:
    video_dir: str; audio_dir: str; output_dir: str
    matches: list[SceneMatch]
    selection: list[int]            # ordered list of SceneMatch.index
    selection_trims: dict           # "index" -> {"start": s, "end": s}

    def save(path=config.PROJECT_FILE) -> None
    @classmethod load(path) -> Project
    def get_match(index) -> SceneMatch | None
    def get_ordered_scenes() -> list[SceneMatch]   # copies, selection + trims applied
```

- **Offset contract:** `offset = t_audio - t_video`. `offset > 0`: the same event occurs later in the audio file than in the video (recorder started `offset` s before the camera); the audio clip's source in-point is advanced by `offset`. `offset < 0`: the audio is placed `|offset|` s later on the timeline.
- **Trim semantics:** `trim_start` / `trim_end` are seconds cut from the start / end of the clip, not in/out timestamps. They are stored only and applied in Resolve. `selection_trims` may be keyed by int or str; its values override the per-match trims. `get_ordered_scenes()` returns `dataclasses.replace` copies, so stored matches are never mutated.
- **Atomic save:** `save()` writes a temp file in the target directory and `os.replace`s it, so a crash never leaves a half-written `project.json`.
- **Unknown-key tolerance:** `load()` drops keys that `SceneMatch` does not know, so older or newer project files still open.
- Numeric fields are native `float`/`int` before `json.dump` (numpy scalars are not serialisable).

### 2.3 `matcher.py` — pairing algorithm

**Inputs:** `video_dir`, `audio_dir`, optional `max_duration`.
**Output:** `list[SceneMatch]` sorted by video filename (indices renumbered after sorting).

Pipeline:

1. **Discover** files via `find_files()` (both lower/upper-case extensions).
2. **Durations** via `ffprobe -show_entries format=duration` for every file. A file ffprobe cannot read is skipped and reported.
3. **Cost matrix** `C[i][j] = |dur_video_i − dur_audio_j|`.
4. **Initial assignment** `scipy.optimize.linear_sum_assignment(C)`; unequal counts leave extra files unassigned.
5. **Ambiguity detection.** For each video *i* with assigned audio *j\**, candidates are `{ j : |C[i][j] − C[i][j*]| ≤ 5 s }`. Rows with more than one candidate get correlation scores.
   - Audio is extracted as mono 16 kHz PCM (first 60 s) for the video and all candidates, **in parallel** (`ThreadPoolExecutor`, 4 workers) and cached per file.
   - Score = peak of the **normalised cross-correlation (NCC) of onset envelopes**: 20 ms mean-abs windows, `log(env + eps)`, positive first difference, mean removed. Steady ambience contributes ~0, claps and transients dominate, gain differences do not matter. Scores lie in [0, 1].
6. **Hungarian re-solve with correlation-adjusted cost.** For every row that has scores, each column gets `+ W · (best_score_in_row − score)` (missing score counts as 0, `W = 20 × 5 s`, so a 0.1 correlation difference equals 10 s of duration error). The matrix is solved once, globally, so chained reassignments cannot undo each other. Every score and reassignment is logged to the console.
7. **Confidence (margin based, 0..1).**
   - Row decided by correlation (scores for ≥ 2 columns): `(score_chosen − best_other) / 0.2`.
   - Otherwise duration margin: `(runner_up_cost − chosen_cost) / 5 s`.
   - Caps: duration difference ≥ 30 s caps at 0.1, 10–30 s caps at 0.25.
   - Console status buckets: `<10 s OK`, `10–30 s WARN`, `≥30 s BAD`.
8. **Unmatched files** (videos without audio, audio without video, ffprobe-skipped files) are listed in the console.
9. **Sync offset** per final pair via `_find_offset()`:
   - *Coarse:* NCC of onset envelopes gives a lag in 20 ms windows.
   - *Fine:* cross-correlation of high-passed (500 Hz Butterworth) raw signals within ±0.1 s (+1 window) of the coarse lag, with **parabolic interpolation** around the peak for sub-sample precision.
   - Returns `t_audio − t_video` in seconds; 0.0 if either signal is empty.

Correlation decisions are console-only; the UI shows only the final confidence.

### 2.4 `thumbnails.py`
`extract_thumbnail(video_path, out_path)` → `ffmpeg -ss 3 -i … -vframes 1 -vf scale=480:-1`. `extract_all_thumbnails(project, output_dir)` is used by `main.py match`; the server calls `extract_thumbnail` per scene so it can report progress. Both write to `thumbnails/` **next to `project.json`** (`thumb_<index>.jpg`) and fill `SceneMatch.thumbnail_path`. The old standalone HTML selector was removed.

### 2.5 `syncer.py` — audio copy/rename

```python
def sync_all_scenes(project, output_dir=None) -> list[dict]
def remove_copied_audio(output_dir) -> list[str]
```
- `out_dir = output_dir or project.video_dir`.
- For each ordered scene: `new_name = Path(video).stem + Path(audio).suffix`; `shutil.copy2(audio, out_dir / new_name)` unless source == destination. If the destination exists with a different size (`os.path.getsize`) a warning is logged before it is overwritten.
- Returns `[{order, scene_index, video_path, audio_original, audio_copy, offset, trim_start, trim_end}, …]`.
- **Manifest:** every copy is listed in `ambient_audio_copies.json` in the output folder (`{"copies": [file names]}`), merged with an existing manifest and written atomically.
- **Undo:** `remove_copied_audio(output_dir)` deletes only files named in the manifest (plain file names; entries with path components are skipped), then the manifest, and returns the removed paths. CLI: `python3 main.py sync --undo [--output DIR]` (output folder defaults to the project's output/video folder). Satisfies PRD F14 for copies. The server has no undo endpoint yet.
- **Never** invokes ffmpeg. Trims are stored only; they are applied in Resolve.

### 2.6 `renamer.py`
Legacy, CLI-only helper that renames *both* files of a pair to `scene_NN.*` and writes `rename_log.json`; `undo_renames()` reverses it. Not used by the UI. Before any rename, `_check_collisions()` verifies that no target exists (other than the file itself) and that no two renames share a target; on a conflict it raises `FileExistsError` listing the problems and **no file is touched** (and no log is written). Undo runs the same check for old names that are already occupied.

### 2.7 `resolve_integration.py`

```python
def build_timeline_plan(scenes, fps) -> list[dict]     # pure, no Resolve import
def resolve_media_paths(project, scenes) -> list[tuple]
def apply_plan(resolve, project_name, timeline_name, scenes, plan=None, media_paths=None, fps=None) -> dict
def create_resolve_project(project, project_name, timeline_name, fps=None) -> dict
def list_resolve_projects() -> list
```
1. `build_timeline_plan()` is pure and unit-tested. Per scene it returns `{"index", "video": clipInfo, "audio": clipInfo | None}` with `clipInfo = {startFrame, endFrame, recordFrame, trackIndex, mediaType}`. Video: `trackIndex=1, mediaType=1`; audio: `trackIndex=2, mediaType=2`. Source frames come from trims; audio `startFrame = video start + round(offset·fps)`, a negative result becomes a `recordFrame` delay; audio is clamped to the file length. Scenes are laid back-to-back; `recordFrame` is 0-based relative to the timeline start.
2. `resolve_media_paths()` prefers the renamed sibling `<video_stem><audio suffix>` in the video folder or `project.output_dir`, falling back to the original `audio_path`.
3. `apply_plan()` does the API calls: `_get_resolve()` imports `DaVinciResolveScript` (auto-detects the Modules path, raises `ConnectionError` with setup steps); loads or creates the project; optionally sets `timelineFrameRate`; imports media; creates the timeline; ensures two audio tracks; reads the timeline frame rate if none was given; builds the plan if needed; offsets `recordFrame` by `timeline.GetStartFrame()`; calls `AppendToTimeline`. Files not found in the Media Pool are skipped and reported.
4. **Timeline name suffix:** if the timeline name already exists in the project, ` 2`, ` 3`, … is appended instead of failing.

**Flagged API assumptions** (not verifiable without Resolve; check when first run against a real install):
- `endFrame` in `AppendToTimeline` clip infos is treated as exclusive.
- `mediaType` 1 = video, 2 = audio, and `trackIndex` 2 for audio places clips on A2.
- `timelineFrameRate` only takes effect before the project's first timeline exists.
- Media Pool items are matched back to files by their `File Path` property after `ImportMedia`.
- `recordFrame` is absolute: the plan's 0-based frames are shifted by `timeline.GetStartFrame()` (typically 86400 at 24 fps for 01:00:00:00).

**Constraint:** external invocation may require Resolve **Studio**. For the free edition the same logic must be executed from inside Resolve (see §6).

### 2.8 `server.py` — HTTP API

`ThreadingHTTPServer` bound to `127.0.0.1`. Static: `GET /` → `ui.html`.

| Method | Path | Body / Query | Effect |
|--------|------|--------------|--------|
| GET | `/api/status` | — | `{step, message, progress}` of the current background job (`step`: idle, matching, thumbnails, matched, syncing, synced, resolve, resolve_done, error) |
| GET | `/api/project` | — | Project as JSON; each match gains `video_file`, `audio_file`, `has_thumbnail`, `thumb_version` |
| GET | `/api/browse` | `?path=` | `{current, entries:[{name, path, is_dir, size}]}` for the folder picker |
| GET | `/api/thumbnail/<index>` | — | Image bytes (`Cache-Control: no-cache`) |
| POST | `/api/match` | `{video_dir, audio_dir}` | Validates folders (400), starts matcher + thumbnail thread |
| POST | `/api/select` | `{selection:[idx…], selection_trims:{idx:{start,end}}}` | Saves order/trims to `project.json` |
| POST | `/api/sync` | `{output_dir?}` | Starts `sync_all_scenes` thread (defaults to the video dir) |
| POST | `/api/resolve` | `{project_name, timeline_name}` | Starts `create_resolve_project` thread (legacy keys `name`, `timeline` still accepted) |

- **Job guard:** match, sync and resolve share one slot; starting a second returns **409** `Another job is already running`. The slot is released in `finally`.
- **Host/Origin check (403):** every request must carry `Host: localhost:<port>` or `127.0.0.1:<port>`; POSTs with an `Origin` header must be `http://` plus one of those. See §8.
- **No CORS headers** are sent; browsers block cross-origin reads by default.
- **No `/api/rename`:** renaming is CLI-only (legacy).
- **`thumb_version`:** the thumbnail file's mtime in ns; the UI appends it as `?v=` so thumbnails refresh after a re-match.
- State: a module-level `_project` guarded by `_project_lock`; workers deep-copy the project before use and build new projects locally so the lock is not held during ffmpeg. A saved `project.json` is loaded at start-up (resume).
- Errors: invalid JSON → 400, unexpected exception → 500 `{error}`; worker exceptions end up in `status` with `step: "error"`.

### 2.9 `ui.html`
Single file, vanilla JS, no build step. Three tabs:

1. **Match** — folder picker modal (uses `/api/browse`), Match button, result table (video, audio, Δduration, offset, confidence).
2. **Select & Order** — thumbnail grid (click to toggle), ordered timeline strip with HTML5 drag-and-drop, per-scene trim inputs.
3. **Copy Audio & Push to Resolve** — output folder (prefilled with video dir), *Copy & Rename Audio Files* button, Resolve project/timeline name fields, *Push to Resolve* button.

Polling loop (1 s) calls `loadProject()` **before** `updateStatus()` so result views never render stale data.

### 2.10 `main.py` — CLI

```
python3 main.py                     # same as `app`
python3 main.py [-p FILE] app [--port N]
python3 main.py match   --video DIR --audio DIR [--output DIR]
python3 main.py sync    [--selection JSON | --selection-file FILE] [--output DIR]
python3 main.py sync    --undo [--output DIR]       # remove the audio copies
python3 main.py resolve [--project-name N] [--timeline N] [--fps F] [--list]
python3 main.py rename  [--pattern P] [--dry-run] [--undo rename_log.json]   # legacy
```

`-p/--project` is a global option (before the subcommand). `match` writes `project.json` and thumbnails; `sync` falls back to the selection stored in `project.json`; there is no `select` subcommand (selection happens in the UI).

## 3. Data Flow & Persistence

`project.json` (in the CWD) is rewritten atomically after every mutating step. Shape:

```json
{
  "video_dir": "/…/video",
  "audio_dir": "/…/audio",
  "output_dir": "",
  "matches": [ { "index": 0, "video_path": "…IMG_2231.MOV", "audio_path": "…ZOOM0012.WAV",
                 "video_duration": 182.4, "audio_duration": 183.1, "offset": 0.65,
                 "confidence": 0.996, "thumbnail_path": "/…/thumbnails/thumb_000.jpg",
                 "label": "", "trim_start": 0.0, "trim_end": 0.0 } ],
  "selection": [3, 0, 7],
  "selection_trims": { "0": { "start": 2.0, "end": 12.0 } }
}
```

Thumbnails live in `thumbnails/` next to `project.json`. The only other state is `ambient_audio_copies.json` (copy manifest) in the output folder and, for the legacy rename, `rename_log.json` in the video folder.

## 4. External Dependencies

| Dependency | Min version | Install |
|------------|-------------|---------|
| Python | 3.9 | — (no 3.10-only syntax; developed and tested on 3.9) |
| numpy | 1.24 | `pip install -r requirements.txt` |
| scipy | 1.10 | ″ |
| tqdm | 4.65 | ″ |
| ffmpeg + ffprobe | any recent | `brew install ffmpeg` |
| DaVinci Resolve | 18+ (free or Studio) | optional, for §2.7 / §6 |

## 5. Error Handling

- Missing `ffmpeg`/`ffprobe` → `FileNotFoundError` with an install hint (`brew install ffmpeg`), surfaced in the UI status.
- `ffprobe` failure on a single file → warning, file skipped from matching and listed as skipped.
- No readable video or audio at all → `RuntimeError`.
- Audio extraction failure during the ambiguity check → duration match kept for that row, logged; offset detection failure → offset 0.0 with a warning.
- Destination exists with a different size during copy → warning, file overwritten (recorded in the manifest, so `sync --undo` removes it).
- Legacy rename target collision → `FileExistsError` before any file is touched.
- Missing audio copy when pushing to Resolve → falls back to the original audio path, otherwise the audio is skipped with a warning.
- Resolve unreachable → `ConnectionError` with the setup steps; the copy step is unaffected.
- A second job while one runs → HTTP 409. Bad Host/Origin → 403. Invalid JSON → 400.
- All worker exceptions are caught and reported through `/api/status`; the server never dies on a job failure.

## 6. Planned: In-Resolve Import Script (free edition)

File: `resolve_import.py`, installed to
`/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`.

Behaviour when run from *Workspace → Scripts → resolve_import*:

1. Obtain `resolve = bmd.scriptapp("Resolve")` (available implicitly inside Resolve).
2. Prompt for (or read a fixed path to) `project.json`.
3. Execute the same import/timeline logic as `create_resolve_project`, reusing `build_timeline_plan()` / `apply_plan()` so both entry points stay identical.
4. Report counts and any skipped files in the Fusion console.

Acceptance: running the script on a fresh Resolve project yields V1 with the selected videos in order and A2 with the matching `.WAV`s offset by `offset` seconds, within ±1 frame.

## 7. Testing

Run: `python3 -m unittest discover -s tests -v` (stdlib `unittest`, no extra dependencies, no ffmpeg or Resolve needed).

| File | Covers |
|------|--------|
| `tests/test_matcher.py` | Offset sign and precision (positive/negative shift, loud ambience), correlation score ordering, correlation-adjusted Hungarian re-solve (chained reassignment, partial scores, unequal counts), margin-based confidence, end-to-end pairing |
| `tests/test_resolve_integration.py` | `build_timeline_plan` (back-to-back, trims, positive/negative offset, clamping), project save/load round trip and unknown-key tolerance, `apply_plan` against fake Resolve objects |
| `tests/test_syncer.py` | Byte-identical copies, default output dir, manifest write/merge, `remove_copied_audio` removes exactly the copies, overwrite warning |
| `tests/test_renamer.py` | Collision and duplicate-target abort without renaming, dry run, rename + undo round trip |

Not covered: the HTTP server (Host/Origin checks, job guard), the UI, real ffmpeg extraction and a real Resolve connection; these are verified manually (26-scene shoot, 2026-10: all pairs matched once the correlation tiebreaker was added).

## 8. Security / Privacy

- Binds to `127.0.0.1` only and sends no CORS headers.
- **Host check:** every request must carry `Host: localhost:<port>` or `127.0.0.1:<port>`, otherwise 403. This defeats DNS-rebinding, where a hostile page resolves its own name to 127.0.0.1 and would otherwise read the API.
- **Origin check:** POST requests that carry an `Origin` header must have `http://localhost:<port>` or `http://127.0.0.1:<port>`, otherwise 403, which blocks cross-site form/fetch posts (CSRF). Requests without `Origin` (curl, same-origin navigation) are allowed.
- `/api/browse` lists directories for any path the user can read — acceptable for a single-user local tool, but the server must not be exposed on a LAN interface without adding auth.
- The sync undo only deletes names listed in its manifest and rejects entries containing path components.
