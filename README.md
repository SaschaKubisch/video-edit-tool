# Ambient Video Automation

A local tool for the BMPCC 4K + Zoom H2N dual-system workflow. It pairs each camera clip with its H2N recording, finds the sync offset, and builds the film from the camera's `.braw` files and the recorder's WAV files itself (the sections below).

## What it does

1. **Match** - pairs videos with audio files. Both are assumed to be recorded and named in sequential order (natural sort: `ZOOM0009` before `ZOOM0010`), so pairs never cross: audio 9 is never paired with video 2. Either side may have files that were not recorded (video 9 can pair with audio 8 when one clip has no audio). The pairing is an order-preserving alignment on duration similarity; pairs differing by more than 10 s are not matched. When several audio files are admissible with near-identical durations, onset-envelope cross-correlation between the camera scratch track and the candidates breaks the tie. The same correlation yields the sync offset per pair. Videos or audio files left without a partner are listed in the console.
2. **Build the film** - preview copies, steady scenes, the timeline file, look, sound, subtitles and the final render: the sections below.

DaVinci Resolve is not used. Earlier versions copied the recorder's files next to the videos under the videos' names (`sync`) and pushed a timeline into Resolve (`resolve`); both were removed on 2026-10-27 (decision D4). The git history keeps the code.

## Quick start

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
brew install ffmpeg        # provides ffmpeg and ffprobe
.venv/bin/python main.py   # opens http://localhost:8765
```

Requires Python 3.12 (in the `.venv` folder next to the tool), the packages in `requirements.txt` (numpy, scipy, tqdm, vapoursynth with vapoursynth-brawsource, opencv-python-headless, Pillow, budoux) and ffmpeg/ffprobe on the PATH. The tests run with `.venv/bin/python -m unittest discover -s tests -v`; 60 tests of version 1.1.0 pass on Python 3.12 without code changes.

## Editing the film (work in progress, branch `editor-step2`)

After `match` and `convert` (preview copies on the SSD), these commands build and show a rough film:

```bash
.venv/bin/python main.py analyse --clap --scenes --work /Volumes/SSD/work   # sync clap, steady scenes
.venv/bin/python main.py assemble --work /Volumes/SSD/work                   # timeline.json next to project.json
.venv/bin/python main.py timeline validate                                   # check it, with the files
.venv/bin/python main.py timeline summary                                    # the film as a table
.venv/bin/python main.py preview --work /Volumes/SSD/work                    # small film from the preview copies
.venv/bin/python main.py app --work /Volumes/SSD/work                        # the page: tab 3, Film
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
put its line in the video description. A film longer than 2 minutes is rendered in chunks of whole scenes
(`--chunk-seconds S` changes the length, 0 renders in one process); the finished chunks and the sound are kept in
`<work>/chunks/<export>/`, so after a stop (`kill PID`, Ctrl+C, a failure, a crash) the same command goes on where it
was, and `--fresh` starts over. The chunks are joined by copy, and the free space asked for is about 2.1 times the
file. A short (under 2 minutes) is rendered in one process and starts over when it is stopped.

**Shotcut, for rare hand work.** `main.py export-shotcut --work DIR` writes `DIR/film.mlt` for Shotcut (it uses the
preview copies, because Shotcut cannot read BRAW) and `DIR/audio-synced/`, and checks the project with Shotcut's
`melt` without opening Shotcut. After the hand work, `main.py import-shotcut --work DIR` reads the cut points and
the order back into the timeline file (the old file is kept as a snapshot); filters, transitions and audio edits made
in Shotcut do not come back, and the command lists them. Then `render` makes the 4K film from the `.braw` files. The film's subtitles go into the project as a subtitle track, the form Shotcut 24 and later keeps (`--lang en,ja` for more tracks, `--lang none` for none; a translation must have been made with `subtitles translate`); the first track is drawn over the picture and all of them are in Shotcut's Subtitles panel. `import-shotcut` does not read them back; it says whether the English track was changed in Shotcut and how to keep the changes (save the track as an SRT file, then `subtitles import FILE.srt --replace`). The creative LUT does not tint the black bars of a wide clip in Shotcut (measured with `melt`).

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
export OPENROUTER_API_KEY=...                                    # only for translate; or keep the key in the macOS keychain (below)
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

**The OpenRouter key** is read from the environment variable `OPENROUTER_API_KEY`; when that is not set, from the
macOS login keychain: a generic password whose service name is `OPENROUTER_API_KEY` (the setting
`VET_OPENROUTER_KEYCHAIN_SERVICE` names another service; an empty value switches the keychain off). Add it once with
`security add-generic-password -a OPENROUTER_API_KEY -s OPENROUTER_API_KEY -w` (it asks for the key). The tool runs
`security find-generic-password -s OPENROUTER_API_KEY -w` when it needs the key, so macOS may ask once whether to allow
the access. The key goes into one request header and is never printed, logged, cached or written to a file.

**Lines of the translations.** Each language is wrapped for its own limit (42 characters for pt-BR, de and ru, 35 for
Thai, 16 for Chinese, 13 full-width characters for Japanese; two lines). Chinese, Japanese and Thai have no spaces, so
the lines are cut only at phrase boundaries (the `budoux` package), in Japanese preferably after a particle (は が を に
で と の も へ や から まで より), and never so that a line starts with a closing mark, a small kana or `ー`, or ends
with an opening bracket. A translation that needs more than two lines or is read
faster than its language allows (Japanese 4, Chinese 9, the others 17 characters per second) is sent to the model once
more, with the most characters it may have (the smaller of two lines and the reading speed times the length of the
cue). The shorter text is kept only when it fits better; the first text and its warning stay otherwise. The command
prints how many cues were asked again and which were shortened; the follow-up counts in the tokens and the cost.
`--no-shorten` leaves it out. A request is tried 3 times (waits of 1 s and 3 s) after an answer that cannot be read, a
network error or a server error. HTTP 429 and 503 wait for the `Retry-After` of the answer (at most 60 s) or 5, 15 and
30 s, and make up to 4 tries. Every request has an output limit (`max_tokens`, from the number of cues in it); an answer
that ends at the limit counts as failed even when the JSON in it can be read, is tried once more, and a second one ends
the request. The command lists the tries that failed, and `translate-report.json` has every try with its tokens. `--save-raw DIR` writes the text of every answer of the model that cannot
be read to a file in `DIR` (the text only, never the key or a header) and names the files in the warnings, to see what
the model sent.

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
cues that do not fit the safe area. Not built: titles in the Shotcut project (the film's subtitles are in it as a
subtitle track, see the Shotcut paragraph above). See `SPECS.md` section 9.

**Burned-in translations (Japanese, Simplified Chinese, Thai and the other three).** After `subtitles translate` has made the translations, the film or a short can be rendered with its subtitles drawn in a language:

```bash
export VET_NOTO_FONT_DIR=/Volumes/SSD/fonts/noto     # NotoSansJP-Bold.otf, NotoSansSC-Bold.otf, NotoSansThai-SemiBold.ttf (SIL Open Font License 1.1), not installed on the system
.venv/bin/python main.py render --export film-4k --lang ja --work /Volumes/SSD/work        # film-4k.ja.mp4 next to film-4k.mp4
.venv/bin/python main.py render --export film-4k --lang th --subtitles both --work /Volumes/SSD/work   # also film-4k.th.srt
.venv/bin/python main.py short --file /Volumes/SSD/work/exports/short-60.json --lang zh-Hans --work /Volumes/SSD/work
```

The text of each cue is read from the translation cache without a request; a cue with no translation stops the render before it writes anything and names the command to run. Inter has no glyphs for these three languages, so each uses its own Noto font (Thai with Pillow's raqm layout, which the tool checks for), its own line height (Japanese and Chinese 1.3 em, Thai 1.45 em, measured) and lines cut at phrase boundaries inside the safe width of the picture. Latin letters and digits inside a cue are drawn by the same font. The English film is not touched; the file of a language has the code in its name, and its credit file (`<film>.<lang>.credit.txt`) names the font and its licence after the line of the LUT. Pt-BR, German and Russian are drawn with Inter. `SPECS.md` 9.6 and 9.13 have the numbers and the run on the first shoot.

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

## Vertical shorts (step 6, work in progress)

A short is a 9:16 video for phones, cut from the film and rendered from the `.braw` files with the same look, loudness (-14 LUFS, true peak at most -1 dBTP) and fades as the film. You say how long it should be (60 seconds if you say nothing); the tool picks the scenes, gives each its time, cuts the 9:16 window from the picture, lays the subtitles out again for the narrow frame, and renders MP4, H.264, 1080 x 1920, 29.97 frames per second, about 8 Mbit/s, AAC at 48 kHz.

```bash
export VET_FONT_DIR=/Volumes/SSD/fonts/inter                      # for burned-in subtitles and titles
.venv/bin/python main.py short --file timeline.json --work /Volumes/SSD/work --length 60 --dry-run   # the plan, nothing written
.venv/bin/python main.py short --file timeline.json --work /Volumes/SSD/work --length 60 --stills    # plan, render, stills
.venv/bin/python main.py short --file timeline.json --work /Volumes/SSD/work --length 30 --only s040,s041,s042,s043   # of these scenes
.venv/bin/python main.py scenes pin s012,s040                     # a short always keeps pinned scenes
.venv/bin/python main.py scenes crop s040 --still --file timeline.json --work /Volumes/SSD/work   # the window on a frame
.venv/bin/python main.py scenes crop s040 --x 0.3                 # move the window (0 left, 0.5 centre, 1 right)
.venv/bin/python main.py scenes crop s040 --from 0.2 --to 0.6     # a slow pan
.venv/bin/python main.py short --file /Volumes/SSD/work/exports/short-60.json --work /Volumes/SSD/work   # render the plan as it is
```

**What the tool chooses.** About one scene per 4 seconds of the short. It cuts the film into that many parts of equal length and keeps one scene of each part: a pinned scene or one you named with `--prefer`, else a scene that has scene text or subtitles, else the longest. Each kept scene gets time in proportion to its length in the film, at least 2.0 seconds (the last scene at least 2.25 seconds, because of the 2 seconds of fade-out), at most 8 seconds; the times add up to the length you asked for, to the frame. Inside a scene it takes the middle, or the stretch that holds whole subtitles and does not cut through one. The short fades in over 1 second and out over 2, as the film does. A film that is shorter than the length you ask for becomes a short that is the whole film.

**The window.** The 9:16 window is cut from the full-size picture: 29.7 percent of the width of a 4096 x 2160 clip and 23.6 percent of a 4096 x 1720 clip (the wide ones, which the short enlarges by 11.6 percent). It is centred unless a scene sets it with `scenes crop`. There is no subject tracking: you or Claude Code look at `scenes crop SCENE --still` (a picture of a frame with the window outlined, and what the window shows) and choose the place. A short has no black bars, because it cuts the window out of the picture instead of fitting the picture into the frame.

**Subtitles and titles.** The subtitles of the film that fall in a kept stretch come along, laid out again for the 9:16 frame: two lines of about 24 characters, a long subtitle split into several, all inside the safe area (top 15, bottom 35, left 4, right 18 percent), in the same size as always. A subtitle that is cut stays only if at least 1 second, or 0.8 of it, whichever is more, stays. A title of the film goes to the scene that held its start, if that stretch is kept. The `lower_third` title sits above the bottom 35 percent too. The form of the subtitles is a setting of the export (`burned` for a short; `--subtitles srt|burned|both|none` changes it). `--lang LANG` renders the short with its subtitles in a translated language (`short-60.ja.mp4`; the plan stays English; see the section on subtitles).

**Files.** `short-60.mp4`, the plan `short-60.json` next to it (a timeline file of kind `short`: edit it, validate it with `timeline validate`, render it again with `short --file short-60.json`), `short-60.credit.txt` when the LUT asks for a credit, and with `--stills` one still per scene in `short-60.stills/` and the contact sheet `short-60.contact.jpg`. The film file is not changed; only `scenes pin` and `scenes crop` write to it, on request. Planning again over an existing plan keeps the old plan as a snapshot (`timeline undo --file short-60.json`). `--detach` renders in the background with a log. `SPECS.md` section 11 has the rules and the run on the first shoot.

## UI workflow

1. **Match** - pick the video folder and the audio folder, click Match. The result list shows each pair, its offset and a confidence value (green high, red low - check red ones by hand).
2. **Select & Order** - click thumbnails to add scenes to the timeline, drag to reorder, set optional trim values (seconds cut from the start and from the end of the clip). Save the selection.
3. **Film** - the film from the timeline file: preview, trims, look, loudness, subtitles, titles, exports.

Only one job (match, assemble, preview, render, subtitles) runs at a time. State is saved to `project.json`, so the app resumes where you left off.

## CLI

Every step also works without the browser. The global `--project/-p FILE` option (default `project.json`) goes before the subcommand.

```bash
python3 main.py                       # same as: app
python3 main.py app [--port 8765]
python3 main.py match --video DIR --audio DIR
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
python3 main.py scenes pin|unpin ID[,ID...] [--dry-run] [--file timeline.json]            # step 6
python3 main.py scenes crop SCENE (--x X | --from A --to B | --clear) [--still] [--at S] [--dry-run]
python3 main.py short [--file timeline.json] [--length S] [--output FILE.mp4] [--size WxH] [--prefer|--only|--exclude IDS] [--lang LANG] [--dry-run] [--no-render] [--stills] [--detach]
```

- `match` writes `project.json` and the thumbnails.
- `convert` makes one small preview copy (H.264 MP4, half size, the base LUT baked in, the camera audio) of every matched `.braw` clip in `DIR/copies/` and records what it did in `DIR/conversion.json`. `DIR` is a folder on the SSD (or `work_dir` in `project.json`); it must not lie inside a folder that holds the originals, which are only read. Clips whose copy is current are skipped, so an interrupted run resumes. The base LUT is read from Resolve's LUT folder (`VET_BASE_LUT` or `--lut` name another file) and is never copied into the repository. Run it with `.venv/bin/python`; it needs VapourSynth with `vapoursynth-brawsource` (see `requirements.txt`).
- `analyse --clap` finds the sync clap at the start of each matched clip: a sound that the camera's scratch audio and the recorder's WAV both heard at the stored offset. It writes `analysis/<clip>/clap.json` and the report `reports/clap-report.txt` and `.json` in `DIR`, and marks clips for review (no clap, a clap that only one microphone heard, a late or weak clap, an offset outside the usual range). The originals are only read.
- `analyse --drift` measures the clock drift between camera and recorder: the offset again on the last minute of every clip of 120 s or more, against the offset from matching. It writes `analysis/<clip>/drift.json` and the report `reports/drift-report.txt` and `.json` in `DIR`, and marks every clip whose drift is more than half a frame. The originals are only read.
- `timeline validate` checks the timeline file (`timeline.json`, format in `SPECS.md` section 3) against its rules and prints the findings as JSON; exit code 1 when there are errors. A valid file that differs from the newest snapshot is kept as a snapshot in `timeline.history/` (the number is printed on standard error).
- `timeline history` lists the versions of the timeline file with their time and what changed; `timeline undo` goes back to the previous version (the version it replaces is kept in `timeline.history/undone/`). Several timeline files in one folder share one `timeline.history` folder, and each snapshot has a record `NNNN.owner.json` with the file it was made from. `history` and `undo` use only the snapshots of the file named by `--file`, so an undo of one file never writes another file's film into it. `history` also says how many snapshots of other files the folder holds. Snapshots made before the records existed belong to no file: `history` lists them apart (with the film title and the number of segments in each), no command restores them, and to use one you copy it by hand to a file name of your own.
- `timeline frames` writes PNG stills of the film at film times (`--film-s`) or of a clip at clip times (`--source CLIP --at`) into `DIR/stills/` and prints the paths. It reads the preview copies.
- `rename` is a legacy helper that renames both files of each pair to `scene_NN.*`. It refuses to run if a target name already exists, and `--undo` reverses it from `rename_log.json`. The normal flow does not need it.

## Offset sign

`offset = t_audio - t_video`. A positive offset means the same event occurs later in the audio file than in the video, i.e. the recorder was started before the camera. In the render the sound of a segment is read from the recorder's file starting at the in-point plus the offset. A negative offset places the sound that much later in the segment.

## Files

- `project.json` is written to the current working directory (path configurable with `--project`).
- Thumbnails go to a `thumbnails/` folder next to `project.json`.

## Audio copies of earlier versions

Version 1.1 copied the recorder's files next to the videos (`IMG_2231.MOV` and `IMG_2231.WAV`) and listed them in `ambient_audio_copies.json`. The command that did this and the command that undid it were removed. Copies made earlier stay where they are; delete the files listed in `ambient_audio_copies.json` by hand if you do not want them. The originals are never touched.

## Camera formats

`.braw` (Blackmagic RAW) files are supported for matching, sync and the film: ffprobe reads them as QuickTime containers and ffmpeg extracts their PCM audio. ffmpeg cannot decode the BRAW video, so **no thumbnail** can be generated for them; the UI shows a "No thumbnail" placeholder and everything else works. Video and audio may live in the same folder.

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
