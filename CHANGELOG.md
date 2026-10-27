# Changelog

## Unreleased (editor, step 2 in progress)

### Added (step 6, vertical shorts)
- `short`: plans a vertical 9:16 short from the film and renders it from the `.braw` files with the film's renderer (look,
  loudness -14 LUFS with a true peak of at most -1 dBTP, 1 s fade-in, 2 s fade-out), as MP4, H.264, 1080 x 1920,
  29.97 frames per second, about 8 Mbit/s, AAC 192 kbit/s at 48 kHz. `--length` (default 60 s, decision D15), `--output`,
  `--size`, `--prefer`, `--only`, `--exclude`, `--subtitles`, `--dry-run` (the plan: kept scenes with film and short
  times, kept and dropped subtitles, notes and warnings), `--no-render`, `--stills` (one still per scene and a contact
  sheet), `--detach`. The plan is a timeline file of kind `short` next to the output (`short-60.json`); `short --file
  short-60.json` renders it as it is. The film is not changed.
- The rule (`shorts.py`): about one scene per 4 s; one scene from each equal part of the film, chosen by importance
  (pinned or named, then scene text or subtitles, then the longest); time in proportion to the length of the scene, at
  least 2.0 s (the last scene at least 2.25 s), at most 8 s, the sum exact to the frame; the window of the scene that
  holds whole subtitles and does not cut one, else the middle. The cap is raised, with a note, when the scenes cannot
  reach the length. A film shorter than the length becomes the whole short.
- Subtitles of the film laid out again for 9:16 (two lines of 24 characters, a long one split), kept when at least
  max(1.0 s, 0.8 of the cue) of them stays inside the window; titles follow their scene.
- `scenes pin` and `scenes unpin` (`priority` `must`), `scenes crop SCENE --x X | --from A --to B | --clear` and
  `scenes crop SCENE --still [--at S]`, a picture of a frame with the 9:16 window outlined and what it shows, for Claude
  Code to look at (decision D16: by hand or by prompt, no subject tracking). `CLAUDE.md` tells Claude Code how to make a
  short from a request such as "make a 30 second short of the salad".
- The preset `short` for `export add`.
- The first real runs (2026-10-28, `SPECS.md` 11.10): a short of 60 s from the first shoot, 15 scenes, 1798 frames, 59.993 s,
  1080 x 1920, 60.1 MB, 8.02 Mbit/s, -14.2 LUFS, true peak -1.2 dBTP, rendered in 178 s (0.353 times real time) with a busy
  machine; the same with the 44 subtitles of the first shoot in 173 s. No black bar. With the window centred about 3 of
  15 scenes need a hand crop.

### Fixed (step 6)
- The `lower_third` title lay at 72 percent of the height, inside the bottom 35 percent of a 9:16 frame that the
  safe-zone rule keeps free (y = 1382 against the limit of 1248 at 1080 x 1920). Titles are now kept inside the safe area
  of the frame; in 16:9 nothing moves.
- A film that keeps a `crop` (the window of its shorts) no longer gets the warning V13 that the crop has no effect;
  a short whose own `fit` is `fit` still does.
- `export-shotcut` refuses a short (the project has no 9:16 window) instead of writing a letterboxed one.
- SPECS 11.1 and check 6.3 named ASS files and libass; the tool has neither (it draws text with Pillow).

### Removed (D4, 2026-10-27: the Resolve push and the audio copy step)
- The commands `sync` (copy the recorder's files next to the videos, with `--undo`) and `resolve` (push a timeline into
  DaVinci Resolve), the modules `syncer.py` and `resolve_integration.py`, the endpoints `/api/sync` and `/api/resolve`,
  the third tab of the page (Sync & Export; the Film tab is now tab 3), the option `match --output`, the project field
  `output_dir`, and the tests of these parts. The owner's words: the tool "no longer needs DaVinci Resolve for these
  films". The git history keeps the code. An old `project.json` with `output_dir` still loads. Copies made earlier stay
  where they are; `ambient_audio_copies.json` lists them. The tests of `Project` moved to `tests/test_project.py`;
  `tests/test_removed.py` keeps the removed parts removed.

### Changed (decisions of 2026-10-27)
- D4, the audio form of D11, D14, D15 and D16 are settled (`PRD.md` 2.5, `SPECS.md` 16). Step 6 follows them.

### Fixed (translations, limits of a request, waits, Japanese cuts)
- Every request to the model carries `max_tokens`, sized from its cues. In the second real run two follow-up requests for
  9 short cues were billed 65538 tokens out because the model ran on without a limit. An answer that ends at the limit
  (`finish_reason` "length", "MAX_TOKENS", or as many tokens out as allowed) is a failed try with the reason "the answer
  was cut off at max_tokens", also when the JSON in it can be read; it is tried once more and a second one ends the
  request. `translate-report.json` lists every try with its tokens, and the command names the tries that failed.
- HTTP 429 and 503 wait for `Retry-After` (seconds or an HTTP date, at most 60 s), else 5, 15 and 30 s, and make up to 4
  tries; other errors keep 1 s, 3 s and 3 tries.
- A Japanese line is cut after a particle (は が を に で と の も へ や から まで より) rather than between other phrases,
  without adding a line. For this the word model of BudouX (`ja_word.json`) adds cuts after particle words that the phrase
  model does not offer: `粘土のすり鉢とまな` / `板が準備されている。` (the word まな板 split) is now `粘土のすり鉢と` /
  `まな板が準備されている。`. Chinese and Thai are cut as before.

### Fixed (translations, answers of the model)
- In the first real run every Japanese follow-up request failed with "the answer is not JSON" and one Portuguese one
  with HTTP 429. The answer is now read when the JSON object stands among prose, in a fence that is not the whole text,
  or holds a line break inside a text, and when it comes as a list of `{"id", "translation"}`; a cut-off answer, a
  trailing comma or a missing id are still refused. The follow-up prompt shows the answer with the ids of its request.
- `subtitles translate --save-raw DIR` writes every answer of the model that cannot be read to a file (the text
  only) and names the files in the warning, to see what the model sent.
- The warning of a follow-up that failed says "after 3 tries". HTTP 429 was already tried again with the waits of the
  first request; a test now checks it.

### Fixed (`timeline undo` and the shared history folder)
- All timeline files of one folder share one `timeline.history` folder, and `timeline undo` took the newest snapshot
  that differed from the file, even one made from another file, and wrote that file's film into this one. Each
  snapshot now has a record `NNNN.owner.json` with the resolved absolute path of the file it was made from.
  `undo`, `history`, `snapshot_if_changed` and the limit of 200 snapshots use only the snapshots of the file named by
  `--file`. A snapshot without a record (made before this change) belongs to no file: no command restores it, nothing
  deletes it, and `history` lists it apart with a note. The advice in `CLAUDE.md` to run `timeline history` first is
  gone.

### Fixed (translations, line breaks)
- Japanese, Chinese and Thai lines are cut at phrase boundaries only (`linebreak.py`, the `budoux` package,
  Apache-2.0, pinned in `requirements.txt`), with the line-start and line-end rules of W3C JLREQ and CLREQ. Before,
  the lines were cut by the count of characters: `ハン|ドブレンダー`, `倒|进`, `จา|ก`, and a line could begin with
  `ー` (first real run, 2026-10-27).
- A translated cue that needs more than two lines or is read faster than its language allows is sent once more to the
  model with a maximum number of characters (the smaller of two lines and the reading speed times the cue's
  duration). The shorter text is kept when it fits better. The follow-up is in the token and cost report, is cached
  under its own key, and `subtitles translate --no-shorten` leaves it out.
- The reading speed of a translation no longer counts the space that joined its lines.

### Added (step 5, prompt edits)
- The scene index (`scenes index`): `index.json` and `index.md` in `<work>/index/<timeline name>/` with one entry per
  scene (film times, clip and frames, the speech the transcript has in the scene, the scene text, the cues, the sheet),
  and the hash of the timeline file it was made from.
- Still sheets (`scenes sheets`): one JPEG per scene with 3, 6 or 9 stills labelled with the scene id and film time,
  and overview pages with one still for each of 12 scenes. Sheets that are up to date are kept, and the stills are
  cached, so a removal only draws new labels. `scenes frames SCENE --every 1s` is a finer look inside one scene.
- `scenes remove`, `keep`, `restore` and `cut ID --from S --to S` (film seconds), each with `--dry-run` (the film
  length before and after, the scenes and cues affected, the film titles that sit by film time) and one snapshot
  for `timeline undo`. A cut splits the scene; the second part has the film's own sound crossfade.
- `CLAUDE.md` (new) tells Claude Code how to handle an edit request; the README has the sausage example.
- Film tab: a Restore button on a removed scene.
- D14 is still open: "cut out" is read as remove, `keep` exists, the search is Claude Code reading sheets and the index.

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
  (`VET_TRANSLATE_MODEL`, key from `OPENROUTER_API_KEY` or, when that is not set, from the macOS keychain), batches with context, answers checked, cached by
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
