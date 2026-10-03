# Technical Specification — Ambient Video Auto

**Version:** 1.0 — 2026-10-03
Companion to `PRD.md`. Describes the implemented system as it exists in this folder.

---

## 1. Architecture Overview

```
┌──────────────┐   HTTP (localhost:8765)   ┌───────────────────────────────┐
│  ui.html     │ ◄──────────────────────► │  server.py  (http.server)     │
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

Everything runs in one Python process. Long operations (match, sync, resolve) run on daemon threads; the UI polls `/api/status`.

## 2. Module Reference

### 2.1 `config.py`
Constants only. Notable values:

| Name | Value | Purpose |
|------|-------|---------|
| `VIDEO_EXTENSIONS` | `.mp4 .mov .avi .mkv .mxf` | Discovery (case-insensitive) |
| `AUDIO_EXTENSIONS` | `.wav .flac .aiff .aif` | Discovery |
| `MATCH_SAMPLE_RATE` | 16000 | Downsample rate for correlation |
| `MATCH_MAX_DURATION_SEC` | 60 | Only first N seconds extracted for correlation |
| `THUMBNAIL_TIME_SEC` / `_WIDTH` | 3.0 / 480 | Frame grab position and size |
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
    offset: float          # seconds; positive = audio starts later than video
    confidence: float      # 0..1, 1 - |Δduration| / video_duration
    thumbnail_path: str | None = None
    label: str = ""
    trim_start: float = 0.0
    trim_end: float | None = None

@dataclass
class Project:
    video_dir: str
    audio_dir: str
    output_dir: str | None
    matches: list[SceneMatch]
    selection: list[int]               # ordered list of SceneMatch.index
    selection_trims: dict[int, tuple]  # index -> (trim_start, trim_end)

    def save(path=config.PROJECT_FILE) -> None
    @classmethod load(path) -> Project
    def get_ordered_scenes() -> list[SceneMatch]   # applies selection + trims
```

All numeric fields are cast to native `float`/`int` before `json.dump` (numpy scalars are not serialisable).

### 2.3 `matcher.py` — pairing algorithm

**Inputs:** `video_dir`, `audio_dir`, optional `max_duration`.
**Output:** `list[SceneMatch]` sorted by video filename.

Pipeline:

1. **Discover** files via `find_files()` (both lower/upper-case extensions).
2. **Durations** via `ffprobe -show_entries format=duration` for every file (fast, metadata only).
3. **Cost matrix** `C[i][j] = |dur_video_i − dur_audio_j|`.
4. **Global assignment** `scipy.optimize.linear_sum_assignment(C)` → initial 1-to-1 mapping minimising total duration error. Handles unequal counts (extra files unassigned).
5. **Ambiguity detection.** For each video *i* with assigned audio *j\**, collect candidates `{ j : |C[i][j] − C[i][j*]| ≤ 5.0 s }`. If more than one candidate exists:
   - Extract mono 16 kHz PCM for the first 60 s of the video and every candidate (`ffmpeg … -f s16le pipe:1`), cached per file.
   - Compute an amplitude envelope (50 ms RMS windows), L2-normalise, and take the peak of `fftconvolve(env_b, env_a[::-1])` as the similarity score (`_correlation_score`).
   - Choose the highest-scoring candidate. If it is already assigned to another video, **swap** the two assignments so the mapping stays 1-to-1.
   - Log every score and swap to stdout.
6. **Confidence** `= max(0, 1 − Δdur / dur_video)`; status buckets `<10 s OK`, `10–30 s WARN`, `≥30 s BAD`.
7. **Sync offset** per final pair via `_find_offset()` — same envelope cross-correlation, returning `(peak_idx − (len(env_a) − 1)) × 0.05 s`. Reuses cached audio where available.

Complexity: O(V·A) for the cost matrix (trivial), O(V·A³) worst case for Hungarian (fine for ≤ 100 files), plus at most one ffmpeg extraction per file.

### 2.4 `thumbnails.py`
`extract_thumbnail(video_path, out_path)` → `ffmpeg -ss 3 -i … -vframes 1 -vf scale=480:-1`. `extract_all_thumbnails(project)` writes to `thumbnails/` next to `project.json` and fills `SceneMatch.thumbnail_path`.

### 2.5 `syncer.py` — audio copy/rename

```python
def sync_all_scenes(project, output_dir=None) -> list[dict]
```
- `out_dir = output_dir or project.video_dir`.
- For each ordered scene: `new_name = Path(video).stem + Path(audio).suffix`; `shutil.copy2(audio, out_dir / new_name)` unless source == destination.
- Returns `[{order, video, audio_src, audio_dst, offset}, …]`.
- **Never** invokes ffmpeg. Trims are stored only; they are applied in Resolve.

### 2.6 `renamer.py`
Legacy helper that renames *both* files of a pair to `scene_NN.*` and writes `rename_log.json`; `undo_renames()` reverses it. Not used by the default UI flow.

### 2.7 `resolve_integration.py`

```python
def create_resolve_project(project, project_name, timeline_name) -> dict
```
1. `_get_resolve()` imports `DaVinciResolveScript` (needs `PYTHONPATH` to Resolve's `Developer/Scripting/Modules`) and calls `scriptapp("Resolve")`; raises `ConnectionError` with setup instructions on failure.
2. Creates/opens project, gets `MediaPool`, imports each video and its renamed sibling audio (`<video_stem>.WAV` in `video_dir`, falling back to the original path).
3. Creates an empty timeline, appends video clips in selection order on V1 (with `startFrame/endFrame` from trims), then appends audio clips on A2 with `recordFrame` shifted by `round(offset × fps)`.

**Constraint:** external invocation only works on Resolve **Studio**. For the free edition the same logic must be executed from inside Resolve (see §6).

### 2.8 `server.py` — HTTP API

Static: `GET /` → `ui.html`.

| Method | Path | Body / Query | Effect |
|--------|------|--------------|--------|
| GET | `/api/status` | — | `{state, message, progress, error}` of the current background job |
| GET | `/api/project` | — | Full `Project` as JSON |
| GET | `/api/browse` | `?path=` | `{dirs:[…], parent}` for the folder picker |
| GET | `/api/thumbnail/<index>` | — | JPEG bytes |
| POST | `/api/match` | `{video_dir, audio_dir}` | Starts matcher + thumbnail thread |
| POST | `/api/select` | `{selection:[idx…], trims:{idx:[s,e]}}` | Saves order/trims |
| POST | `/api/sync` | `{output_dir?}` | Starts `sync_all_scenes` thread (defaults to video dir) |
| POST | `/api/resolve` | `{project_name, timeline_name}` | Starts `create_resolve_project` thread |
| POST | `/api/rename` | — | Runs `renamer.rename_matches` |

A single module-level `_project` guarded by `threading.Lock`; handlers deep-copy before passing to workers. Errors are captured into `status.error` and surfaced by the UI.

### 2.9 `ui.html`
Single file, vanilla JS, no build step. Three tabs:

1. **Match** — folder picker modal (uses `/api/browse`), Match button, result table (video, audio, Δduration, offset, confidence).
2. **Select & Order** — thumbnail grid (click to toggle), ordered timeline strip with HTML5 drag-and-drop, per-scene trim inputs.
3. **Copy Audio & Push to Resolve** — output folder (prefilled with video dir), *Copy & Rename Audio Files* button, Resolve project/timeline name fields, *Push to Resolve* button.

Polling loop (1 s) calls `loadProject()` **before** `updateStatus()` so result views never render stale data.

### 2.10 `main.py` — CLI

```
python main.py            # same as `app`
python main.py app        # start server, open browser
python main.py match  --video DIR --audio DIR
python main.py rename
python main.py select     # legacy standalone HTML selector
python main.py sync   [--out DIR]
python main.py resolve [--project NAME] [--timeline NAME]
```

## 3. Data Flow & Persistence

`project.json` (in the CWD) is rewritten after every mutating step. Shape:

```json
{
  "video_dir": "/…/video",
  "audio_dir": "/…/audio",
  "output_dir": null,
  "matches": [ { "index": 0, "video_path": "…IMG_2231.MOV", "audio_path": "…ZOOM0012.WAV",
                 "video_duration": 182.4, "audio_duration": 183.1, "offset": 0.65,
                 "confidence": 0.996, "thumbnail_path": "thumbnails/0.jpg",
                 "label": "", "trim_start": 0.0, "trim_end": null } ],
  "selection": [3, 0, 7],
  "selection_trims": { "0": [2.0, 170.0] }
}
```

Thumbnails live in `./thumbnails/`. No other state is kept.

## 4. External Dependencies

| Dependency | Min version | Install |
|------------|-------------|---------|
| Python | 3.10 | — |
| numpy | 1.24 | `pip install -r requirements.txt` |
| scipy | 1.10 | ″ |
| tqdm | 4.65 | ″ |
| ffmpeg + ffprobe | any recent | `brew install ffmpeg` |
| DaVinci Resolve | 18+ (free or Studio) | optional, for §2.7 / §6 |

## 5. Error Handling

- Missing `ffmpeg` → `FileNotFoundError` surfaced in UI status with install hint.
- `ffprobe` failure on a single file → warning, file skipped from matching.
- Audio extraction failure during ambiguity check → duration match kept, logged.
- Resolve unreachable → `ConnectionError` with the three setup steps; copy step is unaffected.
- All worker exceptions are caught and reported through `/api/status`; the server never dies on a job failure.

## 6. Planned: In-Resolve Import Script (free edition)

File: `resolve_import.py`, installed to
`/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`.

Behaviour when run from *Workspace → Scripts → resolve_import*:

1. Obtain `resolve = bmd.scriptapp("Resolve")` (available implicitly inside Resolve).
2. Prompt for (or read a fixed path to) `project.json`.
3. Execute the same import/timeline logic as `create_resolve_project`, reusing a shared pure function so both entry points stay identical.
4. Report counts and any skipped files in the Fusion console.

Acceptance: running the script on a fresh Resolve project yields V1 with the selected videos in order and A2 with the matching `.WAV`s offset by `offset` seconds, within ±1 frame.

## 7. Testing Notes

- Manual verification on a 26-scene shoot (2026-10): all pairs matched after the correlation tiebreaker was added; previous duration-only pass mis-paired clips with equal lengths.
- Unit-test candidates (not yet written): `_correlation_score` on synthetic envelopes with a known shift; `linear_sum_assignment` swap logic with a 3×3 synthetic cost matrix; `sync_all_scenes` on temp dirs asserting no re-encode (byte-identical copies).

## 8. Security / Privacy

Binds to `127.0.0.1` only. `/api/browse` returns directory listings for any path the user can read — acceptable for a single-user local tool, but the server must not be exposed on a LAN interface without adding auth.
