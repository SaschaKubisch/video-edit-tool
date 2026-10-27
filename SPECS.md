# Technical Specification - Ambient Video Auto

**Version:** 2.4 (draft for the owner's review)
**Date:** 2026-10-27
Companion to `PRD.md`. Sections 1 to 17 specify the planned editor. Appendix A describes the system built as v1.1.0, which the plan reuses. Appendix B lists the sources.

Of sections 1 to 17, step 2 is built on the branch `editor-step2`, except the contact sheets of 7.4: the Python 3.12 move, the `convert` command (section 6, with `decoder.py`, `safety.py`, `tools.py`), the timeline file's loader, validator and saver with `timeline validate` and `timeline summary` (section 3, rules V0 to V23), the clap finder with `analyse --clap` (section 7.2), the drift measurement with `analyse --drift` (7.3), the steady-scene finder with `analyse --scenes` (7.4), the assembly with `assemble` (7.5), the film preview with `preview` (5.5; the render plan of 5.1 and the sound path of 5.4 are built, the final film of 5.3 is not), saving with snapshots, the hash check, `timeline undo`, `timeline history` and `timeline frames` (4.1, 4.3), the page's operations (4.4), the owner's sync offset per clip (3.2, V23, 7.6) and the Film tab with its endpoints (7.6, section 12). Of step 3 (section 8) the following is built on the same branch: the levelling of every segment and of the film with the true-peak limiter (5.4, 8.4), the creative LUT (`look`, 8.2, 8.3), the final film from the `.braw` files with the presets and the checks (`render`, `export`, 5.3, 5.6, 5.7, 8.5) and the ProRes master. The Shotcut project (`export-shotcut`) and its read-back (`import-shotcut`, 8.6) are built too; the page's part of step 3 (8.7) is not built unless a line there says so. The module table in section 2.3 says which module is built. Where the build found the plan wrong, the text says "changed in the build". The test of step 1 was run by hand with scripts that are not in the repository (section 6.1). Every other interface named here (commands, endpoints, file names, module names) is planned, and names may change when the code is written.

Version 2.4 records the owner's answers of 2026-10-27 for step 4: the Whisper model (D12, "large-v3-turbo"), the subtitle form (D13, "This should be configurable actually."), the font (D13, "Inter"), a text box per scene, one subtitle style for all films and the translations. Section 9.0 quotes them. Step 4 is being built on the branch `editor-step2`; where it says "As built", the code does it.

Version 2.2 adds the owner's decisions on the clap (D6), steady scenes (D7, with the clip that has no steady scene: "Leave it out, list it"), transitions (D8), reading Shotcut cuts back (D11), the encoder (D17), loudness (D9, "Each scene, then film"), the sources of creative LUTs (D10, "Research free packs") and the offsets of two clips ("Leave them, I set them"). Step 2 is being built on the branch `editor-step2`; where the build found the plan wrong, the text is changed and marked "changed in the build".

Version 2.1 records the owner's decisions of 2026-10-26 (storage option A, colour, DCI 4K frame, Python 3.12 with OpenCV, the export format) and the results of step 1. The ProRes working copies of version 2.0 are replaced by H.264 preview copies, and the final render reads the `.braw` files.

---

## Terms

The terms of `PRD.md` apply (clip, scene, segment, film, timeline file, offset, preview copy, film preview, ProRes, LUT, LUFS, and the others). These terms are added.

- **Frame number**: the integer position of a frame in a clip, counted from 0. The `.braw` file and its preview copy hold the same frames under the same numbers. A segment's `in` is the first frame it uses. Its `out` is the first frame it does not use.
- **Base LUT**: the LUT that converts the camera's Blackmagic Film picture to Rec.709. The default is "Blackmagic Gen 5 Film to Video". **Creative LUT**: an optional second LUT for the look, applied after the base LUT.
- **Fit** and **fill**: two ways to place a clip's picture in the film frame. Fit keeps the whole picture and adds black bars. Fill covers the frame and cuts off the overflow (section 5.2).
- **Real-time factor**: seconds of film made per second of work. 2.0 means twice as fast as playing the film; 0.5 means half as fast.
- **PSNR**: peak signal-to-noise ratio in dB, a measure of how close two pictures are. Higher is closer; above 40 dB differences are hard to see [inference].
- **Load average**: the average number of busy processes on the machine over one minute. A high load slows every measurement.
- **Film time**: seconds from the first frame of the film. **Source time**: seconds from the first frame of a clip.
- **Frame rate**: written as a ratio of two integers, for example 30000/1001 (29.97), so the rate is exact.
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

Tags: **[measured]** checked on the owner's machine or files on 2026-10-26; **[documented]** a source states it (Appendix B, written Src1, Src2 and so on); **[arithmetic]** computed from stated numbers; **[inference]** reasoning, not checked; **[unverified]** a design statement that a check in this document settles. **Decision** means an owner decision from `PRD.md` section 3. **Starting value** is a number chosen to begin with. It is not a decision, and the step that uses it calibrates it on real clips.

---

## 1. Overview

Version 1.1.0 matches camera clips with recorder files and prepares them for Resolve. The plan adds six steps. They turn the matched pairs into a finished film inside the tool.

1. **Conversion test (done).** A test by hand on two clips showed that the decoder works and measured speed, size and colour (section 6.1). Its method becomes the `convert` command, which makes one small H.264 preview copy of each `.braw` clip on the SSD. That command is built first in step 2.
2. **Preview copies, timeline file and assembly.** The tool makes the preview copies, finds the clap and the steady scenes, writes the timeline file and renders a film preview.
3. **Look and sound.** The base LUT in the final render, an optional creative LUT, levelled loudness, the DCI 4K export from the `.braw` files, an optional ProRes master and the Shotcut project.
4. **Words.** English subtitles and titles.
5. **Prompt edits.** Claude Code changes the timeline file from a description.
6. **Vertical shorts.** A 9:16 short from the film.

The timeline file is the centre. Every step reads it or writes it. Every output (film preview, film, Shotcut project, short) is made from it.

## 2. Architecture

### 2.1 Data flow

```
 ORIGINALS (read only)          THE TOOL                         WORK DIRECTORY (SSD)
 .braw  .WAV
   |                  +-------------------+
   +----------------> | matcher.py        | --> project.json (pairs, offsets, work_dir)
   |                  +-------------------+
   |                  +-------------------+
   +----------------> | convert.py        | --> copies/<stem>.mp4 (preview copies), conversion.json
   |                  +-------------------+
   |                  +-------------------+
 preview copies, WAV  | clap.py stable.py | --> analysis/<stem>/clap.json, scenes.json
 ------------------>  | words.py index.py | --> transcript.json, contact sheets, index.md
                      +-------------------+
                      +-------------------+
 project.json,        | assemble.py       | --> timeline.json (next to project.json)
 analysis/*  -------> +-------------------+            ^
                                                       | edits
            page (trim operations) / Claude Code (file edits) / tool commands
                                                       |
                      +-------------------+            v
 timeline.json -----> | render.py         | --> previews/ (film preview, from the preview copies)
 preview copies ----> |                   |
 .braw (full size) -> |                   | --> renders/*.mp4, *.mov, *.srt, *.ass (from the .braw files)
                      | export_shotcut.py | --> film.mlt, audio-synced/  (uses the preview copies)
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
  conversion.json                     one entry per clip: status, decoder, metadata, speed, size, baked LUT hash
  copies/<stem>.mp4                   preview copy (while written: <stem>.mp4.partial)
  audio-synced/<stem>.wav             WAV with the offset applied, made for the Shotcut export
  analysis/<stem>/clap.json           clap position in the camera's scratch audio and in the WAV
  analysis/<stem>/scenes.json         steady scenes and movement (named stable.json in the plan; changed in the build)
  analysis/<stem>/transcript.json     English transcript with times
  analysis/<stem>/sheets/*.jpg        contact sheets
  analysis/index.md                   text index for Claude Code
  stills/                             LUT comparison sheets and frames asked for by the commands
  previews/seg-<hash>.mp4             film preview cache, one file per segment
  previews/preview.mp4                the assembled film preview
  renders/<name>.mp4, .mov, .srt, .ass  final files (the export names are set in the timeline file)
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
| `project.py` | `Project`, `SceneMatch`, `project.json` | Kept, one new field (`work_dir`) | 2 |
| `tools.py` | Find `ffmpeg-full`, `ffprobe`, VapourSynth; check filters. Built so far: the paths of `ffmpeg` and `ffprobe` (`VET_FFMPEG`, else `PATH`). | New, partly built | 2 |
| `safety.py` | `assert_not_inside`, `originals_folders`: the protection of the originals (M9). Not in the plan before the build (changed in the build). | New, built | 2 |
| `decoder.py` | The decoder interface (section 6.2): probe a `.braw` clip, give frames and camera audio. Used by `convert.py` and `render.py`. Built: the interface and decoder 1 (`BrawSourceDecoder`). | New, built | 2 |
| `convert.py` | Probe, make preview copies, verify, `conversion.json` | New, built | 2 |
| `clap.py` | Find the clap, measure drift. Built: the clap (`find_clap`, `analyse_clip`, `analyse_project`) and the drift (`measure_drift`, `analyse_drift_project`). | New, built | 2 |
| `stable.py` | Find steady runs. Built (changed in the build: the result file is `scenes.json`, and OpenCV does the correlation, see 7.4). | New, built | 2 |
| `timeline.py` | Load, save, validate, hash, derived values. Built: load, atomic save, validate (V0 to V23), hash, derived values, the offset a segment's sound uses (`effective_offset`). Snapshots and the hash check are in `store.py`. | New, built | 2 |
| `store.py` | Save the timeline file with a numbered snapshot of the old file and a hash check (4.1); `undo`, `history`, the snapshot after a validate (4.3). Not in the plan before the build (changed in the build). | New, built | 2 |
| `edits.py` | The operations the page may send (4.4), including the owner's sync offset per clip (`set_offset`, `reset_offset`), and the summary of the film (4.3). Not in the plan before the build. | New, built | 2 |
| `frames.py` | Stills at film times or source times (`timeline frames`, 4.3). Not in the plan before the build. | New, built | 2 |
| `fsutil.py` | Atomic writes of JSON and text files, shared by the analysis, assembly and render modules. | New, built | 2 |
| `assemble.py` | Build `timeline.json` from matches and analysis. Built. | New, built | 2 |
| `render.py` | Render plan, sound path, film preview. Built: the plan, the sound path (`build_mix`, with the levelling of 5.4) and the film preview. The final film is in `export.py` (changed in the build). | New, built | 2, 3 |
| `export.py` | The final film from the `.braw` files: presets, the segment and encoder processes, the sound file, the checks, the credit file, the render report, the background start. Not in the plan before the build (changed in the build). | New, built | 3 |
| `look.py` | The creative LUT: finding the `.cube` files in `VET_LUT_DIR`, checking one, the hash-named copy, `look.lut` with its credit. The comparison stills (8.2) are not built. | New, partly built | 3 |
| `loudness.py` | Measure (ebur128), the gain of a segment, the gain and limiter of the film | New, built | 3 |
| `export_shotcut.py` | Write `film.mlt` and `audio-synced/`; check the project with melt | New, built | 3 |
| `import_shotcut.py` | Read cut points and order from `film.mlt` back into the timeline file (D11) | New, built | 3 |
| `words.py` | Transcribe, build cues, titles, ASS and SRT | New | 4 |
| `index.py` | Contact sheets and `index.md` | New | 5 |
| `shorts.py` | Build a vertical timeline | New | 6 |
| `thumbnails.py` | Frame grabs | Kept, reads preview copies | 2 |
| `server.py`, `ui.html`, `main.py`, `config.py` | Page, API, commands, constants. Built for step 2: the Film tab and its endpoints (section 12), `analyse --scenes`, `assemble`, `preview`, `timeline summary`, `app --timeline --work --no-browser`. | Kept, extended | 2 to 6 |
| `syncer.py`, `resolve_integration.py` | Audio copy, Resolve push | Kept only if D4 says so | - |
| `renamer.py` | Legacy rename | Kept, untouched | - |

The code stays flat in the repository folder, as today.

### 2.4 Programs

| Program | Used for | How the tool finds it |
|---------|----------|-----------------------|
| Python 3.12 in a virtual environment (D3, settled) | The whole tool | A `.venv` folder next to the tool, git-ignored [Design]. Python 3.12 is installed with Homebrew [measured]. The test of step 1 used a scratch environment. |
| `ffmpeg-full` 9.0.2 (GPL, keg-only) | All new decode, render, encode, loudness measurement, subtitle burn-in, Whisper | Environment variable `VET_FFMPEG`, else `$(brew --prefix ffmpeg-full)/bin/ffmpeg`. It is not on `PATH` because it is keg-only [documented, Src3]. |
| `ffprobe` of `ffmpeg-full` | Stream facts | The same folder. |
| Existing `ffmpeg` 8.1 and `ffprobe` on `PATH` | The unchanged matching code. The step-1 tests (preview copies, full-size render) also ran on it: it has `lut3d`, `pad`, `libx264`, `h264_videotoolbox`, `hevc_videotoolbox` and `prores_videotoolbox` [measured]. | `PATH`, as today. |
| VapourSynth R81 with `vapoursynth-brawsource` 0.5.0 | Decoding BRAW (decoder 1): the preview copies and the final render | Python import in the tool's environment [measured: both clips decode]. |
| Blackmagic RAW SDK | Decoding BRAW (decoder 2, only if the plugin fails) | Not written yet. |
| Shotcut 26.9.28 | Hand work; opening `film.mlt`. Its `melt` program checks the project file. | `/Applications/Shotcut.app`; `melt` at `/Applications/Shotcut.app/Contents/MacOS/melt` (version 7.41.0) [measured]. Run `melt` with `QT_QPA_PLATFORM=offscreen` so that no window opens. |
| `whisper-cli` 1.8.3 (whisper.cpp) | Speech to text for the automatic subtitles (step 4, D12) | `VET_WHISPER_BIN`, else `whisper-cli` in `/Applications/Shotcut.app/Contents/MacOS/`, else on `PATH` [measured]. Run as a command-line program only; Shotcut is not started. |
| The model `ggml-large-v3-turbo.bin` | Whisper (D12) | `VET_WHISPER_MODEL`. Downloaded once by hand, never by the tool. |
| Inter 4.1 (SIL Open Font License 1.1) | The font of subtitles and titles (D13) | `VET_FONT_DIR`, the folder with `Inter-SemiBold.ttf`, `Inter-Bold.ttf` and `LICENSE.txt`. Not installed on the system, not committed. |

A command `python3 main.py tools check` prints each program, its version and whether the needed filters exist: `lut3d`, `pad`, `subtitles`, `ebur128`, `alimiter`, `whisper`, `crop`, `scale`, and the encoders `libx264`, `h264_videotoolbox`, `hevc_videotoolbox`, `prores_videotoolbox`, `prores_ks`. [unverified until `ffmpeg-full` is installed; the existing ffmpeg 8.1 has all of them except `subtitles` and `whisper`.]

### 2.5 Processes

The server keeps the one-job-at-a-time rule of v1.1: convert, assemble, render, transcribe and index share one job slot, and a second request gets HTTP 409. Edits to the timeline file are not jobs. They finish at once.

Conversion and rendering run as separate processes started by the server: a decoder process (Python with VapourSynth) feeds `ffmpeg` through a pipe. The whole tool runs in one Python 3.12 environment (D3, settled). These processes do not depend on the server's threads. The page polls their status.

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
| `look` | object | yes | The base LUT and the optional creative LUT. |
| `audio` | object | yes | Sample rate and loudness. |
| `titles` | array | no | Title texts. |
| `subtitles` | object | no | Subtitle cues and style. |
| `exports` | array | no | Output files to make. Needed to render the final film. |
| `derived_from` | object | short only | Which film file and which settings made this short. |

**`film`**

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `title` | str | no | Name for people. |
| `fps` | `{num, den}` | yes | Film frame rate as a ratio, for example 30000/1001 (29.97). |
| `frame` | `{width, height}` | yes | Output picture size in pixels. Both even. The default is 4096 x 2160, DCI 4K (decision D5). |
| `fit` | str | no | `fit` (default): keep the whole picture of each clip. It is scaled down only if it does not fit the frame, and centred. Black bars fill the rest: a 4096 x 1720 clip in a 4096 x 2160 frame gets bars of 220 pixels above and below. `fill`: the picture covers the frame and the overflow is cut off; each segment's `crop` places the window. Shorts use `fill` (section 5.2). |
| `fade_in_s` | num | no | Fade from black and from silence at the film's start. Starting value 1.0. |
| `fade_out_s` | num | no | Fade to black and to silence at the film's end. Starting value 2.0. |
| `audio_join` | `{duration_s, curve}` | no | Default crossfade at every join. Starting value 0.5 s, curve `equal_power`. |

**`sources[<id>]`** (the id is the clip's file name without extension)

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `original_video` | str | yes | The `.braw` file. The final film is decoded from it at full size. Scratch audio is read from it. |
| `preview_copy` | str | yes | The preview copy (MP4, 2048 pixels wide). Analysis, the film preview and the Shotcut project use it. |
| `audio` | str | yes | The original H2N WAV. Sound comes from here. |
| `audio_offset_s` | num | yes | Offset from matching: t_audio - t_video. Never changed after `assemble` wrote it. |
| `audio_offset_hand_s` | num | no | **Added in the build, after the owner's answer "Leave them, I set them" (`PRD.md` section 3).** The offset the owner typed for this clip, in seconds, same sign as `audio_offset_s`; negative values are allowed, the range is -30 to 30 s (rule V23). When it is present the sound uses it instead of `audio_offset_s`; when it is absent the sound uses `audio_offset_s`. The offset from matching stays beside it, so it stays visible and the owner can go back to it (the operation `reset_offset` removes this key). It lives in the timeline file, not in `project.json`, so it has a snapshot, a hash and an undo like every other change. Set by the page (4.4), by `assemble --replace` (carried over, 7.5) or by hand in the file. |
| `audio_drift_ppm` | num | no | Clock drift between recorder and camera in parts per million. Default 0. Set only after step 2 measures it (section 7.3). |
| `frames` | int | yes | Frame count of the clip. The `.braw` file and the preview copy have the same count. |
| `fps` | `{num, den}` | yes | Must equal `film.fps` (rule V4). |
| `size` | `{width, height}` | yes | Frame size of the full-size decoded picture (4096 x 2160 or 4096 x 1720 for the clips seen), not of the preview copy. |
| `copy_lut_sha256` | str | yes | Hash of the base LUT baked into the preview copy. Copied from `conversion.json`. |

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
| `base` | `{mode, path, sha256}` | yes | `mode` is `lut` (apply the `.cube` at `path` first) or `none` (no base conversion; for tests). The default file is Blackmagic "Gen 5 Film to Video". The preview copies have it baked in. The final render applies it to the RAW decode. The film preview does not apply it again. |
| `lut` | `{path, sha256, interp, name, credit}` | no | An optional creative LUT, applied after the base LUT, in the film preview and in the final render. `interp` must be `tetrahedral` (decision). **Added in the build:** `name` (str, no), the file name for people; `credit` (str, no), the credit line that the LUT's licence asks for (read from a `CREDIT.txt` next to the `.cube` file by `look set --lut`). When it is present, the export writes it into a text file next to the film (8.5). One LUT per film, not per segment (`PRD.md` section 12). |

**`audio`**

| Field | Type | Req | Meaning |
|-------|------|-----|---------|
| `sample_rate` | int | yes | Mix rate. 48000. |
| `loudness.mode` | str | yes | `off` (no levelling; used by the rough film of step 2 and by tests), `segment_then_film` (level every segment, then the film) or `film_only`. |
| `loudness.target_lufs` | num | unless mode is `off` | Target integrated loudness. Settled with D9 (2026-10-26, "Each scene, then film"): -14 for the owner's films, with `true_peak_dbtp` -1. The code has no built-in default; the file carries the value when step 3 sets the mode. |
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
| `output` | str | yes | Path of the file: `.mp4` for the delivery film, `.mov` for the ProRes master. Never inside a folder that holds originals. |
| `enabled` | bool | no | Default true. False: the entry is kept and not rendered. The ProRes master is written with `false` and becomes true only when the owner ticks it (E6). |
| `width`, `height` | int | yes | Output size. Same shape as `film.frame` (rule V5). |
| `video` | object | yes | `codec` (`h264_videotoolbox` or `libx264` for MP4; `prores_videotoolbox` for MOV), `rate_control` (`{"mode":"bitrate","mbps":45}`, or `{"mode":"crf","crf":18}` for `libx264`), `preset` (`libx264` only), `profile` (`hq`, ProRes only). Starting values are in section 5.6. |
| `audio` | object | yes | `codec` (`aac` for MP4, `pcm_s24le` for MOV), `bitrate_kbps` (AAC only), `sample_rate`. |

**`derived_from`** (short only): `film` (path of the film file), `target_length_s`, `settings` (the short builder's settings, kept so the short can be built again).

### 3.3 Example

The values are an illustration. The frame rate, frame sizes, frame counts and the base LUT's file name are those of the owner's clips and decision (the clips `A002_01011540_C002` and `A002_01060615_C083` of `Recordings_261003`); the paths, hashes, offsets, the creative LUT and the settings are made up. `mode: both` in `subtitles` is an example of open decision D13, not a choice. `-14.0` as target and `segment_then_film` as mode are the settled values of D9. `h264_videotoolbox` is the default encoder (decision D17).

```json
{
  "version": 1,
  "kind": "film",
  "film": {
    "title": "Recordings 2026-10-03",
    "fps": {"num": 30000, "den": 1001},
    "frame": {"width": 4096, "height": 2160},
    "fit": "fit",
    "fade_in_s": 1.0,
    "fade_out_s": 2.0,
    "audio_join": {"duration_s": 0.5, "curve": "equal_power"}
  },
  "sources": {
    "A002_01011540_C002": {
      "original_video": "/Volumes/CARD/Recordings/A002_01011540_C002.braw",
      "preview_copy": "/Volumes/SSD/film/copies/A002_01011540_C002.mp4",
      "audio": "/Volumes/CARD/Recordings/ZOOM0001.WAV",
      "audio_offset_s": 0.276,
      "audio_drift_ppm": 0,
      "frames": 2915,
      "fps": {"num": 30000, "den": 1001},
      "size": {"width": 4096, "height": 1720},
      "copy_lut_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    },
    "A002_01060615_C083": {
      "original_video": "/Volumes/CARD/Recordings/A002_01060615_C083.braw",
      "preview_copy": "/Volumes/SSD/film/copies/A002_01060615_C083.mp4",
      "audio": "/Volumes/CARD/Recordings/ZOOM0002.WAV",
      "audio_offset_s": 0.248,
      "audio_drift_ppm": 0,
      "frames": 805,
      "fps": {"num": 30000, "den": 1001},
      "size": {"width": 4096, "height": 2160},
      "copy_lut_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  },
  "segments": [
    {"id": "s001", "source": "A002_01011540_C002", "in": 38, "out": 900,
     "label": "stove, wide", "origin": "assembly",
     "note": "starts 0.5 s after the clap"},
    {"id": "s002", "source": "A002_01011540_C002", "in": 1320, "out": 2380,
     "label": "stove, wide, after the camera was moved", "origin": "assembly"},
    {"id": "s003", "source": "A002_01060615_C083", "in": 30, "out": 650,
     "label": "pan with sausage", "origin": "prompt",
     "audio_join": {"duration_s": 0.25, "curve": "linear"}, "gain_db_adjust": -2.0}
  ],
  "look": {
    "base": {"mode": "lut",
             "path": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/Blackmagic Design/Blackmagic Gen 5 Film to Video.cube",
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
     "width": 4096, "height": 2160,
     "video": {"codec": "h264_videotoolbox", "rate_control": {"mode": "bitrate", "mbps": 45}},
     "audio": {"codec": "aac", "bitrate_kbps": 384, "sample_rate": 48000}},
    {"name": "film-master", "output": "/Volumes/SSD/film/renders/film-master.mov", "enabled": false,
     "width": 4096, "height": 2160,
     "video": {"codec": "prores_videotoolbox", "profile": "hq"},
     "audio": {"codec": "pcm_s24le", "sample_rate": 48000}}
  ]
}
```

### 3.4 Derived values (computed, never stored)

- **Film order.** The enabled segments in the order of the `segments` array.
- **Segment length** in frames: `out - in`.
- **Film start frame** of a segment: the sum of the lengths of the enabled segments before it. The film length is the sum of all lengths. A crossfade does not shorten the film, because the picture cuts at the join and only the sound overlaps (section 5.4).
- **Film sample position** of a frame: `round(frame * sample_rate * den / num)`. Each segment's sample length is the difference of two such positions, so rounding never adds up.
- **Source audio position** of a segment: `round((in * den / num + offset) * sample_rate)`, where `offset` is `audio_offset_hand_s` when the source has one and `audio_offset_s` otherwise, with the drift correction of section 7.3 if `audio_drift_ppm` is not 0.
- **Picture placement** of a segment: the window or the bars (section 5.2).

### 3.5 Validation rules

`timeline.py` checks these rules (V1 to V22, and V0 and V23, which the build added). Errors stop the tool. Warnings are listed and do not stop it. "File checks" read the disk and can be switched off for tests.

| Rule | Check | Level |
|------|-------|-------|
| V0 | **Added in the build.** Structure: a required field is present and has the right type (a number is not a string; true and false are not integers); `film.fit` is `fit` or `fill`; `look.base.mode` is `lut` or `none`, and mode `lut` has `path` and `sha256`; `origin` and `priority` take their listed values; the file holds one JSON object; no key appears twice in one object. | error |
| V1 | `version` is a version the tool knows. | error |
| V2 | No unknown key in a known object, unless it starts with `x_`. The message names the closest known key. | error |
| V3 | `kind` is `film` or `short`. `derived_from` exists exactly when `kind` is `short`. | error |
| V4 | `fps` values are positive integers. Every source's `fps` equals `film.fps`. | error |
| V5 | Frame sizes are even positive integers. Each export has the same shape as `film.frame`, within one pixel of rounding. | error |
| V6 | Source ids match `[A-Za-z0-9_-]+` and are unique. Segment ids match `s[0-9]+`, title ids `t[0-9]+`, cue ids `c[0-9]+`, and are unique in their list. | error |
| V7 | File checks: each `preview_copy`, `audio` and `original_video` exists (the final render needs the `.braw` file), and `frames` equals the preview copy's frame count. | error |
| V8 | Every segment names an existing source. | error |
| V9 | `0 <= in < out <= sources[source].frames`. | error |
| V10 | At least one enabled segment. | error |
| V11 | Each enabled segment is long enough for its fades and joins: its length in seconds is at least half the duration of the join before it plus half the duration of the join after it. For the first segment the film fade-in replaces the half join before it, and the film fade-out replaces the half join after the last segment. The film fades count in full. | error |
| V12 | Join durations lie between 0 and 5 s. The curve is `equal_power` or `linear`. An `audio_join` on the first enabled segment is ignored. | error, warning for the ignored one |
| V13 | `crop` uses `x` alone or `x_from` with `x_to`; values lie between 0 and 1. A `crop` in a film whose `fit` is `fit` has no effect. | error, warning for the unused one |
| V14 | `gain_db_adjust` lies between -30 and 30. | error |
| V15 | Every anchor is `film` or an existing segment id. `start_s < end_s`. A cue or title interval that reaches outside its segment is a warning, and the part outside is not shown. | error, warning |
| V16 | File checks: the `sha256` of each LUT equals the file's hash. `look.lut.interp` is `tetrahedral`. A source whose `copy_lut_sha256` differs from `look.base.sha256` gets a warning: its preview copy carries another base LUT, so the film preview shows it wrongly. The final render is not affected, because it applies the base LUT itself. | error, warning |
| V17 | `audio.loudness.mode` is `off`, `segment_then_film` or `film_only`. Unless it is `off`, `target_lufs` is present and between -40 and -5. `true_peak_dbtp` is at most 0. `max_gain_db` lies between 0 and 30. | error |
| V18 | Export names are unique. No export `output` lies inside a folder that holds an original (any parent folder of a `.braw` or WAV used). | error |
| V19 | `subtitles.mode` is `burn`, `sidecar` or `both`. Cue text is not empty. A cue with more than two lines or more than `max_chars_per_line` per line is a warning. | error, warning |
| V20 | For a short: `derived_from.film` exists (file check). | error |
| V21 | In version 1 the container follows the file extension. `.mp4` needs video codec `h264_videotoolbox` or `libx264` and audio codec `aac`. `.mov` needs video codec `prores_videotoolbox` with profile `hq` and audio codec `pcm_s24le`. | error |
| V22 | A short is not longer than 180 seconds. | warning |
| V23 | **Added in the build.** `sources[].audio_offset_hand_s` is a finite number from -30 to 30 s (`OFFSET_HAND_MAX_SEC`, a starting value; a value of another type is a V0 error). With a function that gives the length of a WAV (ffprobe when the file checks are on; the page passes its own), a warning when, at that offset, the WAV of a clip with a hand offset holds no sound for an enabled segment of the clip ("the segment is silent"), or sound for only part of it (a frame or more missing). Clips without a hand offset are not checked here: the render reports them (5.4). A WAV whose length cannot be read gives one warning, not an error. | error, warning |

**Readings the build fixed** (the plan left them open): an optional number that is absent counts as 0 for rule V11 (no fade-in, no fade-out, no crossfade, so the sound cuts hard); rule V5 applies to the `size` of a source as well as to `film.frame` and the exports; rule V19 demands `language` `en`; rule V21 allows rate control `crf` only with `libx264`; the shape test of V5 passes when the width or the height is within one pixel of the value that the other dimension gives; rule V6 finds a repeated source id by reading the JSON text, because a JSON parser keeps only the last of two equal keys; a free key (`x_...`) is allowed in every object, including `fps`, `size` and `rate_control`.

**Output of `python3 main.py timeline validate`.** Exit code 0 when there are no errors, else 1 (2 when the file is missing). Options: `--file FILE` (default `timeline.json`), `--no-file-checks`. A file that is not JSON, or has a version the tool does not know, gives one finding (`V0` or `V1`) and exit code 1. Standard output is a JSON list, one object per finding:

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
3. **Snapshots.** Before the tool replaces the file, it copies the old file to `timeline.history/NNNN.json` (numbers rising, 200 kept). A successful `timeline validate` also snapshots the file when it differs from the newest snapshot, so the last valid version is always kept even after a direct edit. **As built:** the validate prints the snapshot's number on standard error (standard output stays the JSON list); a file with errors is not snapshotted; `undo` and `history` are in 4.3.
4. **Hash check.** The page sends the hash of the version it started from. If the file on disk has another hash, the server answers 409 and changes nothing. The page then reloads. Claude Code has no lock: it re-reads the file before editing and changes it in place with small edits, not by rewriting the whole file from memory.
5. **Validation.** Every tool-driven write is validated first and refused with the findings if it has errors. A direct edit is validated when the tool next reads the file; `render` and `preview` refuse an invalid file.

### 4.2 What each writer may change

| Part of the file | Tool commands | Page | Claude Code |
|------------------|---------------|------|-------------|
| `sources` | `assemble`, `convert` write it. `assemble --replace` carries `audio_offset_hand_s` over. | `audio_offset_hand_s` only (operations `set_offset`, `reset_offset`) | `audio_offset_hand_s` only. Otherwise re-run `assemble`. |
| `segments`: `in`, `out`, `enabled`, order | yes | yes | yes |
| `segments`: `label`, `note`, `crop`, `audio_join`, `gain_db_adjust`, `priority` | yes | `label` only | yes |
| `segments`: new segment | yes | no | yes. New id: `s` plus the highest number so far plus one. |
| `segments`: `id` of an existing segment | never | never | never |
| `film`, `audio`, `exports` | `look`, `render`, `loudness`, `export` commands | `audio.loudness.mode` (`POST /api/loudness`) and a new entry of `exports` from a preset (`POST /api/render`) | yes |
| `look` | `look set` | the creative LUT only, by an id from the LUT folder (`POST /api/look`, 12; the server finds the file, copies it and writes the hash; the page names no path) | by running `look set`, not by hand (the command writes the hash) |
| `titles`, `subtitles.cues` | yes | no | yes |
| `segments` after hand work in Shotcut: `in`, `out`, `enabled`, order, new segments | `import-shotcut` only (section 8.6) | no | no |
| `version`, `kind` | never | never | never |

### 4.3 How Claude Code works with the file

The repository will carry a `CLAUDE.md` section (written in step 2) that states these rules. The loop is:

1. **Look.** `python3 main.py timeline summary` prints the film as text. Example:

   ```
   timeline.json  film  29.97 fps  4096x2160  hash 9f2c1a47  3 segments, 3 enabled, 84.8 s
   id    film time               len     source               frames        label
   s001  00:00:00.00-00:00:28.76  28.8 s  A002_01011540_C002   38-900        stove, wide
   s002  00:00:28.76-00:01:04.13  35.4 s  A002_01011540_C002   1320-2380     stove, wide, after the camera was moved
   s003  00:01:04.13-00:01:24.82  20.7 s  A002_01060615_C083   30-650        pan with sausage
   ```

2. **Find.** In step 5, the index (section 10) tells which clip and time hold the scene.
3. **Edit.** Edit `timeline.json` directly. To remove a scene, set `"enabled": false`. To keep the cut for later, add a `note`.
4. **Check.** `python3 main.py timeline validate`. Exit 0 means the file is valid. Exit 1 prints the findings with the field path.
5. **See.** `python3 main.py timeline frames --film-s 12.0,48.5` writes one PNG per time into `stills/` and prints the paths. Claude Code opens them. `python3 main.py preview --range 60:90` renders only that part of the preview. **As built (`frames.py`):** `timeline frames [--file F] [--work DIR] (--film-s T1,T2 | --source CLIP --at T1,T2) [--no-file-checks]`. A time gives the frame nearest to it (a time typed as 1.6683 is frame 50 at 29.97 fps, not 49). A film time finds the segment that plays then (removed segments have no film time) and the frame of its clip; the still is cut from the clip's preview copy (so the base LUT is in it), placed like the film preview (960 pixels wide, the creative LUT if there is one, the black bars of a letterboxed clip) and without the film fades. A source time gives the frame of the clip's preview copy as it is. Files are `<work>/stills/film-<T>s.png` and `<work>/stills/<CLIP>-<T>s.png`, written to a temporary name and renamed; the paths are printed one per line, in the order of the times. A time that is negative, beyond the film or the clip, or a clip that is not in the file is exit code 1 with the reason on standard error and nothing written; wrong options are exit code 2; a work folder inside a folder that holds originals is refused.
6. **Undo.** `python3 main.py timeline undo` restores the previous snapshot. `timeline history` lists the snapshots with time and a one-line diff. **As built (`store.py`):** `undo` restores the newest snapshot whose bytes differ from the file, so a snapshot that a validate made of the file as it is gets skipped, and two undos go two versions back (not back and forth). The restored file is the snapshot's bytes, written atomically. The version that `undo` replaces is first copied to `timeline.history/undone/NNNN.json` (the newest 200 kept), so an undo never loses work; there is no redo command, the owner or Claude Code can copy that file back. The snapshot that was restored stays, so a later direct edit followed by a validate can be undone too; the snapshots newer than it, which were copies of the undone version, are removed. With no differing snapshot the command prints that there is no earlier version and exits 1; a missing file is exit code 2. The command prints the number restored, what the undo changed (`describe_change`) and where the replaced version is kept. `timeline history [--file F] [--json]` lists every snapshot, oldest first, and the file last ("current"), each with the time the version was written, the first 8 characters of its hash and one line that says what differs from the version before: segments added or taken out, `in` and `out` moved, removed from or put back into the film, the order, labels, offsets set by hand, and other parts of the file by name; at most three changes are written out and the rest counted.

### 4.4 Operations the page may send

The page does not send whole files. It sends a list of operations with the hash it started from, to `POST /api/timeline/edit`:

| Operation | Fields |
|-----------|--------|
| `trim_in` | `segment`, `frames` (positive moves the start later) |
| `trim_out` | `segment`, `frames` (positive moves the end later) |
| `set_enabled` | `segment`, `enabled` |
| `move` | `segment`, `to_index` |
| `set_label` | `segment`, `label` |
| `set_offset` | `source`, `offset_s` (seconds, any sign): writes `sources[source].audio_offset_hand_s`. **Added in the build.** |
| `reset_offset` | `source`: removes `audio_offset_hand_s`, so the offset from matching is used again. **Added in the build.** |

The server applies the operations in memory, validates, saves a snapshot and the new file, and answers with the new hash and the summary. Findings give HTTP 422 with the list of section 3.5. A stale hash gives 409. An unknown operation gives 400.

**As built** (the plan named the operations and fields but not the JSON; chosen in the build). The body is `{"hash": "<sha256 of the file the page read>", "operations": [...]}`. An operation is an object with the field `op` and the fields of the table: `{"op": "trim_in", "segment": "s001", "frames": 30}`, `trim_out` the same, `{"op": "set_enabled", "segment": "s001", "enabled": false}`, `{"op": "move", "segment": "s001", "to_index": 0}` (an index into the whole `segments` list, from 0), `{"op": "set_label", "segment": "s001", "label": "text"}`, `{"op": "set_offset", "source": "A002_01011851_C047", "offset_s": 0.02}` (a clip id, not a segment id; a value that is not a number is 400, a value out of range 422 with rule V23) and `{"op": "reset_offset", "source": "A002_01011851_C047"}`. All operations of one request are applied or none: a malformed one (400) or a result that breaks a rule (422) saves nothing. Answers: 200 `{hash, summary, findings, preview}` (findings are warnings only); 400 `{error}`; 404 when there is no timeline file; 409 `{error, hash}` with the hash of the file as it is; 422 `{error, findings}`. The check of a page edit skips the file checks of rule V7 and V16: an edit cannot change a file name, a frame count or a LUT, and `assemble` has already made those checks. The file is read, compared, changed and saved under one lock, so two requests with the same hash give one 200 and one 409.

---

## 5. Rendering and Export (used by steps 2 to 6)

Two kinds of rendering exist. The **film preview** (section 5.5) is made from the preview copies and is small. The **final film** (sections 5.3 and 5.6) is made from the `.braw` files at full size.

### 5.1 The render plan

`render.py` first turns a valid timeline file into a **render plan**. This is a pure function: it reads no media and starts no program, so tests can run it with no files. The plan lists, for every enabled segment:

- the film start frame and length (section 3.4),
- the source frames to read and the picture placement (section 5.2),
- the audio span, the handles and the fades (section 5.4),
- warnings, for example "audio does not cover the segment".

Every render (preview, final film, Shotcut export, short) starts from the plan.

### 5.2 Picture placement: fit and fill

The output picture has the size `film.frame`. The default is 4096 x 2160 (DCI 4K, decision D5). A clip's decoded picture has the size `sw` x `sh`: 4096 x 2160 for a `brlt` clip and 4096 x 1720 for a `brhq` clip [measured]. The preview copy has half of that size, and the same rules apply to it at half scale.

- **`fit`** (default): the scale is `min(fw/sw, fh/sh, 1)`: a picture is reduced to fit and is never enlarged. The picture is placed in the middle: `X = round_even((fw - w) / 2)` and `Y = round_even((fh - h) / 2)`, where `w` and `h` are the scaled picture size. Black bars fill the rest. The bars are black in the output's colour range: the filter chain adds them in RGB, after the LUT and before the conversion to video levels, so that they come out as limited-range black (Y = 16) [measured: bars of Y 16 and U, V 128; a first test that added the bars after the conversion gave Y = 0].
- **`fill`**: the window is the largest rectangle of the output's shape inside the picture. If `sw/sh >= fw/fh`, it is `ww = round_even(sh * fw / fh)` wide and `wh = sh` high. Otherwise it is `sw` wide and `wh = round_even(sw * fh / fw)` high. The window is then scaled to `fw` x `fh`. Its position is `X = round_even((sw - ww) * x)`, where `x` is the segment's `crop.x` (default 0.5), and `Y = round_even((sh - wh) / 2)`. With `x_from` and `x_to` the window moves in a straight line from the first to the second position over the segment's frames.
- `round_even` rounds to the nearest even number; a tie goes to the larger one.

For the clips seen [arithmetic from the measured sizes]:

| Source | Film frame | Mode | Placement | Scale |
|--------|-----------|------|-----------|-------|
| 4096 x 2160 (`brlt`) | 4096 x 2160 | `fit` | the whole picture, no bars | none |
| 4096 x 1720 (`brhq`) | 4096 x 2160 | `fit` | the whole picture, bars of 220 pixels above and below | none |
| 4096 x 2160 | 1080 x 1920 (9:16) | `fill` | window 1216 x 2160 | 0.888, a reduction |
| 4096 x 1720 | 1080 x 1920 (9:16) | `fill` | window 968 x 1720 | 1.116, an enlargement |

A short from a `brhq` clip is therefore enlarged by 12 percent. This is a consequence of the wide frame and of the owner's decision that shorts are cut from the source picture; it is listed in `PRD.md` section 13.

The `crop` filter of ffmpeg evaluates `x` and `y` for every frame, so a moving window needs no extra tool [measured: a synthetic test moved the window and only the moving interval changed].

### 5.3 Picture path of the final film

The final film is rendered from the original `.braw` files, decoded at full size. It is never rendered from a preview copy or from the film preview (decision D1, option A; requirement E2). Each export is its own pass over the `.braw` files: a second export costs a second decode.

For each enabled segment, in film order, three kinds of process work together:

1. **Decoder process** (Python with VapourSynth). It opens the clip with `braw.Source(path, pipeline='metal', scale=1)`, keeps the frames `in` to `out - 1` (VapourSynth selects frames by number, so the cut is exact), puts the planes in the order green, blue, red (VapourSynth gives red, green, blue; ffmpeg's `gbrp16le` wants green, blue, red) and writes raw 16-bit RGB frames to a pipe. The decoder output is the picture as recorded: gamma "Blackmagic Design Film", gamut "Blackmagic Design", full range.
2. **Segment filter process** (`ffmpeg`). It reads that pipe and applies, in this order: the base LUT, the creative LUT if there is one, the placement of section 5.2 (`pad` for `fit`, `crop` and `scale` for `fill`), and the conversion to BT.709 video levels. It writes raw frames of the film's frame size to a second pipe.
3. **Encoder process** (`ffmpeg`). It reads the second pipe for the whole film, applies the fades and the subtitles in film time, converts to the encoder's pixel format and encodes.

The tested chain. The check of section 6.1 ran the steps of processes 1 to 3 in one decoder process and one `ffmpeg` process, for a single segment. These are its arguments:

```
ffmpeg -f rawvideo -pix_fmt gbrp16le -s 4096x2160 -r 30000/1001 -i -
       -vf "lut3d=file='<base LUT>':interp=tetrahedral,
            [pad=4096:2160:(ow-iw)/2:(oh-ih)/2:color=black,]
            scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int+bicubic,
            format=yuv420p"
       -c:v h264_videotoolbox -b:v 45M -profile:v high -allow_sw 0 -g 30 -tag:v avc1
       -color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv  -an  out.mp4
```

(written on several lines here; one line in practice). The multi-segment design adds one pipe stage between processes 2 and 3. Its speed is [unverified] until check 3.8.

Encoder settings and the colour tags are in section 5.6. The encoder writes to `<name>.mp4.partial`. When it ends, the tool checks the file (section 5.7) and then renames it.

Why this shape [inference]:

- A single filter graph with one input per segment keeps one 4K decoder open per input. The pipes keep one decoder open at a time.
- The two clip kinds have different picture sizes. The segment filter process brings every segment to the film's frame size, so the encoder sees one size.
- Fades and subtitles are applied once, in film time, by the encoder process. The segment processes know nothing about film time.
- The LUTs run on the 16-bit RGB picture, before the conversion to video levels and to 8 bit.

Measured speed of the tested chain (5 s = 150 frames of a `brlt` clip, 4096 x 2160; section 6.1 has the table):

- Decode alone: 37 fps at load average 6.
- Decode, LUT and colour conversion, no encoder: 17.6 and 19.2 fps at load average 17. This part sets the upper limit, whatever the encoder.
- With `h264_videotoolbox` at 45 Mbit/s: 13.9 to 15.2 fps, 0.46 to 0.51 times real time, at load average 6 to 26. A 20-minute film at 29.97 fps takes about 40 minutes [arithmetic].
- With `libx264` preset slow at 45 Mbit/s: 4.1 to 4.4 fps, 0.14 to 0.15 times real time. A 20-minute film takes about 2.4 hours [arithmetic].
- A `brhq` clip letterboxed into the DCI 4K frame: 20.6 fps with `h264_videotoolbox`, because it has fewer pixels to decode.
- A repeat at a lighter load (load average 3 to 5): 27.3 fps without an encoder, 26.1 fps with `h264_videotoolbox` (0.87 times real time; a 20-minute film in about 23 minutes) and 6.6 fps with `libx264` slow (0.22 times real time; about 1.5 hours) [arithmetic from the speeds]. So the load changes the speed by up to a factor of 1.7; the figures at the load of a working day are the first set.

Details that this section does not settle:

- The pipe carries 53 MB per frame at 16 bit RGB and 4096 x 2160 [arithmetic]. The tested single-process chain ran at the speeds above, so the pipe is not a hard limit. Whether the two-stage design for many segments keeps that speed is [unverified] until check 3.8.
- Whether the conversion to 8 bit at the end shows banding in smooth skies or walls is [unverified]. Check 3.8 includes a visual look at a gradient scene.
- Frame-exact reading of the preview copies (for the film preview) uses `-ss` before `-i` with re-encoding; it is tested in check 2.11.

**As built** (`export.py`; changed in the build where it says so). The three kinds of process are as planned: the decoder runs inside the render process, through `Decoder.write_frame_range(path, scale, sink, first, last)` (the frames `in` to `out - 1`; `BrawSourceDecoder` selects them with a VapourSynth slice, and the default of the interface decodes from the first frame and drops the frames before `first`, so a second decoder needs no new method); one segment filter process per segment; one encoder process for the whole film. The segment filter's standard output is the encoder's standard input, which the render holds open between segments.

- *Segment filter.* `lut3d` for the base LUT (mode `lut`), `lut3d` for the creative LUT (`look.lut`), the placement of 5.2 (for `fit`: `scale` if the picture is reduced, then `pad` with black; for `fill`: `crop` and `scale`), then `scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int+bicubic` and `format=yuv420p` (`yuv422p10le` for ProRes). The placement is made for the export's own size (`render.make_plan(tl, preview_width=export width)`), so an export at half size scales the picture and its bars together. Every LUT is `interp=tetrahedral`.
- *Encoder.* Raw frames on the standard input, the finished sound as a second input, `fade` for the film fades (the last frame of a fade-out is black, as the last sample of the sound is silent: the fade takes the `m - 1` frames before it), the encoder of the export entry, the colour tags, `-timecode 00:00:00:00`, `-movflags +faststart`, the output `<name>.<ext>.partial`. For H.264 the colour tags are set with `-color_primaries`, `-color_trc`, `-colorspace`, `-color_range` and the `h264_metadata` filter of 5.6. **For ProRes the VideoToolbox encoder writes only the matrix** whatever those options say [measured: the `colr` atom held primaries 2, transfer 2, matrix 1], so the frames are tagged with `setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv` first [measured: the atom then holds 1, 1, 1].
- *Order of the work.* (1) Each clip the film uses is opened once (size, frame rate and frame count must equal the timeline file's, the original and the WAV must exist, the LUT files must have their hash). (2) The sound is mixed (5.4), levelled and written: for AAC it is encoded to an MP4 audio file, decoded again and measured, and when the decoded true peak is over the limit the sound gets a limiter with a lower ceiling and is encoded again (at most three times); for PCM it is a raw float file. (3) The picture runs. (4) The file is checked (below), its loudness and true peak are measured on the finished file, and it is renamed.
- *Checks* (5.7 and more) on `<name>.<ext>.partial`: container and codec (H.264 High, 8-bit 4:2:0, or ProRes HQ, 10-bit 4:2:2), size, frame rate, BT.709 and TV-range tags, as many video frames as the film has, one sound track of the codec and rate asked for, picture and sound within one frame in length and in start time, the timecode, the `moov` box before the `mdat` box. A failed check keeps the `.partial` file, sets `ok` to false in the render report and gives exit code 1. The loudness and true peak of the file, and a data rate more than 25 percent from the one asked for, are warnings in the report.
- *Space.* `render` estimates the file (the data rate times the length for H.264; 3.9 MB per DCI 4K frame for ProRes) and the raw sound file, multiplies the file by 1.1 and refuses to start when the free space of the output folder is less (E6, for every export).
- *Stop and restart.* `kill PID` (SIGTERM) stops the render the clean way: the encoder and the filter are killed, the `.partial` file, the scratch folder and the lock `.render.lock` are removed, exit code 130. **A stopped render is not resumed; it starts over from the first frame** (changed in the build: the plan has no resume, and one encoder process for the whole film cannot be resumed). `render --detach` starts the render as a process of its own session, with a log and a pid file in `<work>/reports/`.
- *Not built:* subtitles and titles in the encoder (step 4); the choice of a decode scale below 1 for small exports (every export decodes at full size).
- *Measured* on the first scenes of the first shoot (51.9 s, 1555 frames, 3 segments from 2 `brhq` clips of 4096 x 1720 that are letterboxed into 4096 x 2160; `h264_videotoolbox` at 45 Mbit/s; the load average was 2 to 13 because other jobs ran): with the slay Film LUT as creative LUT 16.6 and 17.2 fps (0.56 and 0.57 times real time), without a creative LUT 19.8 fps (0.66 times real time); the data rate of the file is 43.1 Mbit/s (45 asked for); peak memory of the render process 2.3 to 2.8 GB. The second `lut3d` costs about 15 percent of the speed. The loudness of the files, measured on the decoded sound, is -14.16 LUFS and -1.29 dBTP (target -14, limit -1). Stills of the file at seven film times against the stills that `timeline frames` makes from the preview copies (the film preview's picture, with the same creative LUT): the black bars are at the same place (51 against 52 rows above, 51 against 50 below: the preview's placement rounds to even rows), the edges of the picture agree (normalised correlation 0.94 to 0.98), the mean colour of a still differs by at most 2.1 of 255 (the file is 0.5 to 2 codes greener in dark scenes), PSNR 26 to 30 dB at the size of the preview and 36 to 38 dB after a blur; at the first and the last second the file is darker, because it fades and the stills do not [measured]. The whole shoot (105,337 frames, 58:34.7) was started as the last step of this build; its numbers are in `reports/render-report.json` when it ends.

### 5.4 Sound path

The sound is built in `numpy`, not in an ffmpeg filter graph [Design]. This makes every rule below a pure function that tests can check on arrays.

1. **Decode each WAV once.** `ffmpeg` decodes the whole WAV to 32-bit float stereo at the mix rate (48000 Hz). The H2N files checked are 96 kHz [measured], so this resamples. The longest clip of the first shoot is 271 s, which is about 104 MB as float stereo at 48 kHz [arithmetic]. The array is kept while the segments of that clip are processed.
2. **Place each segment.** The source start is `s0 = round((in*den/num + offset) * rate)`, with `offset` the owner's `audio_offset_hand_s` when the clip has one, else `audio_offset_s`. The segment's sample length `n` is from section 3.4. The core span is the samples `[s0, s0+n)`.
   - If `s0 < 0` the segment starts with `-s0` samples of silence. If the span ends after the file, the rest is silence. Each case is a warning with the number of seconds. (The first shoot has offsets from -5.03 to +1.94 s [measured].)
   - This is the same rule as `build_timeline_plan` of v1.1: a positive offset advances the audio in-point, a negative offset delays the audio and clamps at the file's start (Appendix A.7). The tests of that function are ported (check 2.8).
3. **Handles and joins.** At a join of duration `d` the picture cuts at the film frame `f`. The sound of the segment before is extended by `d/2` after its end, and the sound of the segment after is extended by `d/2` before its start. Both come from the WAV files, so the handle may reach into material that the edit cut out. Over the interval `[f - d/2, f + d/2]` the gain of the earlier segment falls and the gain of the later one rises:
   - `equal_power`: falling `cos(pi/2 * x)`, rising `sin(pi/2 * x)`, with `x` from 0 to 1. The squares sum to 1, so the power stays constant for sound that does not correlate (ambience) [inference].
   - `linear`: falling `1 - x`, rising `x`. The amplitudes sum to 1, which suits two parts of the same continuous sound [inference]. **Changed in the build:** for two sounds that do not correlate, a linear crossfade lowers the power by 3 dB in the middle of the join (the squares `x^2 + (1-x)^2` fall to 0.5) [arithmetic, and measured in the tests]. Check 2.8 therefore tests `equal_power` on uncorrelated noise and `linear` on two parts of the same sound; the plan's wording "both curves" on uncorrelated noise cannot hold.
   - If a handle is longer than the WAV allows, the crossfade is shortened to what exists, and the render report says so. **As built:** the join is as long as the shorter of the two handles allows, down to a hard cut when one side has none (a clip whose segment starts at the first sample of its WAV). `build_mix` returns the wanted and the used length of every join and a warning for each shortened one.
4. **Film fades.** The first segment rises from silence over `fade_in_s`, with the `sin` curve. The last segment falls to silence over `fade_out_s`, with the `cos` curve. Picture fades are made by `fade` in the encoder process (section 5.3) over the same frames.
5. **Level each segment** (mode `segment_then_film`). Measure the integrated loudness `L` of the core span with `ebur128` (section 8.4). The gain is `min(target - L, max_gain_db) + gain_db_adjust`. A segment with no sound above the absolute gate (-70 LUFS) gets gain 0 and a warning. Mode `film_only` skips this step.
6. **Mix.** Add the windowed, gained segments into one float buffer for the film.
7. **Level the film.** Measure integrated loudness and true peak of the buffer. Apply one gain so the loudness equals the target. If the true peak is then above the limit, apply `alimiter` with a ceiling 0.5 dB under the limit, measure again, and lower the ceiling by 0.5 dB until the limit holds or three tries are used. The final numbers go into the render report; a result more than 0.5 LU from the target is a warning.
8. **Write** `mix.wav` (32-bit float) in the work directory. The encoder reads it.

Memory: a 20-minute film as float stereo at 48 kHz is about 460 MB [arithmetic]. The buffer is kept in a memory-mapped file in the work directory.

**As built** (`loudness.py` and `render.build_mix`; changed in the build where it says so). Loudness is measured by piping float stereo into `ffmpeg -af ebur128=peak=true:metadata=1,ametadata=print` and reading the last `lavfi.r128.I` and the largest `lavfi.r128.true_peak` [measured: three decimals; `I` is -70.000 when nothing is above the gate, which is reported as "silent"]. The gain of a segment (step 5) is `min(target - L, max_gain_db)`; `gain_db_adjust` is added in the mix. A segment shorter than 0.4 s has no measurable loudness (one gating block) and is left at gain 0 with a warning. Segments are measured four at a time, and the measured value is kept in `previews/loudness-cache.json` (preview) or `reports/loudness-cache.json` (export), keyed by the WAV (path, size, modification time, decoded length), the sample where the segment's sound starts, its length and the rate, so a changed offset or trim is measured again and nothing else is. The film step (step 7) is `loudness.level_film`:

- One gain brings the integrated loudness to the target. If the true peak is over the limit, `alimiter` (look-ahead 5 ms, release 50 ms, `level=disabled`, `latency=1` so that the length and the timing stay) runs with a ceiling 0.5 dB under the limit; if the true peak measured after it is still over, the ceiling goes down by 0.5 dB or by the overshoot, whichever is more. When the peak holds but the loudness fell more than 0.1 LU short of the target (a limiter takes loudness away), the gain before the limiter is raised by the loss, scaled by the slope seen between the last two runs, and the limiter runs again. **Changed in the build:** at most six limiter runs, not three. The first shoot used all six (below). The limit is applied as 0.01 dB under the value in the file, because `ebur128` reports the peak as a linear number with three decimals.
- The report (`MixResult.levels`) holds the mode and numbers, one row per segment (measured loudness, gain, `adjust_db`, `capped` with the gain that was wanted, `silent`), and for the film the loudness and true peak before and after, the one gain, the true peak after that gain and before the limiter, every ceiling that was tried, and whether the target and the limit were reached. Warnings: a capped segment, a silent segment, a film that needed more than `max_gain_db`, a film that missed the target by more than 0.5 LU or the limit.
- **Measured on the first shoot** (`timeline-all.json`, 171 segments, 58:35, defaults of D9 and `max_gain_db` 12): the scenes are quiet, with a median of -44.5 LUFS over the first 60 segments and a crest factor (true peak minus loudness) of 22 dB at the median. 137 of 171 segments are capped at +12 dB. After the segment gains the film measured -16.56 LUFS and +12.0 dBTP; the film gain of +7.4 dB gave a true peak of +14.6 dBTP before the limiter. The limiter loop (ceilings -1.5, -4.29, then -4.79 four times) ended at -14.15 LUFS and -1.38 dBTP in 185 s for the whole mix on this machine, with a limiter that lowers some peaks by more than 15 dB [measured]. The numbers of D9 are reached, but through heavy limiting of the transients of a quiet recording. Whether that sounds right is for the owner to judge; `loudness set --max-gain` and `--target` change the trade.

### 5.5 Film preview

The film preview is a small copy of the film that a browser plays. The preview copies are H.264 MP4 files, which a browser plays, but the film has segments, fades, joins and a mixed sound, so the page plays a film preview made from the timeline file.

- **Pictures.** One small file per segment, in `previews/seg-<hash>.mp4`, cut from the clip's preview copy: 960 pixels wide, H.264 with short groups of pictures (`-g 12 -bf 0`) so seeking is quick, no sound. The placement of section 5.2 applies at this size, so a `brhq` segment shows its black bars. The preview copy already carries the base LUT, so only a creative LUT is applied again. The `<hash>` covers the preview copy's path, size and modification time, `in`, `out`, the placement, the frame size, the `look` hashes, the film fades that touch this segment and the preview settings. An unchanged segment is not rendered again.
- **Frame accuracy.** The preview copy has a key frame every 30 frames (about 1 s). A cut at any frame is exact because the segment is encoded again, and `ffmpeg` decodes at most 30 frames from the key frame before it [arithmetic].
- **Join.** The segment files are joined with ffmpeg's `concat` input in copy mode. Every file starts with a key frame, so the join needs no new encode.
- **Sound.** The full mix of section 5.4 is made each time (sound is quick to build) and encoded as AAC. The film preview has the final loudness and joins.
- **Text.** Titles and subtitles are not burned into the film preview. The page shows them from a WebVTT file made from the cues and titles [Design]. Their style in the preview differs from the final film.
- **Range.** `preview --range A:B` renders only the segments that touch film seconds A to B.
- **As built** (changed in the build). `render_preview(tl, work_dir, name)` writes `previews/<name>.mp4` and `previews/<name>.json` (file name, timeline hash, frames, seconds, segment ids, settings, time). The page calls a preview up to date when the hash in that file equals the hash of the timeline file. A segment file is `previews/seg-<key>.mp4`, the key being a hash of what is listed above. The segment is cut with `-ss` half a frame before its first frame, `-frames:v` for its length and a filter `setpts=N/(30000/1001)/TB`, so that its frames are exactly `in` to `out - 1` and its time stamps start at 0 (checked in `tests/test_seek_exact.py`, with and without B-frames in the source). The fades are the `fade` filter by frame count, inside the first and the last segment file. Encoder `h264_videotoolbox` with `-q:v 60`, key frame every 12 frames, no B-frames; `libx264` (`-preset veryfast`) can be chosen. The sound is the mix of 5.4, written as raw 32-bit float next to the file, encoded as AAC (160 kbit/s) and muxed with the joined picture. The checks of 5.7 run on the file before it is renamed: as many video frames as the film, a sound track, sound and picture within one frame of each other.
- **What the next preview has to do** (added with the hand offset). `render.sound_keys` gives each segment a key that changes when anything that shapes the segment's own sound changes: the WAV, the sample where its sound starts (so a clip's offset, hand offset and drift), its length, the handles and curves of its two joins, the film fades and the gain; it does not depend on where the segment stands in the film. The picture key (`segment_key`) does not contain the offset. `previews/<name>.json` records `sound_keys` and `sound_changed` (the segments whose sound differs from the preview before). `render.pending_work` compares the film as it is with the last preview and the picture cache without reading media: `picture` lists the segments whose picture file is missing, `sound` the segments whose sound key is new. A changed offset therefore marks the sound of that clip's segments and no picture: the next preview renders no picture again. The sound is still built as a whole each time (step 6 of 5.4); the keys say which segments' sound is new.
- **Served** at `GET /api/preview/<file>` with HTTP range requests (section 12, server).

The film preview uses the same render plan as the final film. Its colour comes from the base LUT baked into the preview copies and from the same creative LUT, so colour and timing agree [inference]. Its detail does not: it is half size, and it is an H.264 copy of a half-size decode. The browser's own colour handling can still differ from the final file's player [inference].

### 5.6 Export settings

An entry in `exports[]` sets one output. Version 1 accepts the codecs of rule V21.

| Setting | Starting value | Basis |
|---------|----------------|-------|
| Container | MP4, `-movflags +faststart` | The most common format; YouTube asks for the moov atom at the front [documented, Src11]. |
| Frame | 4096 x 2160 (DCI 4K). Other sizes of the same shape are allowed. | Owner decision D5. |
| Video, delivery | H.264 High profile, 8-bit 4:2:0, about 45 Mbit/s. | Owner decision: "about 45 Mbit/s"; YouTube recommends 35 to 45 Mbit/s for 2160p at 24 to 30 frames per second [documented, Src11]. |
| Encoder, delivery | Default (decision D17, "Hardware (fast)"): `h264_videotoolbox -b:v 45M`: 45.7 Mbit/s and 13.9 to 15.2 fps at 4096 x 2160 [measured]. Selectable in the export entry: `libx264 -preset slow -b:v 45M`: 49.1 Mbit/s and 4.1 to 4.4 fps with one pass [measured]. `-preset medium` gave the same size at 5.3 fps [measured]. | Owner decision D17. A two-pass `libx264` run would hit the bitrate more closely [inference] and was not measured. |
| Colour tags | BT.709 primaries, transfer and matrix, TV range. `h264_videotoolbox` writes only the matrix into the stream, and the MP4 muxer writes no `colr` box for it. The bitstream filter `-bsf:v h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0` sets the other values in the stream header [measured: `ffprobe` then reports all four]. For ProRes: `prores_videotoolbox` also writes only the matrix; `setparams` on the frames makes the `colr` atom carry all three [measured, 5.3]. | Explicit tags keep players from guessing [inference]. How QuickTime and YouTube display a BT.709-tagged picture made with the "Film to Video" LUT is [unverified] until check 3.8. |
| Audio, delivery | AAC-LC, 48 kHz, 384 kbps stereo | YouTube recommendation for stereo [documented, Src11]. |
| Master | ProRes 422 HQ in a `.mov` file, 10-bit 4:2:2, 24-bit PCM audio. Only when the owner ticks it (E6). | Owner decision. |
| Master size | About 3.9 MB per frame at 4096 x 2160: Apple's 354 GB per hour at 25 frames per second is 98 MB per second, which is 3.9 MB per frame [arithmetic from Src10]. That is 118 GB for 20 minutes at 25 frames per second and 141 GB at 29.97. The estimate given with the decision was about 130 GB. | `render` refuses to start a master when the free space is less than 1.1 times the estimate (E6). |
| Master speed | Not measured [unverified]. ProRes 422 LT through `prores_videotoolbox` at full size ran at 5.6 to 18.4 fps in step 1, with the load noted in section 6.1. | Check 3.10. |
| Vertical short | MP4, H.264, 1080 x 1920, AAC. Starting value 8 Mbit/s (YouTube's 1080p figure at 24 to 30 frames per second [documented, Src11]), same encoder as the film. | The owner named the format and size; the data rate is a starting value, not a decision. |
| Custom resolution | Any even `width` x `height` with the shape of `film.frame` | A different shape is rejected (rule V5); the tool does not add black bars to an export. |

### 5.7 Checks the renderer runs on its own output

After the encoder ends and before the rename:

- `ffprobe` reports as many video frames as the film has.
- The audio duration differs from the video duration by less than one frame.
- The file size is not zero and `ffprobe` reads the whole file without an error.

A file that fails stays as `.partial` and the render report names the failed check.

### 5.8 The render report

`reports/render-report.json` records, per render: the timeline hash, the tool and ffmpeg versions, the elapsed time per stage, the speed in frames per second, the warnings of the plan, each shortened crossfade (join, wanted and used seconds), each segment whose gain was capped, each silent segment, the film's loudness and true peak before and after levelling, and the check results of section 5.7.

---

## 6. Step 1: Conversion Test (Done) and the `convert` Command

### 6.1 The test and what it found

**Purpose.** Show on real clips what decoding BRAW costs on this machine (speed, size, colour), and settle how the tool stores the footage. Status: **done on 2026-10-26.** The test was run by hand with scripts, not with tool commands. The scripts and logs are on the SSD: `/Volumes/Untitled/video-edit-tool-work/step1/` (`scripts/`, `logs/`, `stills/`, `prores/`) and `/Volumes/Untitled/video-edit-tool-work/step2-preview/` (`scripts/`, `logs/`, `previews/`, `render/`, `stills/`, `shotcut_check/`). They are not in the repository. The original `.braw` and WAV files were opened read-only; nothing was written beside them.

All numbers in this section are [measured] on 2026-10-26 on the Apple M4. Other jobs loaded the machine. The load average was 21 to 73 during the conversion test and 6 to 26 during the preview-copy check and the final-render check; each speed names the load where it was logged. A repeat at a lighter load is in the tables C and E; speeds on an idle machine would be higher still [inference].

**The two test clips.**

| Clip | Codec tag | Container | Decoded by the SDK | Half-size decode | Frames | Length | Timecode |
|------|-----------|-----------|--------------------|------------------|--------|--------|----------|
| `A002_01060615_C083` | `brlt` | 4128 x 2176 | 4096 x 2160 | 2048 x 1080 | 805 | 26.86 s | 06:15:34:16 |
| `A002_01011826_C040` | `brhq` | 4128 x 1736 | 4096 x 1720 | 2048 x 860 | 3635 | 121.29 s | 18:26:12:24 |

The folder `Recordings_261003` holds 116 clips at 30000/1001 frames per second: 51 `brlt` (1425 s) and 65 `brhq` (3168 s), 76.6 minutes in all. The clip C083 carries colour science generation 5, gamma "Blackmagic Design Film", gamut "Blackmagic Design" and 12:1 compression, from a camera of type "Blackmagic Pocket Cinema Camera 4K" with firmware 7.9.1.

**A. Decoder and full-size ProRes conversion (the plan of version 2.0).** Script `step1/scripts/convert.py`: `braw.Source(pipeline='metal', scale=1)`, then `resize.Bicubic` to 10-bit 4:2:2 in the 709 matrix with ordered dither, then a `yuv4mpegpipe` into `ffmpeg -c:v prores_videotoolbox -profile:v lt`.

| What was measured | Result | Load average |
|-------------------|--------|--------------|
| Decode only, Metal, full size, C083 | 29.1, 34.7 and 33.4 fps; later 77.0, 76.0 and 66.6 fps | 34 to 47; 22 to 27 |
| Decode only, Metal, half size, C083 | 71.6, 77.8 and 75.5 fps | 34 to 46 |
| Decode only, CPU pipeline, C083 | 7.5 fps full size; 29.3 fps half size | 39 to 42 |
| Decode and conversion to 10-bit 4:2:2, no encoder | 25.5 fps full size; 74.9 fps half size | 42 to 47 |
| ProRes 422 LT, full size, C083, six logged runs | 5.6 to 18.4 fps (the six runs: 12.4, 5.6, 12.4, 18.4, 17.3, 16.8) | 21 to 73 where logged |
| ProRes 422 LT, half size, C083 | 48.0 and 48.6 fps | 55 to 68 |
| ProRes 422 LT, full size, C040 (4096 x 1720) | 20.9 fps (174 s for 3635 frames) | 29 to 46 |
| `prores_ks` (software ProRes), 120 frames, full size | 4.2 fps | 46 to 55 |
| Peak memory, full-size ProRes run | Python 1.7 to 2.2 GB, `ffmpeg` 0.4 GB, peak footprint 2.3 to 2.8 GB | |
| File size of the ProRes 422 LT copies | 1,268,387,842 bytes for C083 (2.8 GB per minute); 4,845,710,894 bytes for C040 (2.4 GB per minute) | |
| Frames compared with the decoded picture, C083 | 805 of 805; Y PSNR minimum 52.1, mean 53.4, maximum 54.3 dB; Cb and Cr means 58.3 and 57.5 dB; largest difference 37 code values of 1023 | |
| Same, C040, frames 0, 1817 and 3634 | Y PSNR 53.1 to 53.3 dB; RGB end-to-end PSNR 48.9 to 49.3 dB | |

Extrapolation for the folder: 23.75 minutes of `brlt` at 2.8 GB per minute and 52.8 minutes of `brhq` at 2.4 GB per minute give about 194 GB of ProRes 422 LT copies [arithmetic]. This is the number that led the owner to choose option A.

**B. Colour comparison.** The script `step1/scripts/stills.py` made six stills of one frame of C083 (`step1/stills/`). The log does not record the frame number; comparing the still (d) with every frame of the preview copy found frame 400 as the only close match. The stills are: (a) as recorded, (b) the decoder's own Rec.709, (c) "Blackmagic Pocket 4K Film to Video v4", (d) "Blackmagic Gen 5 Film to Video", (e) "Blackmagic Gen 5 Film to Extended Video", (f) "Blackmagic Pocket 4K Film to Extended Video v4". The LUTs are applied with `lut3d=...:interp=tetrahedral` to the 16-bit RGB picture. The owner chose (d). The LUT files are in Resolve's LUT folder, `/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/Blackmagic Design/`; they were read, not copied.

**C. Preview-copy check (the addendum).** Settings: half-size decode (`scale=2`), LUT (d) applied on the 16-bit RGB picture, then conversion to BT.709 limited range and 8-bit 4:2:0, `h264_videotoolbox` with `-q:v 73`, a key frame every 30 frames, 29.97 frames per second, source timecode, the camera audio as AAC at 192 kbit/s. The method is section 6.3. Script `step2-preview/scripts/preview.py`.

| | C083 (`brlt`) | C040 (`brhq`) |
|---|---|---|
| Copy size | 2048 x 1080 | 2048 x 860 |
| File size | 31,650,145 bytes | 74,282,904 bytes |
| Data rate, whole file | 9.43 Mbit/s | 4.90 Mbit/s |
| Size per minute | 70.7 MB | 36.7 MB |
| Speed, two runs | 53.6 and 54.5 fps | 61.8 and 62.8 fps |
| Real-time factor | 1.79 and 1.82 | 2.06 and 2.10 |
| Wall time, two runs (`/usr/bin/time`) | 15.4 s and 15.0 s | 59.4 s and 58.5 s |
| Peak memory, two runs | Python 927 and 913 MB; `ffmpeg` 243 and 244 MB; footprint 1.10 and 1.08 GB | Python 770 and 749 MB; `ffmpeg` 205 and 201 MB; footprint 0.91 and 0.89 GB |
| Load average (1 minute) at start and end of the two runs | 16.0 to 15.7, then 15.7 to 18.1 | 18.1 to 19.3, then 19.3 to 17.6 |

Extrapolation to the 116 clips, one clip after another [arithmetic from the table]: 51 `brlt` clips of 23.75 minutes make 1.68 GB in 13.2 minutes; 65 `brhq` clips of 52.8 minutes make 1.94 GB in 25.4 minutes. In all about 3.6 GB (3.4 GiB) and 39 minutes at a load average of 16 to 19. A repeat of both clips ran at 73.6 and 76.6 fps (2.46 and 2.56 times real time) at a load average of 9 to 15, which would be about 31 minutes for the folder [arithmetic]. The size depends on the picture: at the same `-q:v` the two clips differ by a factor of two in data rate. Fixed-rate encoding (`-b:v 10M`) gave 10.2 Mbit/s on both clips, which would be 5.9 GB for the folder [arithmetic].

Quality sweep (data rate of the whole file; PSNR of the H.264 against a lossless copy of the same filter chain, first 240 frames; the scratch files were deleted, the numbers are in `step2-preview/logs/quality_sweep.txt`):

| Setting | C083 data rate | C083 PSNR | C040 data rate | C040 PSNR |
|---------|----------------|-----------|----------------|-----------|
| `-q:v 60` | 3.65 Mbit/s | 40.3 dB | | |
| `-q:v 65` | 4.76 Mbit/s | 41.2 dB | | |
| `-q:v 70` | 7.70 Mbit/s | 42.7 dB | 3.85 Mbit/s | 44.1 dB |
| `-q:v 73` (kept) | 9.43 Mbit/s | 43.2 dB | 4.90 Mbit/s | not measured |
| `-q:v 76` | 13.27 Mbit/s | 44.0 dB | 7.31 Mbit/s | 45.2 dB |
| `-b:v 10M` | 10.24 Mbit/s | 41.0 dB | 10.23 Mbit/s | 44.4 dB |

What the preview copies were checked for (commands in the logs; the checks are the base of C5 in section 6.6):

| Check | Result for both files |
|-------|-----------------------|
| Frame count equals the source (`ffprobe -count_frames`; the source counts come from the decoder) | 805 and 3635; a full decode with `ffmpeg -v error -f null` printed no error |
| Duration | 26.860167 s and 121.287833 s; the `.braw` container says 26.860467 s and 121.288767 s |
| Frame rate | 30000/1001 |
| Timecode | A `timecode` tag and a `tmcd` track: 06:15:34:16 and 18:26:12:24, equal to the source |
| Key frames | One every 30 frames (27 and 122 key frames) |
| Codec and level | H.264 High profile, level 4.2 and 4.0, `yuv420p`, tag `avc1`; AAC-LC, 48 kHz, stereo |
| Colour tags | `ffprobe` reports BT.709 for matrix, transfer and primaries, TV range (with the bitstream filter of section 5.6) |
| `moov` box at the start | Yes (`-movflags +faststart`); the box order is `ftyp`, `moov`, `free`, `mdat` |
| Plays in the engine of QuickTime Player | A headless Swift program (`step2-preview/scripts/avcheck.swift`) with AVFoundation: `isPlayable` true, tracks `vide avc1`, `soun aac`, `tmcd`; `AVAssetReader` decoded 805 and 3635 frames. QuickTime Player itself was not opened. |
| Sound | The sound of C083 correlates with the source at 0.93 (100 to 4000 Hz) and 0.91 (4 to 12 kHz), at a lag of 0 samples. AAC does not keep the waveform; the copy is for listening, not for sync. |
| A frame matches the step-1 still (d) | Frame 400 of C083 in the preview copy, scaled to 1920 x 1012 with Lanczos like the still: PSNR 36.2 dB (red 35.0, green 41.5, blue 34.8). The mean colour differs by -0.3, -0.3 and -0.6 of 255. After a Gaussian blur (sigma 4) the PSNR is 47.4 dB. So colour and tone agree; the difference is fine detail (half-size decode, 4:2:0, H.264). |

**D. Shotcut check.** Shotcut 26.9.28 was installed with `brew install --cask shotcut`. The GUI was not opened. `melt 7.41.0` ran headless with `QT_QPA_PLATFORM=offscreen`:

- `melt previews/<clip>.mp4 -consumer xml:<file>.mlt all=1` parsed both files: 2048 x 1080 and 2048 x 860, 30000/1001 frames per second, 805 and 3635 frames, H.264 and AAC, colour space 709, and the timecode (`meta.attr.0.stream.timecode.markup`).
- `melt previews/<clip>.mp4 in=300 out=359 -consumer avformat:<out>.mp4 vcodec=libx264 crf=20 preset=veryfast acodec=aac ab=128k` rendered 60 frames (2.002 s) of each file in 1.7 s. `ffprobe` read 60 frames, H.264, 48 kHz AAC. While `melt` read the whole file for the XML output it printed "Could not update timestamps for discarded samples" for the AAC track; it printed no other error.
- Shotcut's own `ffmpeg` (version 9.0.2) finds no decoder for the video of a `.braw` clip (read-only test). Shotcut can use only the preview copies.

**E. Final-render check (option A).** Script `step2-preview/scripts/render_test.py`: 150 frames (5.0 s) of C083 from frame 300, decoded at full size from the `.braw` with Metal, LUT (d) on the RGB picture, conversion to BT.709 limited range, picture only. Files are in `step2-preview/render/`.

| Chain | Speed | Real-time factor | Data rate | Load average |
|-------|-------|------------------|-----------|--------------|
| Decode alone | 37.2 fps | 1.24 | | 5.9 |
| Decode, LUT, conversion, no encoder (two runs) | 17.6 and 19.2 fps | 0.59 and 0.64 | | 16.6 to 17.1 |
| `h264_videotoolbox -b:v 45M` (two runs) | 13.9 and 15.2 fps | 0.46 and 0.51 | 45.7 Mbit/s | 5.9 to 7.8; 24.2 to 25.8 |
| `hevc_videotoolbox` 10-bit main10 at 45 Mbit/s | 16.0 fps | 0.53 | 45.4 Mbit/s | 23.5 to 24.2 |
| `libx264 -preset slow -b:v 45M` (two runs) | 4.1 and 4.4 fps | 0.14 and 0.15 | 49.1 Mbit/s | 7.8 to 13.1; 25.8 to 22.8 |
| `libx264 -preset medium -b:v 45M` | 5.3 fps | 0.18 | 49.1 Mbit/s | 13.1 to 23.5 |
| C040 (4096 x 1720) letterboxed into 4096 x 2160, `h264_videotoolbox` | 20.6 fps | 0.69 | 45.7 Mbit/s | 7.4 to 8.1 |
| The same, `libx264 -preset slow` | 4.4 fps | 0.15 | 52.9 Mbit/s | 7.2 to 13.3 |
| Repeat at a lighter load, C083, no encoder | 27.3 fps | 0.91 | | 2.9 to 3.9 |
| Repeat at a lighter load, C083, `h264_videotoolbox -b:v 45M` | 26.1 fps | 0.87 | | 3.9 to 5.2 |
| Repeat at a lighter load, C083, `libx264 -preset slow -b:v 45M` | 6.6 fps | 0.22 | | 5.2 to 10.8 |

The load of the `libx264` runs includes the encoder itself, which uses all cores. Peak memory: Python 2.0 to 2.5 GB; `ffmpeg` 0.7 to 0.9 GB with VideoToolbox and 2.7 to 3.1 GB with `libx264`. Each output has 150 frames; `ffprobe` reports H.264 High (or HEVC Main 10), 4096 x 2160, `yuv420p` (or `yuv420p10le`). Frame 100 of each render (source frame 400) against the step-1 still (d) at 1920 x 1012: PSNR 39.1 dB (`h264_videotoolbox`), 39.3 dB (`libx264`), 40.0 dB (HEVC). The letterbox bars measured Y 16 and U 128, V 128 (`h264_videotoolbox`) after the bars were added in RGB. The first letterbox test added them after the colour conversion and gave Y 0; those files were deleted.

**F. What the test did not settle.** The speed of a render with many segments; the quality of `h264_videotoolbox` against `libx264` beyond one frame; the speed of the ProRes master from the RAW decode; how QuickTime Player and YouTube show the BT.709-tagged result; the content tracks of the two timed-metadata streams; the sizes of preview copies of the other 114 clips (they vary with the picture); whether two conversions at once are faster than one. Section 17 lists them with the check that settles each.

### 6.2 Decoder interface

Both decoders sit behind one interface [Design], so that the decoder can change if the plugin fails (C7):

```
probe(braw_path)  ->  ClipInfo: width, height, fps (num, den), frames, timecode_start,
                      colour_science_gen, iso, white_balance_k, has_sidecar, metadata_tracks
frames(braw_path, first, count, scale)  ->  16-bit RGB frames to a pipe, as recorded (Blackmagic Film)
audio(braw_path)  ->  the camera's scratch audio (48 kHz, 24-bit, stereo for the clips seen)
```

- **Decoder 1: `vapoursynth-brawsource` 0.5.0** (MIT). Pipelines `cpu`, `metal`, `cuda`, `opencl`; bit depths 8, 16 and 32; resolution scales 1, 1/2, 1/4, 1/8; overrides for gamma, gamut, colour science generation, white balance, ISO and exposure; sidecar files applied automatically; clip metadata as frame properties; wheels for macOS 14 on arm64 that bundle the Blackmagic runtime [documented, Src1]. **Measured in step 1:** `braw.Source(path, pipeline='metal', scale=1 or 2, gamma=..., gamut=...)` works; the frame properties carry `BRAWTimecode`, `BRAWColorScienceGen`, `BRAWGamma`, `BRAWGamut`, `BRAWISO`, `BRAWSidecarAttached`, `BRAWSensorRate` and others; `braw.AudioSource(path)` gives 48000 Hz, 24 bits (in 32-bit integers, the value in the upper 24 bits), 2 channels, and covers the same duration as the picture (1,289,288 samples for C083); the output is 16-bit RGB with the planes in the order red, green, blue. The author's numbers on an M1 Pro at 4608 x 2592 are about 73 frames per second on the CPU and 196 on Metal [documented, Src1; not an M4 number]. On the M4 at 4096 x 2160 the step-1 numbers above apply (decode alone 29 to 77 fps on Metal, depending on the load, and 7.5 fps on the CPU). The options for bit depth, white balance, ISO and exposure were not used [unverified].
- **Decoder 2: Blackmagic RAW SDK 6.0** (a small own wrapper). Not written. Written only if decoder 1 fails the checks of section 6.10 later. The SDK licence lets the owner download and use the SDK. Redistribution is allowed only for the runtime inside an application built with it; the licence text was summarised, not read in full [documented, Src2]. The repository ships neither the SDK nor the runtime.

### 6.3 Preview-copy process (the method of the `convert` command)

For one clip:

1. **Probe** the clip with the decoder. Write the facts to `conversion.json`.
2. **Audio.** Read the camera audio with `braw.AudioSource` and write it as a raw 32-bit little-endian file in the work directory (10 MB for 27 s). Delete it after the copy is made.
3. **Picture.** The decoder process opens the clip with `scale=2` (half size, on the GPU), puts the planes in the order green, blue, red and writes raw 16-bit frames (`gbrp16le`) to `ffmpeg`:

   ```
   ffmpeg -f rawvideo -pix_fmt gbrp16le -s 2048x1080 -r 30000/1001 -i -
          -f s32le -ar 48000 -ac 2 -i <audio.s32le>
          -map 0:v:0 -map 1:a:0
          -vf "lut3d=file='<base LUT>':interp=tetrahedral,
               scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int+bicubic,
               format=yuv420p"
          -c:v h264_videotoolbox -profile:v high -allow_sw 0 -g 30 -tag:v avc1 -q:v 73
          -color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv
          -c:a aac -b:a 192k  -timecode <start timecode>
          -bsf:v h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0
          -map_metadata -1 -movflags +faststart  copies/<stem>.mp4.partial
   ```

   (written on several lines here; one line in practice). The LUT is applied on the full-range RGB picture, then the picture is converted. The size is not scaled to 1920: the width is 2048 so that the half-size decode needs no resampling.
4. Run the check C5 (section 6.6). Only a copy that passes is renamed to `<stem>.mp4` and marked `done`.

The settings are `config.py` values with starting values from the test: `PREVIEW_SCALE` 2, `PREVIEW_Q` 73, `PREVIEW_GOP` 30, `PREVIEW_AUDIO_KBPS` 192. Fixed-rate encoding (`-b:v 10M`) is the alternative when a predictable size matters more than a predictable quality.

The tool converts one clip at a time. Whether two at once run faster is measured later [unverified].

The preview copy carries the camera's scratch audio so that it plays with sound. The sound of the film comes from the H2N WAV files (section 5.4). The matcher reads the scratch audio from the `.braw` file with `ffmpeg`, as today [measured: the first shoot matched 105 clips this way].

### 6.4 `conversion.json`

One file in the work directory. The values below are an illustration (the sizes and the timecode are those of clip C083; the hashes, times and the path of the work directory are made up).

```json
{
  "version": 1,
  "decoder": "vapoursynth-brawsource 0.5.0",
  "encoder": "h264_videotoolbox",
  "settings": {"scale": 2, "quality": 73, "gop": 30, "audio_kbps": 192, "encoder": "h264_videotoolbox"},
  "base_lut": {"path": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/Blackmagic Design/Blackmagic Gen 5 Film to Video.cube",
               "sha256": "0000000000000000000000000000000000000000000000000000000000000000"},
  "clips": {
    "A002_01060615_C083": {
      "original": "/Volumes/CARD/Recordings/A002_01060615_C083.braw",
      "original_bytes": 163000000,
      "original_mtime_ns": 1759478132000000000,
      "preview_copy": "/Volumes/SSD/film/copies/A002_01060615_C083.mp4",
      "status": "done",
      "frames": 805,
      "fps": {"num": 30000, "den": 1001},
      "size": {"width": 4096, "height": 2160},
      "copy_size": {"width": 2048, "height": 1080},
      "timecode_start": "06:15:34:16",
      "colour_science_gen": "5",
      "gamma": "Blackmagic Design Film",
      "gamut": "Blackmagic Design",
      "iso": 500,
      "white_balance_k": 4000,
      "has_sidecar": false,
      "audio": {"sample_rate": 48000, "channels": 2, "samples": 1289288},
      "lut_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "settings": {"scale": 2, "quality": 73, "gop": 30, "audio_kbps": 192, "encoder": "h264_videotoolbox"},
      "warnings": [],
      "convert_seconds": 15.0,
      "convert_fps": 53.6,
      "realtime_factor": 1.79,
      "bytes": 31650145,
      "check": {"codec": "h264", "pix_fmt": "yuv420p", "frames": 805, "audio": "aac", "timecode": "06:15:34:16"},
      "converted_at": "2026-10-26T14:00:00Z"
    }
  }
}
```

`status` is `partial` (being converted, or interrupted), `done`, `failed` or `skipped` (not a `.braw` file). A clip that has no entry is `pending`. The command skips a `done` clip when all of these hold (C2): the copy exists with the recorded size, the original has the recorded size and modification time, the clip's `lut_sha256` equals the hash of the current base LUT (C3), and the clip's `settings` equal the current ones. Otherwise the clip is converted again. **Changed in the build:** the example in the plan used relative paths and lacked the fields `original_bytes`, `original_mtime_ns`, `lut_sha256`, `settings`, `audio`, `warnings`, `error`, `realtime_factor` and `converted_at`; section 2.6 says all paths in `conversion.json` are absolute, so the file stores absolute paths, and it carries those fields. The top level also carries `settings` (scale, quality, GOP, audio bit rate, encoder) of the last run.

### 6.5 Colour

D2 is settled. The decoder is given no colour overrides: it outputs the picture as recorded, gamma "Blackmagic Design Film" and gamut "Blackmagic Design" [measured]. The base LUT converts it to Rec.709 video levels.

- The default base LUT is "Blackmagic Gen 5 Film to Video" (panel d, section 6.1 B). The file is in Resolve's LUT folder on the owner's Mac; the tool reads it from there and never copies it into the repository. The path is a setting.
- The LUT is applied with `lut3d` and `interp=tetrahedral` to the 16-bit full-range RGB picture, before the conversion to video levels.
- The preview copies carry the base LUT baked in (section 6.3). The final render applies it to the RAW decode (section 5.3). The film preview does not apply it again.
- Tags: every file is tagged BT.709 for primaries, transfer and matrix, with TV range (section 5.6). Whether the LUT's output follows the BT.709 transfer curve or a pure gamma of 2.4 was not checked, so how a player shows the file is [unverified] until check 3.8.
- `convert` reads the colour science generation of every clip. A clip that is not generation 5 is converted all the same, but marked in the report: the default LUT is made for generation 5 [inference from the LUT's name; not checked on other generations].

### 6.6 Checks on a finished copy (C5)

`ffprobe` on the copy must report:

- codec `h264`, profile High, pixel format `yuv420p`,
- width and height equal to the source's decoded size divided by `PREVIEW_SCALE`,
- the frame rate of the source, and as many frames as `probe` reported. **Changed in the build:** the count is the number of packets in the video stream (`ffprobe -count_packets`), because an MP4 holds one packet per frame and reading packets is much faster than decoding the file; step 1 used `-count_frames`, which decodes. The first and the middle frame are decoded as the last item of this list,
- one AAC audio stream at 48 kHz, as long as the source's own audio to within one frame. **Changed in the build:** the plan said "as long as the video". A real clip (`A002_01011735_C021`) holds 4.000 s of audio and 4.87 s of picture (the file says so: `ffprobe` and the decoder agree), so the copy cannot be as long as the video. The copy is compared with the source audio's length (samples divided by the sample rate), and the clip gets a warning in `conversion.json` when the audio and the picture differ by a frame or more,
- a timecode equal to the source's,
- the `moov` box before the `mdat` box.

Two frames, the first and the middle, must not be entirely one value (a black or constant frame points to a failed decode).

### 6.7 Space and the size estimate (C6)

Before the first conversion the tool measures the size per minute on the first clip of each kind (`brlt` and `brhq` differ by a factor of two in size per minute: 71 and 37 MB [measured]), multiplies by the minutes of that kind in the matched clips, adds 25 percent because the size depends on the picture, and refuses to start if the SSD has less free space. For the first shoot the estimate is about 3.6 GB before the margin.

**Built as:** before the first conversion the estimate uses the size per minute of step 1 for a 4096 x 2160 picture (`PREVIEW_PRIOR_MB_PER_MIN_DCI4K`, 71 MB), scaled by the number of pixels for other sizes, until a clip of that kind has been converted. After every converted clip the estimate for the remaining clips is made again from the measured sizes (the clips in `conversion.json` made with the current settings), and the run stops with `NotEnoughSpace` if the free space is below it. The estimate also counts the largest raw camera-audio scratch file (48 kHz, 2 channels, 4 bytes per sample).

The ProRes 422 HQ master of section 5.6 has its own check before it starts: about 3.9 MB per frame times the film's frames, times 1.1 [arithmetic from Src10].

### 6.8 What the `convert` command reports

For the test clip and then for all clips: decoder speed, encoder speed, end-to-end speed in frames per second, the real-time factor, size per minute, total time and total size for the whole shoot, the peak memory, and the load average at the start and end. It also reports the colour science generation, the frame rate, the frame size, the timecode, whether a sidecar exists and the names of the metadata tracks of each clip. If the metadata tracks hold camera motion data, a later step may use them instead of the picture analysis (section 7.4); this is [unverified] and only reported here.

### 6.9 Other changes with the `convert` command

- `Project` gets the field `work_dir`. Loading a file without it still works.
- `thumbnails.py` reads the preview copy when one exists, so `.braw` clips get thumbnails (M8).
- A function `assert_not_inside(path, folders)` refuses any write whose target lies inside a folder that holds an original (M9). Every writer calls it. **Changed in the build:** it lives in `safety.py`, with `originals_folders(project)`, which collects the folders of all matched videos and audio files. It resolves symbolic links and `..` before it compares. `convert` calls it for the work directory before it creates anything.
- `tools.py` and `python3 main.py tools check` (section 2.4).
- The tool moves to Python 3.12 in its own environment (D3). The 60 tests of v1.1 are run there first, and what breaks is fixed before anything else [unverified until run].

### 6.10 Checks for the `convert` command

The `convert` command is planned and not built. These checks belong to it; they are the first work of step 2. "Check" commands run from the repository folder. Checks that need the owner's clip read it from the environment variable `VET_REAL_BRAW`; they are skipped when it is not set. Checks that write need `VET_WORK_DIR` (a folder on the SSD). The check numbers 1.1 to 1.10 are kept from version 2.0.

| Check | Command | Passes when |
|-------|---------|-------------|
| 1.1 | `python3 -m unittest tests.test_convert tests.test_convert_flow tests.test_convert_cli tests.test_decoder -v` | The `ffmpeg` command line is built as in section 6.3 and the decoder process as in section 5.3. `conversion.json` round-trips. A `done` clip with an existing file is skipped. A `partial` clip is converted again. A `done` clip whose baked LUT hash differs is converted again. The fake decoder (`tests/fakes.py`) makes tiny synthetic frames and a sine wave; `ffmpeg` is real and encodes with `libx264` so that the pipe, the filter, the check and the rename run. A LUT path with a space, a colon, a comma and a quote is accepted by `ffmpeg` (**changed in the build:** a quote and a colon in the path need escaping for both parsing levels of the filter description; `convert.escape_filter_path`). |
| 1.2 | `python3 -m unittest tests.test_convert_verify -v` | The check of section 6.6 accepts an MP4 made from `ffmpeg -f lavfi -i testsrc2=size=640x360:rate=30000/1001 -f lavfi -i sine -t 2 -c:v libx264 -c:a aac -timecode 01:00:00:00 -movflags +faststart` and rejects one with the wrong frame count, a wrong pixel format, no audio stream, no timecode or the `moov` box at the end. |
| 1.3 | `python3 -m unittest tests.test_convert_space -v` | With a fake free-space value, the tool refuses when the estimate plus margin exceeds it and starts when it does not. The estimate uses the size per minute of each clip kind. The tool stops when the space runs out during a run. An SSD that is not mounted is refused before anything is created. |
| 1.4 | `python3 -m unittest tests.test_originals -v` | A run of convert on fixture originals (a fake decoder) leaves their sha256 unchanged. A write to a path inside the originals' folder raises an error. A work directory inside the originals' folder is refused before any file is made. |
| 1.5 | `python3 main.py tools check` | Exit 0 and a list in which `ffmpeg-full`, the filters and encoders of section 2.4 and the `vapoursynth` import are all present. [after the installs] |
| 1.6 | `python3 main.py convert --probe "$VET_REAL_BRAW"` | Exit 0. The JSON on standard output has width, height, fps, frames, colour science generation, the sidecar flag and the names of the metadata tracks. |
| 1.7 | `python3 main.py convert --test "$VET_REAL_BRAW" --work "$VET_WORK_DIR" --seconds 10` | Exit 0. `ffprobe` on the copy shows `h264`, `yuv420p`, half the decoded frame size, AAC audio, the source timecode and 10 seconds of frames. The first frame of the copy is compared with the first frame of the RAW decode through the same LUT (a frame the check makes), in 8-bit RGB at the copy's size. **Changed in the build:** the plan asked for a PSNR of at least 35 dB. On the first 8 frames tested of `A002_01060615_C083` (q 73, load average about 50) the plain PSNR was 32.8 to 36.8 dB, because the half-size decode, 4:2:0 and H.264 lose fine detail [measured, 2026-10-26]. The check now asks for: plain PSNR of at least 30 dB; the mean of each colour channel within 1.5 of 255 (measured 0.0 to 0.5); and the PSNR after a Gaussian blur of sigma 4, which removes the fine detail, of at least 40 dB (measured 48.8 to 49.7 dB; step 1 gave 47.4 dB). The test is `tests/test_convert_real.py` and needs `VET_REAL_BRAW` and `VET_WORK_DIR`. **The owner opens the copy** in QuickTime Player and in Shotcut and looks at the colour. |
| 1.8 | `python3 main.py convert --test "$VET_REAL_BRAW" --work "$VET_WORK_DIR"` | The whole clip converts and passes C5. The printed report has the numbers of section 6.8. The speed lies within a factor of two of the step-1 figure for that kind of clip at a comparable load (1.8 times real time for `brlt`, 2.1 for `brhq`). |
| 1.9 | `python3 main.py convert --work "$VET_WORK_DIR"` | All matched clips of the first shoot are `done` and pass C5. Run again: no clip is converted a second time. The report lists, for every clip, the frame rate, frame size, colour science generation and sidecar flag, and for every WAV the sample rate and channel count. |
| 1.10 | `python3 -m unittest discover -s tests -v` | All earlier tests still pass, on Python 3.12. |

Checks 1.8 and 1.9 write files to the SSD. They run on the work directory named in `VET_WORK_DIR`.

**Results of the build** [measured, 2026-10-26, load average 100 to 140 from other jobs]: (a) The copy of `A002_01011826_C040` made by `convert` has the same size in bytes (74,282,904), the same codec facts, frame count (3635) and timecode as the copy made by hand in step 1, and `ffmpeg`'s `psnr` filter reports infinity for all 3635 frames: the decoded pictures are identical. (b) The first 3 s of `A002_01060615_C083` pass the check against the RAW decode (section above, check 1.7). (c) Speeds of the clips converted so far under that load: 8.6 to 27 fps (0.29 to 0.91 times real time), against 1.8 to 2.1 times real time in step 1 at a load of 16 to 19. (d) One clip (`A002_01011735_C021`) has 4.000 s of audio and 4.87 s of picture in the source file (section 6.6). The full run over the first shoot was started in the background; its result is not recorded here.

---

## 7. Step 2: Timeline File and Assembly

### 7.1 Purpose and outputs

From the matched pairs and the preview copies, make a rough film: clips in recording order, the clap and the camera movement cut out, scenes split, sound crossfaded, the film faded in and out. Write it to `timeline.json`, render a film preview, and show it in the page with trim controls. The first work of step 2 is the `convert` command (section 6), because every later part reads the preview copies.

Outputs: `copies/`, `conversion.json`, `analysis/<stem>/clap.json`, `analysis/<stem>/scenes.json`, `timeline.json`, `reports/assembly-report.json`, `previews/`. A rough film without loudness work can be rendered with the renderer of section 5 (`loudness.mode: off`).

Status: built. Needs the result of step 1 (done). D5 to D8 are settled (`PRD.md` section 3).

### 7.2 Finding the clap

`clap.py` offers a pure function `find_clap(samples, rate, search_s)`. It returns the time of the first clap in the first `search_s` seconds, with a strength, or nothing. The method follows the research note on onset detection. It was built and tested on synthetic signals and on the first shoot (below).

1. High-pass the signal at 2 kHz. A clap is a very short broadband sound; wind and room tone sit lower [inference].
2. Take the mean power in windows of 2 ms.
3. Onset strength per window: the rise of the log power from the window before, `max(0, log(P[k]) - log(P[k-1]))`. A log ratio does not depend on gain.
4. A candidate is a window whose onset strength is at least `CLAP_MIN_ONSET` and is the local maximum within 50 ms. It counts as a clap only if the power falls under 20 percent of its peak within `CLAP_DECAY_SEC`. A steady loud sound or a step in level does not decay and is not a clap.
5. Refine the time to the first sample, on the high-passed signal, that reaches a quarter of the peak of the three windows from the window before the onset to the window after it, and at least five times the RMS of the 50 ms before. **Changed in the build:** the plan said "within 20 ms around the window"; with loud ambience (noise at 0.2 of the clap's level) a 20 ms span let a noise sample pull the time 9 ms early, over the 5 ms tolerance of check 2.4.
6. `find_clap` returns the first candidate in time that passes step 4, with its `onset`, `snr_db` (peak power against the median power of the span), `decay_s` and `confidence` (the onset against three times `CLAP_MIN_ONSET`, at most 1), the list of all candidates and the strongest one. It returns nothing when no candidate passes.

Decision D6: "Only at the start". Each clip has one clap at its beginning, so the search covers the first `CLAP_SEARCH_SEC` seconds only and there is no search for a clap at the end.

`analyse --clap` runs it on the scratch audio of each `.braw` (16 kHz mono, as the matcher extracts it, the first `CLAP_SEARCH_SEC` + 10 s) and on the matched WAV, and combines the two (`analyse_clip`). It writes `<work_dir>/analysis/<stem>/clap.json` for each clip and the report `<work_dir>/reports/clap-report.json` and `.txt` (options `--limit N`, `--report-name NAME`, `--work DIR`; it never writes into a folder that holds originals).

**Changed in the build: the clap is a sound that both microphones heard.** The plan took the first clap of each channel and required that `(t_wav - t_video)` equals the stored offset within `CLAP_AGREE_SEC`. On the 105 clips of the first shoot that held for 58 clips and failed for 47 [measured, 2026-10-26]. The envelopes of three failing clips (C008, C010, C012) show why: the real clap is the loudest event near the start, and one channel often has an earlier sound that the other did not hear (a click of 11 to 19 dB under the clap at 0.04 to 0.4 s in the WAV, or in the camera audio). The stored offset is known to about 1 ms, so the method uses it as evidence:

1. A shared event is a camera candidate and a WAV candidate that lie `offset` apart within `CLAP_AGREE_SEC`.
2. The loudness of a shared event is the smaller of its two `peak_db` values.
3. The clap is the first shared event whose loudness is within `CLAP_PAIR_BAND_DB` (10 dB) of the loudest shared event of the clip. A clap is among the loudest sounds; the first of those is the one at the start (D6).

| Result | When | Video time | Review |
|--------|------|------------|--------|
| `agree` | At least one shared event | The camera's time of the clap. | Only for the reasons below. |
| `single` | No shared event, but a clap in at least one channel | The first clap of the camera audio, else the first clap of the WAV minus the offset. | Always. |
| `none` | No clap in either channel | None. No clap cut. The clip is kept from its first steady frame (section 7.5, rule 3) and is listed in the report as "no clap" (D6). | Always. |

Reasons that mark a clip for review: a result of `single` or `none`; a clap later than `CLAP_LATE_SEC` (10 s), because a clap at the start is expected; a confidence under `CLAP_WEAK_CONFIDENCE` (0.5); an offset outside `USUAL_OFFSET_RANGE_SEC`. The last one is the owner's list of nine odd offsets of the first shoot: 96 of the 105 offsets lie between 0.085 and 0.37 s (`PRD.md` section 5 says 0.09; the lowest of the 96 is 0.0851 s), and the nine outside are -5.03, -1.70, 0.63, 0.64, 0.66, 0.83, 0.89, 1.28 and 1.94 s.

**Result on the first shoot** [measured, 2026-10-26, report `clap-report-all.txt` in the work folder]: 98 of 105 clips `agree` (the difference of the claps from the stored offset is at most 2 ms); 7 `single`; none `none`. The times of the 98 claps: median 0.26 s, 90 percent under 5.1 s. 30 clips are marked for review. Of the nine odd offsets, six are confirmed by shared sounds at that offset (0.63, 0.64, 0.66, 0.83, 0.89 and 1.94 s): the offsets are correct. Three have no shared sound at the stored offset: **-5.03 s (A002_01011851_C047), -1.70 s (A002_01060614_C082) and 1.28 s (A002_01060813_C101)**. Their offsets stay unconfirmed [unverified]; the report lists them. The owner decided on two of them, C047 and C101 (the clap report lines their claps up at +0.021 s and +0.179 s [inference, not checked by ear]): **"Leave them, I set them"** (`PRD.md` section 3). The stored offsets stay, and the owner types an offset per clip in the Film tab (7.6). The owner has not decided on C082 (only the recorder heard a clap, so the camera audio cannot check its offset). Whether the claps found are the claps the owner made is [unverified] until the owner listens (check 2.14). The first 10 clips all `agree`, with claps between 0.10 and 1.45 s, except A002_01011540_C002, whose only shared sound is at 15.9 s and weak (marked for review).

`peak_db` uses the median power of the searched span as its reference; a clip with a busy kitchen has a higher median and so lower values, which is why the band is relative to the loudest event of the same clip.

The segment's first frame is `ceil((t_clap + CLAP_GUARD_SEC) * fps)`. The guard keeps the clap's tail out of the film. Because the audio follows the segment, cutting the picture also cuts the sound at the same instant.

| Setting | Starting value |
|---------|----------------|
| `CLAP_SEARCH_SEC` | 20 |
| `CLAP_HIGHPASS_HZ` | 2000 |
| `CLAP_MIN_ONSET` | 2.0 (a rise of about 7 times in power) |
| `CLAP_DECAY_SEC` | 0.1 |
| `CLAP_AGREE_SEC` | 0.02 |
| `CLAP_GUARD_SEC` | 0.5 (the research suggests 0.5 to 1 s) |
| `CLAP_PAIR_BAND_DB` | 10 (added in the build) |
| `CLAP_LATE_SEC` | 10 (added in the build) |
| `CLAP_WEAK_CONFIDENCE` | 0.5 (added in the build) |
| `USUAL_OFFSET_RANGE_SEC` | 0.085 to 0.37 (added in the build) |

All are [inference] starting values. Check 2.14 prints the clap time of every clip so the owner can listen to a sample and correct the values. D6 is settled; the values stay starting values.

### 7.3 Drift between camera and recorder

The matcher measures the offset on the first 60 seconds [Appendix A.3]. A camera and a recorder run on different clocks, so the offset could change along a clip. This is [unverified] for the owner's gear.

`analyse --drift` measures the offset again on the last 60 seconds of every clip of at least 120 seconds. For that, `_extract_audio_raw` gets an optional start time (`-ss` before the input). The drift is `offset_tail - offset_head` seconds over `T - 60` seconds, reported in parts per million. The report marks every clip whose drift exceeds half a frame (20 ms at 25 frames per second).

**As built (`clap.measure_drift`, `clap.analyse_drift_project`).** `analyse --drift [--work DIR] [--limit N] [--only A,B] [--report-name NAME]` (default report name `drift-report`; with `--clap` or `--scenes` and a name, `NAME-drift`). For every matched clip it takes the length from the project (`video_duration`), the head offset from the project (`offset`, the matcher's value on the first 60 s) and the frame rate from `conversion.json` (else 29.97). A clip shorter than 120 s (`DRIFT_MIN_CLIP_SEC`) is skipped, because its two windows would overlap. For the others the camera window is the last 60 s of the clip's scratch audio; the recorder window is the 60 s that start `offset` later in the WAV (not before the start of the file), so the sound that both heard sits at about the same place in both windows. The matcher's `_find_offset` finds the lag; `offset_tail` is the lag plus the shift of the windows. `drift = offset_tail - offset_head`, in seconds, and `ppm = drift / (T - 60) * 1e6`, because the middles of the two windows are `T - 60` seconds apart. The clip is over the limit when `|drift|` is more than half a frame (`DRIFT_LIMIT_FRAMES`) of its frame rate: 16.7 ms at 29.97 fps, 20 ms at 25 fps. A drift of more than 0.25 s (`DRIFT_PLAUSIBLE_SEC`) is no clock drift: the clip is `not_measurable` (the last minute holds no sound that both heard) and the report says so. A clip whose audio cannot be read is `error`, not a crash. It writes `analysis/<stem>/drift.json` for every clip except the skipped ones, and `reports/<name>.json` and `.txt` (one line per clip, the counts, the largest drift, the clips over the limit). It reads the originals only and refuses a work folder inside a folder that holds originals. Measured on synthetic tracks: 50 ppm gives 46.9 to 50.5 ppm over 200 s, and 66.7 ppm over a clip of exactly 120 s, where the windows lie 60 s apart [measured, synthetic].

**Result on the first shoot** [measured, 2026-10-26, report `drift-all.txt` in the work folder, 6.1 s for all 105 clips]. Only 5 of the 105 clips are 120 s or longer, so only those are measured; the other 100 are skipped. The drift (offset on the last minute minus the stored offset) of the five, with the limit of half a frame at 29.97 fps, 16.7 ms: C040 (121.3 s) -0.6 ms (-10.6 ppm), C046 (124.1 s) +1.2 ms (+18.5 ppm), C049 (263.4 s) -1.8 ms (-8.9 ppm), C050 (140.3 s) -0.1 ms (-1.8 ppm), C063 (271.1 s) -1.9 ms (-8.8 ppm). None is over the limit and none is not measurable. The largest drift is 1.9 ms; the signs differ, which is what measurement noise of 1 to 2 ms looks like [inference]. A check by a scratch script (not in the repository), with windows of 60 s every 30 s on the same five clips: 21 of 24 windows (the first window of each clip equals the stored offset by construction) lie within 3 ms of the stored offset; 3 gave 22, -22 and -42 ms, one or two steps of the 20 ms envelope of the coarse search [inference]; the tail windows that the command uses were not among them. **Limit by design:** one tail window can be such an outlier, so a clip that the report marks as over the limit should be confirmed with a second window before it is believed. The clips under 120 s are not measured. At the largest value seen (18.5 ppm) a clip just under 120 s would drift 2.2 ms [arithmetic], but five clips say nothing certain about the others.

`audio_drift_ppm` in the timeline file stays 0 unless the owner sets it. When it is not 0, the start of each segment's audio is shifted by `in/fps * ppm * 1e-6` seconds. Drift inside one segment is not corrected: at 50 ppm over 60 s it is 3 ms [arithmetic].

### 7.4 Finding the steady runs

`stable.py` finds the stretches in which the camera does not move. The owner films static [owner statement], so the method looks for a shift of the whole picture. **Changed in the build:** it uses OpenCV (D3 allows it) for the correlation, and it measures blocks of the picture and fits one camera motion to them (below), so that a person who moves in a still shot is not taken for camera movement. The plan's text follows; the section "As built" after the settings table says what the code does.

**Method** (all numbers are [inference] starting values taken from the research note; the method is a design):

1. Decode the preview copy to 5 frames per second, grey, 480 pixels wide (`fps=5,scale=480:-1:flags=area,format=gray`) through a pipe.
2. Turn each frame into its gradient magnitude, subtract the mean and divide by the standard deviation, so a change in brightness or exposure does not matter. Apply a Hann window to reduce edge effects.
3. For each pair of neighbouring frames, find the shift by phase correlation: multiply one spectrum by the conjugate of the other, normalise to unit magnitude, transform back, and take the peak. Refine the peak to a fraction of a pixel with a parabola through its neighbours. The height of the peak (0 to 1) is the quality.
4. Also measure the shift against the first frame of the current run, to catch slow drift.
5. A step is **moving** if the shift is at least `STABLE_MOVE_PX` or the drift against the run's first frame is at least `STABLE_DRIFT_PX`. A step with a peak under `STABLE_MIN_PEAK` cannot be measured. It is not called moving; the run that holds it is marked `low_confidence`.
6. Steady runs are the stretches of steps that are not moving. A moving stretch shorter than `STABLE_MERGE_GAP_SEC` is ignored, so a tiny shake does not split a scene. A run shorter than `STABLE_MIN_RUN_SEC` is dropped. Each remaining run loses `STABLE_GUARD_SEC` at both ends, so the camera has settled.
7. The score of a run is `1 / (1 + rms_shift / STABLE_MOVE_PX)` over its steps. "Most stable" means the highest score; the length breaks ties.
8. Run times convert to frame numbers at the film frame rate.

**Output** `stable.json`: the settings used, the runs (`in`, `out`, `score`, `low_confidence`), the moving stretches, and the path of a contact sheet that shows the first, middle and last frame of each run and one frame inside each moving stretch. **Changed in the build:** the file is `analysis/<stem>/scenes.json`, and the contact sheet is not built (the stills of check 2.14 are made by hand).

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

**What was tested.** A prototype with `numpy` only, on a synthetic clip of a static texture (made as ProRes for that test), gave a shift of 0 pixels with a peak of 1.00 in the still intervals, and 11 pixels per step in the interval in which the window moved at 150 pixels per second [measured, synthetic, scratch test not in the repository]. A second synthetic clip with a bright 300-pixel block moving over the static texture gave a shift of 0 in every step, with peaks between 0.67 and 0.89 [measured, synthetic]. Real footage has noise, a moving subject of any size and changing light. Whether the settings above separate camera movement from subject movement on the owner's clips is [unverified] until check 2.14.

**As built (changed in the build).** `analyse --scenes [--limit N] [--only A,B] [--work DIR] [--report-name NAME]` reads the preview copy of every matched clip whose status in `conversion.json` is `done` (the other clips get the result `no_copy` in the report), writes `analysis/<stem>/scenes.json` and `reports/<report-name>.json` and `.txt` (default name `scenes-report`), and never writes `conversion.json`. The steps:

1. `ffmpeg` decodes the copy once and passes every 6th frame (`select='not(mod(n,6))'`, 29.97 / 6 = 4.995 sampled frames per second) as grey, 480 pixels wide (`scale=480:H:flags=area`) through a pipe. Frame numbers are those of the file.
2. Each sampled frame is blurred (sigma 1), turned into its gradient magnitude (Sobel) and cut into a grid of 4 x 3 blocks (`STABLE_GRID`). Each block is normalised (mean 0, standard deviation 1) and multiplied by a Hann window. OpenCV's `phaseCorrelate` multiplies its input by a window in place, so the window is applied once in the preparation and `phaseCorrelate` gets none.
3. For two sampled frames every block pair gives a shift (dx, dy) and a peak height. Blocks with a peak under `STABLE_MIN_PEAK` (0.15) are not used. One camera motion is fitted to the others: a similarity transform (shift, zoom, roll). The candidates are the shift of each single block and the transform through each pair of blocks; the winner is the one with the most blocks within `STABLE_INLIER_PX` (0.75 px) of it, and among equals the one with the least motion. It is refined by least squares on its blocks. At least `STABLE_MIN_INLIERS` (3) blocks must agree, else the step cannot be measured. Blocks that do not agree, such as those that a person moves in, are ignored. The motion of a step is the largest displacement of a frame corner under the fitted transform, so a zoom or a roll counts as movement like a shift does.
4. A step that cannot be measured is a **cut** (movement) when the picture changed by at least `STABLE_CUT_DIFF` (0.12 of the full scale, mean absolute difference of 64 x 36 copies), else it is not movement and the run that holds it is marked `low_confidence` (a dark or flat picture).
5. A step is **moving** when its motion is at least `STABLE_MOVE_PX` or it is a cut. Every third sample the shift against the first sample of the run is measured; at `STABLE_DRIFT_PX` the run ends and a new one starts (a slow creep). When a movement ends, the stretch is ignored (the scene goes on) only if it was shorter than `STABLE_MERGE_GAP_SEC`, the camera is back within `STABLE_DRIFT_PX` of the start of the run and the change is not a cut: a bump that moves the framing splits the scene.
6. Each run loses `STABLE_GUARD_SEC` at both ends, in frames: `in = first sampled frame + 9`, `out = last sampled frame + 1 - 9`. A run shorter than `STABLE_MIN_RUN_SEC` goes to `dropped` with its length (only those of 0.5 s or more are listed). The score is `1 / (1 + rms / STABLE_MOVE_PX)` over the measured steps of the run.

`scenes.json` holds `fps`, `frames`, `seconds`, the `settings`, `runs` (`in`, `out`, `score`, `low_confidence`, `seconds`), `dropped`, `moving` (frame ranges between runs), `kept_seconds`, `dropped_seconds`, `moving_seconds` and, for tuning, `samples` (the frame, the motion into it, the shift against the run's start and the kind of every step).

| Setting added in the build | Value |
|----------------------------|-------|
| `STABLE_GRID` | 4 columns, 3 rows of blocks |
| `STABLE_MIN_INLIERS` | 3 blocks |
| `STABLE_INLIER_PX` | 0.75 |
| `STABLE_CUT_DIFF` | 0.12 |

The other settings keep the starting values of the table above. OpenCV's sub-pixel estimate lies below the true shift for shifts under 0.5 px (0.1 px is read as 0.05, 0.3 px as 0.2) and is accurate above [measured, synthetic].

**Tests** (`tests/test_stable.py`, `tests/test_stable_video.py`; synthetic video made with numpy and OpenCV, so nothing is stored): a still camera gives one scene that loses only the guard; a pan, a shaking camera and a zoom give none; still-pan-still gives two scenes whose edges lie 0.1 to 0.8 s from the movement; a bump that moves the framing, a cut to another picture and a slow creep each split the scene; a small wobble that returns does not; flicker and sensor noise do not move a scene. **People who move in a still shot do not count as camera movement:** a person a quarter and a third of the picture wide who walks through the shot, a person who moves in place, and two people at once give one scene and no movement; a camera pan with a person in the shot is still found. The same checks run through `ffmpeg` on H.264 files, and `analyse --scenes` is tested over a project with preview copies.

**Result on the first 10 clips of the first shoot** [measured, 2026-10-26, report `scenes-first10.txt` in the work folder]: 10 clips of 484.0 s together give 16 scenes of 403.0 s; the assembly (7.5) uses 15 of them, 392.4 s, after the clap cut. 5 clips hold 5 to 23 s of movement each (a camera tilting and panning by hand between takes), 5 clips hold less than 1 s. The analysis took 38.6 s for the 484 s of video, one clip after the other, at a load average of 10 to 16 (12.5 times real time); at a load of 100 or more one 97 s clip took 63 s. The steps that count as steady lie at a median of 0.02 to 0.18 px per clip, their 99th percentile at 0.26 to 0.47 px; of 2,383 measured steps, 2,071 are under 0.5 px and 44 lie between 0.5 and 0.8 px, so the limit of 0.5 px sits at the upper end of the steady noise. No starting value was changed. For all 15 scenes the shift between the first and the last frame is 0.08 to 1.30 px (limit of a split 3 px); 9 of the 15 still sheets and two sheets of the flagged movement of the first clip were looked at by hand: scene starts and ends sit on steady frames, and rain, ripples and people moving in a still shot did not split a scene. One clip (`A002_01011548_C006`, a dark scene) has steps that could not be measured and is marked `low_confidence`.

**Limits by design.** A subject that fills most of the frame can dominate the correlation. The method reports the peak quality and the report lists every cut, so the owner can see what was decided. When blocks of the background carry too little detail (a flat wall, a dark picture) fewer than three blocks agree, the step cannot be measured and the camera movement in it is found only if the picture changes by `STABLE_CUT_DIFF`; the clip is marked `low_confidence`.

**Decode cost.** The analysis reads the preview copy, which is H.264 at 2048 pixels wide, so reading 5 frames per second out of 29.97 is cheap compared with the 4K ProRes copy of version 2.0 [inference; not measured]. Check 2.14 measures it. OpenCV is allowed (D3) and may replace the hand-written phase correlation (`cv2.phaseCorrelate`) if it is faster or simpler [inference]; the test in check 2.6 stays the same.

**Camera motion data.** The clip checked earlier has two timed-metadata tracks [measured, PRD section 5]; the test clips have two data streams that `ffprobe` shows as `unknown` [measured]. If they hold gyroscope data, they could replace this analysis. Their content is [unverified] and is read by `convert` (check 1.6); this section does not depend on it.

### 7.5 Assembly rules

`assemble.py` builds `timeline.json`. Inputs: `project.json`, `conversion.json`, the files in `analysis/`.

1. **Clips.** If `project.selection` is not empty, use those clips in that order. Otherwise use all matched clips in recording order (the natural order of the file names, as in v1.1) (M7, A1).
2. A clip without a `done` preview copy is left out; the report says why.
3. **Earliest frame.** The first frame after the clap (section 7.2). With result `none` there is no clap cut (the earliest frame is 0), so the first segment starts at the clip's first steady frame, and the clip is listed in the report (D6).
4. **Runs.** Each steady run of the clip, cut at the earliest frame, becomes one segment if it is at least `STABLE_MIN_RUN_SEC` (2 s) long. Decision D7: every steady scene is kept, and only the movement between scenes is dropped. A clip with several runs gives several segments in time order (A3, A4).
5. **No steady run.** The clip is not put on the timeline. The report names it, with the reason. Decision D7, the owner's answer: "Leave it out, list it". The report gives the clip's name, its preview copy and its frame count, so the owner can add the clip back by hand as a segment with `origin: "manual"`. The setting `on_no_steady_run: keep_whole` of the earlier plan is not built.
6. **No `scenes.json`** (named `stable.json` in the plan; changed in the build). The clip after the clap becomes one segment marked `unanalysed` (if it is at least `STABLE_MIN_RUN_SEC` long) and flagged for review. **No `clap.json`:** there is no clap cut and the clip is flagged for review.
7. **Segment fields.** `id` `s001`, `s002` and so on, running on over all clips; `label` the clip name plus the number of the scene in the clip (`<clip> scene 2`, the number the detector gave it, so a scene dropped before it leaves a gap); `origin` `assembly`; `note` with the clap guard and the clap time (when the clap cut set the start) and the scene score.
   A scene is cut at the earliest frame (rule 3). If it ends before it, it is dropped ("ends before the clap cut"); if less than `STABLE_MIN_RUN_SEC` is left, it is dropped ("shorter than 2 s after the clap cut"). Both go to `scenes_dropped` in the report. **Added in the build:** a clip whose clap result is `single` or `none`, or whose clap the clap report marks for review, or whose scenes are `low_confidence`, is flagged `review` in the report with the reasons.
8. **Film fields.** `fps` from the sources (all must agree, rule V4). `frame` and `fit` default to 4096 x 2160 and `fit` (D5, settled); `assemble --frame 4096x2160 --fit fit` can set them. The wide clips get black bars (section 5.2). Fades and join take the starting values of section 3.2.
9. **Loudness and look.** Step 2 writes `loudness.mode: off`, and `look.base` from the base LUT recorded in `conversion.json` (the LUT baked into the copies). Step 3 sets a loudness mode and a target, and a creative LUT if the owner wants one.
10. **Overwrite.** If `timeline.json` exists, `assemble` refuses unless `--replace` is given, and then snapshots the old file first (`timeline.history/NNNN.json`). Trim work by hand is not lost by running assembly again. **Added in the build:** the offsets the owner typed (`audio_offset_hand_s`) are carried over into the new file for the clips that are in it; the report lists the offsets kept (`hand_offsets_kept`) and the ones dropped because their clip is not in the new film (`hand_offsets_dropped`; the old file is in the history). An old file that cannot be read is a warning in the report.
11. **Fades that do not fit (added in the build).** Rule V11 demands that the first scene is at least the fade-in plus half the join, and the last scene at least the fade-out plus half its join (a film of one scene: both fades). With the starting values (fade-out 2 s, join 0.5 s) a last scene of 2.0 to 2.25 s would break the rule. Assembly then shortens the film fade to what fits (rounded down to a millisecond), writes the shortened value into `film.fade_in_s` or `film.fade_out_s`, and says so in the report (`fades_shortened`, `warnings`). The alternative, dropping the scene, was not chosen.
12. **Order of work (as built).** `assemble [--work DIR] [--output FILE] [--replace] [--limit N] [--only A,B] [--frame WxH] [--fit fit|fill] [--fade-in S] [--fade-out S] [--join S] [--min-scene S] [--clap-guard S] [--title T] [--report-name NAME] [--no-file-checks]`. It reads `conversion.json` (never writes it), the `clap.json` and `scenes.json` of each clip and the project; it validates the timeline **with the file checks** (rule V7 reads every preview copy) before it writes, so an invalid timeline is never written; it refuses a work folder or an output file inside a folder that holds originals. The timeline file is `timeline.json` next to the project file unless `--output` says otherwise. `--limit` and `--only` choose clips after the order is set.

Everything `assemble` decides is also written to `reports/assembly-report.json` and `.txt` (name set by `--report-name`): per clip the clap result and time, the earliest frame, the scenes found and the scenes dropped with the reason, the segments made, the seconds of the clip and the seconds kept, and for each clip left out or marked for review the reason (A7). A clip that is left out also has its original, its preview copy and its frame count, so the owner can add it back by hand. The drift is not in the assembly report; `analyse --drift` writes a report of its own (7.3).

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

**As built (changed in the build).** The tab is the fourth, **Film**. It has the buttons Assemble the film (with a question first when a timeline file exists, then `replace` is sent) and Render preview, a badge (no preview, preview is up to date, preview is out of date), the player (the file is served by `GET /api/preview/<file>` with range requests, so the player can seek), and a table with one row per segment: number, label (a text field), film time, length, start and end (frame number, source time and the buttons -1 s, -1 f, +1 f, +1 s), a checkbox that removes and restores the segment, and the buttons up and down. A click on a row jumps the player to the segment; the row of the segment that plays is marked. A refusal of the server (422, 409, 400) is shown as text with the field path, and the table goes back to the saved values. **A change does not start a preview job by itself**: the badge says the preview is out of date and the owner presses Render preview (changed in the build; the plan started the job by itself). The page script is checked by running it in Node against a real server with a stub for the page (`tests/ui_harness.js`, `tests/test_server_timeline.py`); **the page was not opened in a browser** while it was built.

**Undo button.** The plan does not ask for one on the page: section 4.4 has no undo operation, and undo is the command `timeline undo` (4.3). So the Film tab has none.

**Sync offset per clip (added after the owner's answer "Leave them, I set them").** A second card under the scenes, **Sync offset per clip**, has one row per clip of the file: the clip's name, its number of scenes, the offset from matching (always shown), a field for the owner's offset in seconds (a minus sign and a decimal comma are allowed; the field shows the measured offset as its hint when empty, and a gold frame when the owner has set one), the offset the sound uses (marked "yours" for a typed one), a button **Original** that removes the typed offset, and the "check" flag of the assembly report. Typing a value sends `set_offset`; emptying the field or pressing Original sends `reset_offset`. A value that is not a number is refused in the page; a value out of range is refused by the server (422, rule V23) with the field path; a WAV with no sound, or only part of the sound, for a scene at the typed offset is a warning shown above the table (rule V23). After a change the note under the player says what the next preview has to do (`pending`, 5.5): for an offset change "all pictures kept; sound of N scenes to render again". The measured offset in the file is never changed by the page. SPECS 4.2 and 4.4 list the operations.

### 7.7 Other changes in step 2

- `.gitignore` gets `timeline.json`, `short-*.json` and `timeline.history/`.
- `CLAUDE.md` of the repository gets the section of section 4.3. (Not done in the build.)
- `config.py` gets the settings of this section and of section 5.
- `matcher._extract_audio_raw` gets the optional start time (`start`, `-ss` before the input). Nothing else in `matcher.py` changes.

### 7.8 Checks for step 2

Synthetic fixtures are made inside the tests with `ffmpeg` and `numpy`, so no media file is stored in the repository. Tests that need `ffmpeg-full` are skipped when it is missing.

| Check | Command | Passes when |
|-------|---------|-------------|
| 2.1 | `python3 -m unittest tests.test_timeline_format -v` | The example of section 3.3 parses and validates with file checks off. It saves and loads to the same bytes. The derived values equal: film length 2542 frames, segment film starts 0, 862 and 1922. |
| 2.2 | `python3 -m unittest tests.test_timeline_rules -v` | For each rule V1 to V22 there is a broken copy of the example. The validator reports that rule at the expected field path, and the valid example reports nothing. |
| 2.3 | `python3 -m unittest tests.test_timeline_store -v` | A save makes a snapshot. `undo` restores the previous one. A validate after a direct edit makes a snapshot. A stale hash is refused. A write interrupted halfway leaves the old file whole. **Built:** the snapshots (numbered, the newest 200 kept), the stale hash and the interrupted write, `undo` (`tests/test_timeline_history.py`: byte for byte, twice, not a toggle, the replaced version kept, a failed write changes nothing), the snapshot after a validate, `history` and the one-line change. |
| 2.4 | `python3 -m unittest tests.test_clap -v` | On tracks made with `make_track` of `tests/test_matcher.py` (claps at known times, with and without loud ambience, and with a wind-like low-frequency step): the clap time is found within 5 ms; the step is not taken as a clap; a track with no clap gives `none`; a disagreeing pair gives `single`. |
| 2.5 | `python3 -m unittest tests.test_drift -v` | A WAV made by resampling a track by +50 ppm gives a measured drift within 15 ppm of 50. A 100-second clip is skipped. **As built:** the recorder's events are placed at `t * (1 + ppm * 1e-6) + offset` (the same as a resampled track for the method, which looks at claps); +50, -50, 0 and +200 ppm are measured (the last is over the limit); the limit is half a frame of the clip's frame rate; a clip of 120 s is measured, one of 100 s skipped; a last minute with nothing in common is `not_measurable`; an extraction that fails is `error`; the windows are the last 60 s and the recorder window follows the offset; `_extract_audio_raw` with a start; over a project the files, the counts, the clips over the limit, `--limit`, `--only`, the frame rate from `conversion.json`, the originals untouched, the command line and its report names. |
| 2.6 | `python3 -m unittest tests.test_stable tests.test_stable_video -v` | On the synthetic clip of section 7.4 (still 0 to 4 s, moving 4 to 6 s, still 6 to 10 s) the runs lie inside 0 to 4 s and 6 to 10 s, each run's edge next to the moving part is between 0.1 s and 0.8 s away from it (the guard of 0.3 s plus the 0.2 s sample step), and the moving part is outside every run. On the clip with a moving block over a static texture, no movement is found. A clip that moves throughout gives no run. **As built:** the tests listed in 7.4, with people moving in a still shot, a zoom, a bump, a cut, a slow creep and a flat picture as well; real clips in `tests/test_steady_real.py`. |
| 2.7 | `python3 -m unittest tests.test_assemble tests.test_assemble_project -v` | With analysis results given as data: clips in recording order; a clip with two runs gives two segments; the clap cut raises the first segment's `in`; a clip with no run is left out and reported; an existing timeline is not overwritten without `--replace`; the assembled file validates. **As built:** also the order from the project's selection, the film fields, the fades shortened when they do not fit, the report, the originals and `conversion.json` unchanged, and the command line. |
| 2.8 | `python3 -m unittest tests.test_audio_place -v` | The cases of `PlanTests` in `tests/test_resolve_integration.py` (back-to-back, trims, positive offset, negative offset, clamped length), restated for the audio span function, give the same sample positions. A crossfade of two uncorrelated noise segments keeps the power within 1 dB at the join (`equal_power`; for `linear` the same holds for two parts of the same sound, see 5.4: changed in the build). A shortened handle shortens the crossfade and is reported. The first and last 10 ms of the film are silent when the fades are on. |
| 2.9 | `python3 -m unittest tests.test_render_plan -v` | The placements of the table in section 5.2 come out: no bars for a 4096 x 2160 source in a 4096 x 2160 frame, bars of 220 pixels for a 4096 x 1720 source, and the windows 1216 x 2160 and 968 x 1720 with the positions of section 5.2 for a 1080 x 1920 `fill` frame. The film start frames and sample positions follow section 3.4, with no drift over 150 segments at 24000/1001 frames per second. |
| 2.10 | `python3 -m unittest tests.test_sync_marker -v` | A fixture clip has a white frame at frame 100 and a WAV with a click at `100/fps + offset`. After building a timeline for the fixture and rendering it, the first bright frame and the click in the output lie within 1 ms of each other. |
| 2.11 | `python3 -m unittest tests.test_seek_exact -v` | On a synthetic H.264 file made like a preview copy (`-g 30`, each frame a different grey value) and, when `VET_WORK_DIR` has a preview copy, on a real one: the frames the film preview reads for `in` and `out` equal the frames selected by number. With `VET_REAL_BRAW` set: the decoder process of section 5.3 returns for a segment exactly the frames `in` to `out - 1` (the first and last frame equal the frames the decoder gives when it reads the whole clip). **As built:** the synthetic part with and without B-frames (`tests/test_seek_exact.py`), and the real part in `tests/test_steady_real.py` (needs `VET_REAL_PROJECT` and `VET_WORK_DIR`): a 24-frame segment of a real preview copy is compared with the copy's own frames, each frame matches its own number better than its neighbours. **Not built:** the part with the decoder process, which belongs to the final render (step 3). |
| 2.12 | `python3 -m unittest tests.test_preview tests.test_preview_cli -v` | A second preview of an unchanged film renders no segment. A trim of one segment renders exactly that segment. The joined preview has as many frames as the film. **As built:** also the duration within one frame of the timeline, the sound as long as the picture within one frame, the picture and sound fades at the start and the end (measured), the letterbox bars, the frame of every film frame, the range, the refusals, and the default encoder `h264_videotoolbox`. |
| 2.13 | `python3 -m unittest tests.test_server_timeline -v` | A server on an ephemeral port answers: `GET /api/timeline` with the hash; an edit with the right hash is saved; a stale hash gives 409; an invalid result gives 422 with the field path; a request with a wrong Host gives 403; a preview request with a path outside the work directory gives 404; a range request returns the bytes asked for. **As built:** every endpoint of section 12 that is built (`tests/test_server_timeline.py`, 49 tests), also the two jobs and the job slot, and the page's script run in Node against the server. |
| 2.14 | `python3 main.py analyse --work "$VET_WORK_DIR" && python3 main.py assemble && python3 main.py timeline validate` | On the first shoot: exit 0, a report with a clap result for every clip, drift for the longest clips, and the runs. The tool prints the times each stage took. **The owner** listens to a sample of claps, looks at the contact sheets of the clips the report marks, and watches the preview. The owner sets the starting values from what they saw. **As built, first 10 clips** [measured, 2026-10-26]: `analyse --scenes`, `assemble --only <10 clips>`, `timeline validate` (exit 0, no findings) and `preview` ran; the report is `scenes-first10.txt` in the work folder (15 scenes, 392.4 s, no clip left out), the preview film is `previews/preview-first10.mp4` (11760 frames, 392.392 s, `ffprobe` duration equal to the timeline), the stills are in `reports/stills/`. **Whole shoot (105 clips)** [measured, 2026-10-26, reports `scenes-all.txt`, `assembly-all.txt`, `clap-report-all.txt`, `drift-all.txt` in the work folder]: `analyse --clap`, `analyse --scenes`, `assemble`, `timeline validate` (file checks on, exit 0, no findings) and `preview` ran; 171 scenes from 101 clips, 105337 frames, 3514.745 s (58:34.7); 4 clips left out (C053, C091, C110, C112); `previews/preview-all.mp4` (105337 frames; `ffprobe` duration 3514.7446 s, equal to the timeline). After the offset field, undo, history and the drift were built, `assemble --replace` gave the same timeline file byte for byte, `analyse --drift` found no drift over the limit (7.3), and the preview was made again with all 171 segments reused (57.4 s). The owner has not yet listened, looked or watched. |
| 2.15 | `python3 -m unittest discover -s tests -v` | All earlier tests still pass. |
| 2.16 | `python3 -m unittest tests.test_offset_hand tests.test_server_timeline tests.test_assemble_project -v` | **The offset typed by hand (added in the build).** The sound starts at the hand offset when there is one and at the offset from matching otherwise, for every segment of the clip and for no other clip; a negative offset puts silence before the sound, an offset past the end of the WAV makes the segment silent, both with a warning and no crash. In a synthetic film with a wrong stored offset, the click in the mix lies within 1 ms of the white frame once the right offset is typed. Rule V23: values from -30 to 30 s (negative ones and 0 included) pass; 30.001, -30.001, NaN and infinity are errors at `sources.<id>.audio_offset_hand_s`; a string, a boolean or null is a V0 error; the warnings for a WAV without sound or with part of the sound, for a WAV whose length cannot be read, and none for clips without a hand offset. The operations `set_offset` and `reset_offset`: stored beside the unchanged `audio_offset_s`, refused for an unknown clip or a value that is not a number, the page can set no other field. The endpoint: 200 with the new hash and the clip rows, 422 for a value out of range, 409 for a stale hash, a snapshot of the old file, `project.json` untouched, warnings in the answer. After a change `pending.sound` lists the segments of that clip only and the second preview renders no picture again. `assemble --replace` keeps the typed offsets and names the kept and the dropped ones. The page script, run in Node, sets, resets and refuses offsets. |
| 2.17 | `python3 -m unittest tests.test_timeline_history tests.test_timeline_commands -v` | **Undo, history and frames (added in the build).** `undo` restores the previous version byte for byte, twice goes two versions back, never toggles, skips a snapshot equal to the file, keeps the replaced version in `undone/`, refuses a stale hash and leaves the file and the history whole when the write fails. The workflow of 4.3 (edit by hand, validate, undo) returns to the version before the edit. `history` lists the versions with the time, the hash and a one-line change; the lines are checked for trims, removal, order, labels, added and taken-out segments, offsets set by hand and other parts. `timeline validate` snapshots a valid file once and prints the number on standard error. `timeline frames` writes stills whose grey value is the value of the frame of the clip that the film time or source time asks for (synthetic clips whose frames differ by their grey value), shows the black bars of a letterboxed clip, ignores removed segments, refuses a time past the end, an unknown clip and wrong options. |

---

## 8. Step 3: Look and Sound

### 8.1 Purpose and outputs

Apply the look (the default base LUT, and a creative LUT if the owner wants one), level the loudness, export the DCI 4K film from the `.braw` files, make the ProRes master if the owner ticks it, and write the Shotcut project.

Outputs: `stills/`, `look` in `timeline.json`, `renders/film-4k.mp4`, `renders/film-master.mov` (only if ticked), `reports/render-report.json`, `film.mlt`, `audio-synced/`.

Status: partly built on the branch `editor-step2`. Built: the base and creative LUT in the final render and the film preview (8.2, 8.3, `look`), the loudness (8.4, `loudness`), the final film and the exports (8.5, `render`, `export`). The Shotcut project and its read-back (8.6) are built. Not built: the comparison stills of 8.2 and the page's part (8.7), unless a line there says so. The audio form of D11 is still open. D9 and D10 are settled. The final render is the one place where the decoder runs at full size, so this step also settles the speed of a whole film (check 3.8).

### 8.2 The base LUT and choosing a creative LUT from stills (L1)

**The base LUT is settled.** The owner chose "Blackmagic Gen 5 Film to Video" (panel d of the step-1 comparison, section 6.1). `python3 main.py look set --base <file>` writes `look.base` with the file's hash. The default file is in Resolve's LUT folder: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/Blackmagic Design/Blackmagic Gen 5 Film to Video.cube` [measured: the file exists]. The file is read from there and never copied into the repository.

**A creative LUT is optional.** `python3 main.py look stills --clips 6 --luts "$VET_LUT_DIR"` makes the comparison for it.

1. **Candidates.** Every `.cube` file in the folder named by `VET_LUT_DIR`, plus one entry "base only" (no creative LUT). The folder is on the owner's machine and is never committed. Where creative LUTs come from is settled by D10 ("Research free packs"): see the research step below. The owner's pick of 2026-10-27 is **"slay Film + Portrait"**: the files `slay-04-film.cube` and `slay-05-portrait.cube`, installed with their `LICENSE` and a `CREDIT.txt` in a folder below the one that `VET_LUT_DIR` names.
2. **Clips.** Six clips spread evenly over the shoot, or the clips named with `--clips A002_..._C002,...`.
3. **Frame.** The middle frame of the clip's first steady run (step 2), or the frame at 10 s if the clip was not analysed.
4. **Picture.** The frame is read from the clip's preview copy, which already carries the base LUT. It is placed in the film frame (section 5.2), scaled to 640 pixels wide, and passed through the candidate LUT. This is the chain of the film preview, and the same LUT chain as the final render.
5. **Sheets.** One sheet per clip: a grid of 4 x 3 tiles, "base only" first, each tile labelled with the LUT's file name; more LUTs make more pages. One sheet per LUT: the same LUT on the six clips, in a grid of 3 x 2. Sheets are PNG files in `stills/lut-by-clip/` and `stills/lut-by-lut/`. They are composed with `xstack` and `drawtext` of `ffmpeg-full`.
6. The owner (or Claude Code, on request) looks at the sheets. `python3 main.py look set --lut <file>` copies the file to `<work_dir>/luts/<sha256>.cube`, writes `look.lut` with its hash and `interp: tetrahedral`, and validates the result.

**The research step (D10, settled 2026-10-26: "Research free packs"; not built).** Before the first `look stills` run, a research step finds free LUT packs and reads the licence of each. It offers only packs whose licence allows use in the owner's videos, and it records for each pack its name, where it comes from, its licence and what the licence allows. It makes the stills of this section from the owner's footage with each look, using LUT files that are downloaded for the stills only. The owner looks at the stills and picks which packs to install; only those go into the folder named by `VET_LUT_DIR`. No pack is bundled with the tool or committed to the repository.

**The pick (D10, 2026-10-27): "slay Film + Portrait".** The pack is github.com/slaycamera/luts, licence CC BY 4.0, 33-point `.cube` files. The licence asks for the credit line `LUTs: [slay](https://www.slaycamera.com/?utm_source=github&utm_medium=repo&utm_campaign=luts) (CC BY 4.0)` when work made with the LUTs is published. The two files, the `LICENSE` and a `CREDIT.txt` that holds this line are copied (not committed) to `luts/slay/` in the work area; `VET_LUT_DIR` names that folder. The base look stays Gen 5 Film to Video and the creative LUT goes after it, on Rec.709. There is no creative LUT unless the owner chooses one (`look.lut` is absent). The choice is per film, not per segment: one LUT for the whole film is a decision of `PRD.md` section 12. `look set --lut FILE` reads a `CREDIT.txt` that lies next to the file and stores the line in `look.lut.credit`; when a film with a `look.lut.credit` is exported, the export writes the line into `<film>.credit.txt` next to the film.

A creative LUT must expect Rec.709 input, not log, and goes after the base LUT [inference]. The tool cannot tell from a `.cube` file which input it expects; the stills show it.

**Changing the base LUT later.** The preview copies carry the old base LUT. `convert` sees the changed hash and makes all copies again (about 39 minutes for the first shoot, section 6.1). Until then the film preview shows the old look and rule V16 gives a warning; the final render is correct, because it applies the base LUT itself.

**As built** (`look.py`, `main.py look`). `look list [--luts DIR]` prints the `.cube` files below `VET_LUT_DIR` (or `--luts`), down to three folders, by id (the path relative to that folder), with the size of the cube, its title and its credit. `look set [--file F] [--work DIR] [--luts DIR] (--lut ID_OR_FILE | --no-lut | --base FILE)` changes the timeline file through the validator, with a snapshot of the old file. `--lut` takes a file or an id from `look list`; the file is checked first (`look.check_cube`: a 3D cube with the default domain 0 to 1 and exactly `LUT_3D_SIZE` cubed entries, otherwise a reason and nothing is written), copied once to `<work>/luts/<sha256>.cube`, and `look.lut` is written with that path, its hash, `interp: tetrahedral`, `name` and, if a `CREDIT.txt` lies next to the cube, `credit`. A work folder inside a folder of originals is refused. The ids that the page may send (8.7) can name only files inside the LUT folder: `..`, an absolute path or a link out gives an error. The files of the owner's pick are installed in `/Volumes/Untitled/video-edit-tool-work/luts/slay/` (`slay-04-film.cube`, `slay-05-portrait.cube`, `LICENSE`, `CREDIT.txt`); `VET_LUT_DIR` is set to that folder when the page or `look list` is started. The comparison stills (`look stills`, L1) are not built: `xstack` and `drawtext` need `ffmpeg-full`, which is not installed.

### 8.3 Applying the look (L2, L3)

- **Final render.** The filter chain of section 5.3 applies, in this order: the base LUT, the creative LUT, the placement (section 5.2), the conversion to video levels. The base LUT acts on the RAW decode.
- **Film preview.** The segments are cut from the preview copies, which carry the base LUT. The chain applies only the creative LUT.
- **Shotcut project.** It uses the preview copies and so applies only the creative LUT (section 8.6).
- Filter: `lut3d=file=<path>:interp=tetrahedral`. Tetrahedral is also ffmpeg's default [measured].
- The path of a creative LUT is the hash-named copy in `<work_dir>/luts/`, so it has no spaces or special characters that filter strings treat as syntax. The base LUT's path contains spaces ("Application Support"); in the tests of step 1 it worked inside single quotes in the filter string (`lut3d=file='...'`) [measured]. The timeline file may point to the LUT anywhere.
- Speed: `lut3d` ran at about 47 frames per second at 4096 x 2160 and about 355 at 1920 x 1080 on this Mac, on synthetic video, with a 33-point cube [measured, research]. In the tested render chain, decode, `lut3d` and colour conversion together ran at 17.6 to 19.2 fps at 4096 x 2160 (section 6.1 E).
- `look.base.mode` `none` skips the base LUT.
- Open: whether the 16-bit input keeps its precision through the LUT, and which matrix is used for the conversion to video levels (`bt709` is set explicitly in the tested chain) [unverified]. Check 3.1.

The Blackmagic `.cube` files are used from the local Resolve folder. Their redistribution terms were not found [unverified], so they are never copied into the repository. The copy in `<work_dir>/luts/` stays on the owner's drive.

**As built** (`export.segment_filter`, `render.segment_filter`). The final render and the film preview share the order of the LUTs: in the final render `lut3d` of the base LUT, `lut3d` of the creative LUT, the placement, the conversion to video levels (5.3); in the film preview only the creative LUT, on the preview copy, which carries the base LUT. Tests (checks 3.1 and 3.2) run 16-bit RGB frames through the real final chain and read the luma and chroma that come out: an identity cube differs from the chain without a cube by at most 1 code value; an inverting cube gives the inverse within 2; a cube with curves matches a tetrahedral interpolation in numpy within 2 on colour bars and greys; the base LUT acts before the creative LUT (a halving base and an inverting creative LUT give 255 - v/2, not (255 - v)/2); the bars of a wide clip stay at Y = 16 even when the LUT inverts; a path with spaces, quotes and colons works. The 33-point slay cubes were also run through the whole export in the real-data run (below).

### 8.4 Loudness (L4, L5)

`loudness.py` has two functions.

- `measure(samples, rate)` pipes 32-bit float stereo into `ffmpeg -f f32le -ar 48000 -ac 2 -i - -af ebur128=peak=true -f null -`, reads the summary at the end of the log, and returns integrated loudness (LUFS), loudness range (LU) and true peak (dBTP). Integrated loudness uses the standard gates: -70 LUFS absolute and 10 LU relative [measured: the filter exists and offers `peak=true`; the gate values are the EBU R128 definition].
- `level(...)` is the procedure of section 5.4, steps 5 to 7.

Speed: the first pass of `loudnorm` took 15.5 s for 20 minutes of synthetic stereo, about 77 times faster than real time [measured, research]. The speed of `ebur128` was not measured.

The method is a measured fixed gain followed by a limiter. `loudnorm` in dynamic mode is not used, because it can change the loudness over time in material with a wide range [inference, research B5]. `pyloudnorm` would do the same measurement in Python; it would be a new package (D3).

The target is `audio.loudness.target_lufs` of the timeline file. The owner's decision D9 ("Each scene, then film") is mode `segment_then_film` with a target of -14 LUFS integrated and a true peak of at most -1 dBTP (the numbers shown to the owner). The code has no built-in default; step 3 writes the values into the file.

**As built** (`loudness.py`, `render.build_mix`, `main.py loudness`). `loudness set --mode off|segment_then_film|film_only [--target T] [--peak P] [--max-gain G] [--file F]` writes `audio.loudness` into the timeline file through the validator; for the two levelling modes the values of D9 (`config.LOUDNESS_*`: -14 LUFS, -1 dBTP, 12 dB) fill what is not given. `loudness show` prints the block. The code has no built-in mode: a file without the block, or with `off`, is not levelled (a timeline file that `assemble` wrote still says `off`). The details of the method as built are under 5.4.

### 8.5 Export (E2, E3, E6)

`python3 main.py render --export film-4k` renders the export named in `exports[]` (sections 5.3 and 5.6) from the `.braw` files. `render --all` renders every export that is enabled. The default entry `film-4k` is MP4, H.264, 4096 x 2160, about 45 Mbit/s, AAC. A custom resolution is an export entry with another `width` and `height` of the same shape as the film.

**The ProRes master.** The entry `film-master` (`.mov`, ProRes 422 HQ, 24-bit PCM sound) is written with `"enabled": false`. The tab Export shows a checkbox for it, unticked, with the estimated size (section 5.6) and the free space of the SSD. Ticking it sets `"enabled": true` (the owner can also edit the file or run `python3 main.py export enable film-master`). `render` checks the free space first and refuses to start if it is below 1.1 times the estimate. The master is its own pass over the `.braw` files.

The film preview and the final file use the same render plan.

**As built** (`export.py`, `main.py export` and `main.py render`).

- `export list` shows the exports of the timeline file with the state, size, codec, an estimate of the file's size and the last render. `export add --preset film-4k|film-4k-x264|film-master [--name N] [--width W --height H] [--output-dir D] [--replace]` adds an entry (the files go to `<work>/renders/` by default; a custom size must have the shape of the film frame, rule V5); `export enable NAME` and `export disable NAME` switch an entry. The presets: `film-4k` is MP4, H.264 High, the `h264_videotoolbox` encoder at 45 Mbit/s, AAC 384 kbit/s at 48 kHz; `film-4k-x264` is the same with `libx264`, preset `slow`, 45 Mbit/s; `film-master` is `.mov`, ProRes 422 HQ through `prores_videotoolbox`, 24-bit PCM sound, written with `enabled: false`.
- `render --export NAME` renders one export and `render --all` every export that is switched on. A switched-off export is refused with the command that switches it on (`--force` renders it anyway). Exit code 0 when every file passed its checks, 1 when one failed or was refused, 2 for wrong options or a missing timeline file, 130 after SIGTERM or Ctrl+C. `render --detach` starts the same command as a process of its own session, with the log `<work>/reports/render-<name>.log` and the pid file `<work>/reports/render-<name>.pid`, and prints how to watch, stop and restart it.
- When the timeline file's `look.lut` has a `credit`, the render writes the credit line as plain text (a Markdown link `[text](url)` becomes `text (url)`) into `<film>.credit.txt` next to the film, for the YouTube description, and prints the path.
- The render report is `reports/render-report.json`: `renders`, a list of the newest 30, each with the export, the times, the timeline hash, the tools and the Python version, the export entry as rendered, frames, seconds, elapsed time per stage (`mix_s`, `audio_s`, `video_s`, `check_s`), the speed (`video_fps`, `realtime_factor`), the load average at the start and the end, the peak memory of the render process, the file size and data rate, the loudness (mode, target, the film before and after levelling as built by 5.4, and as measured on the finished file), the capped and the silent segments, the warnings (shortened crossfades among them), the problems and the checks.

### 8.6 The Shotcut project (E4)

**What it is for.** Rare hand work, done last. Decision D11, "Tool reads cuts back": after the hand work, `import-shotcut` reads the cut points and the order from the saved project back into the timeline file, and the 4K film is rendered from the `.braw` files. Effects made only in Shotcut do not come back. A new run of `export-shotcut` writes the file anew and does not merge.

**Method.** The project format of Shotcut is documented as unstable: it wants annotations (`shotcut=1` on the tractor, `shotcut:filter` on filters, `shotcut:name` on playlists) and says they may change [documented, Src6]. Developers of Kdenlive say the same of their format [documented, Src7]. So the writer works from a template:

1. At the start of step 3, the owner saves one small project in Shotcut by hand (two clips, a LUT filter, a fade, a subtitle filter). The version number of Shotcut is noted.
2. The writer takes that file's structure as its template and fills in the film. Prior art exists: a small project writer for Shotcut validates each edit with `melt` [documented, Src9; young, maturity unproven]. It records the Shotcut version in the output. The version listed as current in the research is 26.9.27 [documented, Src6]; the installed version is 26.9.28 [measured]. A different installed version gives a warning, not an error. The writer checks its output with `melt <film.mlt> -consumer xml` or a short render with `melt` (run with `QT_QPA_PLATFORM=offscreen`); `melt` 7.41.0 read the preview copies and rendered a 2 s piece of each in 1.7 s on 2026-10-26 [measured, section 6.1 D].

**Content** [all unverified until check 3.9]:

- A profile with the preview size (the film frame at the scale of the preview copies: 2048 x 1080 for the default frame) and the film's frame rate. The video entries are the preview copies, so Shotcut works at that size (D11).
- A video track with one entry per enabled segment, with the segment's `in` and `out` frames. MLT counts `out` as the last frame used, so `out_mlt = out - 1` [inference].
- An audio track with one entry per segment, from `audio-synced/<stem>.wav`, with the same `in` and `out` frames as the picture.
- Between neighbouring audio entries, a `mix` transition for the crossfade: constant power for `equal_power`, linear for `linear` [documented, Src5].
- A `volume` filter per audio entry with the gain that levelling computed at export time.
- Fades at the film's start and end: a `brightness` filter for the picture and a `volume` filter for the sound.
- The creative LUT, if there is one, as a LUT filter on the video track. The base LUT is not added: the preview copies carry it. Shotcut's LUT filter uses ffmpeg's `lut3d` [documented, secondary].
- The placement of section 5.2. For `fit`, a `brhq` preview copy (2048 x 860) sits in the 2048 x 1080 profile; MLT's resize step is expected to add the black bars [unverified until check 3.9]. For `fill`, a crop filter that cuts the window.
- Subtitles through the MLT `subtitle` filter, which reads SRT [documented, Src5]. Titles as a second SRT with its own style.

**Synced audio.** MLT places clips on whole frames. The offset between camera and recorder is a fraction of a frame (0.276 s is 8.3 frames at 29.97 frames per second). To keep the sound exact, the tool writes `audio-synced/<stem>.wav` for each clip: the original WAV, at its own sample rate and bit depth, shifted by the offset in whole samples so that its time 0 is the picture's time 0. If the offset is negative, silence is added at the start. The file ends where the picture ends. A segment's picture and sound then have the same frame numbers in Shotcut and stay aligned to the sample. (D11 asks whether the owner prefers one premixed audio track instead.)

**Known differences from the tool's own render.** Shotcut has no true-peak limiter step in this project and processes in 8 bit [inference]. The tool's render is the reference. A render from Shotcut will not equal the tool's render bit for bit. Shotcut cannot decode BRAW [measured], so it renders from the preview copies: a film exported from Shotcut is 2048 pixels wide, not 4K. That is why the cuts are read back and the 4K film is rendered by the tool (D11).

**Reading back (`import-shotcut`, D11)** [design; all unverified until check 3.9]:

- The writer puts the segment id into each video entry as a property (`vet:segment`), so the reader can tell which entry is which segment after the owner moved, trimmed or split it in Shotcut [inference: MLT keeps properties it does not know].
- The reader parses the saved project and takes, for the video track, the entries in playlist order. For each entry it reads the clip (matched to a source by the preview copy's path), `in` and `out`. MLT counts `out` as the last frame used, so the segment's `out` is `out_mlt + 1`.
- An entry with a known `vet:segment` id changes that segment's `in`, `out` and place in the order. An entry without an id (a clip split in Shotcut, or a clip added from a known preview copy) becomes a new segment with `origin: "manual"`. A segment whose id no longer appears gets `enabled: false` and stays in the file.
- Nothing else in the timeline file changes. Filters, transitions, volume changes, titles and audio edits made in Shotcut are not read. The command lists the Shotcut filters it saw and ignored.
- The result goes through the rules of section 4.1: validation, a snapshot before the write, and the hash check. Cut points that break a rule (V9, V11) are refused with the field path, and nothing is written.
- The preview copy and the `.braw` file have the same frame numbers (section 3.2), so the segments can be rendered from the `.braw` files without further work.

**As built** (`export_shotcut.py`, `import_shotcut.py`; changed in the build where it says so). `export-shotcut [--file F] [--work DIR] [--out FILE] [--no-check]` writes `<work>/film.mlt` and the shifted sound in `<work>/audio-synced/<clip>.wav`; `import-shotcut [--file F] [--work DIR] [--mlt FILE] [--dry-run]` reads the saved project back. The owner has not yet saved a template project from Shotcut (step 1 of the method above), so the writer follows the format of MLT 7 as Shotcut writes it, from the MLT documentation and from `melt`'s own behaviour, not from a Shotcut file [inference]. It targets Shotcut 26.9.28 (`melt` 7.41.0) and warns when another version is installed.

- *The project.* The root `mlt` element names `main_bin` as Shotcut does. The profile is the film frame at the scale of the preview copies (2048 x 1080 for the default frame), the film's frame rate, BT.709. Tracks: 0 a black background as long as the film, 1 V1 (`hide="audio"`), 2 A1 and 3 A2 (`hide="video"`). V1 has one `entry` per enabled segment (`in` the segment's `in`, `out` its `out - 1`, because MLT's `out` is the last frame used) over its own `chain` of the preview copy, with the properties `vet:segment` (the segment id), `vet:source` (the clip id) and `shotcut:caption`. The properties are on the chain, not on the entry: MLT keeps a producer's properties when Shotcut saves it, and a clip split in Shotcut then has the same producer in both halves.
- *The sound (D11, still open: this is the plan's default).* One `chain` per segment of `audio-synced/<clip>.wav`, on A1 and A2 in turn, with the same frame numbers as the picture, so that the sound of two neighbours can overlap at a join. Each entry reaches half the join (in whole frames; 4 frames for 0.25 s at 29.97 fps) before and after its segment, as far as the clip has sound; there is a `blank` where a track is empty. A `volume` filter in each sound chain carries the segment's gain in dB: the levelling gain of 5.4 plus `gain_db_adjust` plus the film's one gain (the writer measures the film as `render.build_mix` does). **Changed in the build:** the plan names a `mix` transition for the crossfade. MLT's `mix` transition does offer a constant-power and a linear crossfade (`start` -2 and -1), but a transition between two tracks leaves the tracks' own always-on mix to count the second track again, so the crossfade is made with two `volume` filters on the two tracks over the overlap: one falls and one rises, in dB, with a key per frame following the amplitude curve of 5.4 (`equal_power` or `linear`). The film fade-in and fade-out of the sound are `volume` filters over the first and last frames, of the picture `brightness` filters. Both always-on mixes (`mix`, `always_active`, `sum`) and the video composite (`qtblend`) are tractor transitions.
- *Synced sound.* `sync_audio` writes the WAV at its own sample rate and sample format, shifted by the offset in whole samples (`atrim=start_sample` for a positive offset, `adelay=NS` for a negative one), padded or cut to the clip's length. The file is kept when the WAV, the offset and the length are the same. The clock drift (`audio_drift_ppm`) is not applied; a clip with a drift gets a warning.
- *The creative LUT* is an `avfilter.lut3d` filter on V1 (`av.file`, `av.interp tetrahedral`); the base LUT is not in the project. Whether the LUT acts before or after MLT's letterbox of a wide preview copy is [unverified]: if after, the black bars take the LUT's black (the slay Film cube lifts black to a dark blue).
- *Marks.* Every filter and transition the writer makes has the property `vet:own`; the reader lists the others as made in Shotcut.
- *Check with melt* (`check_with_melt`, also run by `export-shotcut` unless `--no-check`): `melt` reads a copy of the project (without the root `producer` attribute, which names Shotcut's empty bin, and with `avformat` in place of `avformat-novalidate`, so that a missing file is reported) and renders its first frames to nothing, with `QT_QPA_PLATFORM=offscreen`; an error line or a non-zero exit is a failure. Shotcut itself is not started.
- *Measured with melt on the first scenes of the first shoot* (52 s, 3 segments from 2 clips, 2048 x 1080): melt rendered the whole project in 13 s. Its sound lies on the tool's own mix to within one sample at 29.97 fps (MLT puts sound on frames of 1601.6 samples, the tool on samples) with a correlation of 0.99 or better inside the segments; the levels differ by 0.02 to 0.55 dB and the film's loudness is -13.6 LUFS against the tool's -14.0 (the limiter and the sine curves of the tool are not in the project); the true peak is +9 dBTP, because Shotcut has no limiter step here (known difference, below). Its picture has the frames of the segments, the fades, and 110-row black bars (Y = 16) on a wide preview copy: MLT's resize letterboxes by itself [measured]. The synthetic tests check the same in `tests/test_export_shotcut.py` (check 3.7).
- *Read back.* `import-shotcut` reads the video track (the first playlist that has `shotcut:video` or a `vet:segment` producer; further video tracks and other tracks are listed). Times may be frame numbers, a clock `HH:MM:SS.mmm` (as Shotcut writes) or a timecode `HH:MM:SS:FF`; an entry without `in` and `out` takes its producer's. A resource is matched to a clip by the preview copy's path (a relative path is read from the root attribute or the project's folder), else by its file name when that is unique; a clip that is not a preview copy of the film is refused. An entry whose producer's segment id was not used by an earlier entry changes that segment (`in`, `out + 1`, place in the order); every other entry is a new segment with `origin: "manual"`, with a `note` ("cut in Shotcut from s003", or "added in Shotcut"), and the label of the segment it was split from plus "(split)". A segment that no entry names gets `enabled: false` and keeps its place next to the segment that stood before it; a segment the tool had switched off stays as it is. Cut points that break a rule are refused with the rule and the field path, and nothing is written (`--dry-run` writes nothing). The report lists what was added in Shotcut and is not read: filters (named), transitions, gaps, nested transitions between clips, other tracks.
- *Known differences from the tool's own render* (as in the plan): no true-peak limiter and no film-level loudness measurement in Shotcut; the gains are those computed at export time; the fades follow dB keys, not the sine curves; frame-quantised joins (4 frames for 0.25 s); a render from Shotcut is preview-sized. A project that Shotcut saves again may drop or rename what it does not know [unverified]; the reader depends only on the producers' `resource` and `vet:*` properties, the entries' `in` and `out`, and the order.
- *Not done:* check 3.9, which is by hand in Shotcut itself (the owner may be working in it); the saving of a template project by the owner (step 1 of the method).

### 8.7 The page in step 3

A tab **Look** shows the stills sheets. A tab **Export** lists the entries of `exports[]`, a button to render each, the progress and the render report. The ProRes master has an unticked checkbox with the estimated size and the free space (section 8.5). Choosing a LUT is done by command, or by telling Claude Code.

**As built** (changed in the build): the page's part is two cards on the Film tab, not two tabs, and the Look tab with the stills sheets is not built (8.2). *Look and sound*: a list of the creative LUTs in the LUT folder (`VET_LUT_DIR`) with "none (the base look only)" first; the credit line of the chosen LUT; the loudness mode (off, each scene then the film, the film only) with the target, the true-peak limit and the largest gain; and a readout of the loudness the film has: the film preview's (integrated loudness, true peak, whether the target and the limit were reached, how many scenes are at the largest gain or silent, and a note when the film changed since) and the loudness measured on the file of the last export. *Export*: one row per entry of `exports[]` with its size, codec, an estimate of the file's size, whether it is on, the last render (ok or failed, speed, problems) and a Render button; a switched-off entry needs its box ticked (the ProRes master also asks for confirmation with the estimate and the free space); two buttons add and render the default film (MP4, H.264, DCI 4K) or the ProRes master; the free space of the work folder; the path of the credit file. The render shows its progress in the status line of the tab (percent, frames per second, times real time, minutes to go). A creative LUT is for the whole film: SPECS 3.2 has `look.lut` once, not per segment, and the owner's rule is one LUT per film (`PRD.md` section 12), so the page has no LUT per segment.

### 8.8 Checks for step 3

| Check | Command | Passes when |
|-------|---------|-------------|
| 3.1 | `python3 -m unittest tests.test_lut -v` (built) | The tests write a 17-point identity cube and an inverting cube. Through the real filter chain, applied to a 10-bit gradient and to colour bars (primaries and greys), the identity cube changes no 8-bit output value by more than 1, and the inverting cube gives `255 - input` within 2. A cube with a non-linear curve matches a `numpy` tetrahedral reference within 2. |
| 3.2 | `python3 -m unittest tests.test_lut tests.test_look -v` (built; the filter-string tests are in `test_lut`, the hash-named copy and the `look` commands in `test_look`) | The filter string for a LUT path with spaces (like "Application Support"), quotes and colons is valid. The hash-named copy is made once. `interp=tetrahedral` is in the string. The base LUT comes before the creative LUT. A changed LUT file fails rule V16. |
| 3.3 | `python3 -m unittest tests.test_stills -v` | With three synthetic preview copies and five cubes, the sheets exist, have the expected sizes, and carry the file names. Thirteen cubes give two pages. |
| 3.4 | `python3 -m unittest tests.test_levelling -v` (built) | Noise segments at three known levels, levelled to a test target, each measure within 0.5 LU of it. A segment too quiet for `max_gain_db` is capped and reported. A silent segment gets gain 0 and a warning. Mode `film_only` keeps the differences between segments. |
| 3.5 | `python3 -m unittest tests.test_film_loudness -v` (built) | The film's integrated loudness is within 0.5 LU of the target. The true peak is at or under the limit. Material with strong clicks goes through the limiter loop and ends under the limit, or the report says it could not. |
| 3.6 | `python3 -m unittest tests.test_export -v` (built; the synthetic film is 320 x 180, `VET_SLOW=1` does not add a 4096 x 2160 variant; the real clip is `tests.test_export_real`) | A synthetic film at 1920 x 1080 exports to an MP4 that `ffprobe` reads as H.264, 8-bit 4:2:0, the right size and frame count, AAC at 48 kHz, BT.709 tags, with the `moov` box before `mdat`. An export of another size of the same shape works. An export of another shape fails with rule V5. A film with a source of 1920 x 800 in a 1920 x 1080 frame has black bars (luma 16) above and below and the picture untouched. `VET_SLOW=1` adds the same test at 4096 x 2160. |
| 3.7 | `python3 -m unittest tests.test_export_shotcut -v` (built; adds melt reading the project and rendering it, compared with the tool's own picture and sound) | The XML is well formed. It has one video and one audio entry per enabled segment with the in and out frames of section 8.6, as many `mix` transitions as joins, one LUT filter for the creative LUT with the right file (and none for the base LUT), and the profile of section 8.6. Each `audio-synced` WAV is as long as its picture in samples, and a click in it lies where the original click plus the offset puts it. |
| 3.7b | `python3 -m unittest tests.test_import_shotcut -v` (built) | An MLT file written by the exporter and edited as text (one entry trimmed, one entry moved, one entry split, one entry removed, one filter added) is read back: the timeline file gets the new `in`, `out` and order, one new segment with `origin: "manual"`, one segment with `enabled: false`, and nothing else changed. The added filter is listed as ignored. Cut points beyond the clip's frames are refused with rule V9 and nothing is written. |
| 3.8 | `python3 main.py render --export film-4k`, once with `h264_videotoolbox` (the default) and once with `libx264` | On the first shoot: exit 0, the file reads as H.264 at 4096 x 2160 and about 45 Mbit/s and passes the checks of section 5.7, and the report shows the film's loudness and true peak. The speed (frames per second, real-time factor) and the load average are recorded for each encoder and compared with section 6.1 E (0.46 to 0.51 times real time for a single segment with `h264_videotoolbox`). **The owner** watches both films, a smooth-gradient scene for banding, and compares the colour with the chosen stills in QuickTime Player and in the browser the owner uses for YouTube. **The owner** confirms that the quality of the default encoder is enough, or sets `libx264` in the export entry. |
| 3.9 | `python3 main.py export-shotcut`, then open `film.mlt` in Shotcut (not done; `melt` has read and rendered the project, see 8.6) | **By hand.** The segments are in order. The first 10 seconds play. After a trim and a move made in Shotcut, `python3 main.py import-shotcut` shows the new cut points in `timeline summary`. The wide clips show their black bars. The creative LUT shows, if there is one, and the base look is not applied twice. The fades and crossfades are there. Subtitles show. The result and the Shotcut version are written to the render report's notes. Before that, `melt film.mlt -consumer xml` reads the file without errors. |
| 3.10 | `python3 -m unittest tests.test_export_master -v` (built) and, by hand, `python3 main.py render --export film-master` | The tests: with the entry `enabled: false` no file is made. With a fake free-space value below 1.1 times the estimate `render` refuses; above it, a synthetic 1920 x 1080 film exports to a `.mov` that `ffprobe` reads as `prores`, profile HQ, `yuv422p10le`, with 24-bit PCM sound. By hand, with the owner's tick: a 60-second piece of the first shoot renders, and its size and speed are recorded and compared with the estimate of section 5.6. |
| 3.11 | `python3 -m unittest discover -s tests -v` | All earlier tests still pass. |

---

## 9. Step 4: Words

### 9.0 Decisions of 2026-10-27

The owner's words, as recorded. The numbers and rules that follow from them are in 9.2 to 9.9.

| Topic | The owner's answer |
|-------|--------------------|
| D12, Whisper model | "large-v3-turbo". Run with whisper.cpp. |
| D13, subtitle form | "This should be configurable actually." So the form is a setting of each export (`srt`, `burned`, `both`). Starting values, marked as such: the main film `srt`, a vertical short `burned`. |
| D13, font | "Inter". |
| Subtitle language | "English" (settled earlier, asked again). |
| How subtitles are written | "Maybe it's best to just have a text input where I use my local Superwhisper model and then just fill a text box with text. And then in the background we will split this text up into subtitles that we then spread across the video wherever it fits. And then I as a user can also move these sections myself." Follow-up: "One box per scene". Placement: "Across the whole scene". Suggestion for the text: "Local cleanup rules". Language: "English". |
| Translations | "Would be good to also translate the subtitles into Portuguese, Chinese, Thai and German. To have them as exportable files that I can then use for the YouTube subtitles and so on." Then: "also japanese and russian". Translator: "openrouter cheap model". Portuguese: "Brazilian (pt-BR)". Chinese: "Simplified (zh-Hans)". |

**Whisper, measured by hand on 2026-10-27** before the tool existed. `whisper-cli` is in `/Applications/Shotcut.app/Contents/MacOS/` (whisper.cpp 1.8.3 with ggml 0.9.5, a universal binary with Metal; it was started as a command-line program, Shotcut was not started). The model `ggml-large-v3-turbo.bin` (1,624,555,275 bytes) was downloaded from `huggingface.co/ggerganov/whisper.cpp` to `/Volumes/Untitled/video-edit-tool-work/models/`; its sha256 `1fc70f774d38eb169993ac391eea357ef47c88757ef72ee5943879b7e8e2bc69` equals the value that the repository's file listing publishes. Timing test on the first 120 s of the film's sound (the mix of the first four scenes of `timeline-full.json`, mono 16 kHz, loudness -35 LUFS): the program reported 21.5 s in total, of which 1.3 s was loading the model and 12.2 s the encoder (four 30 s windows, 3.0 s each), on an Apple M4 with 4 threads and the Metal backend, a load average of about 5 [measured]. That is 5.6 times real time including the load. The text was `*Pain*` in every window: there is no speech in these two minutes, and the model wrote a stock phrase for the ambient sound [measured]. A clip with more going on (`A002_01040645_C063`, 271 s) gave "Good morning." for the first 5 s and then "Thank you.", "I'll see you next time.", "We'll be right back." and "*Clapping*", one phrase per 30 s window [measured]; whether anyone speaks in those windows was not checked by ear [unverified]. Whisper models write stock phrases for sound without speech [documented, widely reported; here measured twice], so section 9.2 filters its output and 9.1 makes the scene text box the main way.

**Spoken language.** The language detection of the model (`-dl`, the first 30 s and the 30 s from 60 s of 14 of the louder clips) gave English with a probability of 0.86 to 0.96 where the clip has speech-like sound (`A002_01040645_C063`, `A002_01040651_C064` at 60 s), English with 0.27 to 0.58 for five more, and French, Japanese or Korean with 0.26 to 0.65 for six clips [measured]. The low values come with ambient sound; no clip gave a confident answer other than English. The model is not trained for translation, so a clip in another language would get wrong English text, not a translation [documented, the model card of Whisper large-v3-turbo]. Nothing was changed because of this; section 18 lists it as an open issue for the owner.

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
| `timeline frames --film-s T1,T2` or `--source STEM --at T1,T2` | Write PNG stills at the given film times or source times and print their paths. Built (4.3 step 5). |
| `timeline cut --from-film A --to-film B` | Remove the film time range: segments wholly inside are disabled, a segment that contains the range is split into two with new ids, segments that overlap an edge are shortened. |
| `timeline keep --from-film A --to-film B` | The opposite: disable everything outside the range. |
| `timeline history`, `timeline undo` | List snapshots; restore the previous one. Built (4.3 step 6). |
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

A new timeline file with `kind: short`, `film.frame` 1080 x 1920, `film.fit` `fill`, `derived_from` (the film file, the length, the settings), the same `sources`, `look` and `audio` as the film, and segments copied from the film's enabled segments with new ids. Cues and titles are copied with their anchors, and those outside a shortened segment drop out. One export entry is added: MP4, 1080 x 1920, H.264 with the encoder of the film's delivery export, the starting values of section 5.6 (8 Mbit/s). A custom resolution is another export entry.

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

The window is 1216 x 2160 pixels for a `brlt` clip (reduced to 1080 x 1920) and 968 x 1720 pixels for a `brhq` clip (enlarged by 12 percent to 1080 x 1920) (section 5.2). `crop.x` places it in each segment. The default is `SHORT_CROP_X` (0.5, the centre; a technical default, not a decision). The owner or Claude Code sets it per segment after looking at frames, or gives `x_from` and `x_to` for a slow pan. The vertical short is cut from the full-size picture decoded from the `.braw` file, which is the 4K source (decision).

Automatic subject tracking is not included. The tools the research found for it are young, need installs the owner has not allowed, and the older one is described by its successor as no longer supported [documented, research B9] (D16).

### 11.6 Subtitles for 9:16 (S4)

The frame is 1080 x 1920. Third-party guidance for short-video platforms says to keep text out of the top 15 percent (288 px), the bottom 35 percent (672 px), the left 4 percent (48 px) and the right 18 percent (192 px) [documented, secondary, Src14]. That leaves a box from x = 48 to 888 (840 px wide) and from y = 288 to 1248.

- Cues are re-wrapped to two lines of about 24 characters (starting value [inference]), and long cues are split with the time shared by characters (section 9.3).
- The size is found by fitting: the tool renders the longest cue as white text on black through libass, measures the bounding box of the lit pixels, and reduces the font size by 4 percent until the box lies inside the safe area (at most ten rounds). The same size is used for all cues of the short.
- Titles are scaled by the same factor. The `lower_third` template sits above y = 1248.

### 11.7 Export (S5)

MP4, H.264 and AAC as in section 5.6, at 1080 x 1920. A short that is longer than 180 seconds gets a warning (rule V22), because YouTube allows Shorts up to 3 minutes since 2024-10-15 [documented, secondary, Src14].

### 11.8 Checks for step 6

| Check | Command | Passes when |
|-------|---------|-------------|
| 6.1 | `python3 -m unittest tests.test_short_trim -v` | With given scores: the result length equals the target to the frame; kept segments are contiguous; the highest-scoring seconds are kept; a `must` segment is never shortened; `must` longer than the target is an error that names both lengths; `drop` segments are absent; segments below the minimum are removed; a film not longer than the target is unchanged; the order is unchanged. |
| 6.2 | `python3 -m unittest tests.test_short_crop -v` | For a 4096 x 2160 source the window is 1216 x 2160; `x` of 0, 0.5 and 1 give X of 0, 1440 and 2880. For a 4096 x 1720 source the window is 968 x 1720 and X is 0, 1564 and 3128. A moving window moves in a straight line. |
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
| `thumbnails.py` | **Changed.** | Reads the preview copy when one exists, so `.braw` clips get thumbnails. Also serves the stills and frame requests of the later steps. |
| `server.py` | **Extended.** | Keeps the Host and Origin checks, the one-job rule and the 127.0.0.1 binding. Adds the endpoints below and range requests for video. |
| `ui.html` | **Extended.** | Tabs Match and Clips (the old Select and Order, with the trim inputs removed, because trims move into the timeline file) stay. New tabs Film, Look and Export. The Resolve tab stays only if D4 says so. |
| `main.py` | **Extended.** | New subcommands: `tools check`, `convert`, `analyse`, `assemble`, `timeline` (`summary`, `validate`, `frames`, `cut`, `keep`, `history`, `undo`, `migrate`), `preview`, `look` (`stills`, `set`), `render`, `export-shotcut`, `import-shotcut`, `words`, `subtitles build`, `title add`, `index`, `find`, `short`. Built in step 3: `look` (`list`, `set`), `loudness` (`set`, `show`), `export` (`list`, `add`, `enable`, `disable`), `render`, `export-shotcut`, `import-shotcut`. |
| `config.py` | **Extended.** | The settings named in sections 7 to 11. Settings for open decisions have no default value. |
| `requirements.txt` | **Extended.** | `vapoursynth` and `vapoursynth-brawsource` (D3). |
| `README.md`, `CHANGELOG.md` | **Not changed by this plan.** | They still describe v1.1.0. Each step updates them when it is built. |
| Tests | **Kept and extended.** | All 60 tests of v1.1.0 stay and must pass after every step (the last check of each step). |

**New server endpoints**

| Method | Path | Effect |
|--------|------|--------|
| GET | `/api/timeline` | The timeline file's summary (`edits.summary`: totals and one row per segment with film times, length, source frames, label, review reasons, and one row per clip with its segments and its offsets: `offset_original_s`, `offset_hand_s`, `offset_s`), its hash, the validation findings (warnings of rule V23 included) and the state of the preview, with `pending`: the segments whose picture is not in the cache and the segments whose sound differs from the last preview. Built. **In step 3** the answer also holds `look` (the chosen creative LUT with its credit, the LUTs found in the LUT folder with their problems, the folder), `loudness` (the settings and the film preview's loudness report), `exports` (each entry with an estimate of the file's size and its last render) and `free_bytes`; the answers of the edit, look and loudness requests hold the same. |
| POST | `/api/timeline/edit` | Operations of section 4.4 with the starting hash. Answers 409, 422 or 400 as described there. Built. |
| GET | `/api/preview/<file>` | A file from `<work_dir>/previews/`, with HTTP range requests (`Accept-Ranges`, `206`; `416` for a range past the end). Any other path gives 404. Built. |
| GET | `/api/stills/<path>` | A file from `<work_dir>/stills/`, same rule. |
| POST | `/api/look` | **Built in step 3.** Body `{hash, lut}`: `lut` is the id of a `.cube` file in the LUT folder (`VET_LUT_DIR`, or what the server was started with), or null for none. The server checks the file, copies it to `<work_dir>/luts/<sha256>.cube` and writes `look.lut`. An id that leaves the folder, names no file or names a broken cube gives 400; a stale hash 409; a result that breaks a rule 422. Answers with the film as `GET /api/timeline` does. |
| POST | `/api/loudness` | **Built in step 3.** Body `{hash, mode, target_lufs?, true_peak_dbtp?, max_gain_db?}`; the values of decision D9 fill what the file does not have. 400 for an unknown mode or a value that is not a number, 409, 422 as above. |
| POST | `/api/convert`, `/api/assemble`, `/api/preview`, `/api/render` | Start the job. Share the job slot (409 if busy). Built: `/api/assemble` (body `{replace?, limit?, only?}`; 409 when the timeline file exists and `replace` is not true; 400 without matched clips or without a work folder), `/api/preview` (404 without a timeline file, 422 for an invalid one) and `/api/render` (body `{export: name}` or `{preset, name?, width?, height?}`, with `enable: true` to switch a switched-off export on; the preset adds the entry to the timeline file first; 400 for an unknown export or preset or without a work folder, 404 without a timeline file, 422 for an invalid file or a switched-off export, 409 when the slot is taken; the job runs `export.render_export` in the server's process, status step `rendering`, then `rendered` or `error`). Not built: `/api/convert`. The page cannot name a path in any of them. |
| GET | `/api/status` | As today. New `step` values: `converting`, `analysing`, `assembling`, `previewing`, `rendering`, `transcribing`, `indexing`. Built: `assembling` and `assembled`, `previewing` and `previewed`; the job ends at progress 100. |

The server is configured by `app --timeline FILE --work DIR --no-browser`; without them the timeline file is `timeline.json` next to the project file and the work folder is `work_dir` of the project file.

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
  | `VET_WHISPER_MODEL` | The Whisper model file (D12): `ggml-large-v3-turbo.bin`. |
  | `VET_WHISPER_BIN` | The `whisper-cli` program. Default: the one in Shotcut's app folder, else on `PATH`. |
  | `VET_WHISPER_VAD_MODEL` | Optional. A whisper.cpp voice-activity model (for example `ggml-silero-v5.1.2.bin`). Not downloaded by this build (section 9.2). |
  | `VET_FONT_DIR` | The folder with the Inter font files (D13). |
  | `VET_TRANSLATE_MODEL` | The OpenRouter model that translates the subtitles (section 9.9). |
  | `OPENROUTER_API_KEY` | The OpenRouter key. Read from the environment only; never written to any file. |
  | `VET_OPENROUTER_URL` | Optional. Replaces `https://openrouter.ai/api/v1` (the tests point it at a fake server on a local port). |
  | `VET_REAL_WHISPER` | `1` runs the test that starts the real `whisper-cli` (needs `VET_WHISPER_MODEL`). |
  | `VET_SLOW` | `1` adds the 4K variants of render tests. |

- A test whose requirement is missing is **skipped with its reason printed**. A skipped test is not a pass. A step is done when its checks marked real or by hand have been run and their output is saved under `<work_dir>/reports/`.
- A check is never weakened to make it pass. If a starting value is wrong, the value changes in `config.py` and the reason is written in the commit.

## 14. Error Handling in the New Parts

These cases are added to Appendix A.13.

| Situation | What the tool does |
|-----------|--------------------|
| `ffmpeg-full` missing | Commands that need it (render, words, stills) stop with the install hint `brew install ffmpeg-full`. Matching and conversion keep working with the existing ffmpeg. |
| VapourSynth or the BRAW plugin missing | `convert` and the final render stop with the install steps. Matching is not affected. |
| SSD not mounted or not writable | Every command that writes to the work directory stops before any work. |
| Not enough free space | `convert` stops and prints the estimate and the free space (C6). A ProRes master stops the same way (E6). |
| Conversion interrupted | A `.partial` file is deleted at the next start and that clip is converted again. There is no resume inside a clip. |
| A copy fails check C5 | The clip is marked `failed` with the reason. The run goes on with the next clip. The exit code is 1 at the end. |
| Preview copy missing for the film preview, the analysis or the Shotcut project | Stop, name the clip, suggest `convert`. |
| `.braw` original missing at the final render | Stop, name the clip. |
| A preview copy carries another base LUT than `look.base` | Warning (rule V16). The final render is not affected. Suggest `convert` to make the copies again. |
| Timeline invalid | `render` and `preview` stop and print the findings. |
| Timeline changes while a render runs | The render uses the file as it read it at the start and records its hash. The page shows that the film changed since the last render. |
| No clap, no steady run, unanalysed clip | Not an error. Written to the assembly report and flagged. |
| Audio does not cover a segment; crossfade shortened; gain capped; silent segment; limiter could not reach the limit | Warnings in the render report. The render finishes. |
| The owner's offset leaves a WAV without sound for a scene, or with sound for only part of it; the length of the WAV cannot be read | Warning V23 in the answer of the page and of `timeline validate`; the render makes the scene silent (or partly silent) and reports it. Not an error. |
| The owner's offset is not a number, or outside -30 to 30 s | Error V23 (or V0 for a value of another type) with the field path; the page answers 422 and saves nothing. |
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
| D1 | Storage. **Settled 2026-10-26: option A.** | No working copies. Preview copies (section 6.3); the final render reads the `.braw` files (section 5.3). `work_dir` is a required setting with no default. | 2 |
| D2 | Colour. **Settled 2026-10-26.** | Generation 5, Blackmagic Design Film as recorded. Default base LUT "Gen 5 Film to Video", baked into the preview copies and applied to the RAW decode in the render (section 6.5). | 2 |
| D3 | Python and packages. **Settled 2026-10-26: 3.12 and OpenCV.** | The whole tool runs on Python 3.12 in its own environment. `numpy`, `scipy`, `tqdm`, `vapoursynth`; `opencv-python` only when code uses it. | 2 |
| D4 | Resolve option stays or goes | Code stays and is not extended (section 12). | any |
| D5 | Frame shape of the 4K film. **Settled 2026-10-26: DCI 4K, bars for wide.** | `film.frame` 4096 x 2160, `film.fit` `fit` (section 5.2). | 2 |
| D6 | Clap position, guard, no-clap policy. **Settled 2026-10-26: "Only at the start".** | One clap at the start of each clip. Guard 0.5 s (starting value). A clip with no clap is kept from its first steady frame and listed in the report. | 2 |
| D7 | Meaning of "most stable scene"; shortest scene; no-run policy. **Settled 2026-10-26: "Every steady scene".** No-run policy, settled the same day: **"Leave it out, list it"**. | Every steady run of at least 2 s (starting value) becomes a segment; only the movement between them is dropped. A clip with no run is not put on the timeline and is named in the report; the owner can add it back by hand. | 2 |
| D8 | Picture between scenes; fade lengths. **Settled 2026-10-26: "Hard cut".** | Hard cut for the picture. Fade-in 1.0 s, fade-out 2.0 s, audio crossfade 0.5 s as starting values. | 2 |
| D9 | Loudness target; per segment or per film. **Settled 2026-10-26: "Each scene, then film".** | Mode `segment_then_film`: every segment is levelled to the same loudness, then the film to -14 LUFS integrated with a true peak of at most -1 dBTP (the values shown to the owner). `target_lufs` is still required when the mode is not `off`. | 3 |
| D10 | LUT collection and sources. **Default settled 2026-10-26: Gen 5 Film to Video. Creative LUT sources settled the same day: "Research free packs". Pick settled 2026-10-27: "slay Film + Portrait".** | The base LUT is the Resolve file (section 8.2). A research step finds free LUT packs whose licence allows use in the owner's videos and shows stills of the owner's footage with each look; the owner picks which to install, into the folder that `VET_LUT_DIR` names. Nothing is bundled. | 3 |
| D11 | Audio in the Shotcut project; Shotcut works at preview size. **Preview size settled 2026-10-26: "Tool reads cuts back".** The audio form is open. | One synced audio clip per segment (the plan; not decided). Shotcut uses the preview copies; the tool reads the cuts back and renders the 4K film (section 8.6). | 3 |
| D12 | Whisper model. **Settled 2026-10-27: "large-v3-turbo".** | whisper.cpp, `whisper-cli` from Shotcut's folder; the model file is named by `VET_WHISPER_MODEL` (section 9.2). | 4 |
| D13 | Subtitle form; font. **Settled 2026-10-27: form "This should be configurable actually."; font "Inter".** | A setting of each export: `srt`, `burned`, `both` (starting values: film `srt`, short `burned`). Inter, from `VET_FONT_DIR` (section 9.5). | 4 |
| D14 | Meaning of "cut out" in prompts; scene search method | "Cut out" is read as remove. `keep` exists. Search is by Claude Code reading sheets and the transcript. | 5 |
| D15 | Default short length | `--length` is required. | 6 |
| D16 | Crop and importance | Crop 0.5 (centre) unless set. Weights of section 11.3 as starting values. | 6 |
| D17 | Encoder of the default export. **Settled 2026-10-26: "Hardware (fast)".** | `h264_videotoolbox` at 45 Mbit/s is the default; `libx264` is selectable in the export entry. Check 3.8 renders both. | 3 |

## 17. Unverified Statements and What Settles Them

Every statement tagged [unverified] above, and a few [inference] ones that the design leans on, are listed here. None of them is recorded as a fact.

| Statement | Settled by |
|-----------|------------|
| The `vapoursynth-brawsource` options of section 6.2 that step 1 did not use (bit depth, white balance, ISO, exposure, sidecar handling). The options it used (`pipeline`, `scale`, `gamma`, `gamut`, the frame properties and `AudioSource`) are measured. | Checks 1.6, 1.7 |
| The frame rate, frame size and colour generation of all clips outside `Recordings_261003`, and the colour generation of the other clips inside it. (The frame rate and sizes of the 116 clips are measured; one clip from a later shoot is 4128 x 2176 at 25 frames per second.) | Check 1.9 (the probe of every clip) |
| What the two timed-metadata tracks of a clip hold. | Check 1.6 report |
| Whether two preview-copy conversions at once are faster than one. The sizes of the preview copies of the other 114 clips; they depend on the picture (section 6.1 C). | Checks 1.8, 1.9 |
| The v1.1 code and its 60 tests run on Python 3.12. | Check 1.10 (the first work of step 2) |
| The clap method and its starting values find the real claps. | Check 2.14 |
| Clock drift between camera and recorder over a long clip. | Check 2.14. Settled for the five clips of 120 s or more by `analyse --drift` (7.3): at most 1.9 ms against a limit of 16.7 ms. Not measured for the other 100 clips. |
| The offsets of -5.03 s, -1.70 s and the seven between 0.63 and 1.94 s in the first shoot are correct. | Check 2.14 (the coverage warnings of the render plan) |
| The steady-run settings separate camera movement from subject movement on real footage. | Check 2.14 |
| Reading the preview copies at 5 frames per second for the analysis is fast enough. | Check 2.14 |
| `-ss` before `-i` is frame-exact on a real preview copy, and the decoder process returns exactly the frames `in` to `out - 1`. | Check 2.11 |
| `lut3d` keeps its precision and uses the right matrix around the RGB conversion. (The tested chain sets the `bt709` matrix explicitly and ran on 16-bit input.) | Check 3.1 |
| The two-stage pipe design for many segments (section 5.3) keeps the speed of the tested single-process chain (13.9 to 15.2 fps at 4096 x 2160 with `h264_videotoolbox`), and the 8-bit result shows no banding. | Check 3.8 |
| `h264_videotoolbox` and `libx264` at 45 Mbit/s differ in quality; whether the default encoder's quality is enough. | Check 3.8 (D17) |
| Speed of the ProRes 422 HQ master from the RAW decode. | Check 3.10 |
| The Shotcut template, the annotations and the filters of section 8.6 open and play as described, and MLT letterboxes the wide preview copies. | Check 3.9 |
| The `whisper` filter's option names, voice-activity model and word times. Its speed. | Check 4.8 and the two-minute timing test |
| Claude Code finds scenes from sheets and a transcript. | Check 5.5 |
| All H2N files are stereo at 96 kHz (one was checked). | Check 1.9 (the probe of every WAV) |
| A file made with the "Film to Video" LUT and tagged BT.709 as in section 5.6 displays with the right brightness in QuickTime Player, the browser and YouTube. | Check 3.8 |

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

*Plan: changed to read the preview copy when one exists (section 12).*

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

*Plan: extended (sections 2.4 and 12). v1.1 states Python 3.9. The tool moves to Python 3.12 in its own environment (D3, settled 2026-10-26); the BRAW plugin needs 3.12 or newer [documented, Src1].*

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
| Src10 | Apple ProRes white paper: https://www.apple.com/euro/final-cut-pro/f/generic/docs/Apple_ProRes_White_Paper.pdf | Data rates per hour. The master size per frame (section 5.6) is arithmetic from them. |
| Src11 | YouTube recommended upload settings: https://support.google.com/youtube/answer/1722171 | Bitrates, audio settings, MP4 with `moov` first. |
| Src12 | YouTube loudness figure (secondary): https://opus.pro/blog/best-loudness-normalizers | About -14 LUFS and -1 dBTP. The official page above gives no number. |
| Src13 | FFmpeg `whisper` filter source: https://ffmpeg.org/doxygen/8.0/af__whisper_8c_source.html | The filter, its outputs and its voice-activity option. |
| Src14 | Shorts safe zones and length (secondary): https://www.clipspeed.ai/blog/youtube-shorts-size.html, https://blitzcutai.com/blog/youtube-shorts-length-limits-2026 | Safe area; 3 minutes since 2024-10-15. |
| Src15 | Claude vision documentation: https://platform.claude.com/docs/en/build-with-claude/vision | Token cost and size limits of images. |

Facts tagged [measured] were checked on 2026-10-26 on the owner's machine: the installed ffmpeg's filters and encoders, a read-only `ffprobe` of one clip and one WAV, the sizes and free space of the volumes, the owner's `project.json` (not committed), the v1.1.0 test run (60 tests, all passing), and two small scratch tests with synthetic video (frame-exact `-ss` on ProRes; phase correlation on a static texture). The scratch tests are not in the repository; the fixtures of section 7.8 recreate them.

The step-1 tests of section 6.1 were also run on 2026-10-26: the BRAW decoder on two real clips, the ProRes conversion, the preview copies, the Shotcut and `melt` check and the full-size render check. Their scripts, logs, preview copies and render files are on the SSD in `/Volumes/Untitled/video-edit-tool-work/` (`step1/` and `step2-preview/`). They are not in the repository and contain the owner's footage.
