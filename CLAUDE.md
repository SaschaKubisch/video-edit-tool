# Working in this repository

Ambient Video Automation: a local tool that turns BMPCC 4K clips (BRAW) and Zoom H2N recordings (WAV) into a film. `PRD.md` says what it is for, `SPECS.md` how it is built, `README.md` how to run it.

- Run the tool with `.venv/bin/python main.py ...`. Run the tests with `.venv/bin/python -m unittest discover -s tests`.
- The originals (`.braw`, WAV) are read-only. Everything the tool makes goes to the work folder (`--work DIR`, or `work_dir` in `project.json`). Do not put large files in git.
- The film is one file, the timeline file (`timeline.json`, format in `SPECS.md` section 3). Commands take `--file FILE` (default: `timeline.json` next to `project.json`).
- No emojis in files, commands or messages.

## Editing the film from a request

The owner describes a change ("cut out the scene where I am frying a sausage"). A **scene** is one segment of the timeline file; its id (`s017`) is what the commands take. **Film time** counts from the first frame of the film and is what the owner and the commands use. You cannot see the film. The tool writes what you can read: an index of every scene and one still sheet per scene.

How the owner's words are read until the owner decides otherwise (open decision D14): "cut out", "remove", "delete" a scene means `scenes remove`; "keep only" means `scenes keep`; "cut out the part where ..." or a stretch of seconds inside a scene means `scenes cut`; "put back" means `scenes restore`. Say which reading you used.

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
