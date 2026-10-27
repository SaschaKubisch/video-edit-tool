# Ambient Video Automation

A local tool for the BMPCC 4K + Zoom H2N dual-system workflow. It pairs each camera clip with its H2N recording, copies the recording next to the video under the video's file name (no re-encoding), and optionally builds an ordered timeline in DaVinci Resolve.

## What it does

1. **Match** - pairs videos with audio files. Both are assumed to be recorded and named in sequential order (natural sort: `ZOOM0009` before `ZOOM0010`), so pairs never cross: audio 9 is never paired with video 2. Either side may have files that were not recorded (video 9 can pair with audio 8 when one clip has no audio). The pairing is an order-preserving alignment on duration similarity; pairs differing by more than 10 s are not matched. When several audio files are admissible with near-identical durations, onset-envelope cross-correlation between the camera scratch track and the candidates breaks the tie. The same correlation yields the sync offset per pair. Videos or audio files left without a partner are listed in the console.
2. **Copy and rename** - each matched audio file is copied next to its video as `<video stem>.<audio ext>` (`IMG_2231.MOV` and `IMG_2231.WAV`). Originals are untouched and nothing is transcoded. The copies are recorded in `ambient_audio_copies.json` so they can be removed again.
3. **Resolve (optional)** - creates a project and timeline with the selected scenes in order: video on V1, audio on A2, offset and trims applied.

## Quick start

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
brew install ffmpeg        # provides ffmpeg and ffprobe
.venv/bin/python main.py   # opens http://localhost:8765
```

Requires Python 3.12 (in the `.venv` folder next to the tool), the packages in `requirements.txt` (numpy, scipy, tqdm, vapoursynth with vapoursynth-brawsource, opencv-python-headless) and ffmpeg/ffprobe on the PATH. The tests run with `.venv/bin/python -m unittest discover -s tests -v`; 60 tests of version 1.1.0 pass on Python 3.12 without code changes.

## Editing the film (work in progress, branch `editor-step2`)

After `match` and `convert` (preview copies on the SSD), these commands build and show a rough film:

```bash
.venv/bin/python main.py analyse --clap --scenes --work /Volumes/SSD/work   # sync clap, steady scenes
.venv/bin/python main.py assemble --work /Volumes/SSD/work                   # timeline.json next to project.json
.venv/bin/python main.py timeline validate                                   # check it, with the files
.venv/bin/python main.py timeline summary                                    # the film as a table
.venv/bin/python main.py preview --work /Volumes/SSD/work                    # small film from the preview copies
.venv/bin/python main.py app --work /Volumes/SSD/work                        # the page: tab 4, Film
```

The film is the clips in recording order with the clap and the camera movement cut out, a hard cut between scenes, the sound crossfaded over 0.5 s, and a fade in (1 s) and out (2 s). A clip with no steady scene of 2 s is left out and listed in `reports/assembly-report.txt`. The originals are never written.

## Look, sound and the final film (step 3, work in progress)

```bash
export VET_LUT_DIR=/Volumes/SSD/luts/slay                                  # the folder with your .cube files
.venv/bin/python main.py loudness set --mode segment_then_film             # each scene, then the film: -14 LUFS, -1 dBTP
.venv/bin/python main.py look list                                         # the creative LUTs you have
.venv/bin/python main.py look set --lut slay-04-film.cube --work /Volumes/SSD/work   # after the base look
.venv/bin/python main.py export add --preset film-4k --work /Volumes/SSD/work             # MP4, H.264, DCI 4K, 45 Mbit/s
.venv/bin/python main.py render --export film-4k --detach --work /Volumes/SSD/work        # in the background, log + pid file
```

`render` decodes the `.braw` files at full size (never the preview copies), applies the base LUT and the
creative LUT, adds the letterbox bars of wide clips, levels the sound, and writes the file with its
checks. `export add --preset film-master` adds a ProRes 422 HQ master that stays off until you run
`export enable film-master`. A film that uses a LUT with a credit gets `<film>.credit.txt` next to it:
put its line in the video description. A stopped render (`kill PID`) removes its files and starts over
when you run the command again.

**Shotcut, for rare hand work.** `main.py export-shotcut --work DIR` writes `DIR/film.mlt` for Shotcut (it uses the
preview copies, because Shotcut cannot read BRAW) and `DIR/audio-synced/`, and checks the project with Shotcut's
`melt` without opening Shotcut. After the hand work, `main.py import-shotcut --work DIR` reads the cut points and
the order back into the timeline file (the old file is kept as a snapshot); filters, transitions and audio edits made
in Shotcut do not come back, and the command lists them. Then `render` makes the 4K film from the `.braw` files.

**Sync offset per clip.** The tool measures one offset per clip when it matches the files (the seconds by which a sound comes later in the recorder's file than in the camera's). If one is wrong, the Film tab has a card, Sync offset per clip: type the right offset in seconds (a minus sign is allowed). The tool stores it in the timeline file as `audio_offset_hand_s`, beside the measured `audio_offset_s`, which is never changed. The sound of that clip's scenes uses the typed offset; the pictures do not change, so the next preview renders only sound again. Empty the field, or press Original, to use the measured offset again. A value beyond 30 seconds is refused, and a typed offset that leaves the recorder file without sound for a scene is a warning. `timeline summary` lists the offsets set by hand, and `assemble --replace` keeps them. `project.json` is not written. `PRD.md` and `SPECS.md` describe the plan and say which parts are built.

## Subtitles and titles (step 4, work in progress)

```bash
export VET_FONT_DIR=/Volumes/SSD/fonts/inter                     # Inter (SIL Open Font License), not installed on the system
export VET_WHISPER_MODEL=/Volumes/SSD/models/ggml-large-v3-turbo.bin   # only for subtitles auto
.venv/bin/python main.py subtitles text s003 --text "Um, the pan is hot. We add the sausage and wait."   # the text box of a scene
.venv/bin/python main.py subtitles list                          # the cues with their film times
.venv/bin/python main.py subtitles add --at 30 --length 3 --text "Hello there."
.venv/bin/python main.py subtitles auto --work /Volumes/SSD/work  # let Whisper listen (optional)
.venv/bin/python main.py title add --text "Sunday kitchen" --start 1 --length 4 --template center
.venv/bin/python main.py export set film-4k --subtitles both     # srt, burned, both or none
.venv/bin/python main.py render --export film-4k --work /Volumes/SSD/work
export OPENROUTER_API_KEY=...                                    # only for translate; read from the environment, never written
.venv/bin/python main.py subtitles translate --lang pt-BR,zh-Hans,th,de,ja,ru --work /Volumes/SSD/work
```

**The text box of a scene is the main way to write subtitles.** Type the words of a scene (or dictate them with
Superwhisper) into its box in the Film tab, or give them to `subtitles text`. The tool removes filler words (um, uh),
fixes spacing and capitals, splits the text into subtitles of two lines at most (about 42 characters, broken after
punctuation and before conjunctions), and spreads them across the scene in proportion to their length. If the
text needs more time than the scene has, it says so and shortens the subtitles instead of dropping any. Drag a
subtitle in the strip to move it, drag its edges to change its length, or type its times; such a subtitle is marked
as changed by hand, and saving the box again asks before it replaces it. A trim of the scene moves the subtitles with
the picture, or spreads them again if nobody moved them.

**The form of the subtitles is a setting of each export:** `srt` writes `<film>.en.srt` next to the film (the starting
value for a film), `burned` draws them into the picture (the starting value for a vertical short), `both`, `none`.
Titles are always drawn. Change it in the Subtitles card, with `export set`, or for one render with `render --subtitles`.
The style is one for all films (`config.py`: Inter SemiBold, 4.5 percent of the frame height, white with a black outline,
inside the safe area of the picture; a subtitle that does not fit is split, never made smaller).

**Whisper and the footage.** `subtitles auto` runs whisper.cpp with the model large-v3-turbo on the recorder's
WAV of each clip, once per clip. On ambient sound the model writes text where nobody speaks ("Thank you.", `*Pain*`);
the tool drops stock phrases, loops and phrases over sound at the noise floor and lists everything it dropped in
`reports/subtitles-auto-report.json`. What is kept still needs a person's check. A voice-activity model
(`VET_WHISPER_VAD_MODEL`, for example `ggml-silero-v5.1.2.bin` from the whisper.cpp project) improves this; it is not
downloaded for you.

**Claude Code writes subtitles and titles from a prompt** by running these commands, or by writing a JSON file that the
tool checks and imports. Example: the owner says "put the title Sunday kitchen over the first 4 seconds, and subtitle the
sausage scene with what I say in it". Claude Code looks (`timeline summary`, `timeline frames`), then:

```bash
cat > /tmp/titles.json <<'EOF'
{"titles": [{"text": "Sunday kitchen", "start_s": 1, "length_s": 4, "template": "center"}]}
EOF
.venv/bin/python main.py title import /tmp/titles.json
.venv/bin/python main.py subtitles text s017 --text "Now the pan is hot enough. We add the sausage and wait for it to cook."
cat > /tmp/cues.json <<'EOF'
{"cues": [{"start_s": 312.4, "end_s": 315.0, "text": "Listen to that sizzle."}]}
EOF
.venv/bin/python main.py subtitles import /tmp/cues.json
.venv/bin/python main.py timeline validate && .venv/bin/python main.py subtitles check
```

Times in the files are film seconds (a cue or title with `"anchor": "s017"` counts from the start of that scene). Every
command goes through the validator and keeps the old file as a snapshot (`timeline undo`). `subtitles check` reports
cues that do not fit the safe area. Not built: burned-in subtitles in Chinese, Japanese or Thai (Inter has no such
glyphs; the SRT files are fine) and subtitles and titles in the Shotcut project. See `SPECS.md` section 9.

## Prompt edits (step 5, work in progress)

You tell Claude Code what to change in words, and it finds the scene. Example: you type "please cut out the scene where I
am frying a sausage". The tool cannot show the film to Claude Code, so it prepares what Claude Code can read: an index
of every scene (film times, clip, speech if a transcript exists, subtitles, scene text) and one still sheet per scene.
`CLAUDE.md` in this repository tells Claude Code what to do with a request like this:

1. It makes or refreshes the index and the sheets, reads the index, and looks at the overview pages and the sheets
   of the scenes that could match.
2. It tells you what it found: scene ids, film times, what it saw in which still, how sure it is. If no scene fits it
   says so.
3. When you agree, it runs the command with `--dry-run` (what would change: scenes, film length before and after,
   subtitles affected), shows you, and runs it for real. The tool keeps the old file as one snapshot.
4. It runs `timeline validate`. If you do not like the result, `timeline undo` takes the change back.

```bash
.venv/bin/python main.py scenes index --sheets --file timeline.json --work /Volumes/SSD/work   # index.md, index.json and the sheets
.venv/bin/python main.py scenes sheets --file timeline.json --work /Volumes/SSD/work           # only the sheets (kept when unchanged)
.venv/bin/python main.py scenes frames s017 --every 1s --file timeline.json --work /Volumes/SSD/work   # a finer look inside one scene
.venv/bin/python main.py scenes remove s017 --dry-run          # what would change; nothing is written
.venv/bin/python main.py scenes remove s017                    # take the scene out of the film (it stays in the file)
.venv/bin/python main.py scenes keep s017,s018                 # keep only these scenes
.venv/bin/python main.py scenes cut s017 --from 724.5 --to 731 # take a moment (film seconds) out of one scene
.venv/bin/python main.py scenes restore s017                   # put a removed scene back
.venv/bin/python main.py timeline validate && .venv/bin/python main.py timeline undo   # check; go back
```

The files go to `<work>/index/<timeline name>/`: `index.md` (read by Claude Code), `index.json`, `overview-01.jpg` and
more (one still from the middle of each of 12 scenes), `sheets/s017.jpg` (3, 6 or 9 stills across the scene, each
labelled with the scene id and its film time) and `frames/` (finer looks). The stills come from the preview copies. A
sheet is drawn again only when its scene, its place in the film, its frames or its label changed, and the stills are
kept, so after a removal only new labels are drawn. The index says which timeline hash it was written from; run
`scenes index` again after every change, because the film times after a removed scene move.

"Cut out" is read as "remove from the film" (decision D14 is still open: the owner has not confirmed this reading).
`scenes cut` splits a scene: the first part keeps its id, the second part is a new scene; the sound crossfades over the
usual half second at the new join, subtitles go with their picture, and the text box of the scene stays with the first
part. The scene search is Claude Code reading still sheets, the scene texts and, when `subtitles auto` has run, the
Whisper transcript. It can be wrong; the dry run, the preview and `timeline undo` are there for that. See `SPECS.md`
section 10.

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
python3 main.py convert --work DIR [--lut FILE] [--encoder E] [--limit N] [--only A,B] [--dry-run]
python3 main.py convert --probe CLIP.braw
python3 main.py convert --test CLIP.braw --work DIR [--seconds S]
python3 main.py analyse --clap --work DIR [--limit N] [--report-name NAME]
python3 main.py analyse --drift --work DIR [--limit N] [--only A,B] [--report-name NAME]
python3 main.py timeline validate [--file timeline.json] [--no-file-checks]
python3 main.py timeline history [--file timeline.json] [--json]
python3 main.py timeline undo [--file timeline.json]
python3 main.py timeline frames [--file timeline.json] [--work DIR] (--film-s 12.0,48.5 | --source CLIP --at 3.0)
python3 main.py scenes index|sheets [--file timeline.json] [--work DIR]       # the scene index and still sheets (step 5)
python3 main.py scenes frames SCENE [--every 1s] [--from S --to S] [--file timeline.json] [--work DIR]
python3 main.py scenes remove|keep|restore ID[,ID...] [--dry-run] [--file timeline.json]
python3 main.py scenes cut SCENE --from S --to S [--dry-run] [--file timeline.json]
```

- `match` writes `project.json` and the thumbnails.
- `sync` uses the selection saved in `project.json` unless one is passed, e.g. `--selection '{"selection":[3,1,5],"selection_trims":{"3":{"start":2,"end":1}}}'`. The selection holds scene indices in timeline order.
- `convert` makes one small preview copy (H.264 MP4, half size, the base LUT baked in, the camera audio) of every matched `.braw` clip in `DIR/copies/` and records what it did in `DIR/conversion.json`. `DIR` is a folder on the SSD (or `work_dir` in `project.json`); it must not lie inside a folder that holds the originals, which are only read. Clips whose copy is current are skipped, so an interrupted run resumes. The base LUT is read from Resolve's LUT folder (`VET_BASE_LUT` or `--lut` name another file) and is never copied into the repository. Run it with `.venv/bin/python`; it needs VapourSynth with `vapoursynth-brawsource` (see `requirements.txt`).
- `analyse --clap` finds the sync clap at the start of each matched clip: a sound that the camera's scratch audio and the recorder's WAV both heard at the stored offset. It writes `analysis/<clip>/clap.json` and the report `reports/clap-report.txt` and `.json` in `DIR`, and marks clips for review (no clap, a clap that only one microphone heard, a late or weak clap, an offset outside the usual range). The originals are only read.
- `analyse --drift` measures the clock drift between camera and recorder: the offset again on the last minute of every clip of 120 s or more, against the offset from matching. It writes `analysis/<clip>/drift.json` and the report `reports/drift-report.txt` and `.json` in `DIR`, and marks every clip whose drift is more than half a frame. The originals are only read.
- `timeline validate` checks the timeline file (`timeline.json`, format in `SPECS.md` section 3) against its rules and prints the findings as JSON; exit code 1 when there are errors. A valid file that differs from the newest snapshot is kept as a snapshot in `timeline.history/` (the number is printed on standard error).
- `timeline history` lists the versions of the timeline file with their time and what changed; `timeline undo` goes back to the previous version (the version it replaces is kept in `timeline.history/undone/`).
- `timeline frames` writes PNG stills of the film at film times (`--film-s`) or of a clip at clip times (`--source CLIP --at`) into `DIR/stills/` and prints the paths. It reads the preview copies.
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
.venv/bin/python -m unittest discover -s tests -v
```

See `PRD.md` for requirements and `SPECS.md` for the technical design.

## License

MIT, see `LICENSE`.
