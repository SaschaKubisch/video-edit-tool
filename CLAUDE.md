# Working in this repository

Ambient Video Automation: a local tool that turns BMPCC 4K clips (BRAW) and Zoom H2N recordings (WAV) into a film. `PRD.md` says what it is for, `SPECS.md` how it is built, `README.md` how to run it.

- Run the tool with `.venv/bin/python main.py ...`. Run the tests with `.venv/bin/python -m unittest discover -s tests`.
- The originals (`.braw`, WAV) are read-only. Everything the tool makes goes to the work folder (`--work DIR`, or `work_dir` in `project.json`). Do not put large files in git.
- The film is one file, the timeline file (`timeline.json`, format in `SPECS.md` section 3). Commands take `--file FILE` (default: `timeline.json` next to `project.json`).
- No emojis in files, commands or messages.

## Editing the film from a request

The owner describes a change ("cut out the scene where I am frying a sausage"). A **scene** is one segment of the timeline file; its id (`s017`) is what the commands take. **Film time** counts from the first frame of the film and is what the owner and the commands use. You cannot see the film. The tool writes what you can read: an index of every scene and one still sheet per scene.

How the owner's words are read (decision D14, settled by the owner on 2026-10-27): "cut out", "remove", "delete" a scene means `scenes remove`; "keep only" means `scenes keep`; "cut out the part where ..." or a stretch of seconds inside a scene means `scenes cut`; "put back" means `scenes restore`. Say which reading you used.

Do this, in this order:

1. **Prepare.** `python main.py scenes index --sheets --file FILE --work DIR`. Sheets that are up to date are kept, so this is quick when nothing changed. Run it again after every change to the film: the film times after a removed scene move.
2. **Read.** Open `DIR/index/<timeline name>/index.md` (search it for the words of the request) and look at the pictures:
   - the overview pages `overview-01.jpg` ... show one still of the middle of each of 12 scenes, with scene id and film time: use them to find candidates cheaply;
   - the sheet of a candidate (`sheets/sNNN.jpg`, path in the index) shows 3, 6 or 9 stills across the scene, each labelled with scene id and film time;
   - `scenes frames SCENE --every 1s [--from S --to S]` gives a still every second inside one scene, to find where something starts and ends.
   The "heard" lines are what Whisper made of the sound; on ambient footage they are mostly empty, wrong or invented, so the pictures carry the search. `subtitles search WORDS --all` also searches the phrases the filter dropped. `index.md` is long (about 10 lines a scene): search it (`grep -n -i WORD index.md`) instead of reading it whole. An overview page costs about 1.5k tokens and a sheet about 2k, so look at all overview pages first and open only the sheets of candidates.
3. **Say what you found.** List each candidate: scene id, film time, clip, what you saw in which still or heard in which phrase, and how sure you are. Say what you could not see. If the request names an action (saws, fries, blends) and the stills show another one (sands, bakes), say what they show instead of matching the words. A still cannot show whether a machine is switched on or what a scene sounds like. If nothing matches, say so; do not name the nearest scene as a match. Do not edit yet.
4. **Ask the owner to confirm** which scenes or which seconds.
5. **Dry run.** `python main.py scenes remove ID[,ID...] --dry-run --file FILE` (or `keep`, `restore`, `cut ID --from S --to S`, film seconds). Read the output: scenes leaving the film, film length before and after, subtitles and titles affected, new warnings. Tell the owner what it says.
6. **Do it.** The same command without `--dry-run`. It validates and keeps the old file as one snapshot.
7. **Check.** `python main.py timeline validate --file FILE` (exit 0). `python main.py scenes index --file FILE --work DIR` again. Tell the owner the new film length and the snapshot number.
8. **Undo.** `python main.py timeline undo --file FILE` restores the version before the last change of FILE (each timeline file has its own snapshots, even when the folder holds several). `scenes restore ID` puts one removed scene back and leaves later changes alone.

For `scenes cut`:
- Look at the stills at both edges first (`scenes frames SCENE --every 0.5s --from S --to S`). Stay about 0.5 s away from speech and do not cut through a sound event: the sound crossfades over half a second at the new join, so it takes in a quarter second on each side of the cut.
- A cut that touches the first or the last frame of a scene shortens the scene. A cut of a whole scene is refused: use `scenes remove`.
- The first part keeps the id. The second part is a new scene (`origin` `prompt`). Subtitles go with their picture; the scene's text box stays with the first part.

Rules for every edit of the timeline file:
- Use the `scenes` commands for removing, keeping, restoring and cutting. They check the file and keep a snapshot. Do not edit the JSON by hand for these.
- If you edit the file by hand anyway: read it again first, change it in place with small edits, never rewrite it from memory, never change an existing segment's `id`, `version` or `kind`, then run `timeline validate` once.
- One request is one change and one snapshot. Do not change anything the owner did not ask for.
- Titles are placed by film time. After a change that moves film times, say which titles may now be over other scenes (the dry run lists them).
- Do not start Shotcut or open a browser for this.

## Making a vertical short from a request

The owner says "make a 30 second short of the salad" or "a short of the whole day". A **short** is a 9:16 video for phones, cut from the film. The tool plans it, renders it from the `.braw` files and writes the plan next to the file. The film is not changed. If the owner names no length, the short is 60 seconds (decision D15). Do this, in this order:

1. **Prepare and find the scenes.** As in the section above: `python main.py scenes index --sheets --file FILE --work DIR`, then read the index and look at the overview pages and the sheets. If the request names a subject ("of the salad"), list the scenes that show it, with scene id, film time and how sure you are, and say what you could not see. If it names no subject, you need no list.
2. **Ask the owner to confirm** the scenes and the length. Say how the choice works: `short` keeps about one scene per 4 seconds, takes one scene from each equal part of the film (the longest one, unless a scene has scene text or subtitles, or is pinned or named), gives each scene time in proportion to its length (at least 2 seconds, at most 8) and takes the middle of the scene or the stretch that holds whole subtitles.
3. **Tell the tool the subject.** For this short only: `--only ID,ID,...` makes the short from those scenes alone; `--prefer ID,ID,...` keeps those scenes and fills the rest from the whole film; `--exclude ID,ID,...` leaves scenes out. For every later short too: `scenes pin ID[,ID...]` (take it off with `scenes unpin`); a pinned scene is always kept. Say which you used.
4. **Dry run.** `python main.py short --file FILE --work DIR --length SECONDS --only IDS --dry-run`. Read the table: the kept scenes with film time and short time, the length (exact to the frame), the subtitles that stay and go, the notes and warnings. Tell the owner what it says. Nothing is written.
5. **Look at the window.** The 9:16 window shows 23.6 percent of the width of a wide clip (4096 x 1720) and 29.7 percent of the other clips (4096 x 2160), and it is centred unless a scene says otherwise. For each kept scene run `python main.py scenes crop SCENE --still --file FILE --work DIR` and look at the picture it prints: the yellow outline is the window, the right side is what the short shows. If the subject is not inside, choose a place with `--x X` (0 is the left edge, 0.5 the centre, 1 the right edge); try places with `--x X --still --dry-run` first, and save the one that works with `--x X`. For a subject that moves use `--from A --to B`. `scenes crop SCENE --clear` goes back to the centre. The crop is stored in the film, so every later short uses it. There is no subject tracking: you choose the place by looking.
6. **Render.** `python main.py short --file FILE --work DIR --length SECONDS --only IDS --stills`. A short of 60 seconds takes about 3 minutes; for a longer one add `--detach` and watch the log. The tool writes the MP4, `short-N.json` (the plan) and, with `--stills`, one still per scene and `short-N.contact.jpg`. Look at the contact sheet and say what you see: whether the subject is in each picture, whether a black bar shows, whether the subtitles are readable. Give the owner the path of the MP4 and the plan.
7. **Change it.** To change a scene's crop, use `scenes crop` and run `short` again (the old plan is kept as a snapshot). To change one thing in a short that is planned, edit the plan file or use `scenes` and `subtitles` commands on it, then `python main.py short --file PLAN` renders it as it is.

Rules for shorts:
- The short is a derived file. Never write to the film to make a short, except the crop and pin marks above, which the owner asked for by asking for a short.
- Titles and subtitles of the film come along when they fall in a kept stretch; a subtitle that is cut stays only if most of it stays. The text is laid out again for the narrow frame and stays out of the bottom 35 percent, where the platforms show their buttons. Say which subtitles were dropped.
- If a render fails, say so with the message of the tool. Do not start a second render while one runs.
