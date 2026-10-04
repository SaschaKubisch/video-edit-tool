# Ambient Video Automation

A local tool for the BMPCC 4K + Zoom H2N dual-system workflow. It pairs each camera clip with its H2N recording, copies the recording next to the video under the video's file name (no re-encoding), and optionally builds an ordered timeline in DaVinci Resolve.

## What it does

1. **Match** - pairs videos with audio files. Both are assumed to be recorded and named in sequential order (natural sort: `ZOOM0009` before `ZOOM0010`), so pairs never cross: audio 9 is never paired with video 2. Either side may have files that were not recorded (video 9 can pair with audio 8 when one clip has no audio). The pairing is an order-preserving alignment on duration similarity; pairs differing by more than 10 s are not matched. When several audio files are admissible with near-identical durations, onset-envelope cross-correlation between the camera scratch track and the candidates breaks the tie. The same correlation yields the sync offset per pair. Videos or audio files left without a partner are listed in the console.
2. **Copy and rename** - each matched audio file is copied next to its video as `<video stem>.<audio ext>` (`IMG_2231.MOV` and `IMG_2231.WAV`). Originals are untouched and nothing is transcoded. The copies are recorded in `ambient_audio_copies.json` so they can be removed again.
3. **Resolve (optional)** - creates a project and timeline with the selected scenes in order: video on V1, audio on A2, offset and trims applied.

## Quick start

```bash
pip install -r requirements.txt
brew install ffmpeg        # provides ffmpeg and ffprobe
python3 main.py            # opens http://localhost:8765
```

Requires Python 3.9 or newer, numpy, scipy, tqdm and ffmpeg/ffprobe on the PATH.

## UI workflow

1. **Match** - pick the video folder and the audio folder, click Match. The result list shows each pair, its offset and a confidence value (green high, red low - check red ones by hand).
2. **Select & Order** - click thumbnails to add scenes to the timeline, drag to reorder, set optional trim values (seconds cut from the start and from the end of the clip). Save the selection.
3. **Copy Audio & Push to Resolve** - copy and rename the audio (output folder defaults to the video folder), then optionally enter a project and timeline name and push to Resolve.

Only one job (match, copy, Resolve) runs at a time. State is saved to `project.json`, so the app resumes where you left off.

## CLI

Every step also works without the browser. The global `--project/-p FILE` option (default `project.json`) goes before the subcommand.

```bash
python3 main.py                       # same as: app
python3 main.py app [--port 8765]
python3 main.py match --video DIR --audio DIR [--output DIR]
python3 main.py sync [--selection JSON | --selection-file FILE] [--output DIR]
python3 main.py sync --undo [--output DIR]
python3 main.py resolve [--project-name NAME] [--timeline NAME] [--fps N] [--list]
python3 main.py rename [--pattern P] [--dry-run] [--undo rename_log.json]   # legacy
```

- `match` writes `project.json` and the thumbnails.
- `sync` uses the selection saved in `project.json` unless one is passed, e.g. `--selection '{"selection":[3,1,5],"selection_trims":{"3":{"start":2,"end":1}}}'`. The selection holds scene indices in timeline order.
- `rename` is a legacy helper that renames both files of each pair to `scene_NN.*`. It refuses to run if a target name already exists, and `--undo` reverses it from `rename_log.json`. The normal flow does not need it.

## Offset sign

`offset = t_audio - t_video`. A positive offset means the same event occurs later in the audio file than in the video, i.e. the recorder was started before the camera. In Resolve the audio clip's source in-point is advanced by the offset. A negative offset places the audio that much later on the timeline.

## Resolve

- DaVinci Resolve must be running. External scripting (what this tool uses) may require **Resolve Studio**, with Preferences > System > General > External scripting using: **Local**.
- The tool looks for Resolve's scripting modules automatically; otherwise set `PYTHONPATH` to `.../DaVinci Resolve/Developer/Scripting/Modules/`.
- Free edition: running the same logic from inside Resolve via Workspace > Scripts is planned (`resolve_import.py`, not yet shipped). Until then use Resolve's Auto Sync Audio by file name on the copied files.
- Layout API calls are built from a pure `build_timeline_plan()`; a few Resolve API behaviours (clip info keys, end frame handling) are assumptions flagged in SPECS.md.

## Files

- `project.json` is written to the current working directory (path configurable with `--project`).
- Thumbnails go to a `thumbnails/` folder next to `project.json`.
- `ambient_audio_copies.json` lives in the output folder (default: the video folder).

## Undo the copy step

```bash
python3 main.py sync --undo            # uses the output folder from project.json
python3 main.py sync --undo --output DIR
```

This deletes only the files listed in `ambient_audio_copies.json` in that folder, then the manifest. Videos and original recordings are never touched. Note that a copy which replaced an existing file of the same name is also removed; the tool logs a warning when it overwrites a file of different size.

## Camera formats

`.braw` (Blackmagic RAW) files are supported for matching, sync and Resolve: ffprobe reads them as QuickTime containers and ffmpeg extracts their PCM audio. ffmpeg cannot decode the BRAW video, so **no thumbnail** can be generated for them; the UI shows a "No thumbnail" placeholder and everything else works. Video and audio may live in the same folder.

## Configuration

To turn off the sequential-recording rule (e.g. when mixing cards whose numbering resets), set `MATCH_PRESERVE_ORDER = False` in `config.py`; matching then uses a global duration assignment that ignores file order. `MATCH_MAX_DURATION_DIFF_SEC` (default 10) and `MATCH_SKIP_PENALTY` (default 6) tune the order-preserving mode.

`config.py` holds the constants: file extensions, `MATCH_SAMPLE_RATE`, `MATCH_MAX_DURATION_SEC` (seconds of audio used for correlation, default 60; raise it if scenes start with long silence), thumbnail time/width/quality, the port and `PROJECT_FILE`.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

See `PRD.md` for requirements and `SPECS.md` for the technical design.

## License

MIT, see `LICENSE`.
