# Changelog

## Unreleased (editor, step 2 in progress)

### Added (step 4, words)
- Decisions of 2026-10-27 (`PRD.md` section 3): Whisper model large-v3-turbo (D12), the subtitle form is a setting
  of each export, font Inter (D13), a text box per scene, one subtitle style, translations by OpenRouter.
- Subtitles in the timeline file: `segments[].text` (the words of a scene), `subtitles.cues[]` with `origin`
  (`auto`, `text`, `manual`) and `locked`, `exports[].subtitles` (`srt`, `burned`, `both`, `none`; film `srt`,
  short `burned` unless set), `titles[].size_pct`. `subtitles.mode` and `subtitles.style` are gone (V2 says
  where they went). New rules V24 (cues overlap, order, gap), V25 (the form), V26 (titles, scene text), and
  readability warnings in V19 (two lines, 42 characters, 1 to 7 s, 17 characters per second).
- `words.py`: the local cleanup of a scene's text, wrapping and splitting by the measured width, spreading the
  cues across a scene, cue and title operations at film time, SRT. A trim, a move or a cut read back from Shotcut
  keeps the cues with their picture.
- The one style (`config.TEXT_STYLE`, `config.TEXT_LAYOUTS`, `textlayout.py`): Inter SemiBold, safe area
  of EBU R 95 (5 percent), text inside the picture of a letterboxed scene, 9:16 values ready for step 6.
- Burned subtitles and titles in the film preview and the final film (`textlayer.py`): drawn with Pillow,
  laid over the picture with ffmpeg's `overlay` filter (the installed ffmpeg has no libass, freetype or
  drawtext). `<film>.en.srt` for the forms `srt` and `both`. `render --subtitles FORM` for one render.
- `subtitles auto` (whisper.cpp, `transcribe.py`): once per clip, kept in `analysis/<clip>/transcript.json`;
  stock phrases, loops, bracketed sound labels and phrases over sound at the noise floor are dropped and listed
  in `reports/subtitles-auto-report.json`; a second run does not duplicate.
- Commands: `subtitles` (`list`, `add`, `edit`, `remove`, `shift`, `sort`, `text`, `import`, `export`, `check`,
  `auto`, `translate`), `title` (`list`, `add`, `edit`, `remove`, `import`), `export set NAME --subtitles FORM`.
- `subtitles translate`: the cues into pt-BR, zh-Hans, th, de, ja and ru through an OpenRouter model
  (`VET_TRANSLATE_MODEL`, key from `OPENROUTER_API_KEY` only), batches with context, answers checked, cached by
  text and model, one SRT per language with the times of the English cues.
- The Film tab: a Subtitles card (a text box per scene, a strip of cues to drag and resize, numeric times, the
  form of each export, Whisper, the SRT file), a Translations card, a Titles card, and a caption the page draws
  over an out-of-date preview. Operations of the page go through `POST /api/timeline/edit`; new endpoints
  `POST /api/subtitles/auto`, `/api/subtitles/srt`, `/api/subtitles/translate`.

### Added (step 3, look and sound)
- Loudness (decision D9, "Each scene, then film"): `loudness set|show` writes the mode and
  numbers into the timeline file. In the film preview and the final film every scene is
  measured (EBU R128, ffmpeg `ebur128`) and levelled to the target, at most `max_gain_db`;
  the film then gets one gain and, if the true peak is over the limit, a look-ahead
  limiter. The report names the capped and silent scenes and the film's numbers before and
  after. The preview's record (`previews/preview.json`) carries it.
- Creative LUT (decision D10, "slay Film + Portrait"): `look list` and `look set --lut|--no-lut|--base`.
  The LUT is checked, copied to `<work>/luts/<sha256>.cube` and stored in `look.lut` with its
  name and the credit line of its pack. Applied after the base LUT, tetrahedral, in the
  preview and in the final film. `VET_LUT_DIR` names the folder of the owner's `.cube` files.
- The final film (`render`, `export` commands, `export.py`): decoded from the `.braw` files at
  full size, base LUT, creative LUT, letterbox bars added in RGB, BT.709 video levels, fades,
  H.264 (hardware encoder at 45 Mbit/s, or `libx264`) or ProRes 422 HQ, AAC or PCM sound,
  timecode, colour tags, `faststart`. Presets `film-4k`, `film-4k-x264`, `film-master` (off
  until switched on), custom sizes of the film's shape. Checks on the finished file; the
  report `reports/render-report.json`; a credit file next to a film that uses a credited LUT;
  `render --detach` for a background run with a log and a pid file; SIGTERM stops it clean.
- `Decoder.write_frame_range`.
- The Film tab: a *Look and sound* card (the creative LUT from the LUT folder with its credit,
  the loudness mode, a readout of the loudness the film has) and an *Export* card (the exports
  with size estimate and last render, a Render button, the default film and the ProRes master).
  Endpoints `POST /api/look`, `POST /api/loudness`, `POST /api/render`; `GET /api/timeline`
  also answers with `look`, `loudness`, `exports` and `free_bytes`.
- The Shotcut project and its read-back (decision D11): `export-shotcut` writes `film.mlt`
  (the preview copies on V1, the sound of each clip shifted by its offset in whole samples
  on two sound tracks, crossfades and fades as filters, the creative LUT as a filter) and
  checks it with Shotcut's own `melt`; `import-shotcut` reads the cut points and the order
  back after the hand work, lists what was added in Shotcut and is not read, and refuses cut
  points that break a rule of the timeline file.

### Added
- `convert` command: one small preview copy of every matched `.braw` clip
  (decoder: vapoursynth-brawsource; ffmpeg applies the base LUT and encodes),
  with a check of each copy, a resumable `conversion.json`, a free-space check
  and a guard that never writes into the folder of the originals.
- `Project.work_dir` field.
- `timeline.py` and `timeline validate`: the timeline file, version 1 (SPECS 3):
  load, validate (rules V0 to V22), atomic save, derived values.
- `analyse --clap`: finds the sync clap at the start of each clip from the
  camera audio and the WAV together, and writes a report with review flags.
- `analyse --scenes`: finds the steady scenes of each clip in its preview copy
  (OpenCV phase correlation on a grid of blocks, one camera motion fitted to the
  blocks that agree, so people moving in a still shot are not camera movement) and
  writes `analysis/<clip>/scenes.json` and a report.
- `assemble`: builds `timeline.json` from the clips in recording order: the clap
  cut (0.5 s after the clap), every steady scene of 2 s or longer as a segment, a
  clip with no steady scene left out and listed, a sound crossfade of 0.5 s, film
  fades of 1 s and 2 s. Saving keeps numbered snapshots of the old file.
- `preview`: renders the film preview from the preview copies (frame-exact cuts,
  the sound mixed from the WAV files at the stored offset, crossfades and fades),
  with a cache per segment. `timeline summary` prints the film as a table.
- The page has a fourth tab, Film: a player and trim controls per scene (move the
  start or end by 1 s or 1 frame, remove, restore, reorder, rename), saved to the
  timeline file through the validator. Endpoints: `GET /api/timeline`,
  `POST /api/timeline/edit`, `GET /api/preview/<file>` (range requests),
  `POST /api/assemble`, `POST /api/preview`.
- `app --timeline FILE --work DIR --no-browser`.
- A sync offset per clip that the owner types by hand: the Film tab has a field per
  clip (SPECS 7.6). The value is stored in the timeline file as
  `sources[<id>].audio_offset_hand_s`, beside the measured `audio_offset_s`, which is
  never changed; `project.json` is not written. Rule V23 refuses a value beyond 30 s
  and warns when the WAV then holds no sound for a scene. A changed offset marks the
  sound of that clip's scenes for a new render and no picture. `assemble --replace`
  keeps the typed offsets.
- `timeline undo` and `timeline history`; `timeline validate` keeps a snapshot of a
  valid file; `timeline frames` writes stills at film times or clip times.
- `analyse --drift`: the clock drift between camera and recorder, measured on the
  last minute of every clip of 120 s or more against the stored offset, with a
  report that marks every clip over half a frame.

### Changed
- The tool runs on Python 3.12 in its own `.venv`; packages are pinned in
  `requirements.txt`. The 60 tests of 1.1.0 pass unchanged.

## 1.1.0 - 2026-10-04

### Added
- Order-preserving matching: videos and audio are aligned in recording order
  (natural filename sort) and pairs never cross; files may be missing on either
  side. Toggle with `MATCH_PRESERVE_ORDER` in `config.py`.
- Blackmagic RAW (`.braw`) support. Durations and camera audio are read via
  ffprobe/ffmpeg; thumbnails are not available for BRAW and show a placeholder.
- `sync --undo` removes the audio copies made by the tool (tracked in
  `ambient_audio_copies.json`).
- UI: "Add all" and "Clear" in step 2, a selection summary in step 3, and
  guards against saving or copying an empty selection.
- Resolve project and timeline name fields in the UI.
- `--version` flag.
- MIT license.
- Test suite (`python3 -m unittest discover -s tests`, 60 tests).

### Changed
- Correlation tiebreaker uses an onset envelope, so steady ambience (wind,
  water) no longer dominates the score.
- Sync offsets are refined to sub-frame precision (verified within about 1 ms on
  real BMPCC/H2N footage).
- Confidence now reflects the margin to the runner-up instead of the duration
  ratio.
- Resolve timeline is built from a pure, tested plan that applies offsets, trims
  and track placement; existing timeline names get a numeric suffix.
- Output folder defaults to the video folder in the CLI as well.
- Thumbnails are stored next to `project.json`.
- README and SPECS rewritten to match the implementation.

### Fixed
- Sessions now resume after a server restart or page reload, including the
  timeline order and trims.
- `python3 main.py resolve` no longer crashes.
- Stale thumbnails after a re-match.
- Buttons stuck disabled after an error.
- Server stayed unresponsive during thumbnail extraction.

### Security
- Removed the permissive CORS headers and added Host/Origin checks, so other
  websites can no longer read directory listings or start jobs.
- Removed the `/api/rename` endpoint, which renamed original files in place.
- The legacy `rename` command refuses to overwrite existing files.

### Removed
- Legacy standalone scene selector (`select` command) and unused config
  constants.

### Known limitations
- The Resolve push needs external scripting, which the free edition of
  DaVinci Resolve does not allow; the in-Resolve script (`resolve_import.py`)
  is still pending. Some Resolve API assumptions are listed in SPECS 2.7 and
  are not yet verified against a real Resolve timeline.

## 1.0.0 - 2026-10-03

- Initial version: duration-based matching with correlation tiebreaker, browser
  UI for selecting and ordering scenes, audio copy and rename, Resolve
  integration.
