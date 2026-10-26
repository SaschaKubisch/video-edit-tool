# Technical Specification - Ambient Video Auto

**Version:** 2.0 (draft for the owner's review)
**Date:** 2026-10-26
Companion to `PRD.md`. Sections 1 to 17 specify the planned editor. Appendix A describes the system built as v1.1.0, which the plan reuses. Appendix B lists the sources.

Nothing in sections 1 to 17 is built. Each interface named here (commands, endpoints, file names, module names) is a planned interface. Names may change when the code is written.

---

## Terms

The terms of `PRD.md` apply (clip, scene, segment, film, timeline file, offset, working copy, ProRes, LUT, LUFS, and the others). These terms are added.

- **Frame number**: the integer position of a frame in a working copy, counted from 0. A segment's `in` is the first frame it uses. Its `out` is the first frame it does not use.
- **Film time**: seconds from the first frame of the film. **Source time**: seconds from the first frame of a clip.
- **Frame rate**: written as a ratio of two integers, for example 25/1, so 24000/1001 is exact.
- **Handle**: audio that lies outside a segment's `in` and `out` and is used only to build a crossfade.
- **Join**: the place where one segment ends and the next begins.
- **Steady run**: a stretch of a clip in which the camera does not move.
- **Phase correlation**: a way to measure how far one picture is shifted against another, using the Fourier transform.
- **Contact sheet**: one image that holds a grid of small stills, each labelled with its time.
- **Anchor**: the thing a time in the timeline file is counted from: the film or one segment.
- **Cue**: one subtitle: a text with a start and an end.
- **ASS**: a subtitle file format with fonts, colours and positions. libass is the library that draws it. **WebVTT**: the subtitle format browsers play.
- **VideoToolbox**: Apple's hardware video encoder, reached through ffmpeg.
- **ebur128**: the ffmpeg filter that measures loudness by the EBU R128 rules.
- **Hash**: a sha256 checksum of a file's bytes.
- **Snapshot**: a numbered copy of the timeline file kept in the history folder.
- **Work directory**: the folder on the SSD where the tool writes everything it makes.
- **Sidecar**: a small file beside a `.braw` that holds settings made in a Blackmagic tool (for example white balance).

Tags: **[measured]** checked on the owner's machine or files on 2026-10-26; **[documented]** a source states it (Appendix B, written Src1, Src2 and so on); **[inference]** reasoning, not checked; **[unverified]** a design statement that a check in this document settles. **Decision** means an owner decision from `PRD.md` section 3. **Starting value** is a number chosen to begin with. It is not a decision, and the step that uses it calibrates it on real clips.

---

## 1. Overview

Version 1.1.0 matches camera clips with recorder files and prepares them for Resolve. The plan adds six steps. They turn the matched pairs into a finished film inside the tool.

1. **Conversion test.** Each `.braw` clip becomes a 10-bit ProRes working copy on the SSD.
2. **Timeline file and assembly.** The tool finds the clap and the steady scenes, writes the timeline file and renders a preview.
3. **Look and sound.** One LUT, levelled loudness, the 4K export and the Shotcut project.
4. **Words.** English subtitles and titles.
5. **Prompt edits.** Claude Code changes the timeline file from a description.
6. **Vertical shorts.** A 9:16 short from the film.

The timeline file is the centre. Every step reads it or writes it. Every output (preview, film, Shotcut project, short) is made from it.

## 2. Architecture

### 2.1 Data flow

```
 ORIGINALS (read only)          THE TOOL                         WORK DIRECTORY (SSD)
 .braw  .WAV
   |                  +-------------------+
   +----------------> | matcher.py        | --> project.json (pairs, offsets, work_dir)
   |                  +-------------------+
   |                  +-------------------+
   +----------------> | convert.py        | --> working/<stem>.mov, conversion.json
                      +-------------------+
                      +-------------------+
 working copies, WAV  | clap.py stable.py | --> analysis/<stem>/clap.json, stable.json
 ------------------>  | words.py index.py | --> transcript.json, contact sheets, index.md
                      +-------------------+
                      +-------------------+
 project.json,        | assemble.py       | --> timeline.json (next to project.json)
 analysis/*  -------> +-------------------+            ^
                                                       | edits
            page (trim operations) / Claude Code (file edits) / tool commands
                                                       |
                      +-------------------+            v
 timeline.json -----> | render.py         | --> previews/, renders/*.mp4, *.srt, *.ass
                      | export_shotcut.py | --> film.mlt, audio-synced/
                      | shorts.py         | --> short-<name>.json (a second timeline file)
                      +-------------------+
```

### 2.2 Where files live

Originals stay where they are. The tool opens them read-only and never writes into their folders (PRD M9).

```
<folder with project.json>            the tool's working folder (the repository checkout)
  project.json                        pairs, offsets, folders, work_dir     (git-ignored)
  timeline.json                       the film                              (git-ignored)
  short-<name>.json                   a vertical short                      (git-ignored)
  timeline.history/NNNN.json          numbered snapshots of timeline files  (git-ignored)

<work_dir>                            on the SSD (the path is D1)
  conversion.json                     one entry per clip: status, decoder, metadata, speed, size
  working/<stem>.mov                  ProRes working copy (while written: <stem>.mov.partial)
  audio-synced/<stem>.wav             WAV with the offset applied, made for the Shotcut export
  analysis/<stem>/clap.json           clap position in the camera's scratch audio and in the WAV
  analysis/<stem>/stable.json         steady runs and movement
  analysis/<stem>/transcript.json     English transcript with times
  analysis/<stem>/sheets/*.jpg        contact sheets
  analysis/index.md                   text index for Claude Code
  stills/                             LUT comparison sheets and frames asked for by the commands
  previews/seg-<hash>.mp4             preview cache, one file per segment
  previews/preview.mp4                the assembled preview
  renders/<name>.mp4, .srt, .ass      final files (the export names are set in the timeline file)
  reports/assembly-report.json        what assembly decided, per clip
  reports/render-report.json          what render shortened, capped or found missing
  film.mlt                            Shotcut project
```

`project.json`, the timeline files and the history folder hold paths from the owner's machine, so `.gitignore` lists them. `project.json` is already listed. The other three entries are added when step 2 is built.

### 2.3 Modules

"Kept" modules exist in v1.1.0 (Appendix A). Section 12 lists what changes in them.

| Module | Job | Status | Step |
|--------|-----|--------|------|
| `matcher.py` | Pair clips with audio, find offsets | Kept | - |
| `project.py` | `Project`, `SceneMatch`, `project.json` | Kept, one new field (`work_dir`) | 1 |
| `tools.py` | Find `ffmpeg-full`, `ffprobe`, VapourSynth; check filters | New | 1 |
| `convert.py` | Probe, convert, verify, `conversion.json` | New | 1 |
| `clap.py` | Find the clap, measure drift | New | 2 |
| `stable.py` | Find steady runs | New | 2 |
| `timeline.py` | Load, save, validate, snapshot, hash, derived values | New | 2 |
| `assemble.py` | Build `timeline.json` from matches and analysis | New | 2 |
| `render.py` | Render plan, picture path, sound path, preview, export | New | 2, 3 |
| `look.py` | LUT hashing, comparison stills | New | 3 |
| `loudness.py` | Measure and level | New | 3 |
| `export_shotcut.py` | Write `film.mlt` and `audio-synced/` | New | 3 |
| `words.py` | Transcribe, build cues, titles, ASS and SRT | New | 4 |
| `index.py` | Contact sheets and `index.md` | New | 5 |
| `shorts.py` | Build a vertical timeline | New | 6 |
| `thumbnails.py` | Frame grabs | Kept, reads working copies | 1 |
| `server.py`, `ui.html`, `main.py`, `config.py` | Page, API, commands, constants | Kept, extended | 1 to 6 |
| `syncer.py`, `resolve_integration.py` | Audio copy, Resolve push | Kept only if D4 says so | - |
| `renamer.py` | Legacy rename | Kept, untouched | - |

The code stays flat in the repository folder, as today.

### 2.4 Programs

| Program | Used for | How the tool finds it |
|---------|----------|-----------------------|
| `ffmpeg-full` 9.0.2 (GPL, keg-only) | All new decode, render, encode, loudness measurement, subtitle burn-in, Whisper | Environment variable `VET_FFMPEG`, else `$(brew --prefix ffmpeg-full)/bin/ffmpeg`. It is not on `PATH` because it is keg-only [documented, Src3]. |
| `ffprobe` of `ffmpeg-full` | Stream facts | The same folder. |
| Existing `ffmpeg` and `ffprobe` on `PATH` | The unchanged matching code | `PATH`, as today. |
| VapourSynth R81 with `vapoursynth-brawsource` 0.5.0 | Decoding BRAW (decoder 1) | Python import in the tool's environment. |
| Blackmagic RAW SDK | Decoding BRAW (decoder 2, only if step 1 needs it) | Not written yet. |
| Shotcut | Hand work; opening `film.mlt` | `/Applications/Shotcut.app`. |

A command `python3 main.py tools check` prints each program, its version and whether the needed filters exist: `lut3d`, `subtitles`, `ebur128`, `alimiter`, `whisper`, `crop`, `scale`, and the encoders `libx264`, `prores_videotoolbox`, `prores_ks`. [unverified until `ffmpeg-full` is installed]

### 2.5 Processes

The server keeps the one-job-at-a-time rule of v1.1: convert, assemble, render, transcribe and index share one job slot, and a second request gets HTTP 409. Edits to the timeline file are not jobs. They finish at once.

Conversion runs as a separate process started by the server. It can run in another Python environment (D3) and does not depend on the server's threads. The page polls its status.

### 2.6 Conventions

- Times in seconds are decimal numbers. Positions in a clip are integer frame numbers.
- `offset = t_audio - t_video` everywhere, as in v1.1 (Appendix A.2).
- All paths in `project.json`, `conversion.json` and timeline files are absolute.
- JSON is UTF-8, indented by two spaces, keys in the order of this document.
- Every file the tool writes is written to a temporary file in the same folder and then renamed. A crash never leaves a half-written file.
- A file is called "changed" when its sha256 differs.

---

## 3. The Timeline File (version 1)

### 3.1 What it is

One JSON file describes one film or one short. It lives next to `project.json` and is named `timeline.json`. A short is a second file of the same format, named `short-<name>.json`. A file holds everything needed to render, except the media files it points to.

The file stores choices and positions. It does not store anything that can be computed from them: film start times, film length and loudness gains are derived (section 3.4).

### 3.2 Fields

Types: `int` integer, `num` decimal number, `str` string, `bool` true or false. "Req" means required. A key that starts with `x_` is free: the tool keeps it and ignores it, so people and agents can leave notes.

**Top level**

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `version` | int | yes | Format version. This document defines 1. |
| `kind` | str | yes | `film` or `short`. |
| `film` | object | yes | Frame size, frame rate, film fades, default join. |
| `sources` | object | yes | One entry per clip used. Key: source id. Written by the tool (section 4.2). |
| `segments` | array | yes | The pieces of the film, in film order. |
| `look` | object | no | Base conversion and the LUT. Absent: no colour work. |
| `audio` | object | yes | Sample rate and loudness. |
| `titles` | array | no | Title texts. |
| `subtitles` | object | no | Subtitle cues and style. |
| `exports` | array | no | Output files to make. Needed to render the final film. |
| `derived_from` | object | short only | Which film file and which settings made this short. |

**`film`**

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `title` | str | no | Name for people. |
| `fps` | `{num, den}` | yes | Film frame rate as a ratio, for example 25/1. |
| `frame` | `{width, height}` | yes | Output picture size in pixels. Both even. |
| `crop_mode` | str | no | `fit_height` (default): cut a window of the output's shape at full source height and scale it to `frame`. `pixel`: cut a window of exactly `frame` pixels and do not scale (D5). |
| `fade_in_s` | num | no | Fade from black and from silence at the film's start. Starting value 1.0. |
| `fade_out_s` | num | no | Fade to black and to silence at the film's end. Starting value 2.0. |
| `audio_join` | `{duration_s, curve}` | no | Default crossfade at every join. Starting value 0.5 s, curve `equal_power`. |

**`sources[<id>]`** (the id is the clip's file name without extension)

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `original_video` | str | yes | The `.braw` file. Read for scratch audio only. |
| `working_copy` | str | yes | The ProRes file. Picture comes from here. |
| `audio` | str | yes | The original H2N WAV. Sound comes from here. |
| `audio_offset_s` | num | yes | Offset from matching: t_audio - t_video. |
| `audio_drift_ppm` | num | no | Clock drift between recorder and camera in parts per million. Default 0. Set only after step 2 measures it (section 7.3). |
| `frames` | int | yes | Frame count of the working copy. |
| `fps` | `{num, den}` | yes | Must equal `film.fps` (rule V4). |
| `size` | `{width, height}` | yes | Frame size of the working copy. |
| `colour_state` | str | yes | `film` (Blackmagic Film as recorded) or `rec709`. Copied from `conversion.json`. |

**`segments[]`**

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `id` | str | yes | `s` followed by digits. Never reused, never changed. |
| `source` | str | yes | A key of `sources`. |
| `in` | int | yes | First frame used. |
| `out` | int | yes | First frame not used. |
| `enabled` | bool | no | Default true. False: kept in the file, left out of the film. |
| `label` | str | no | Short name for people, for example "frying pan, close". |
| `origin` | str | no | `assembly`, `manual`, `prompt` or `short`. How the segment came to be. |
| `note` | str | no | Longer remark, for example "starts 0.5 s after the clap". |
| `crop` | object | no | `{"x": 0..1}` or `{"x_from": 0..1, "x_to": 0..1}`. Horizontal place of the window. 0 is the left edge, 1 the right edge, 0.5 the centre (default). With `x_from` and `x_to` the window moves slowly from one to the other over the segment. |
| `audio_join` | `{duration_s, curve}` | no | Crossfade into this segment from the one before. Overrides `film.audio_join`. |
| `gain_db_adjust` | num | no | Extra gain after loudness levelling, in dB. Default 0. |
| `priority` | str | no | `normal` (default), `must` or `drop`. Read by the short builder only (section 11). |

**`look`**

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `base` | `{mode, path, sha256}` | yes | `mode` is `lut` (apply the `.cube` at `path` first), `baked` (the working copies already hold Rec.709) or `none`. |
| `lut` | `{path, sha256, interp}` | no | The film's one creative LUT. `interp` must be `tetrahedral` (decision). |

**`audio`**

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `sample_rate` | int | yes | Mix rate. 48000. |
| `loudness.mode` | str | yes | `off` (no levelling; used by the rough film of step 2 and by tests), `segment_then_film` (level every segment, then the film) or `film_only`. |
| `loudness.target_lufs` | num | unless mode is `off` | Target integrated loudness. **No default.** D9. |
| `loudness.true_peak_dbtp` | num | no | Limit. Default -1.0. |
| `loudness.max_gain_db` | num | no | Largest gain levelling may add to a quiet segment. Starting value 12. |

**`titles[]`**

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `id` | str | yes | `t` followed by digits. |
| `text` | str | yes | The title. |
| `anchor` | str | yes | `film` or a segment id. |
| `start_s`, `end_s` | num | yes | Seconds from the start of the anchor. |
| `template` | str | no | `center`, `lower_third` or `opening`. Default `center`. |
| `fade_s` | num | no | Fade in and out of the title. Default 0.5. |

**`subtitles`**

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `language` | str | yes | `en`. |
| `mode` | str | yes | `burn`, `sidecar` or `both`. D13. |
| `style` | object | no | `font` (str), `size_pct` (size as percent of the frame height), `outline_px`, `max_chars_per_line`. |
| `cues[]` | array | yes | Each cue: `id` (`c` and digits), `anchor` (a segment id), `start_s`, `end_s` (seconds from the start of that segment), `text`. |

**`exports[]`**

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `name` | str | yes | Unique name. |
| `output` | str | yes | Path of the MP4. Never inside a folder that holds originals. |
| `width`, `height` | int | yes | Output size. Same shape as `film.frame` (rule V5). |
| `video` | object | yes | `codec` (`libx264`), `rate_control` (`{"mode":"crf","crf":18}` or `{"mode":"bitrate","mbps":40}`), `preset`. Starting values are in section 5.6. |
| `audio` | object | yes | `codec` (`aac`), `bitrate_kbps`, `sample_rate`. |

**`derived_from`** (short only): `film` (path of the film file), `target_length_s`, `settings` (the short builder's settings, kept so the short can be built again).

### 3.3 Example

The values are an illustration. The frame rate, sizes and offsets are taken from the shapes of the owner's clips; the paths, hashes, LUT names and settings are made up. `mode: both` in `subtitles` and `-14.0` as target are examples of open decisions D13 and D9, not choices.

```json
{
  "version": 1,
  "kind": "film",
  "film": {
    "title": "Recordings 2026-10-03",
    "fps": {"num": 25, "den": 1},
    "frame": {"width": 3840, "height": 2160},
    "crop_mode": "fit_height",
    "fade_in_s": 1.0,
    "fade_out_s": 2.0,
    "audio_join": {"duration_s": 0.5, "curve": "equal_power"}
  },
  "sources": {
    "A002_01011540_C002": {
      "original_video": "/Volumes/CARD/Recordings/A002_01011540_C002.braw",
      "working_copy": "/Volumes/SSD/film/working/A002_01011540_C002.mov",
      "audio": "/Volumes/CARD/Recordings/ZOOM0001.WAV",
      "audio_offset_s": 0.276,
      "audio_drift_ppm": 0,
      "frames": 2432,
      "fps": {"num": 25, "den": 1},
      "size": {"width": 4128, "height": 2176},
      "colour_state": "film"
    },
    "A002_01011542_C003": {
      "original_video": "/Volumes/CARD/Recordings/A002_01011542_C003.braw",
      "working_copy": "/Volumes/SSD/film/working/A002_01011542_C003.mov",
      "audio": "/Volumes/CARD/Recordings/ZOOM0002.WAV",
      "audio_offset_s": 0.248,
      "audio_drift_ppm": 0,
      "frames": 672,
      "fps": {"num": 25, "den": 1},
      "size": {"width": 4128, "height": 2176},
      "colour_state": "film"
    }
  },
  "segments": [
    {"id": "s001", "source": "A002_01011540_C002", "in": 38, "out": 900,
     "label": "stove, wide", "origin": "assembly",
     "note": "starts 0.5 s after the clap", "crop": {"x": 0.5}},
    {"id": "s002", "source": "A002_01011540_C002", "in": 1320, "out": 2380,
     "label": "stove, wide, after the camera was moved", "origin": "assembly"},
    {"id": "s003", "source": "A002_01011542_C003", "in": 30, "out": 650,
     "label": "pan with sausage", "origin": "prompt",
     "audio_join": {"duration_s": 0.25, "curve": "linear"}, "gain_db_adjust": -2.0}
  ],
  "look": {
    "base": {"mode": "lut",
             "path": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/Blackmagic Pocket 4K Film to Video v4.cube",
             "sha256": "0000000000000000000000000000000000000000000000000000000000000000"},
    "lut": {"path": "/Volumes/SSD/luts/warm-soft.cube",
            "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
            "interp": "tetrahedral"}
  },
  "audio": {
    "sample_rate": 48000,
    "loudness": {"mode": "segment_then_film", "target_lufs": -14.0,
                 "true_peak_dbtp": -1.0, "max_gain_db": 12.0}
  },
  "titles": [
    {"id": "t001", "text": "Sunday kitchen", "anchor": "film",
     "start_s": 1.0, "end_s": 5.0, "template": "center", "fade_s": 0.5}
  ],
  "subtitles": {
    "language": "en",
    "mode": "both",
    "style": {"font": "Helvetica Neue", "size_pct": 4.5, "outline_px": 3, "max_chars_per_line": 42},
    "cues": [
      {"id": "c001", "anchor": "s001", "start_s": 3.2, "end_s": 6.0,
       "text": "Now the pan is hot enough."}
    ]
  },
  "exports": [
    {"name": "film-4k", "output": "/Volumes/SSD/film/renders/film-4k.mp4",
     "width": 3840, "height": 2160,
     "video": {"codec": "libx264", "rate_control": {"mode": "crf", "crf": 18}, "preset": "slow"},
     "audio": {"codec": "aac", "bitrate_kbps": 384, "sample_rate": 48000}}
  ]
}
```

### 3.4 Derived values (computed, never stored)

- **Film order.** The enabled segments in the order of the `segments` array.
- **Segment length** in frames: `out - in`.
- **Film start frame** of a segment: the sum of the lengths of the enabled segments before it. The film length is the sum of all lengths. A crossfade does not shorten the film, because the picture cuts at the join and only the sound overlaps (section 5.4).
- **Film sample position** of a frame: `round(frame * sample_rate * den / num)`. Each segment's sample length is the difference of two such positions, so rounding never adds up.
- **Source audio position** of a segment: `round((in * den / num + audio_offset_s) * sample_rate)`, with the drift correction of section 7.3 if `audio_drift_ppm` is not 0.
- **Crop window** of a segment (section 5.2).

### 3.5 Validation rules

`timeline.py` checks these rules (V1 to V22). Errors stop the tool. Warnings are listed and do not stop it. "File checks" read the disk and can be switched off for tests.

| Rule | Check | Level |
|------|-------|-------|
| V1 | `version` is a version the tool knows. | error |
| V2 | No unknown key in a known object, unless it starts with `x_`. The message names the closest known key. | error |
| V3 | `kind` is `film` or `short`. `derived_from` exists exactly when `kind` is `short`. | error |
| V4 | `fps` values are positive integers. Every source's `fps` equals `film.fps`. | error |
| V5 | Frame sizes are even positive integers. Each export has the same shape as `film.frame`, within one pixel of rounding. | error |
| V6 | Source ids match `[A-Za-z0-9_-]+` and are unique. Segment ids match `s[0-9]+`, title ids `t[0-9]+`, cue ids `c[0-9]+`, and are unique in their list. | error |
| V7 | File checks: each `working_copy` and `audio` exists, and `frames` equals the working copy's frame count. | error |
| V8 | Every segment names an existing source. | error |
| V9 | `0 <= in < out <= sources[source].frames`. | error |
| V10 | At least one enabled segment. | error |
| V11 | Each enabled segment is long enough for its fades and joins: its length in seconds is at least half the duration of the join before it plus half the duration of the join after it. For the first segment the film fade-in replaces the half join before it, and the film fade-out replaces the half join after the last segment. The film fades count in full. | error |
| V12 | Join durations lie between 0 and 5 s. The curve is `equal_power` or `linear`. An `audio_join` on the first enabled segment is ignored. | error, warning for the ignored one |
| V13 | `crop` uses `x` alone or `x_from` with `x_to`; values lie between 0 and 1. | error |
| V14 | `gain_db_adjust` lies between -30 and 30. | error |
| V15 | Every anchor is `film` or an existing segment id. `start_s < end_s`. A cue or title interval that reaches outside its segment is a warning, and the part outside is not shown. | error, warning |
| V16 | File checks: the `sha256` of each LUT equals the file's hash. `look.lut.interp` is `tetrahedral`. | error |
| V17 | `audio.loudness.mode` is `off`, `segment_then_film` or `film_only`. Unless it is `off`, `target_lufs` is present and between -40 and -5. `true_peak_dbtp` is at most 0. `max_gain_db` lies between 0 and 30. | error |
| V18 | Export names are unique. No export `output` lies inside a folder that holds an original (any parent folder of a `.braw` or WAV used). | error |
| V19 | `subtitles.mode` is `burn`, `sidecar` or `both`. Cue text is not empty. A cue with more than two lines or more than `max_chars_per_line` per line is a warning. | error, warning |
| V20 | For a short: `derived_from.film` exists (file check). | error |
| V21 | In version 1, export video codec is `libx264` and export audio codec is `aac`; the container is MP4. | error |
| V22 | A short is not longer than 180 seconds. | warning |

**Output of `python3 main.py timeline validate`.** Exit code 0 when there are no errors, else 1. Standard output is a JSON list, one object per finding:

```json
[{"level": "error", "rule": "V9", "path": "segments[1].out",
  "message": "out 2500 is greater than the 2432 frames of source A002_01011540_C002",
  "hint": "use a value of 2432 or less"}]
```

### 3.6 Versions

The file has an integer `version`. The tool refuses a file with a higher version than it knows and says which tool version to update. When version 2 exists, a command `timeline migrate` will convert a version 1 file and keep the old file as a snapshot. Until then, there is no migration.

---

## 4. Editing the Timeline File

Three writers change the file: the tool's own commands, the page, and Claude Code. This section sets the rules they share.

### 4.1 Rules for every writer

1. **One source of truth.** The file on disk is the film. The page and the tool read it on every request. Nothing about the film lives only in memory.
2. **Atomic saves.** A write goes to a temporary file in the same folder and is renamed over the old file.
3. **Snapshots.** Before the tool replaces the file, it copies the old file to `timeline.history/NNNN.json` (numbers rising, 200 kept). A successful `timeline validate` also snapshots the file when it differs from the newest snapshot, so the last valid version is always kept even after a direct edit.
4. **Hash check.** The page sends the hash of the version it started from. If the file on disk has another hash, the server answers 409 and changes nothing. The page then reloads. Claude Code has no lock: it re-reads the file before editing and changes it in place with small edits, not by rewriting the whole file from memory.
5. **Validation.** Every tool-driven write is validated first and refused with the findings if it has errors. A direct edit is validated when the tool next reads the file; `render` and `preview` refuse an invalid file.

### 4.2 What each writer may change

| Part of the file | Tool commands | Page | Claude Code |
|------------------|---------------|------|-------------|
| `sources` | `assemble`, `convert` write it | no | no. Re-run `assemble` instead. |
| `segments`: `in`, `out`, `enabled`, order | yes | yes | yes |
| `segments`: `label`, `note`, `crop`, `audio_join`, `gain_db_adjust`, `priority` | yes | `label` only | yes |
| `segments`: new segment | yes | no | yes. New id: `s` plus the highest number so far plus one. |
| `segments`: `id` of an existing segment | never | never | never |
| `film`, `audio`, `exports` | `look`, `render` commands | no | yes |
| `look` | `look set` | no | by running `look set`, not by hand (the command writes the hash) |
| `titles`, `subtitles.cues` | yes | no | yes |
| `version`, `kind` | never | never | never |

### 4.3 How Claude Code works with the file

The repository will carry a `CLAUDE.md` section (written in step 2) that states these rules. The loop is:

1. **Look.** `python3 main.py timeline summary` prints the film as text. Example:

   ```
   timeline.json  film  25 fps  3840x2160  hash 9f2c1a47  3 segments, 3 enabled, 101.7 s
   id    film time               len     source               frames        label
   s001  00:00:00.00-00:00:34.48  34.5 s  A002_01011540_C002   38-900        stove, wide
   s002  00:00:34.48-00:01:16.88  42.4 s  A002_01011540_C002   1320-2380     stove, wide, after the camera was moved
   s003  00:01:16.88-00:01:41.68  24.8 s  A002_01011542_C003   30-650        pan with sausage
   ```

2. **Find.** In step 5, the index (section 10) tells which clip and time hold the scene.
3. **Edit.** Edit `timeline.json` directly. To remove a scene, set `"enabled": false`. To keep the cut for later, add a `note`.
4. **Check.** `python3 main.py timeline validate`. Exit 0 means the file is valid. Exit 1 prints the findings with the field path.
5. **See.** `python3 main.py timeline frames --film-s 12.0,48.5` writes one PNG per time into `stills/` and prints the paths. Claude Code opens them. `python3 main.py preview --range 60:90` renders only that part of the preview.
6. **Undo.** `python3 main.py timeline undo` restores the previous snapshot. `timeline history` lists the snapshots with time and a one-line diff.

### 4.4 Operations the page may send

The page does not send whole files. It sends a list of operations with the hash it started from, to `POST /api/timeline/edit`:

| Operation | Fields |
|-----------|--------|
| `trim_in` | `segment`, `frames` (positive moves the start later) |
| `trim_out` | `segment`, `frames` (positive moves the end later) |
| `set_enabled` | `segment`, `enabled` |
| `move` | `segment`, `to_index` |
| `set_label` | `segment`, `label` |

The server applies the operations in memory, validates, saves a snapshot and the new file, and answers with the new hash and the summary. Findings give HTTP 422 with the list of section 3.5. A stale hash gives 409. An unknown operation gives 400.

---

## 5. Rendering and Export (used by steps 2 to 6)

### 5.1 The render plan

`render.py` first turns a valid timeline file into a **render plan**. This is a pure function: it reads no media and starts no program, so tests can run it with no files. The plan lists, for every enabled segment:

- the film start frame and length (section 3.4),
- the source frames to read and the crop window (section 5.2),
- the audio span, the handles and the fades (section 5.4),
- warnings, for example "audio does not cover the segment".

Every render (preview, final film, Shotcut export, short) starts from the plan.

### 5.2 Crop window

The output picture has the shape of `film.frame`. For a source of `sw` x `sh` pixels and an output of `fw` x `fh`:

- `fit_height` (default): if `sw/sh >= fw/fh`, the window is `ww = round_even(sh * fw / fh)` wide and `wh = sh` high. Otherwise the window is `sw` wide and `wh = round_even(sw * fh / fw)` high. The window is then scaled to `fw` x `fh`.
- `pixel`: the window is exactly `fw` x `fh` and is not scaled. It must fit in the source.
- Position: `X = round_even((sw - ww) * x)` where `x` is the segment's `crop.x` (default 0.5), and `Y = round_even((sh - wh) / 2)`.
- With `x_from` and `x_to` the window moves in a straight line from the first to the second position over the segment's frames.

For the one clip checked, 4128 x 2176 [measured], the windows are:

| Output | Mode | Window | Scale |
|--------|------|--------|-------|
| 3840 x 2160 (16:9) | `fit_height` | 3868 x 2176 | 0.993, a slight reduction |
| 3840 x 2160 (16:9) | `pixel` | 3840 x 2160 | none; 144 pixels lost on each side, 8 at top and bottom |
| 1080 x 1920 (9:16) | `fit_height` | 1224 x 2176 | 0.882 |

The `crop` filter of ffmpeg evaluates `x` and `y` for every frame, so a moving window needs no extra tool [measured: a synthetic test moved the window and only the moving interval changed].

### 5.3 Picture path of the final film

The final film is rendered from the working copies, never from a preview.

For each enabled segment, in film order, one decoder process reads the segment and writes raw frames to a pipe:

```
ffmpeg -v error -ss <in/fps> -i working/<stem>.mov -frames:v <length>
       -vf "crop=<ww>:<wh>:<X>:<Y>,scale=<fw>:<fh>:flags=lanczos,<base LUT>,<creative LUT>,format=yuv420p10le"
       -f rawvideo -pix_fmt yuv420p10le -
```

One encoder process reads the pipe for the whole film:

```
ffmpeg -f rawvideo -pix_fmt yuv420p10le -s <fw>x<fh> -r <fps> -i -   -i mix.wav
       -vf "fade=t=in:st=0:d=<fade_in>,fade=t=out:st=<film_s - fade_out>:d=<fade_out>,subtitles=film.ass"
       -map 0:v -map 1:a -c:v libx264 -preset <preset> -crf <crf> -pix_fmt yuv420p
       -color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv
       -c:a aac -b:a 384k -movflags +faststart  film-4k.mp4.partial
```

Why this shape [inference]:

- A single filter graph with one input per segment keeps one 4K decoder open per input. The pipe keeps one decoder open at a time.
- Fades and subtitles are applied once, in film time, by the encoder process. The segment processes know nothing about film time.
- The LUT runs on the 10-bit picture, before the conversion to 8 bit.

Details that this section does not settle:

- `-ss` before `-i` on an all-intra ProRes file returned the exact frame in a test on a synthetic file made with `prores_ks` [measured: frames 100 and 101 were identical to the frames selected by number]. The test on a real working copy is check 2.11.
- The pipe carries about 25 MB per frame at 10 bit and 4K [arithmetic]. Whether the pipe limits the speed is [unverified] until check 3.8.
- `lut3d` works on RGB; ffmpeg converts the picture around it. Which matrix it uses and whether 10-bit precision survives is [unverified] until check 3.1.
- Whether the conversion to 8 bit at the end shows banding in smooth skies or walls is [unverified]. Check 3.8 includes a visual look at a gradient scene.

The encoder writes to `<name>.mp4.partial`. When it ends, the tool checks the file (section 5.7) and then renames it.

### 5.4 Sound path

The sound is built in `numpy`, not in an ffmpeg filter graph [Design]. This makes every rule below a pure function that tests can check on arrays.

1. **Decode each WAV once.** `ffmpeg` decodes the whole WAV to 32-bit float stereo at the mix rate (48000 Hz). The H2N files checked are 96 kHz [measured], so this resamples. The longest clip of the first shoot is 271 s, which is about 104 MB as float stereo at 48 kHz [arithmetic]. The array is kept while the segments of that clip are processed.
2. **Place each segment.** The source start is `s0 = round((in*den/num + audio_offset_s) * rate)`. The segment's sample length `n` is from section 3.4. The core span is the samples `[s0, s0+n)`.
   - If `s0 < 0` the segment starts with `-s0` samples of silence. If the span ends after the file, the rest is silence. Each case is a warning with the number of seconds. (The first shoot has offsets from -5.03 to +1.94 s [measured].)
   - This is the same rule as `build_timeline_plan` of v1.1: a positive offset advances the audio in-point, a negative offset delays the audio and clamps at the file's start (Appendix A.7). The tests of that function are ported (check 2.8).
3. **Handles and joins.** At a join of duration `d` the picture cuts at the film frame `f`. The sound of the segment before is extended by `d/2` after its end, and the sound of the segment after is extended by `d/2` before its start. Both come from the WAV files, so the handle may reach into material that the edit cut out. Over the interval `[f - d/2, f + d/2]` the gain of the earlier segment falls and the gain of the later one rises:
   - `equal_power`: falling `cos(pi/2 * x)`, rising `sin(pi/2 * x)`, with `x` from 0 to 1. The squares sum to 1, so the power stays constant for sound that does not correlate (ambience) [inference].
   - `linear`: falling `1 - x`, rising `x`. The amplitudes sum to 1, which suits two parts of the same continuous sound [inference].
   - If a handle is longer than the WAV allows, the crossfade is shortened to what exists, and the render report says so.
4. **Film fades.** The first segment rises from silence over `fade_in_s`, with the `sin` curve. The last segment falls to silence over `fade_out_s`, with the `cos` curve. Picture fades are made by `fade` in the encoder process (section 5.3) over the same frames.
5. **Level each segment** (mode `segment_then_film`). Measure the integrated loudness `L` of the core span with `ebur128` (section 8.4). The gain is `min(target - L, max_gain_db) + gain_db_adjust`. A segment with no sound above the absolute gate (-70 LUFS) gets gain 0 and a warning. Mode `film_only` skips this step.
6. **Mix.** Add the windowed, gained segments into one float buffer for the film.
7. **Level the film.** Measure integrated loudness and true peak of the buffer. Apply one gain so the loudness equals the target. If the true peak is then above the limit, apply `alimiter` with a ceiling 0.5 dB under the limit, measure again, and lower the ceiling by 0.5 dB until the limit holds or three tries are used. The final numbers go into the render report; a result more than 0.5 LU from the target is a warning.
8. **Write** `mix.wav` (32-bit float) in the work directory. The encoder reads it.

Memory: a 20-minute film as float stereo at 48 kHz is about 460 MB [arithmetic]. The buffer is kept in a memory-mapped file in the work directory.

### 5.5 Preview

The preview is a small copy of the film that a browser plays. A browser cannot be relied on to play ProRes (Chrome does not decode it [inference]), so the page never plays the working copies.

- **Pictures.** One small file per segment, in `previews/seg-<hash>.mp4`: 960 pixels wide, H.264 with short groups of pictures (`-g 12 -bf 0`) so seeking is quick, no sound. The `<hash>` covers the working copy's path, size and modification time, `in`, `out`, the crop window, the frame size, the `look` hashes, the film fades that touch this segment and the preview settings. An unchanged segment is not rendered again.
- **Join.** The segment files are joined with ffmpeg's `concat` input in copy mode. Every file starts with a key frame, so the join needs no new encode.
- **Sound.** The full mix of section 5.4 is made each time (sound is quick to build) and encoded as AAC. The preview has the final loudness and joins.
- **Text.** Titles and subtitles are not burned into the preview. The page shows them from a WebVTT file made from the cues and titles [Design]. Their style in the preview differs from the final film.
- **Range.** `preview --range A:B` renders only the segments that touch film seconds A to B.
- **Served** at `GET /api/preview/<file>` with HTTP range requests (section 12, server).

The preview uses the same render plan and the same LUTs as the final film, so colour and timing agree. The browser's own colour handling can still differ from the final file's player [inference].

### 5.6 Export settings

An entry in `exports[]` sets one output. Version 1 accepts `libx264` and `aac` (rule V21).

| Setting | Starting value | Basis |
|---------|----------------|-------|
| Container | MP4, `-movflags +faststart` | The most common format; YouTube asks for the moov atom at the front [documented, Src11]. |
| Video | `libx264`, preset `slow`, CRF 18, 8-bit 4:2:0 | Starting values [inference]. The x264 speed measured on synthetic video: 38 frames per second at 4096 x 2160 and 167 at 1920 x 1080 [measured, research]. |
| Colour tags | BT.709 primaries, transfer and matrix, TV range | Explicit tags keep players from guessing [inference]. |
| Audio | AAC-LC, 48 kHz, 384 kbps stereo | YouTube recommendation for stereo [documented, Src11]. |
| Reference bitrates | 2160p: 35 to 45 Mbps at 24 to 30 fps; 1080p: 8 Mbps | YouTube recommended upload settings, SDR [documented, Src11]. A CRF export is not tied to them; `rate_control` can use `bitrate` instead. |
| Custom resolution | Any even `width` x `height` with the shape of `film.frame` | A different shape is rejected (rule V5); the tool does not add black bars. |

### 5.7 Checks the renderer runs on its own output

After the encoder ends and before the rename:

- `ffprobe` reports as many video frames as the film has.
- The audio duration differs from the video duration by less than one frame.
- The file size is not zero and `ffprobe` reads the whole file without an error.

A file that fails stays as `.partial` and the render report names the failed check.

### 5.8 The render report

`reports/render-report.json` records, per render: the timeline hash, the tool and ffmpeg versions, the elapsed time per stage, the speed in frames per second, the warnings of the plan, each shortened crossfade (join, wanted and used seconds), each segment whose gain was capped, each silent segment, the film's loudness and true peak before and after levelling, and the check results of section 5.7.

---

## 6. Step 1: Conversion Test

### 6.1 Purpose

Turn every `.braw` clip into a 10-bit ProRes working copy on the SSD, and measure on a real clip what that costs: speed, size and colour. The result may change the decoder choice (decision). No other step starts before this one works.

Status: not built. A first trial may be made by hand with `ffmpeg` and VapourSynth before the commands exist.

### 6.2 Decoder interface

Both decoders sit behind one interface [Design], so that step 1 can replace the first by the second (C7):

```
probe(braw_path)  ->  ClipInfo: width, height, fps (num, den), frames, timecode_start,
                      colour_science_gen, iso, white_balance_k, has_sidecar, metadata_tracks
frames(braw_path, colour_state, first, count)  ->  10-bit 4:2:2 frames to a pipe
```

- **Decoder 1: `vapoursynth-brawsource` 0.5.0** (MIT). Pipelines `cpu`, `metal`, `cuda`, `opencl`; bit depths 8, 16 and 32; resolution scales 1, 1/2, 1/4, 1/8; overrides for gamma, gamut, colour science generation, white balance, ISO and exposure; sidecar files applied automatically; clip metadata as frame properties; wheels for macOS 14 on arm64 that bundle the Blackmagic runtime [documented, Src1]. The author's numbers on an M1 Pro at 4608 x 2592: about 73 frames per second on the CPU and 196 on Metal [documented, Src1; not an M4 number]. The exact option names are taken from the plugin's README for the installed version [unverified until installed].
- **Decoder 2: Blackmagic RAW SDK 6.0** (a small own wrapper). Not written. Written only if decoder 1 fails the checks of section 6.7. The SDK licence lets the owner download and use the SDK. Redistribution is allowed only for the runtime inside an application built with it; the licence text was summarised, not read in full [documented, Src2]. The repository ships neither the SDK nor the runtime.

### 6.3 Conversion process

For one clip:

1. Probe the clip. Write the facts to `conversion.json`.
2. Start the decoder with the chosen colour state (section 6.5), 10-bit output as 4:2:2 in the 709 matrix.
3. Pipe into `ffmpeg`:

   ```
   ffmpeg -f yuv4mpegpipe -i -  -c:v prores_videotoolbox -profile:v <lt|standard|hq>
          -an  working/<stem>.mov.partial
   ```

   The software encoder `prores_ks` is the second choice. Step 1 times both. `prores_videotoolbox` reached 140 frames per second at 4096 x 2160 (profile LT) on synthetic video [measured, research]. It accepts the profiles proxy, lt, standard, hq, 4444 and xq [measured].
4. Run the check C5 (section 6.6). Only a copy that passes is renamed to `<stem>.mov` and marked `done`.

The tool converts one clip at a time. Whether two at once run faster is measured in step 1 [unverified].

The working copy carries no sound. Scratch audio is read from the `.braw` by `ffmpeg`, as the matcher does today [measured: the first shoot matched 105 clips this way].

### 6.4 `conversion.json`

One file in the work directory. The values below are an illustration.

```json
{
  "version": 1,
  "decoder": "vapoursynth-brawsource 0.5.0",
  "encoder": "prores_videotoolbox",
  "profile": "lt",
  "colour_state": "film",
  "clips": {
    "A002_01011540_C002": {
      "original": "/Volumes/CARD/Recordings/A002_01011540_C002.braw",
      "working_copy": "working/A002_01011540_C002.mov",
      "status": "done",
      "frames": 2432,
      "fps": {"num": 25, "den": 1},
      "size": {"width": 4128, "height": 2176},
      "timecode_start": "00:00:00:00",
      "colour_science_gen": "5",
      "has_sidecar": false,
      "convert_seconds": 31.2,
      "convert_fps": 77.9,
      "bytes": 1234567890,
      "check": {"codec": "prores", "pix_fmt": "yuv422p10le", "frames": 2432}
    }
  }
}
```

`status` is `pending`, `partial`, `done` or `failed`. The command skips `done` clips whose file still exists with the recorded size (C2).

### 6.5 Colour state

D2 is open: the working copies hold either Blackmagic Film as recorded (`film`) or Rec.709 already converted by the decoder (`rec709`). The tool supports both and records the choice in `conversion.json` and in every source of the timeline file.

- `film`: the decoder is told to output gamma Blackmagic Design Film and the matching gamut [documented, Src1, Src2]. The base conversion is applied at render by the `look.base` LUT (`mode: lut`). The Blackmagic conversion files (for example "Blackmagic Pocket 4K Film to Video v4.cube") are in the local Resolve install [measured]; which one fits depends on the generation the clip carries.
- `rec709`: the decoder is told to output Rec.709. `look.base.mode` is `baked`. The SDK converts on the GPU and needs no `.cube` file [documented, Src2].

Tags on the file follow the state: `rec709` copies are tagged BT.709; `film` copies are tagged as unspecified, so no player treats them as Rec.709 [inference].

The colour test of step 1 converts the same ten seconds of one clip in both states and writes stills of: as recorded, the decoder's Rec.709, and `film` plus each Blackmagic conversion LUT found in the Resolve folder. The owner compares the stills (check 1.7). The result decides D2.

### 6.6 Checks on a finished copy (C5)

`ffprobe` on the copy must report:

- codec `prores`, pixel format `yuv422p10le` (or `yuv444p10le` for the 4444 profiles),
- width and height equal to the source's,
- as many frames as `probe` reported,
- no audio stream.

Two frames, the first and the middle, must not be entirely one value (a black or constant frame points to a failed decode).

### 6.7 Space and the size estimate (C6)

Before the first conversion the tool measures `bytes per frame` on the test clip, multiplies by the total frames of all matched clips, adds 10 percent, and refuses to start if the SSD has less free space. Apple's published rates, for orientation: ProRes 422 LT at 4096 x 2160 and 25 frames per second needs about 164 GB per hour [documented, Src10]. A 73-minute shoot at 4128 x 2176 then needs about 203 GB (LT), 292 GB (422) or 437 GB (HQ) [arithmetic].

### 6.8 What step 1 reports

For the test clip and then for all clips: decoder speed alone, encoder speed alone, end-to-end speed in frames per second, size per frame, total time and total size for the whole shoot, the peak memory, and which encoder ran. It also reports the colour science generation, the frame rate, the frame size, the timecode, whether a sidecar exists and the names of the metadata tracks of each clip. If the metadata tracks hold camera motion data, a later step may use them instead of the picture analysis (section 7.4); this is [unverified] and only reported here.

### 6.9 Other changes in step 1

- `Project` gets the field `work_dir`. Loading a file without it still works.
- `thumbnails.py` reads the working copy when one exists, so `.braw` clips get thumbnails (M8).
- A function `assert_not_inside(path, folders)` refuses any write whose target lies inside a folder that holds an original (M9). Every writer calls it.
- `tools.py` and `python3 main.py tools check` (section 2.4).

### 6.10 Checks for step 1

"Check" commands run from the repository folder. Checks that need the owner's clip read it from the environment variable `VET_REAL_BRAW`; they are skipped when it is not set. Checks that write need `VET_WORK_DIR` (a folder on the SSD).

| Check | Command | Passes when |
|-------|---------|-------------|
| 1.1 | `python3 -m unittest tests.test_convert -v` | The pipe command lines are built as in section 6.3 for each profile and decoder. `conversion.json` round-trips. A `done` clip with an existing file is skipped. A `partial` clip is converted again. All use a fake decoder; no media. |
| 1.2 | `python3 -m unittest tests.test_convert_verify -v` | The check of section 6.6 accepts a ProRes file made from `ffmpeg -f lavfi -i testsrc2=size=640x360:rate=25 -t 2 -c:v prores_ks -profile:v 0` and rejects one with the wrong frame count, a wrong pixel format or an audio stream. |
| 1.3 | `python3 -m unittest tests.test_convert_space -v` | With a fake free-space value, the tool refuses when the estimate plus margin exceeds it and starts when it does not. |
| 1.4 | `python3 -m unittest tests.test_originals -v` | A run of convert on fixture originals (a fake decoder) leaves their sha256 unchanged. A write to a path inside the originals' folder raises an error. |
| 1.5 | `python3 main.py tools check` | Exit 0 and a list in which `ffmpeg-full`, the filters of section 2.4, the encoders and the `vapoursynth` import are all present. [after the installs] |
| 1.6 | `python3 main.py convert --probe "$VET_REAL_BRAW"` | Exit 0. The JSON on standard output has width, height, fps, frames, colour science generation and the sidecar flag. |
| 1.7 | `python3 main.py convert --test "$VET_REAL_BRAW" --work "$VET_WORK_DIR" --seconds 10 --colour-stills` | Exit 0. `ffprobe` on the copy shows `prores`, `yuv422p10le`, the source's frame size and 10 seconds of frames. The colour stills exist. **The owner looks at the stills** and records the answer to D2. |
| 1.8 | `python3 main.py convert --test "$VET_REAL_BRAW" --work "$VET_WORK_DIR"` | The whole clip converts and passes C5. The printed report has the numbers of section 6.8. Run once with each encoder. |
| 1.9 | `python3 main.py convert --work "$VET_WORK_DIR"` | All matched clips of the first shoot are `done` and pass C5. Run again: no clip is converted a second time. The report lists, for every clip, the frame rate, frame size, colour science generation and sidecar flag, and for every WAV the sample rate and channel count. |
| 1.10 | `python3 -m unittest discover -s tests -v` | All earlier tests still pass. |

Check 1.8 and 1.9 write large files. They run only after D1 is answered.

---

## 7. Step 2: Timeline File and Assembly

### 7.1 Purpose and outputs

From the matched pairs and the working copies, make a rough film: clips in recording order, the clap and the camera movement cut out, scenes split, sound crossfaded, the film faded in and out. Write it to `timeline.json`, render a preview, and show it in the page with trim controls.

Outputs: `analysis/<stem>/clap.json`, `analysis/<stem>/stable.json`, `timeline.json`, `reports/assembly-report.json`, `previews/`. A rough film without LUT and loudness work can be rendered with the renderer of section 5 (`loudness.mode: off`, no `look`).

Status: not built. Needs the result of step 1 and the answers to D5 to D8.

### 7.2 Finding the clap

`clap.py` offers a pure function `find_clap(samples, rate, search_s)`. It returns the time of the first clap in the first `search_s` seconds, with a strength, or nothing. The method follows the research note on onset detection [inference; the details below are a design, not a tested method]:

1. High-pass the signal at 2 kHz. A clap is a very short broadband sound; wind and room tone sit lower [inference].
2. Take the mean power in windows of 2 ms.
3. Onset strength per window: the rise of the log power from the window before, `max(0, log(P[k]) - log(P[k-1]))`. A log ratio does not depend on gain.
4. A candidate is a window whose onset strength is at least `CLAP_MIN_ONSET` and is the local maximum within 50 ms. It counts as a clap only if the power falls under 20 percent of its peak within `CLAP_DECAY_SEC`. A steady loud sound or a step in level does not decay and is not a clap.
5. Refine the time to the first sample, within 20 ms around the window, whose absolute value reaches a quarter of the local peak.
6. The first candidate in time that passes step 4 is the clap. The result also lists the strongest candidate, for the report.

`analyse --clap` runs it on the scratch audio of each `.braw` (16 kHz mono, as the matcher extracts it) and on the matched WAV. The two results must agree: `(t_wav - t_video)` equals the stored offset within `CLAP_AGREE_SEC`.

| Result | When | Used |
|--------|------|------|
| `agree` | Both found and consistent | The video time. |
| `single` | Only one found, or they disagree | The one found, converted by the offset. Marked for review. |
| `none` | Neither found | No clap cut. Marked for review (D6). |

The segment's first frame is `ceil((t_clap + CLAP_GUARD_SEC) * fps)`. The guard keeps the clap's tail out of the film. Because the audio follows the segment, cutting the picture also cuts the sound at the same instant.

| Setting | Starting value |
|---------|----------------|
| `CLAP_SEARCH_SEC` | 20 |
| `CLAP_HIGHPASS_HZ` | 2000 |
| `CLAP_MIN_ONSET` | 2.0 (a rise of about 7 times in power) |
| `CLAP_DECAY_SEC` | 0.1 |
| `CLAP_AGREE_SEC` | 0.02 |
| `CLAP_GUARD_SEC` | 0.5 (the research suggests 0.5 to 1 s) |

All six are [inference] starting values. Check 2.14 prints the clap time of every clip so the owner can listen to a sample and correct the values. D6 asks where the clap really is.

### 7.3 Drift between camera and recorder

The matcher measures the offset on the first 60 seconds [Appendix A.3]. A camera and a recorder run on different clocks, so the offset could change along a clip. This is [unverified] for the owner's gear.

`analyse --drift` measures the offset again on the last 60 seconds of every clip of at least 120 seconds. For that, `_extract_audio_raw` gets an optional start time (`-ss` before the input). The drift is `offset_tail - offset_head` seconds over `T - 60` seconds, reported in parts per million. The report marks every clip whose drift exceeds half a frame (20 ms at 25 frames per second).

`audio_drift_ppm` in the timeline file stays 0 unless the owner sets it. When it is not 0, the start of each segment's audio is shifted by `in/fps * ppm * 1e-6` seconds. Drift inside one segment is not corrected: at 50 ppm over 60 s it is 3 ms [arithmetic].

### 7.4 Finding the steady runs

`stable.py` finds the stretches in which the camera does not move. The owner films static [owner statement], so the method looks for a shift of the whole picture. It uses `numpy` and `scipy` only (D3).

**Method** (all numbers are [inference] starting values taken from the research note; the method is a design):

1. Decode the working copy to 5 frames per second, grey, 480 pixels wide (`fps=5,scale=480:-1:flags=area,format=gray`) through a pipe.
2. Turn each frame into its gradient magnitude, subtract the mean and divide by the standard deviation, so a change in brightness or exposure does not matter. Apply a Hann window to reduce edge effects.
3. For each pair of neighbouring frames, find the shift by phase correlation: multiply one spectrum by the conjugate of the other, normalise to unit magnitude, transform back, and take the peak. Refine the peak to a fraction of a pixel with a parabola through its neighbours. The height of the peak (0 to 1) is the quality.
4. Also measure the shift against the first frame of the current run, to catch slow drift.
5. A step is **moving** if the shift is at least `STABLE_MOVE_PX` or the drift against the run's first frame is at least `STABLE_DRIFT_PX`. A step with a peak under `STABLE_MIN_PEAK` cannot be measured. It is not called moving; the run that holds it is marked `low_confidence`.
6. Steady runs are the stretches of steps that are not moving. A moving stretch shorter than `STABLE_MERGE_GAP_SEC` is ignored, so a tiny shake does not split a scene. A run shorter than `STABLE_MIN_RUN_SEC` is dropped. Each remaining run loses `STABLE_GUARD_SEC` at both ends, so the camera has settled.
7. The score of a run is `1 / (1 + rms_shift / STABLE_MOVE_PX)` over its steps. "Most stable" means the highest score; the length breaks ties.
8. Run times convert to frame numbers at the film frame rate.

**Output** `stable.json`: the settings used, the runs (`in`, `out`, `score`, `low_confidence`), the moving stretches, and the path of a contact sheet that shows the first, middle and last frame of each run and one frame inside each moving stretch.

| Setting | Starting value |
|---------|----------------|
| `STABLE_SAMPLE_FPS` | 5 |
| `STABLE_WIDTH_PX` | 480 |
| `STABLE_MOVE_PX` | 0.5 (at 480 pixels wide, about 4 pixels at 4K) |
| `STABLE_DRIFT_PX` | 3.0 |
| `STABLE_MIN_PEAK` | 0.15 |
| `STABLE_MIN_RUN_SEC` | 2.0 (D7) |
| `STABLE_MERGE_GAP_SEC` | 0.5 |
| `STABLE_GUARD_SEC` | 0.3 |

**What was tested.** A prototype with `numpy` only, on a synthetic ProRes clip of a static texture, gave a shift of 0 pixels with a peak of 1.00 in the still intervals, and 11 pixels per step in the interval in which the window moved at 150 pixels per second [measured, synthetic, scratch test not in the repository]. A second synthetic clip with a bright 300-pixel block moving over the static texture gave a shift of 0 in every step, with peaks between 0.67 and 0.89 [measured, synthetic]. Real footage has noise, a moving subject of any size and changing light. Whether the settings above separate camera movement from subject movement on the owner's clips is [unverified] until check 2.14.

**Limits by design.** A subject that fills most of the frame can dominate the correlation. The method reports the peak quality and the report lists every cut, so the owner can see what was decided.

**Decode cost.** Decoding every frame of a 4K ProRes copy to read 5 per second may be slow [unverified]. Check 2.14 measures it. If it is too slow, step 1's conversion also writes a small analysis proxy per clip (480 pixels wide, 5 frames per second), and `stable.py` reads that.

**Camera motion data.** The checked clip has two timed-metadata tracks [measured, PRD section 5]. If they hold gyroscope data, they could replace this analysis. Their content is [unverified] and is read in step 1; this section does not depend on it.

### 7.5 Assembly rules

`assemble.py` builds `timeline.json`. Inputs: `project.json`, `conversion.json`, the files in `analysis/`.

1. **Clips.** If `project.selection` is not empty, use those clips in that order. Otherwise use all matched clips in recording order (the natural order of the file names, as in v1.1) (M7, A1).
2. A clip without a `done` working copy is left out; the report says why.
3. **Earliest frame.** The first frame after the clap (section 7.2). With result `none`, frame 0, marked for review.
4. **Runs.** Each steady run of the clip, cut at the earliest frame, becomes one segment if it is at least `STABLE_MIN_RUN_SEC` long. A clip with several runs gives several segments in time order (A3, A4).
5. **No steady run.** The clip is left out of the film and listed in the report (D7: starting plan). A setting `on_no_steady_run: keep_whole` keeps the clip after the clap instead.
6. **No `stable.json`.** The clip after the clap becomes one segment marked `unanalysed`.
7. **Segment fields.** `id` `s001`, `s002` and so on; `label` the clip name plus the run number; `origin` `assembly`; `note` with the clap time and the run score.
8. **Film fields.** `fps` from the sources (all must agree, rule V4). `frame` and `crop_mode` come from the command line (`assemble --frame 3840x2160 --crop-mode pixel`); the tool refuses without them and names D5. Fades and join take the starting values of section 3.2.
9. **Loudness.** Step 2 writes `loudness.mode: off`. Step 3 sets a mode and a target.
10. **Overwrite.** If `timeline.json` exists, `assemble` refuses unless `--replace` is given, and then snapshots the old file first. Trim work by hand is not lost by running assembly again.

Everything `assemble` decides is also written to `reports/assembly-report.json`: per clip the clap result and time, the drift, the runs found with their scores, the segments made, and for each clip left out or marked for review the reason (A7).

### 7.6 The page: preview and trim controls

A new tab **Film** shows the preview and a table of segments (U1 to U3). Wireframe:

```
+--------------------------------------------------------------+
| [ preview player, 960 px wide, subtitles from WebVTT ]       |
| [Render preview]   status: preview is up to date             |
+--------------------------------------------------------------+
| #  label            film time        len    start          end            on |
| 1  stove, wide      0:00 - 0:34     34.5 s  [-1s][-1f][+1f][+1s]  [-1s]..  [x] |
| 2  stove, wide ...  0:34 - 1:16     42.4 s  ...                              [x] |
| 3  pan with sausage 1:16 - 1:41     24.8 s  ...  drag a row to reorder       [ ] |
+--------------------------------------------------------------+
```

- The buttons send the operations of section 4.4. After each answer the table reloads from the new summary.
- A change marks the preview as out of date and starts a preview job for the changed segments (section 5.5). Several changes in a row run one job after the current one ends.
- A segment marked for review in the assembly report carries a flag.
- The page keeps no state of its own about the film. Closing it loses nothing.

### 7.7 Other changes in step 2

- `.gitignore` gets `timeline.json`, `short-*.json` and `timeline.history/`.
- `CLAUDE.md` of the repository gets the section of section 4.3.
- `config.py` gets the settings of this section and of section 5.
- `matcher._extract_audio_raw` gets the optional start time. Nothing else in `matcher.py` changes.

### 7.8 Checks for step 2

Synthetic fixtures are made inside the tests with `ffmpeg` and `numpy`, so no media file is stored in the repository. Tests that need `ffmpeg-full` are skipped when it is missing.

| Check | Command | Passes when |
|-------|---------|-------------|
| 2.1 | `python3 -m unittest tests.test_timeline_format -v` | The example of section 3.3 parses and validates with file checks off. It saves and loads to the same bytes. The derived values equal: film length 2542 frames, segment film starts 0, 862 and 1922. |
| 2.2 | `python3 -m unittest tests.test_timeline_rules -v` | For each rule V1 to V22 there is a broken copy of the example. The validator reports that rule at the expected field path, and the valid example reports nothing. |
| 2.3 | `python3 -m unittest tests.test_timeline_store -v` | A save makes a snapshot. `undo` restores the previous one. A validate after a direct edit makes a snapshot. A stale hash is refused. A write interrupted halfway leaves the old file whole. |
| 2.4 | `python3 -m unittest tests.test_clap -v` | On tracks made with `make_track` of `tests/test_matcher.py` (claps at known times, with and without loud ambience, and with a wind-like low-frequency step): the clap time is found within 5 ms; the step is not taken as a clap; a track with no clap gives `none`; a disagreeing pair gives `single`. |
| 2.5 | `python3 -m unittest tests.test_drift -v` | A WAV made by resampling a track by +50 ppm gives a measured drift within 15 ppm of 50. A 100-second clip is skipped. |
| 2.6 | `python3 -m unittest tests.test_stable -v` | On the synthetic clip of section 7.4 (still 0 to 4 s, moving 4 to 6 s, still 6 to 10 s) the runs lie inside 0 to 4 s and 6 to 10 s, each run's edge next to the moving part is between 0.1 s and 0.8 s away from it (the guard of 0.3 s plus the 0.2 s sample step), and the moving part is outside every run. On the clip with a moving block over a static texture, no movement is found. A clip that moves throughout gives no run. |
| 2.7 | `python3 -m unittest tests.test_assemble -v` | With analysis results given as data: clips in recording order; a clip with two runs gives two segments; the clap cut raises the first segment's `in`; a clip with no run is left out and reported; an existing timeline is not overwritten without `--replace`; the assembled file validates. |
| 2.8 | `python3 -m unittest tests.test_audio_place -v` | The cases of `PlanTests` in `tests/test_resolve_integration.py` (back-to-back, trims, positive offset, negative offset, clamped length), restated for the audio span function, give the same sample positions. A crossfade of two uncorrelated noise segments keeps the power within 1 dB at the join, both curves. A shortened handle shortens the crossfade and is reported. The first and last 10 ms of the film are silent when the fades are on. |
| 2.9 | `python3 -m unittest tests.test_render_plan -v` | The crop windows of the table in section 5.2 come out for a 4128 x 2176 source. The film start frames and sample positions follow section 3.4, with no drift over 150 segments at 24000/1001 frames per second. |
| 2.10 | `python3 -m unittest tests.test_sync_marker -v` | A fixture clip has a white frame at frame 100 and a WAV with a click at `100/fps + offset`. After building a timeline for the fixture and rendering it, the first bright frame and the click in the output lie within 1 ms of each other. |
| 2.11 | `python3 -m unittest tests.test_seek_exact -v` | On a synthetic ProRes file and, when `VET_WORK_DIR` has a working copy, on a real one: the frames the render reads for `in` and `out` equal the frames selected by number. |
| 2.12 | `python3 -m unittest tests.test_preview -v` | A second preview of an unchanged film renders no segment. A trim of one segment renders exactly that segment. The joined preview has as many frames as the film. |
| 2.13 | `python3 -m unittest tests.test_server_timeline -v` | A server on an ephemeral port answers: `GET /api/timeline` with the hash; an edit with the right hash is saved; a stale hash gives 409; an invalid result gives 422 with the field path; a request with a wrong Host gives 403; a preview request with a path outside the work directory gives 404; a range request returns the bytes asked for. |
| 2.14 | `python3 main.py analyse --work "$VET_WORK_DIR" && python3 main.py assemble --frame 3840x2160 --crop-mode pixel && python3 main.py timeline validate` | On the first shoot: exit 0, a report with a clap result for every clip, drift for the longest clips, and the runs. The tool prints the times each stage took. **The owner** listens to a sample of claps, looks at the contact sheets of the clips the report marks, and watches the preview. The owner sets the starting values from what they saw. (The frame options here are examples; D5 decides them.) |
| 2.15 | `python3 -m unittest discover -s tests -v` | All earlier tests still pass. |

---

## 8. Step 3: Look and Sound

### 8.1 Purpose and outputs

Choose one LUT from stills, level the loudness, export the 4K film, and write the Shotcut project.

Outputs: `stills/`, `look` in `timeline.json`, `renders/film-4k.mp4`, `reports/render-report.json`, `film.mlt`, `audio-synced/`.

Status: not built. Needs step 2 and D9 to D11.

### 8.2 Choosing the LUT from stills (L1)

`python3 main.py look stills --clips 6 --luts "$VET_LUT_DIR"` makes the comparison.

1. **Candidates.** Every `.cube` file in the folder named by `VET_LUT_DIR`, plus one entry "base only" (no creative LUT). The folder is on the owner's machine and is never committed. Where the LUTs come from is D10.
2. **Clips.** Six clips spread evenly over the shoot, or the clips named with `--clips A002_..._C002,...`.
3. **Frame.** The middle frame of the clip's first steady run (step 2), or the frame at 10 s if the clip was not analysed.
4. **Picture.** The frame is cut with the crop window of the film, scaled to 640 pixels wide, passed through the base conversion and then through the candidate LUT. This is the same chain as the final render (section 8.3).
5. **Sheets.** One sheet per clip: a grid of 4 x 3 tiles, "base only" first, each tile labelled with the LUT's file name; more LUTs make more pages. One sheet per LUT: the same LUT on the six clips, in a grid of 3 x 2. Sheets are PNG files in `stills/lut-by-clip/` and `stills/lut-by-lut/`. They are composed with `xstack` and `drawtext` of `ffmpeg-full`.
6. The owner (or Claude Code, on request) looks at the sheets. `python3 main.py look set --lut <file>` copies the file to `<work_dir>/luts/<sha256>.cube`, writes `look.lut` with its hash and `interp: tetrahedral`, and validates the result.

The base conversion is chosen the same way. For a `film` working copy, `look stills --base` lists the Blackmagic conversion files found in the local Resolve folder (for example "Blackmagic Pocket 4K Film to Video v4.cube" and "Blackmagic Gen 5 Film to Video.cube" [measured]) beside the clip's colour science generation. Which file matches which generation is not assumed. The owner picks from the stills. `look set --base <file>` writes `look.base`.

A creative LUT must expect Rec.709 input, not log, and goes after the base conversion [inference]. The tool cannot tell from a `.cube` file which input it expects; the stills show it.

### 8.3 Applying the look (L2, L3)

For the final render and the preview, the filter chain of section 5.3 contains, in this order: crop, scale, base LUT, creative LUT.

- Filter: `lut3d=file=<path>:interp=tetrahedral`. Tetrahedral is also ffmpeg's default [measured].
- The path is always the hash-named copy in `<work_dir>/luts/`, so it has no spaces or special characters that filter strings treat as syntax. The LUT in the timeline file may be anywhere.
- Speed: `lut3d` ran at about 47 frames per second at 4096 x 2160 and about 355 at 1920 x 1080 on this Mac, on synthetic video, with a 33-point cube [measured, research]. The scale comes first so a smaller output pays less.
- A `look.base.mode` of `baked` skips the base LUT.
- Open: whether 10-bit precision survives the conversion to RGB and back, and which matrix is used [unverified]. Check 3.1.

The Blackmagic `.cube` files are used from the local Resolve folder. Their redistribution terms were not found [unverified], so they are never copied into the repository. The copy in `<work_dir>/luts/` stays on the owner's drive.

### 8.4 Loudness (L4, L5)

`loudness.py` has two functions.

- `measure(samples, rate)` pipes 32-bit float stereo into `ffmpeg -f f32le -ar 48000 -ac 2 -i - -af ebur128=peak=true -f null -`, reads the summary at the end of the log, and returns integrated loudness (LUFS), loudness range (LU) and true peak (dBTP). Integrated loudness uses the standard gates: -70 LUFS absolute and 10 LU relative [measured: the filter exists and offers `peak=true`; the gate values are the EBU R128 definition].
- `level(...)` is the procedure of section 5.4, steps 5 to 7.

Speed: the first pass of `loudnorm` took 15.5 s for 20 minutes of synthetic stereo, about 77 times faster than real time [measured, research]. The speed of `ebur128` was not measured.

The method is a measured fixed gain followed by a limiter. `loudnorm` in dynamic mode is not used, because it can change the loudness over time in material with a wide range [inference, research B5]. `pyloudnorm` would do the same measurement in Python; it would be a new package (D3).

The target is `audio.loudness.target_lufs` of the timeline file. There is no built-in default (D9).

### 8.5 Export (E2, E3)

`python3 main.py render --export film-4k` renders the export named in `exports[]` (section 5.3 and 5.6). `render --all` renders every export. A custom resolution is an export entry with another `width` and `height` of the same shape as the film. The preview and the final file use the same plan.

### 8.6 The Shotcut project (E4)

**What it is for.** Rare hand work, done last. The decision says Shotcut edits do not flow back. The tool never reads the file again. A new run of `export-shotcut` writes the file anew and does not merge.

**Method.** The project format of Shotcut is documented as unstable: it wants annotations (`shotcut=1` on the tractor, `shotcut:filter` on filters, `shotcut:name` on playlists) and says they may change [documented, Src6]. Developers of Kdenlive say the same of their format [documented, Src7]. So the writer works from a template:

1. At the start of step 3, the owner saves one small project in Shotcut by hand (two clips, a LUT filter, a fade, a subtitle filter). The version number of Shotcut is noted.
2. The writer takes that file's structure as its template and fills in the film. Prior art exists: a small project writer for Shotcut validates each edit with `melt` [documented, Src9; young, maturity unproven]. It records the Shotcut version in the output. The version listed as current in the research is 26.9.27 [documented, Src6]. A different installed version gives a warning, not an error.

**Content** [all unverified until check 3.9]:

- A profile with the film's size and frame rate.
- A video track with one entry per enabled segment, with the segment's `in` and `out` frames. MLT counts `out` as the last frame used, so `out_mlt = out - 1` [inference].
- An audio track with one entry per segment, from `audio-synced/<stem>.wav`, with the same `in` and `out` frames as the picture.
- Between neighbouring audio entries, a `mix` transition for the crossfade: constant power for `equal_power`, linear for `linear` [documented, Src5].
- A `volume` filter per audio entry with the gain that levelling computed at export time.
- Fades at the film's start and end: a `brightness` filter for the picture and a `volume` filter for the sound.
- The base LUT and the creative LUT as two LUT filters on the video track. Shotcut's LUT filter uses ffmpeg's `lut3d` [documented, secondary].
- A crop filter that cuts the window of section 5.2 where `crop_mode` needs one.
- Subtitles through the MLT `subtitle` filter, which reads SRT [documented, Src5]. Titles as a second SRT with its own style.

**Synced audio.** MLT places clips on whole frames. The offset between camera and recorder is a fraction of a frame (0.276 s is 6.9 frames at 25 frames per second). To keep the sound exact, the tool writes `audio-synced/<stem>.wav` for each clip: the original WAV, at its own sample rate and bit depth, shifted by the offset in whole samples so that its time 0 is the picture's time 0. If the offset is negative, silence is added at the start. The file ends where the picture ends. A segment's picture and sound then have the same frame numbers in Shotcut and stay aligned to the sample. (D11 asks whether the owner prefers one premixed audio track instead.)

**Known differences from the tool's own render.** Shotcut has no true-peak limiter step in this project and processes in 8 bit [inference]. The tool's render is the reference. A render from Shotcut will not equal the tool's render bit for bit.

### 8.7 The page in step 3

A tab **Look** shows the stills sheets. A tab **Export** lists the entries of `exports[]`, a button to render each, the progress and the render report. Choosing a LUT is done by command, or by telling Claude Code.

### 8.8 Checks for step 3

| Check | Command | Passes when |
|-------|---------|-------------|
| 3.1 | `python3 -m unittest tests.test_lut -v` | The tests write a 17-point identity cube and an inverting cube. Through the real filter chain, applied to a 10-bit gradient and to colour bars (primaries and greys), the identity cube changes no 8-bit output value by more than 1, and the inverting cube gives `255 - input` within 2. A cube with a non-linear curve matches a `numpy` tetrahedral reference within 2. |
| 3.2 | `python3 -m unittest tests.test_look_args -v` | The filter string for a LUT path with spaces, quotes and colons is valid. The hash-named copy is made once. `interp=tetrahedral` is in the string. The base LUT comes before the creative LUT. A changed LUT file fails rule V16. |
| 3.3 | `python3 -m unittest tests.test_stills -v` | With three synthetic clips and five cubes, the sheets exist, have the expected sizes, and carry the file names. Thirteen cubes give two pages. |
| 3.4 | `python3 -m unittest tests.test_levelling -v` | Noise segments at three known levels, levelled to a test target, each measure within 0.5 LU of it. A segment too quiet for `max_gain_db` is capped and reported. A silent segment gets gain 0 and a warning. Mode `film_only` keeps the differences between segments. |
| 3.5 | `python3 -m unittest tests.test_film_loudness -v` | The film's integrated loudness is within 0.5 LU of the target. The true peak is at or under the limit. Material with strong clicks goes through the limiter loop and ends under the limit, or the report says it could not. |
| 3.6 | `python3 -m unittest tests.test_export -v` | A synthetic film at 1920 x 1080 exports to an MP4 that `ffprobe` reads as H.264, 8-bit 4:2:0, the right size and frame count, AAC at 48 kHz, BT.709 tags, with the `moov` box before `mdat`. An export of another size of the same shape works. An export of another shape fails with rule V5. `VET_SLOW=1` adds the same test at 3840 x 2160. |
| 3.7 | `python3 -m unittest tests.test_export_shotcut -v` | The XML is well formed. It has one video and one audio entry per enabled segment with the in and out frames of section 8.6, as many `mix` transitions as joins, the two LUT filters with the right files, and the profile of the film. Each `audio-synced` WAV is as long as its picture in samples, and a click in it lies where the original click plus the offset puts it. |
| 3.8 | `python3 main.py render --export film-4k` | On the first shoot: exit 0, the file passes the checks of section 5.7, and the report shows the film's loudness and true peak. The speed and the time are recorded. **The owner** watches a smooth-gradient scene for banding and compares the colour with the chosen stills. |
| 3.9 | `python3 main.py export-shotcut`, then open `film.mlt` in Shotcut | **By hand.** The segments are in order. The first 10 seconds play. The LUT shows. The fades and crossfades are there. Subtitles show. The result and the Shotcut version are written to the render report's notes. |
| 3.10 | `python3 -m unittest discover -s tests -v` | All earlier tests still pass. |

---

## 9. Step 4: Words

### 9.1 Purpose and outputs

English transcript, subtitles and titles (W1 to W5).

Outputs: `analysis/<stem>/transcript.json`, `subtitles` and `titles` in `timeline.json`, an ASS file and an SRT file next to the export, subtitles burned into the film when the mode says so.

Status: not built. Needs step 3 (for the ASS burn-in, which needs `ffmpeg-full`) and D12, D13.

### 9.2 Transcript (W1)

- **Tool.** The `whisper` audio filter of `ffmpeg-full`, which is built on whisper.cpp [documented, Src13; `ffmpeg-full` enables it, Src3]. Input: the matched WAV as mono 16 kHz. Language: English (decision). Output: SRT or JSON.
- **Model.** A whisper.cpp model file, downloaded once by the owner (D12). The tool does not download anything while it runs.
- **Speech only.** The filter takes a voice-activity model. Regions without speech get no text. Whisper models invent text on silence and ambience [inference], so the voice-activity model is on by default.
- **Option names.** The names of the filter's options (model, language, queue, voice-activity model, output format) are read from `ffmpeg -h filter=whisper` of the installed build [unverified until installed].
- **Word times.** Whether the filter gives times per word is [unverified]. Times per phrase are enough for subtitles. Word times would sharpen the cue splitting of section 9.3.
- **Speed.** No source with a known audio length was found for an M-series Mac. The owner times a two-minute sample first [documented gap, research B6].
- **Fallbacks.** The whisper.cpp command-line program (Homebrew) or `mlx-whisper`, which one third-party benchmark found about twice as fast as whisper.cpp for one large model [documented, secondary]. Both need installs the owner has not allowed. They are used only if the filter fails the checks, and then after the owner's approval.

`transcript.json`:

```json
{
  "model": "ggml-small.en.bin",
  "language": "en",
  "voice_activity": true,
  "segments": [
    {"start_s": 12.31, "end_s": 15.80, "text": "Now the pan is hot enough."}
  ]
}
```

The times are in the WAV's time. The model name in the example is an illustration of D12, not a choice.

### 9.3 Subtitles on the film (W2)

`python3 main.py subtitles build` turns transcripts into cues in `timeline.json`. For each enabled segment:

1. Take the transcript phrases that overlap the segment's audio span. Convert a phrase time from WAV time to picture time: `t_video = t_audio - offset`.
2. Clip the phrase to `[in/fps, out/fps)` and make its times relative to the segment's start.
3. Keep the cue if the clipped part is at least `CUE_MIN_OVERLAP` (60 percent) of the phrase. Otherwise drop it and list it in the report: the sound was cut mid-phrase.
4. Wrap the text to `max_chars_per_line` (starting value 42) and at most two lines. A longer cue is split at word boundaries into several cues, and the time is shared in proportion to the number of characters. A cue is at least 1.0 s long where the segment allows.

The cues are anchored to segments, so trimming or reordering keeps them attached. A cue that the owner or Claude Code has edited by hand carries `"x_locked": true`; `subtitles build` keeps locked cues and replaces the others. The numbers in this list are [inference] starting values.

### 9.4 Titles (W3)

- **By hand.** Edit `titles` in the file, or run `python3 main.py title add --anchor film --start 1 --end 5 --text "Sunday kitchen" --template center`.
- **By prompt.** Claude Code reads the transcript and the index (section 10) and writes the entries. The text is data in the file. It is not a rendered picture.
- **Templates.** `center`, `lower_third` and `opening` are ASS styles in the tool: position, size as a percent of the frame height, and a fade of `fade_s` at both ends.

### 9.5 Burn-in and files (W4)

- The tool writes `renders/<name>.ass` from the titles and cues, with `PlayRes` equal to the export size, and burns it with the `subtitles` filter of `ffmpeg-full` (libass) in the encoder process of section 5.3. The installed ffmpeg 8.1 cannot do this [measured].
- It writes `renders/<name>.srt` from the cues.
- `subtitles.mode` says what is used: `burn`, `sidecar` (SRT only, nothing burned) or `both` (D13).
- The font is `subtitles.style.font`. The tool has no built-in font, so a file with `burn` and no font is invalid (D13). Fonts are found through the system's fontconfig.
- The preview shows text from a WebVTT file made from the same cues (section 5.5).

### 9.6 The page in step 4

The preview plays the WebVTT track. The page has no subtitle editor (out of scope). Text is changed in the file or through Claude Code.

### 9.7 Checks for step 4

Checks that need Whisper skip when the model or `ffmpeg-full` is missing (`VET_WHISPER_MODEL` names the model file).

| Check | Command | Passes when |
|-------|---------|-------------|
| 4.1 | `python3 -m unittest tests.test_transcribe -v` | A sentence made with the macOS `say` command is transcribed. After lower-casing and removing punctuation, at most one word in five differs from the sentence. |
| 4.2 | `python3 -m unittest tests.test_transcribe_silence -v` | Thirty seconds of noise and a steady tone give no phrase with text. |
| 4.3 | `python3 -m unittest tests.test_cue_map -v` | With phrases at known times, a known offset and two segments: cue times equal the expected values to 10 ms. A phrase with less than 60 percent inside a segment is dropped and reported. Moving a segment does not change its cues' times. Locked cues survive a rebuild. |
| 4.4 | `python3 -m unittest tests.test_cue_wrap -v` | Long text wraps to two lines of at most `max_chars_per_line`. A longer text splits into cues whose times add up to the original. |
| 4.5 | `python3 -m unittest tests.test_ass -v` | The ASS text has `PlayResX` and `PlayResY` of the export. Times are `H:MM:SS.cc`. Titles carry a fade. Characters that ASS treats as syntax are escaped. |
| 4.6 | `python3 -m unittest tests.test_burn -v` | A black film with one cue: a frame inside the cue interval has lit pixels in the lower third and a frame outside has none. With mode `sidecar` no frame has lit pixels and the SRT exists. The SRT parses and its times equal the cues'. |
| 4.7 | `python3 -m unittest tests.test_webvtt -v` | The file starts with `WEBVTT`, its times rise, and its cues equal the timeline's. |
| 4.8 | `python3 main.py words --work "$VET_WORK_DIR"` | On the clips of the first shoot that hold speech: exit 0, a transcript for each, and the time per minute of audio is printed. **The owner** reads the subtitles on a rendered film. |
| 4.9 | `python3 -m unittest discover -s tests -v` | All earlier tests still pass. |

---

## 10. Step 5: Prompt Edits

### 10.1 Purpose and outputs

The owner describes a scene or a change. Claude Code finds the place and edits the timeline file (P1 to P4).

Outputs: the index (`analysis/index.md` and the contact sheets), the commands of section 10.4, and edits to `timeline.json`.

Status: not built. Needs steps 2 and 4 (the transcript). D14 is open.

### 10.2 The index (P1)

`python3 main.py index` builds, for every clip:

- **Contact sheets** in `analysis/<stem>/sheets/`: JPEG images of 1920 x 1080 pixels with 3 x 3 tiles of 640 x 360 pixels, each labelled `STEM mm:ss.s f<frame>`. The size stays under 2000 pixels on a side, the limit above which the Claude API scales images down when there are more than 20 in a request [documented, Src15].
- **Sampling.** For each steady run: the first, middle and last frame, and one frame every `INDEX_EVERY_SEC` (5 s) inside runs longer than 15 s. For each moving stretch: one frame.
- **`analysis/index.md`**: a text file with, per clip in recording order, the name, duration, clap time, the runs (time ranges, with the sheet file and the tile numbers), and the transcript phrases with their times (source time).

Cost of reading sheets, so the work can be planned: an image costs `ceil(w/28) * ceil(h/28)` tokens, at most 1568 on standard-tier models and 4784 on newer ones [documented, Src15]. A sheet of 1920 x 1080 therefore costs up to 1568 tokens for nine stills. One still every 5 s over the 73 minutes of the first shoot is about 876 stills, 98 sheets and about 154,000 tokens if every sheet were read [arithmetic]. So Claude Code reads the text index first and opens only the sheets of candidate clips.

### 10.3 Finding a scene and editing (P2)

The loop for "cut out the scene where I am frying a sausage":

1. Search `index.md` and the transcripts for the spoken words (`python3 main.py find "sausage"`, W5).
2. If the description is visual, open the sheets of the candidate clips. Narrow down with a denser sheet: `python3 main.py index zoom --source STEM --from 12 --to 40 --every 1`.
3. Decide the clip and the time range. Check the edges with `python3 main.py timeline frames --source STEM --at 12.0,13.0`.
4. Apply the edit with the deterministic commands of section 10.4. They do the arithmetic of splitting segments. Claude Code decides the times; the tool changes the file.
5. `timeline validate`, then `preview --range` around the edit, then `timeline frames` at the new join. Report to the owner what changed: segment ids and film times.

Reading of the owner's words: "cut out" means remove, as in "cut out the sync clap" and "cut out camera movement". The request "keep only this scene" is the second command. D14 asks the owner to confirm.

Guidance for the edges: stay inside steady runs, keep a margin of about 0.5 s from speech, and do not cut a sound event in two. These are rules of thumb for Claude Code [inference], written into `CLAUDE.md`.

Whether Claude Code identifies a scene from stills and a transcript is [unverified]. Check 5.5 tests it.

### 10.4 Commands (P3, P4)

| Command | Effect |
|---------|--------|
| `timeline summary` | Print the film as text (section 4.3). |
| `timeline validate` | Check the file (section 3.5). |
| `timeline frames --film-s T1,T2` or `--source STEM --at T1,T2` | Write PNG stills at the given film times or source times and print their paths. |
| `timeline cut --from-film A --to-film B` | Remove the film time range: segments wholly inside are disabled, a segment that contains the range is split into two with new ids, segments that overlap an edge are shortened. |
| `timeline keep --from-film A --to-film B` | The opposite: disable everything outside the range. |
| `timeline history`, `timeline undo` | List snapshots; restore the previous one. |
| `find "words"` | Search the transcripts. Print clip, source time, film time (if the clip is in the film) and text. |
| `index zoom ...` | A denser contact sheet for one time range. |
| `preview --range A:B` | Render part of the preview. |

**One prompt, one snapshot.** `cut` and `keep` write one snapshot each. For edits made by hand in the file, `timeline validate` makes the snapshot (section 4.1), so the rule is: edit, validate once, report the snapshot number to the owner. `timeline undo` returns to the version before.

### 10.5 What is not included

Search by text over thousands of stills with a local image-and-text model (for example SigLIP 2 through `open_clip`) or captions from a local vision-language model (for example Qwen3-VL through `mlx-vlm`) [documented, research B7] would find scenes without Claude Code reading sheets. Both need packages and model downloads that the owner has not allowed (D3, D14). They are not part of this plan.

### 10.6 Checks for step 5

| Check | Command | Passes when |
|-------|---------|-------------|
| 5.1 | `python3 -m unittest tests.test_index -v` | The sheets are 1920 x 1080 with the right number of tiles and labels. The sampling gives the frames of section 10.2 for given runs. `index.md` lists all clips in order. A zoom sheet covers exactly the range asked. |
| 5.2 | `python3 -m unittest tests.test_timeline_cli -v` | The summary has the format of section 4.3. On a synthetic clip in which every frame has a different grey value, `timeline frames --film-s T` returns the frame the render plan maps T to, including across a join. |
| 5.3 | `python3 -m unittest tests.test_find -v` | The search returns the right clip and time for a word in a test transcript, and the film time only for clips in the film. |
| 5.4 | `python3 -m unittest tests.test_cut_keep -v` | A range inside one segment splits it into two with correct `in` and `out` and new ids. A range over a join shortens both neighbours. A range over a whole segment disables it. The film length drops by the length of the range (to the frame). `keep` is the complement. `undo` restores the file byte for byte. |
| 5.5 | By hand, with the owner | The owner names five scenes of the first shoot by description. Claude Code, using only the index and the commands, proposes film ranges. For each, the owner says in the preview whether it is the described scene and whether the edges are clean. The owner sets the pass level after this first attempt. |
| 5.6 | `python3 -m unittest discover -s tests -v` | All earlier tests still pass. |

---

## 11. Step 6: Vertical Shorts

### 11.1 Purpose and outputs

From a 16:9 film with subtitles, build a 9:16 short of a chosen length (S1 to S5).

Command: `python3 main.py short --from timeline.json --length 45 --name sausage`. Outputs: `short-sausage.json` (a timeline file of kind `short`) and, when rendered, `renders/short-sausage.mp4`, with its ASS and SRT files.

Status: not built. Needs steps 3 and 4. D15 and D16 are open; the length has no default until D15 is answered.

### 11.2 What the builder writes

A new timeline file with `kind: short`, `film.frame` 1080 x 1920, `derived_from` (the film file, the length, the settings), the same `sources`, `look` and `audio` as the film, and segments copied from the film's enabled segments with new ids. Cues and titles are copied with their anchors, and those outside a shortened segment drop out. One export entry is added: 1080 x 1920, `libx264`, the starting values of section 5.6. A custom resolution is another export entry.

### 11.3 How important a moment is (S2)

The builder gives every second of the film a score (starting weights are [inference]; D16):

| Signal | How it is measured | Weight |
|--------|--------------------|--------|
| Speech | The second lies inside a cue. | 3.0 |
| Sound event | The onset strength of the second (the measure of section 7.2) is above `SHORT_SOUND_ONSET`. A sizzle starting or a knock shows as an onset. | 1.0 |
| Picture activity | The mean absolute difference between the 5-per-second frames of section 7.4 inside the second, scaled to 0 to 1. | 1.0 |
| Prompt | The second lies in a film range given with `--keep A:B`. Claude Code supplies the ranges after the owner describes what matters. | 3.0 |
| Pinned | The segment has `priority: must`: never shortened. `priority: drop`: left out. | - |

### 11.4 Shortening (S2)

A deterministic procedure, pure and testable:

1. Start with every enabled segment except `drop`, whole. Let `L` be the total length.
2. If `L` is not above the target, change no segment.
3. Otherwise repeat until `L` equals the target to the frame: of all segment edges (the first and the last second of each segment that is not `must`), cut the edge second with the lowest score; ties go to the longer segment. In the last step cut only the part that brings `L` to the target.
4. A segment that would fall below `SHORT_MIN_SCENE_SEC` (starting value 2.0) is removed whole.
5. If the `must` segments alone are longer than the target, stop with an error that names both lengths.
6. The order of segments does not change.

Cutting only at edges means no cut is made inside a scene, so no new jump cut appears [Design]. The joins, the film fades and the loudness are made for the short as for any film. The score step is a rule for a first draft. The owner and Claude Code can change the result by editing the short's file.

### 11.5 Crop (S3)

The window is 1224 x 2176 pixels for the clip checked, scaled to 1080 x 1920 (section 5.2). `crop.x` places it in each segment. The default is `SHORT_CROP_X` (0.5, the centre; a technical default, not a decision). The owner or Claude Code sets it per segment after looking at frames, or gives `x_from` and `x_to` for a slow pan. The vertical short is cut from the working copy, which is the 4K source (decision).

Automatic subject tracking is not included. The tools the research found for it are young, need installs the owner has not allowed, and the older one is described by its successor as no longer supported [documented, research B9] (D16).

### 11.6 Subtitles for 9:16 (S4)

The frame is 1080 x 1920. Third-party guidance for short-video platforms says to keep text out of the top 15 percent (288 px), the bottom 35 percent (672 px), the left 4 percent (48 px) and the right 18 percent (192 px) [documented, secondary, Src14]. That leaves a box from x = 48 to 888 (840 px wide) and from y = 288 to 1248.

- Cues are re-wrapped to two lines of about 24 characters (starting value [inference]), and long cues are split with the time shared by characters (section 9.3).
- The size is found by fitting: the tool renders the longest cue as white text on black through libass, measures the bounding box of the lit pixels, and reduces the font size by 4 percent until the box lies inside the safe area (at most ten rounds). The same size is used for all cues of the short.
- Titles are scaled by the same factor. The `lower_third` template sits above y = 1248.

### 11.7 Export (S5)

`libx264`, AAC and MP4 as in section 5.6. A short that is longer than 180 seconds gets a warning (rule V22), because YouTube allows Shorts up to 3 minutes since 2024-10-15 [documented, secondary, Src14].

### 11.8 Checks for step 6

| Check | Command | Passes when |
|-------|---------|-------------|
| 6.1 | `python3 -m unittest tests.test_short_trim -v` | With given scores: the result length equals the target to the frame; kept segments are contiguous; the highest-scoring seconds are kept; a `must` segment is never shortened; `must` longer than the target is an error that names both lengths; `drop` segments are absent; segments below the minimum are removed; a film not longer than the target is unchanged; the order is unchanged. |
| 6.2 | `python3 -m unittest tests.test_short_crop -v` | For a 4128 x 2176 source the window is 1224 x 2176; `x` of 0, 0.5 and 1 give X of 0, 1452 and 2904; a moving window moves in a straight line. |
| 6.3 | `python3 -m unittest tests.test_short_subtitles -v` | Twenty cues of different lengths, rendered through libass, all have their lit-pixel box inside the safe area. Cue splits add up to the original times. |
| 6.4 | `python3 -m unittest tests.test_short_export -v` | The MP4 is 1080 x 1920, H.264, AAC. Its length is within one frame of the target. A short longer than 180 s gives the V22 warning. |
| 6.5 | `python3 main.py short --from timeline.json --length 45 --name test`, then render | On the first shoot: exit 0, the file passes the checks of section 5.7. **The owner** watches it: are the important moments there, do the subtitles fit, is the crop right. The owner sets the pass level after the first attempt. |
| 6.6 | `python3 -m unittest discover -s tests -v` | All earlier tests still pass. |

---

## 12. Existing Parts: Reused or Replaced

Appendix A describes each part as built in v1.1.0. This table says what the plan does with it.

| Part | Fate | What changes |
|------|------|--------------|
| `matcher.py` | **Kept.** | `_extract_audio_raw` gets an optional start time (section 7.3). `clap.py` imports `_extract_audio_raw`, `_highpass`, `_onset_envelope` and `_find_offset` from it. Matching already works on `.braw`: ffprobe reads the container and ffmpeg reads the scratch audio [measured: 105 pairs in the first shoot]. |
| `Project`, `SceneMatch`, `project.json` | **Kept.** | New field `work_dir`. `selection` stays and means "the clips that go into the film, in order" (M7). `trim_start`, `trim_end`, `selection_trims` and `get_ordered_scenes` are still read, for old files and for the Resolve option. Assembly ignores them. The `offset` contract is unchanged. |
| `build_timeline_plan` in `resolve_integration.py` | **Its rule is reused; the function belongs to the Resolve option.** | The way it applies the offset (advance the audio in-point for a positive offset, delay and clamp for a negative one, clamp to the file length) is the reference for the audio span in section 5.4. Its tests are restated in check 2.8. |
| `syncer.py` (copy and rename the WAV, manifest, undo) | **Kept only if D4 says so** (R1). | Its job, giving Resolve a file with the video's name, is not needed by the tool's own render. For Shotcut the tool writes `audio-synced/` in the work directory instead (section 8.6). Copies made earlier stay in the video folders and `sync --undo` still removes them. |
| `resolve_integration.py` (push, `apply_plan`, name suffixes) | **Kept only if D4 says so** (R2). | Not extended. If D4 drops it: delete the module, the `resolve` command, `/api/resolve` and the Resolve part of the third tab. Its `Project` tests move to `tests/test_project.py`. Its other tests are deleted. |
| `renamer.py` | **Kept, untouched.** | Legacy. |
| `thumbnails.py` | **Changed.** | Reads the working copy when one exists, so `.braw` clips get thumbnails. Also serves the stills and frame requests of the later steps. |
| `server.py` | **Extended.** | Keeps the Host and Origin checks, the one-job rule and the 127.0.0.1 binding. Adds the endpoints below and range requests for video. |
| `ui.html` | **Extended.** | Tabs Match and Clips (the old Select and Order, with the trim inputs removed, because trims move into the timeline file) stay. New tabs Film, Look and Export. The Resolve tab stays only if D4 says so. |
| `main.py` | **Extended.** | New subcommands: `tools check`, `convert`, `analyse`, `assemble`, `timeline` (`summary`, `validate`, `frames`, `cut`, `keep`, `history`, `undo`, `migrate`), `preview`, `look` (`stills`, `set`), `render`, `export-shotcut`, `words`, `subtitles build`, `title add`, `index`, `find`, `short`. |
| `config.py` | **Extended.** | The settings named in sections 7 to 11. Settings for open decisions have no default value. |
| `requirements.txt` | **Extended.** | `vapoursynth` and `vapoursynth-brawsource` (D3). |
| `README.md`, `CHANGELOG.md` | **Not changed by this plan.** | They still describe v1.1.0. Each step updates them when it is built. |
| Tests | **Kept and extended.** | All 60 tests of v1.1.0 stay and must pass after every step (the last check of each step). |

**New server endpoints**

| Method | Path | Effect |
|--------|------|--------|
| GET | `/api/timeline` | The timeline file, its hash, and the derived film times of each segment. |
| POST | `/api/timeline/edit` | Operations of section 4.4 with the starting hash. Answers 409, 422 or 400 as described there. |
| GET | `/api/preview/<file>` | A file from `<work_dir>/previews/`, with HTTP range requests (`Accept-Ranges`, `206`). Any other path gives 404. |
| GET | `/api/stills/<path>` | A file from `<work_dir>/stills/`, same rule. |
| POST | `/api/convert`, `/api/assemble`, `/api/preview`, `/api/render` | Start the job. Share the job slot (409 if busy). |
| GET | `/api/status` | As today. New `step` values: `converting`, `analysing`, `assembling`, `previewing`, `rendering`, `transcribing`, `indexing`. |

---

## 13. How the Checks Are Run

- All checks use the standard `unittest` module or a tool command. The umbrella command is `python3 -m unittest discover -s tests -v`. It runs every test that can run on the machine.
- Tests build their media (video with `ffmpeg` sources such as `testsrc2`, `color`, `geq` and `crop` expressions; audio with `numpy`; speech with the macOS `say` command) inside a temporary folder. The repository stores no media file.
- Environment variables name what only the owner's machine has:

  | Variable | Meaning |
  |----------|---------|
  | `VET_FFMPEG` | Path of the `ffmpeg-full` binary. |
  | `VET_REAL_BRAW` | One real `.braw` clip. |
  | `VET_WORK_DIR` | A work directory on the SSD. |
  | `VET_LUT_DIR` | The folder with the owner's `.cube` files. |
  | `VET_WHISPER_MODEL` | The Whisper model file. |
  | `VET_SLOW` | `1` adds the 4K variants of render tests. |

- A test whose requirement is missing is **skipped with its reason printed**. A skipped test is not a pass. A step is done when its checks marked real or by hand have been run and their output is saved under `<work_dir>/reports/`.
- A check is never weakened to make it pass. If a starting value is wrong, the value changes in `config.py` and the reason is written in the commit.

## 14. Error Handling in the New Parts

These cases are added to Appendix A.13.

| Situation | What the tool does |
|-----------|--------------------|
| `ffmpeg-full` missing | Commands that need it (render, words, stills) stop with the install hint `brew install ffmpeg-full`. Matching and conversion keep working with the existing ffmpeg. |
| VapourSynth or the BRAW plugin missing | `convert` stops with the install steps. Matching is not affected. |
| SSD not mounted or not writable | Every command that writes to the work directory stops before any work. |
| Not enough free space | `convert` stops and prints the estimate and the free space (C6). |
| Conversion interrupted | A `.partial` file is deleted at the next start and that clip is converted again. There is no resume inside a clip. |
| A copy fails check C5 | The clip is marked `failed` with the reason. The run goes on with the next clip. The exit code is 1 at the end. |
| Working copy missing at render | Stop, name the clip, suggest `convert`. |
| Timeline invalid | `render` and `preview` stop and print the findings. |
| Timeline changes while a render runs | The render uses the file as it read it at the start and records its hash. The page shows that the film changed since the last render. |
| No clap, no steady run, unanalysed clip | Not an error. Written to the assembly report and flagged. |
| Audio does not cover a segment; crossfade shortened; gain capped; silent segment; limiter could not reach the limit | Warnings in the render report. The render finishes. |
| Whisper model or voice-activity model missing | `words` stops and prints the path it looked for. |
| Disk full during a render | The `.partial` file stays. The error names the folder and the free space. |
| A write into a folder that holds an original | Refused (M9). |
| Stale hash on a page edit | 409, nothing is changed. |

## 15. Security and Privacy in the New Parts

These are added to Appendix A.15.

- The server stays on `127.0.0.1`, with the Host and Origin checks of v1.1.
- The new file endpoints resolve the requested path and serve it only if it lies inside the folder named in section 12. A path with `..`, a symbolic link out of the folder or an absolute path gives 404.
- The page cannot set a path. Its operations (section 4.4) name segments, not files. `sources` can be written only by the tool's commands.
- Nothing is sent to any network address at run time. Setup downloads (Homebrew, the BRAW plugin, the Whisper model) are done by the owner.
- `ffmpeg-full` is GPL. The tool starts it as a separate program and does not bundle it [inference, not legal advice]. The Blackmagic runtime that the BRAW plugin carries and the Blackmagic `.cube` files are not part of this repository.
- `project.json`, the timeline files and the history folder hold the owner's paths and are git-ignored.

## 16. Open Decisions

The questions are set out in `PRD.md` section 14, with the owner's words. This table says what the design does until each is answered.

| ID | Question | The design until it is answered | Step |
|----|----------|---------------------------------|------|
| D1 | SSD path and space; ProRes profile | `work_dir` and `profile` are required settings; no default. Sizes are estimated from the first converted clip. | 1 |
| D2 | Colour generation and colour state of the copies | Both states are supported (section 6.5). The colour test makes stills of each. | 1 |
| D3 | Python 3.12 for the whole tool or only for `convert`; extra packages | No package beyond `numpy`, `scipy`, `tqdm`, `vapoursynth`. Analysis is written with `numpy` and `scipy`. `convert` runs as its own process, so it can run in another environment. | 1 |
| D4 | Resolve option stays or goes | Code stays and is not extended (section 12). | any |
| D5 | Frame shape of the 4K film | `assemble` requires `--frame` and `--crop-mode`. Both modes work (section 5.2). | 2 |
| D6 | Clap position, guard, no-clap policy | Guard 0.5 s. A clip with no clap is not cut and is flagged. | 2 |
| D7 | Meaning of "most stable scene"; shortest scene; no-run policy | Every steady run of at least 2 s becomes a segment. A clip with no run is left out and reported. | 2 |
| D8 | Picture between scenes; fade lengths | Hard cut. Fade-in 1.0 s, fade-out 2.0 s, crossfade 0.5 s as starting values. | 2 |
| D9 | Loudness target; per segment or per film | `target_lufs` is required when the mode is not `off`. Mode `segment_then_film` is available. | 3 |
| D10 | LUT collection and sources | `VET_LUT_DIR` names a folder. Nothing is bundled. | 3 |
| D11 | Audio in the Shotcut project | One synced audio clip per segment. | 3 |
| D12 | Whisper model | `VET_WHISPER_MODEL`. A timing test on a two-minute sample comes first. | 4 |
| D13 | Subtitle form; font | `mode` and `font` are required in the file when subtitles are burned. | 4 |
| D14 | Meaning of "cut out" in prompts; scene search method | "Cut out" is read as remove. `keep` exists. Search is by Claude Code reading sheets and the transcript. | 5 |
| D15 | Default short length | `--length` is required. | 6 |
| D16 | Crop and importance | Crop 0.5 (centre) unless set. Weights of section 11.3 as starting values. | 6 |

## 17. Unverified Statements and What Settles Them

Every statement tagged [unverified] above, and a few [inference] ones that the design leans on, are listed here. None of them is recorded as a fact.

| Statement | Settled by |
|-----------|------------|
| The `vapoursynth-brawsource` options in section 6.2 exist with those names, and the plugin decodes the owner's clips. | Checks 1.6, 1.7 |
| The frame rate, frame size and colour generation of all clips. One clip is 4128 x 2176 at 25 frames per second. | Check 1.9 (the probe of every clip) |
| What the two timed-metadata tracks of a clip hold. | Check 1.6 report |
| Speed, size and the better encoder (`prores_videotoolbox` or `prores_ks`); whether two conversions at once are faster. | Checks 1.7, 1.8 |
| Which colour state the copies should hold. | Check 1.7, the owner (D2) |
| The clap method and its starting values find the real claps. | Check 2.14 |
| Clock drift between camera and recorder over a long clip. | Check 2.14 |
| The offsets of -5.03 s, -1.70 s and the seven between 0.63 and 1.94 s in the first shoot are correct. | Check 2.14 (the coverage warnings of the render plan) |
| The steady-run settings separate camera movement from subject movement on real footage. | Check 2.14 |
| Decoding 4K ProRes for analysis is fast enough. | Check 2.14 |
| `-ss` before `-i` is frame-exact on a real working copy. | Check 2.11 |
| `lut3d` keeps 10-bit precision and uses the right matrix around the RGB conversion. | Check 3.1 |
| The pipe from the segment decoders to the encoder does not limit the speed, and the 8-bit result shows no banding. | Check 3.8 |
| The Shotcut template, the annotations and the filters of section 8.6 open and play as described. | Check 3.9 |
| The `whisper` filter's option names, voice-activity model and word times. Its speed. | Check 4.8 and the two-minute timing test |
| Claude Code finds scenes from sheets and a transcript. | Check 5.5 |
| All H2N files are stereo at 96 kHz (one was checked). | Check 1.9 (the probe of every WAV) |
| A Rec.709 or Film copy tagged as in section 6.5 displays correctly in the players the owner uses. | Check 3.8 |

---

## Appendix A. The System Built as v1.1.0

This appendix keeps the description of the built system, with the original wording where it still holds. It is the reference for the parts the plan reuses. Each part starts with a line "Plan:" that says what the plan does with it (section 12 has the full table).

### A.0 Process model

Everything runs in one Python process. Long operations (match, sync, resolve) run on daemon threads, one at a time; the UI polls `/api/status`. The plan keeps this (section 2.5).

Terminology: the code and this appendix call a matched pair of one video file and one audio file a "scene" (`SceneMatch`). Sections 1 to 17 call it a clip, and use "scene" for a steady stretch of a clip.

### A.1 `config.py`

*Plan: extended with the settings of sections 7 to 11 (section 12). Settings for open decisions get no default.*

Constants only (unused constants from the old export/re-encode design were removed). Values:

| Name | Value | Purpose |
|------|-------|---------|
| `VIDEO_EXTENSIONS` | `.mp4 .mov .avi .mkv .mxf .braw` | Discovery (case-insensitive) |
| `AUDIO_EXTENSIONS` | `.wav .flac .aiff .aif` | Discovery |
| `MATCH_SAMPLE_RATE` | 16000 | Downsample rate for correlation |
| `MATCH_MAX_DURATION_SEC` | 60 | Only first N seconds extracted for correlation |
| `MATCH_PRESERVE_ORDER` | `True` | Order-preserving (non-crossing) matching; `False` = plain Hungarian |
| `MATCH_MAX_DURATION_DIFF_SEC` | 10.0 | Order mode: pairs with a larger duration difference are disallowed |
| `MATCH_SKIP_PENALTY` | 6.0 | Order mode: cost (s) of leaving one file unmatched |
| `THUMBNAIL_TIME_SEC` / `_WIDTH` / `_FORMAT` / `_QUALITY` | 3.0 / 480 / jpg / 85 | Frame grab position, size, encoding |
| `RENAME_PATTERN` | `scene_{index:02d}` | Legacy `rename` command only |
| `SELECTOR_PORT` | 8765 | HTTP port |
| `PROJECT_FILE` | `project.json` | Persistence path (relative to CWD) |


### A.2 `project.py` - data model

*Plan: kept. New field `work_dir`. The editor no longer uses the trims (section 12).*

```python
@dataclass
class SceneMatch:
    index: int
    video_path: str
    audio_path: str
    video_duration: float
    audio_duration: float
    offset: float          # seconds, t_audio - t_video (see below)
    confidence: float      # 0..1, margin based (see A.3)
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


### A.3 `matcher.py` - pairing algorithm

*Plan: kept. One optional argument is added to the audio extraction (section 7.3).*

**Inputs:** `video_dir`, `audio_dir`, optional `max_duration`.
**Output:** `list[SceneMatch]` sorted by video filename using the natural key (indices renumbered after sorting).
`video_dir` and `audio_dir` may be the same folder (extensions are disjoint).

Pipeline:

1. **Discover** files via `find_files()`: one directory listing, extension compared case-insensitively, hidden files (`._*`) ignored, deduplicated by resolved path (no double entries on case-insensitive filesystems), returned in natural order. `_natural_key(name)` splits digit runs and compares them as numbers, case-insensitive (`ZOOM0009 < ZOOM0010`, `C112 < C113`). Natural order is the **recording order**.
2. **Durations** via `ffprobe -show_entries format=duration` for every file. A file ffprobe cannot read is skipped and reported.
3. **Cost matrix** `C[i][j] = |dur_video_i - dur_audio_j|`, `inf` where the difference exceeds `MATCH_MAX_DURATION_DIFF_SEC` (order mode).
4. **Initial assignment.**
   - **`MATCH_PRESERVE_ORDER = True` (default).** Both lists are in recording order and the matching must be monotonic: if video *i < k* are matched to audio *a, b* then *a < b*. Either side may have unmatched files anywhere. `_align_in_order(cost, skip_penalty)` is a pure DP (sequence alignment with skips, O(n*m)) minimising `sum C[i][j]` over matched pairs `+ MATCH_SKIP_PENALTY` per unmatched video and per unmatched audio file; ties prefer matching. With tolerance 10 s and penalty 6 s a pair is matched whenever its difference is below 10 s (skipping both would cost 12 s), so real pairs (0.1-0.3 s, a few seconds at worst) always match while wildly different durations are skipped instead of forced. Console output lists the sorted order used and every skip (`C108: no audio (skipped)`).
   - **`MATCH_PRESERVE_ORDER = False`.** `scipy.optimize.linear_sum_assignment(C)` on the plain duration matrix; order is ignored (use for cards whose numbering resets).
5. **Ambiguity detection.** For each matched video *i* with audio *j\**, candidates are `{ j : |C[i][j] - C[i][j*]| <= 5 s }`. In order mode only **order-admissible** columns count: those strictly between the audio indices of the previous and next matched video (the neighbours' window) with finite cost. Rows with more than one candidate get correlation scores.
   - Audio is extracted as mono 16 kHz PCM (first 60 s) for the video and all candidates, **in parallel** (`ThreadPoolExecutor`, 4 workers) and cached per file.
   - Score = peak of the **normalised cross-correlation (NCC) of onset envelopes**: 20 ms mean-abs windows, `log(env + eps)`, positive first difference, mean removed. Steady ambience contributes ~0, claps and transients dominate, gain differences do not matter. Scores lie in [0, 1].
6. **Re-solve with correlation-adjusted cost.** For every row that has scores, each column gets `+ W * (best_score_in_row - score)` (missing score counts as 0, `W = 20 x 5 s`, so a 0.1 correlation difference equals 10 s of duration error). The adjusted matrix is solved once, globally: by the DP in order mode (`_resolve_alignment`, so correlation can only choose among order-consistent alignments) or by Hungarian otherwise (`_resolve_assignment`). Every score and reassignment is logged to the console.
7. **Confidence (margin based, 0..1).**
   - Row decided by correlation (scores for >= 2 columns): `(score_chosen - best_other) / 0.2`.
   - Otherwise duration margin: `(runner_up_cost - chosen_cost) / 5 s`. In order mode the runner-up is the best alternative that is **order-admissible** (inside the neighbours' window, via `_admissible_cost`; neighbours' assignments are held fixed). A video with no admissible alternative gets full margin.
   - Caps: duration difference >= 30 s caps at 0.1, 10-30 s caps at 0.25.
   - Console status buckets: `<10 s OK`, `10-30 s WARN`, `>=30 s BAD`.
8. **Unmatched files** (videos without audio, audio without video, ffprobe-skipped files) are listed in the console.
9. **Sync offset** per final pair via `_find_offset()`:
   - *Coarse:* NCC of onset envelopes gives a lag in 20 ms windows.
   - *Fine:* cross-correlation of high-passed (500 Hz Butterworth) raw signals within +-0.1 s (+1 window) of the coarse lag, with **parabolic interpolation** around the peak for sub-sample precision.
   - Returns `t_audio - t_video` in seconds; 0.0 if either signal is empty.

Correlation decisions are console-only; the UI shows only the final confidence.


### A.4 `thumbnails.py`

*Plan: changed to read the working copy when one exists (section 12).*

Videos ffmpeg cannot decode (e.g. `.braw`) make `extract_thumbnail` raise `RuntimeError`; callers (`server.py`, `extract_all_thumbnails`) log it and continue, and the UI shows "No thumbnail".

`extract_thumbnail(video_path, out_path)` -> `ffmpeg -ss 3 -i ... -vframes 1 -vf scale=480:-1`. `extract_all_thumbnails(project, output_dir)` is used by `main.py match`; the server calls `extract_thumbnail` per scene so it can report progress. Both write to `thumbnails/` **next to `project.json`** (`thumb_<index>.jpg`) and fill `SceneMatch.thumbnail_path`. The old standalone HTML selector was removed.


### A.5 `syncer.py` - audio copy/rename

*Plan: kept only if D4 says so (requirement R1).*

```python
def sync_all_scenes(project, output_dir=None) -> list[dict]
def remove_copied_audio(output_dir) -> list[str]
```
- `out_dir = output_dir or project.video_dir`.
- For each ordered scene: `new_name = Path(video).stem + Path(audio).suffix`; `shutil.copy2(audio, out_dir / new_name)` unless source == destination. If the destination exists with a different size (`os.path.getsize`) a warning is logged before it is overwritten.
- Returns `[{order, scene_index, video_path, audio_original, audio_copy, offset, trim_start, trim_end}, ...]`.
- **Manifest:** every copy is listed in `ambient_audio_copies.json` in the output folder (`{"copies": [file names]}`), merged with an existing manifest and written atomically.
- **Undo:** `remove_copied_audio(output_dir)` deletes only files named in the manifest (plain file names; entries with path components are skipped), then the manifest, and returns the removed paths. CLI: `python3 main.py sync --undo [--output DIR]` (output folder defaults to the project's output/video folder). Satisfies PRD F14 for copies. The server has no undo endpoint yet.
- **Never** invokes ffmpeg. Trims are stored only; they are applied in Resolve.


### A.6 `renamer.py`

*Plan: kept, untouched.*

Legacy, CLI-only helper that renames *both* files of a pair to `scene_NN.*` and writes `rename_log.json`; `undo_renames()` reverses it. Not used by the UI. Before any rename, `_check_collisions()` verifies that no target exists (other than the file itself) and that no two renames share a target; on a conflict it raises `FileExistsError` listing the problems and **no file is touched** (and no log is written). Undo runs the same check for old names that are already occupied.


### A.7 `resolve_integration.py`

*Plan: kept only if D4 says so (requirement R2). The offset rule of `build_timeline_plan` is reused (section 5.4).*

```python
def build_timeline_plan(scenes, fps) -> list[dict]     # pure, no Resolve import
def resolve_media_paths(project, scenes) -> list[tuple]
def apply_plan(resolve, project_name, timeline_name, scenes, plan=None, media_paths=None, fps=None) -> dict
def create_resolve_project(project, project_name, timeline_name, fps=None) -> dict
def list_resolve_projects() -> list
```
1. `build_timeline_plan()` is pure and unit-tested. Per scene it returns `{"index", "video": clipInfo, "audio": clipInfo | None}` with `clipInfo = {startFrame, endFrame, recordFrame, trackIndex, mediaType}`. Video: `trackIndex=1, mediaType=1`; audio: `trackIndex=2, mediaType=2`. Source frames come from trims; audio `startFrame = video start + round(offset*fps)`, a negative result becomes a `recordFrame` delay; audio is clamped to the file length. Scenes are laid back-to-back; `recordFrame` is 0-based relative to the timeline start.
2. `resolve_media_paths()` prefers the renamed sibling `<video_stem><audio suffix>` in the video folder or `project.output_dir`, falling back to the original `audio_path`.
3. `apply_plan()` does the API calls: `_get_resolve()` imports `DaVinciResolveScript` (auto-detects the Modules path, raises `ConnectionError` with setup steps); loads or creates the project; optionally sets `timelineFrameRate`; imports media; creates the timeline; ensures two audio tracks; reads the timeline frame rate if none was given; builds the plan if needed; offsets `recordFrame` by `timeline.GetStartFrame()`; calls `AppendToTimeline`. Files not found in the Media Pool are skipped and reported.
4. **Timeline name suffix:** if the timeline name already exists in the project, ` 2`, ` 3`, ... is appended instead of failing.

**Flagged API assumptions** (not verifiable without Resolve; check when first run against a real install):
- `endFrame` in `AppendToTimeline` clip infos is treated as exclusive.
- `mediaType` 1 = video, 2 = audio, and `trackIndex` 2 for audio places clips on A2.
- `timelineFrameRate` only takes effect before the project's first timeline exists.
- Media Pool items are matched back to files by their `File Path` property after `ImportMedia`.
- `recordFrame` is absolute: the plan's 0-based frames are shifted by `timeline.GetStartFrame()` (typically 86400 at 24 fps for 01:00:00:00).

**Constraint:** external invocation may require Resolve **Studio**. For the free edition the same logic must be executed from inside Resolve. The script planned for that, `resolve_import.py`, was never written; whether it will be depends on D4.


### A.8 `server.py` - HTTP API

*Plan: extended with new endpoints (section 12).*

`ThreadingHTTPServer` bound to `127.0.0.1`. Static: `GET /` -> `ui.html`.

| Method | Path | Body / Query | Effect |
|--------|------|--------------|--------|
| GET | `/api/status` | - | `{step, message, progress}` of the current background job (`step`: idle, matching, thumbnails, matched, syncing, synced, resolve, resolve_done, error) |
| GET | `/api/project` | - | Project as JSON; each match gains `video_file`, `audio_file`, `has_thumbnail`, `thumb_version` |
| GET | `/api/browse` | `?path=` | `{current, entries:[{name, path, is_dir, size}]}` for the folder picker |
| GET | `/api/thumbnail/<index>` | - | Image bytes (`Cache-Control: no-cache`) |
| POST | `/api/match` | `{video_dir, audio_dir}` | Validates folders (400), starts matcher + thumbnail thread |
| POST | `/api/select` | `{selection:[idx...], selection_trims:{idx:{start,end}}}` | Saves order/trims to `project.json` |
| POST | `/api/sync` | `{output_dir?}` | Starts `sync_all_scenes` thread (defaults to the video dir) |
| POST | `/api/resolve` | `{project_name, timeline_name}` | Starts `create_resolve_project` thread (legacy keys `name`, `timeline` still accepted) |

- **Job guard:** match, sync and resolve share one slot; starting a second returns **409** `Another job is already running`. The slot is released in `finally`.
- **Host/Origin check (403):** every request must carry `Host: localhost:<port>` or `127.0.0.1:<port>`; POSTs with an `Origin` header must be `http://` plus one of those. See A.15.
- **No CORS headers** are sent; browsers block cross-origin reads by default.
- **No `/api/rename`:** renaming is CLI-only (legacy).
- **`thumb_version`:** the thumbnail file's mtime in ns; the UI appends it as `?v=` so thumbnails refresh after a re-match.
- State: a module-level `_project` guarded by `_project_lock`; workers deep-copy the project before use and build new projects locally so the lock is not held during ffmpeg. A saved `project.json` is loaded at start-up (resume).
- Errors: invalid JSON -> 400, unexpected exception -> 500 `{error}`; worker exceptions end up in `status` with `step: "error"`.


### A.9 `ui.html`

*Plan: extended with new tabs (section 12).*

Single file, vanilla JS, no build step. Three tabs:

1. **Match** - folder picker modal (uses `/api/browse`), Match button, result table (video, audio, deltaduration, offset, confidence).
2. **Select & Order** - thumbnail grid (click to toggle), ordered timeline strip with HTML5 drag-and-drop, per-scene trim inputs.
3. **Copy Audio & Push to Resolve** - output folder (prefilled with video dir), *Copy & Rename Audio Files* button, Resolve project/timeline name fields, *Push to Resolve* button.

Polling loop (1 s) calls `loadProject()` **before** `updateStatus()` so result views never render stale data.


### A.10 `main.py` - CLI

*Plan: extended with new subcommands (section 12).*

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


### A.11 Data flow and persistence

*Plan: kept. `project.json` gets the field `work_dir`. The timeline file is separate (section 3).*

`project.json` (in the CWD) is rewritten atomically after every mutating step. Shape:

```json
{
  "video_dir": "/.../video",
  "audio_dir": "/.../audio",
  "output_dir": "",
  "matches": [ { "index": 0, "video_path": "...IMG_2231.MOV", "audio_path": "...ZOOM0012.WAV",
                 "video_duration": 182.4, "audio_duration": 183.1, "offset": 0.65,
                 "confidence": 0.996, "thumbnail_path": "/.../thumbnails/thumb_000.jpg",
                 "label": "", "trim_start": 0.0, "trim_end": 0.0 } ],
  "selection": [3, 0, 7],
  "selection_trims": { "0": { "start": 2.0, "end": 12.0 } }
}
```

Thumbnails live in `thumbnails/` next to `project.json`. The only other state is `ambient_audio_copies.json` (copy manifest) in the output folder and, for the legacy rename, `rename_log.json` in the video folder.


### A.12 External dependencies

*Plan: extended (sections 2.4 and 12). v1.1 states Python 3.9; the BRAW plugin needs 3.12 or newer, see D3.*

| Dependency | Min version | Install |
|------------|-------------|---------|
| Python | 3.9 | - (no 3.10-only syntax; developed and tested on 3.9) |
| numpy | 1.24 | `pip install -r requirements.txt` |
| scipy | 1.10 | " |
| tqdm | 4.65 | " |
| ffmpeg + ffprobe | any recent | `brew install ffmpeg` |
| DaVinci Resolve | 18+ (free or Studio) | optional, for A.7 |


### A.13 Error handling

*Plan: new cases are in section 14.*

- Missing `ffmpeg`/`ffprobe` -> `FileNotFoundError` with an install hint (`brew install ffmpeg`), surfaced in the UI status.
- `ffprobe` failure on a single file -> warning, file skipped from matching and listed as skipped.
- No readable video or audio at all -> `RuntimeError`.
- Audio extraction failure during the ambiguity check -> duration match kept for that row, logged; offset detection failure -> offset 0.0 with a warning.
- Destination exists with a different size during copy -> warning, file overwritten (recorded in the manifest, so `sync --undo` removes it).
- Legacy rename target collision -> `FileExistsError` before any file is touched.
- Missing audio copy when pushing to Resolve -> falls back to the original audio path, otherwise the audio is skipped with a warning.
- Resolve unreachable -> `ConnectionError` with the setup steps; the copy step is unaffected.
- A second job while one runs -> HTTP 409. Bad Host/Origin -> 403. Invalid JSON -> 400.
- All worker exceptions are caught and reported through `/api/status`; the server never dies on a job failure.


### A.14 Tests

*Plan: all of these tests stay (section 12); new checks are in sections 6 to 11 and 13.*

Run: `python3 -m unittest discover -s tests -v` (stdlib `unittest`, no extra dependencies, no ffmpeg or Resolve needed).

| File | Covers |
|------|--------|
| `tests/test_matcher.py` | Offset sign and precision (positive/negative shift, loud ambience), correlation score ordering, correlation-adjusted Hungarian re-solve (chained reassignment, partial scores, unequal counts), margin-based confidence, end-to-end pairing |
| `tests/test_resolve_integration.py` | `build_timeline_plan` (back-to-back, trims, positive/negative offset, clamping), project save/load round trip and unknown-key tolerance, `apply_plan` against fake Resolve objects |
| `tests/test_syncer.py` | Byte-identical copies, default output dir, manifest write/merge, `remove_copied_audio` removes exactly the copies, overwrite warning |
| `tests/test_renamer.py` | Collision and duplicate-target abort without renaming, dry run, rename + undo round trip |

Not covered: the HTTP server (Host/Origin checks, job guard), the UI, real ffmpeg extraction and a real Resolve connection; these are verified manually (26-scene shoot, 2026-10: all pairs matched once the correlation tiebreaker was added).


### A.15 Security and privacy

*Plan: new rules are in section 15.*

- Binds to `127.0.0.1` only and sends no CORS headers.
- **Host check:** every request must carry `Host: localhost:<port>` or `127.0.0.1:<port>`, otherwise 403. This defeats DNS-rebinding, where a hostile page resolves its own name to 127.0.0.1 and would otherwise read the API.
- **Origin check:** POST requests that carry an `Origin` header must have `http://localhost:<port>` or `http://127.0.0.1:<port>`, otherwise 403, which blocks cross-site form/fetch posts (CSRF). Requests without `Origin` (curl, same-origin navigation) are allowed.
- `/api/browse` lists directories for any path the user can read - acceptable for a single-user local tool, but the server must not be exposed on a LAN interface without adding auth.
- The sync undo only deletes names listed in its manifest and rejects entries containing path components.


---

## Appendix B. Sources

Facts tagged [documented] come from these sources or from the research report that preceded this plan. The report is dated 2026-10-26 and is not stored in this repository. Where the text says "research B6" it means section B6 of that report. Sources marked "secondary" are blogs, forums or vendor pages that repeat a claim; their wording was not checked against a primary source.

| ID | Source | Used for |
|----|--------|----------|
| Src1 | `vapoursynth-brawsource` (MIT): https://github.com/theChaosCoder/vapoursynth-blackmagicraw-source and its benchmark note `doc/gpu-benchmark.md` | Decoder 1: options, pipelines, wheels, speed on an M1 Pro. |
| Src2 | Blackmagic RAW SDK manual https://documents.blackmagicdesign.com/DeveloperManuals/BlackmagicRAW-SDK.pdf, SDK page https://www.blackmagicdesign.com/developer/products/braw/sdk-and-software, licence https://www.blackmagicdesign.com/support/modal/download-with-terms-start/bmd-braw-sdk-2 | Decoder 2, colour handling in the decoder, licence summary. |
| Src3 | Homebrew formula `ffmpeg-full`: https://github.com/Homebrew/homebrew-core/blob/HEAD/Formula/f/ffmpeg-full.rb | What `ffmpeg-full` enables (libass, freetype, zimg, x264, whisper) and that it is keg-only. |
| Src4 | FFmpeg developer list, April 2025 BRAW patch thread: https://ffmpeg.org/pipermail/ffmpeg-devel/2025-April/342224.html | FFmpeg has no BRAW decoder. |
| Src5 | MLT: https://www.mltframework.org/plugins/TransitionMix/, https://www.mltframework.org/plugins/FilterLoudness/, https://www.mltframework.org/plugins/FilterSubtitle/ | `mix` crossfade modes, `loudness` filter, `subtitle` filter (SRT). |
| Src6 | Shotcut: https://shotcut.org/notes/mltxml-annotations/, https://shotcut.org/download/, LUT filter note (secondary) https://forum.shotcut.org/t/lut-file-support/135 | Project annotations and the warning that they may change; current version; LUT filter. |
| Src7 | Kdenlive: https://discuss.kde.org/t/issue-with-python-generated-kdenlive-project-won-t-open/30729 and https://github.com/KDE/kdenlive/blob/master/dev-docs/fileformat.md | Project files are not a stable interface for other tools. |
| Src8 | DaVinci Resolve 21.1 release notes https://www.blackmagicdesign.com/support/content/readme/59dd4eef1f4941c29fb8dc48b33f5c87 and two reports (secondary, conflicting on the free edition): https://digitalproduction.com/2026/09/08/resolve-21-1-adds-mcp-and-finally-gets-presets/, https://www.pugetsystems.com/blog/2026/09/10/how-davinci-resolve-free-v21-1-scripting-changes-affect-puget-bench/ | Scripting moved to Studio; Studio's Claude connection. |
| Src9 | Shotcut project writer with `melt` validation (MIT, young): https://github.com/matrodrigs/shotcut-mcp | Prior art for generating Shotcut projects. |
| Src10 | Apple ProRes white paper: https://www.apple.com/euro/final-cut-pro/f/generic/docs/Apple_ProRes_White_Paper.pdf | Data rates per hour. The sizes for 73 minutes at 4128 x 2176 are arithmetic from them. |
| Src11 | YouTube recommended upload settings: https://support.google.com/youtube/answer/1722171 | Bitrates, audio settings, MP4 with `moov` first. |
| Src12 | YouTube loudness figure (secondary): https://opus.pro/blog/best-loudness-normalizers | About -14 LUFS and -1 dBTP. The official page above gives no number. |
| Src13 | FFmpeg `whisper` filter source: https://ffmpeg.org/doxygen/8.0/af__whisper_8c_source.html | The filter, its outputs and its voice-activity option. |
| Src14 | Shorts safe zones and length (secondary): https://www.clipspeed.ai/blog/youtube-shorts-size.html, https://blitzcutai.com/blog/youtube-shorts-length-limits-2026 | Safe area; 3 minutes since 2024-10-15. |
| Src15 | Claude vision documentation: https://platform.claude.com/docs/en/build-with-claude/vision | Token cost and size limits of images. |

Facts tagged [measured] were checked on 2026-10-26 on the owner's machine: the installed ffmpeg's filters and encoders, a read-only `ffprobe` of one clip and one WAV, the sizes and free space of the volumes, the owner's `project.json` (not committed), the v1.1.0 test run (60 tests, all passing), and two small scratch tests with synthetic video (frame-exact `-ss` on ProRes; phase correlation on a static texture). The scratch tests are not in the repository; the fixtures of section 7.8 recreate them.
