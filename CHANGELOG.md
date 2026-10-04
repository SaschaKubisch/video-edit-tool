# Changelog

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
