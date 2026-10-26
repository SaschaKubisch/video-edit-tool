# Product Requirements Document - Ambient Video Auto

**Version:** 2.0 (draft for the owner's review)
**Date:** 2026-10-26
**Owner:** Sascha Kubisch
**Status:** Matching and sync (requirements M1 to M6) are built and released as v1.1.0. Everything else in this document is planned and not built. Section 2 replaces the goal of v1.1.

Companion document: `SPECS.md` (how it is built, the timeline file format, the tests).

---

## Terms

- **BMPCC 4K**: Blackmagic Pocket Cinema Camera 4K, the camera.
- **BRAW**: Blackmagic RAW, the camera's file format (`.braw`). ffmpeg, the common command-line video tool, cannot decode its pictures.
- **Zoom H2N**: the audio recorder. It writes WAV files, plain uncompressed audio.
- **Clip**: one recording of the camera together with the audio file matched to it. Version 1.1 and its code call this a "scene". This document says clip.
- **Scene**: a stretch of a clip in which the camera does not move. One clip can hold several scenes.
- **Segment**: one piece of a clip placed in the film, with a start (in-point) and an end (out-point).
- **Film**: the finished video that the tool builds from the segments.
- **Timeline file**: the one JSON file that describes the film: which segments in which order, fades, colour, loudness, titles, subtitles, export settings. Defined in `SPECS.md`.
- **Sync clap**: the clap made at the start of a recording so picture and sound can be aligned.
- **Offset**: the number of seconds by which the same event comes later in the audio file than in the video. offset = t_audio - t_video.
- **ProRes**: Apple's editing video format. It stores every frame whole, so a cut at any frame is exact. "10-bit" means 1024 levels per colour channel.
- **Working copy**: the ProRes file the tool makes once from each `.braw` clip. Analysis, preview and render read the working copies.
- **Decoder**: software that turns BRAW data into pictures.
- **SSD**: the external drive that holds the working copies and everything else the tool makes.
- **LUT**: look-up table, a file that maps each input colour to an output colour. The common text form is `.cube`. "Tetrahedral interpolation" is the method that computes colours between the table's entries.
- **Blackmagic Film**: the flat colour curve and colour range the camera records. The **base conversion** turns it into normal display colours (Rec.709, the standard colour space for HD video).
- **LUFS**: the unit of perceived loudness. **LU** is a difference of one LUFS. **dBTP** is the true-peak level, the highest level of the reconstructed sound wave.
- **Fade**: a change of picture or sound from or to nothing. **Crossfade**: one sound fades out while the next fades in at the same time.
- **Shotcut**: a free video editor. Its project file is MLT XML. MLT is the video engine inside it.
- **Whisper**: a speech-to-text model. **SRT**: a plain subtitle file.
- **Vertical short**: a 9:16 video for phones, cut from the 16:9 film.
- **Claude Code**: the AI agent the owner uses in the terminal. It edits files and runs commands.
- **Step**: one of the six parts of the work in section 7.

Tags on facts. **[measured]**: checked on the owner's machine or files on 2026-10-26. **[documented]**: a source states it; the sources are in `SPECS.md`, Appendix B (written Src1, Src2 and so on). **[inference]**: reasoning that was not checked. **[unverified]**: a design statement that only a test can confirm. A statement with no tag is a requirement or a definition.

---

## 1. Problem Statement

Ambient and cooking films are shot dual-system. The BMPCC 4K records BRAW video with scratch audio, a low-quality sound track used only for alignment. The Zoom H2N records the real sound as WAV. The first shoot gave 105 matched clips of 3.5 to 271 seconds, 73 minutes in total [measured, from the owner's `project.json`, which is not committed]. The two sets of files share no names and no timecode.

Version 1.1.0 pairs the files, finds each offset, copies each WAV next to its video under the video's name, and can push an ordered timeline into DaVinci Resolve. All later work is manual in Resolve and is repeated for every film:

- cut off the sync clap,
- find the part of each clip in which the camera stands still,
- order the clips,
- fade, grade, level the sound,
- add titles and subtitles,
- export.

Resolve reads BRAW without conversion. No open-source editor and no ffmpeg build does [documented, Src4]. Leaving Resolve therefore starts with turning BRAW into a format other tools read.

## 2. Goal

**Owner decision:** The owner no longer needs DaVinci Resolve for these films. After matching video and audio, the tool edits the film itself.

The tool does all automatic work on one timeline file. It renders a preview and the final film itself. It writes a Shotcut project for rare hand work, done last.

## 3. Settled Decisions (Owner)

These decisions are settled. This document builds on them and does not reopen them. Each is also a row in section 12 or a requirement in section 9.

| Area | Decision |
|------|----------|
| Editing | The tool does all automatic work on one timeline file (JSON) that Claude Code can edit. The tool renders a preview and the final film itself. The tool's web page gets a small preview with simple trim controls. The tool also writes a Shotcut project (MLT XML) for rare hand work, done last, because Shotcut edits do not flow back. |
| Footage | All clips are BRAW from a BMPCC 4K. Audio comes from a Zoom H2N as WAV. ffmpeg cannot decode BRAW video. Every clip is converted once to 10-bit ProRes (4K) on an external SSD. Analysis, preview and render use these copies. |
| Decoder | Decoding goes through `vapoursynth-brawsource` (a plugin for the VapourSynth video framework, using the Apple GPU through Metal) or through the Blackmagic RAW SDK. The conversion test on a real clip is step 1, and its result may change the decoder choice. |
| Delivery | 4K films. Vertical 9:16 shorts are cut from the 4K source. |
| Colour | One LUT for the whole film, applied after the base conversion from Blackmagic Film. The LUT is chosen from side-by-side stills of real clips. Interpolation is tetrahedral. LUT files from the owner's machine are not committed to the repository. |
| Speech | English. This covers subtitles and transcript search. |
| Installs allowed | `ffmpeg-full` (Homebrew, GPL, keg-only, installed beside the existing ffmpeg), VapourSynth with the BRAW plugin in the tool's own Python environment, and Shotcut. |
| Originals | The tool never modifies or deletes the `.braw` and WAV originals. (Kept from v1.1.) |

## 4. Users

One user, the filmmaker, running the tool locally on macOS with Apple Silicon. A second operator is Claude Code, which edits the timeline file and runs the tool's commands at the user's request. No multi-user, login or cloud requirements.

## 5. Facts About the Footage and the Machine

| Fact | Tag |
|------|-----|
| The owner's camera is a BMPCC 4K; all clips are BRAW; audio comes from a Zoom H2N. | Owner statement |
| The first shoot has 105 matched clips of 3.5 s to 271.1 s, 73.1 minutes in total. | [measured] |
| One checked clip (from a later shoot) is 4128 x 2176 pixels at 25 frames per second, with PCM scratch audio (24-bit, 48 kHz, stereo) and two timed-metadata tracks of about 1025 samples per second each. What the metadata tracks hold was not read. Other clips were not checked. | [measured] |
| One checked H2N file is stereo, 24-bit, 96 kHz WAV. | [measured] |
| In the first shoot, 96 of 105 offsets lie between 0.09 and 0.37 s. Nine lie outside: -5.03, -1.70, 0.63, 0.64, 0.66, 0.83, 0.89, 1.28 and 1.94 s. Whether these nine are correct was not checked. | [measured] |
| The volume that holds the originals has 270 GiB free of 932 GiB. It holds the folders of three shoots. The owner has not said whether it is the SSD for working copies. | [measured] |
| The internal disk has 8.3 GB free of 228 GB (96 percent full). | [measured] |
| Apple M4, 16 GB RAM, macOS 26. Python 3.9.6 is the system Python; Python 3.12 is installed with Homebrew; `uv` is installed. | [measured] |
| The installed ffmpeg 8.1 has no libass, no freetype and no zscale. It cannot draw text or burn subtitles. `ffmpeg-full` has them and can run Whisper. | [measured] |
| DaVinci Resolve 18.6.3 (free edition) is installed. | [measured] |
| ffmpeg has no BRAW decoder. | [measured] |
| ProRes 422 LT at 4096 x 2160 and 25 frames per second needs about 164 GB per hour; ProRes 422 needs 236 GB and 422 HQ needs 354 GB. For 73 minutes at 4128 x 2176, that is about 203 GB (LT), 292 GB (422) and 437 GB (HQ). | [documented, Src10], arithmetic mine |

## 6. Core User Journey

1. The user starts the app (`python3 main.py`). A browser tab opens at `localhost:8765`.
2. **Match.** The user picks the folders with the video and the audio and clicks Match. The tool pairs each clip with its audio and finds the offset. (Built.)
3. **Convert.** The tool converts every clip once into a working copy on the SSD. The page shows progress. A converted clip is not converted again. The time this takes is measured in step 1.
4. **Assemble.** The tool cuts the clap off each clip, finds the steady scenes, drops the camera movement, puts the scenes in recording order, adds the fades, writes the timeline file and renders a preview. The user watches the preview in the page and trims with simple controls.
5. **Look and sound.** The tool shows side-by-side stills of real clips with candidate LUTs. The user picks one. The tool levels the loudness and exports the 4K film.
6. **Words.** The tool writes English subtitles from the speech. The user adds titles by hand or asks Claude Code for them.
7. **Prompt edits.** The user tells Claude Code what to change ("cut out the scene where I am frying a sausage"). Claude Code edits the timeline file, the tool checks it, and the preview shows the result.
8. **Shotcut, last.** For rare hand work the tool writes a Shotcut project. Edits made there do not come back.
9. **Shorts.** The user asks for a vertical short of a given length. The tool builds a second timeline file from the film.

## 7. Steps (Order of Work)

Each step is usable on its own. A later step starts only when the step before it works.

| Step | Name | What the owner can do when it is done | Needed before it starts |
|------|------|---------------------------------------|-------------------------|
| 1 | Conversion test | See how long one real clip takes to convert, how large the copy is and whether its colour is right. Open the copies in any editor. | One real `.braw` clip; D1 (SSD), D2 (colour), D3 (Python and packages) |
| 2 | Timeline file and assembly | Get a rough film in recording order, without clap and camera movement, with audio crossfades and film fades. Watch it in the page and trim it. | Step 1 result; D5 (frame shape), D6 (clap), D7 (steady scene), D8 (fades) |
| 3 | Look and sound | Choose a LUT from stills. Get a levelled, graded 4K film in MP4. Get the Shotcut project. | D9 (loudness), D10 (LUT sources), D11 (Shotcut audio) |
| 4 | Words | Get English subtitles and titles, burned in or as a file. | D12 (Whisper model), D13 (subtitle form and fonts) |
| 5 | Prompt edits | Change the film by describing scenes to Claude Code. | D14 (meaning of "cut out", search method) |
| 6 | Vertical shorts | Get a 9:16 short of a chosen length from the 16:9 film. | D15 (default length), D16 (crop and importance) |

D4 (the Resolve option) can be answered at any time. The open decisions D1 to D16 are in section 14.

## 8. Scope

**In scope**

- Matching and sync of camera and recorder files (built).
- One-time conversion of BRAW clips to working copies.
- The timeline file, automatic assembly, preview, final render, export.
- A web page with a preview player and simple trim controls.
- A Shotcut project written from the timeline file.
- English transcript, subtitles and titles.
- Scene search by description through Claude Code.
- Vertical shorts.

**Out of scope**

- A full timeline editor in the browser. The page has playback and simple trim controls only.
- Reading Shotcut edits back into the timeline file.
- Correcting camera movement. The owner films static; movement is cut out, not corrected.
- Grading beyond one LUT per film: no per-clip correction, no scopes.
- Music, sound effects, voice recording.
- Speech in languages other than English.
- HDR output. Output is SDR Rec.709 [inference: the Blackmagic Film-to-Video conversions target it].
- Several cameras or several recorders. Windows and Linux.
- Upload to YouTube or any other service. Any use of cloud services at run time.
- Transitions between pictures other than a hard cut, until D8 is answered.

## 9. Functional Requirements

### 9.1 Owner requests and where they are met

The wording is the owner's, as recorded.

| Owner request | Requirements | Step |
|---------------|--------------|------|
| Automatic timeline in order | M7, A1 | 2 |
| Cut out the sync clap from each clip and its audio | A2 | 2 |
| Detect the most stable scene and cut out camera movement (the owner films static) | A3 | 2 |
| Split a clip with several static scenes into one clip per scene | A4 | 2 |
| Smooth audio fade out and in between scenes | A5 | 2 |
| Apply state-of-the-art LUTs | L1, L2, L3 | 3 |
| Levelise all audio loudness | L4, L5 | 3 |
| Titles and subtitles manually or automatically via a prompt | W1 to W5 | 4 |
| Prompt-driven edits via Claude Code that find scenes by description ("please cut out the scene where I am frying a sausage") | P1 to P4 | 5 |
| Fade in video and audio at the start and fade out at the end of the film | A6 | 2 |
| Save the project | T4 | 2 |
| Export to the most common video format at a custom resolution | E3 | 3 |
| Automatically convert a 16:9 film with subtitles into a vertical short of a desired length, shortening scenes while keeping the most important moments, and scaling subtitles to fit | S1 to S5 | 6 |

### 9.2 Requirements

Priority: Must, Should, Could. "Built" means released in v1.1.0.

**M - Match and sync (kept from v1.1)**

| ID | Requirement | Priority | State |
|----|-------------|----------|-------|
| M1 | Discover video (`.mov .mp4 .mkv .avi .mxf .braw`) and audio (`.wav .flac .aiff`) files in two chosen folders. (Old F1.) | Must | Built |
| M2 | Match each video to exactly one audio file by duration, solved globally and in recording order. Files may be missing on either side. (Old F2, F15.) | Must | Built |
| M3 | When several audio files have near-identical durations, decide by waveform correlation. (Old F3.) | Must | Built |
| M4 | Compute and store one sync offset per pair. (Old F4.) | Must | Built |
| M5 | Save matching state to `project.json` and resume from it. (Old F10.) | Must | Built |
| M6 | Browser page to choose folders, run matching and show the result. (Part of old F6.) | Must | Built |
| M7 | Choose which clips go into the film and in which order. The default is all clips in recording order. (Old F6, F7; trims move into the timeline file.) | Should | Step 2 |
| M8 | One thumbnail per clip, taken from its working copy. (Old F5; `.braw` had no thumbnail.) | Should | Step 1 |
| M9 | Never modify or delete the `.braw` and WAV originals. Derived files go to the work directory on the SSD. The only exception is the audio copy of R1, if D4 keeps it. | Must | Step 1 |
| M10 | A command-line subcommand for every step. (Old F13.) | Should | All |

**C - Convert (step 1)**

| ID | Requirement | Priority |
|----|-------------|----------|
| C1 | Convert each `.braw` clip once to a 10-bit ProRes working copy at the clip's full frame size on the SSD. | Must |
| C2 | Skip clips whose working copy is complete. Resume after an interrupted run. A copy counts as complete only after it passed the check in C5. | Must |
| C3 | Store per clip: frame size, frame rate, frame count, start timecode, colour science generation, and the colour state the copy holds. | Must |
| C4 | Report the speed (frames per second), the file size and the elapsed time for each clip and in total. | Must |
| C5 | Check each copy after writing: codec, pixel format, frame size and frame count equal what the source says. | Must |
| C6 | Refuse to start when the SSD is missing or has less free space than the estimate plus a margin. The estimate uses the measured size per frame of the first converted clip. | Must |
| C7 | Support a second decoder behind the same interface, so step 1 can change the decoder choice. | Should |

**T - Timeline file (step 2)**

| ID | Requirement | Priority |
|----|-------------|----------|
| T1 | One timeline file (JSON) is the single source of truth for the film. All automatic work writes to it. | Must |
| T2 | The file has a version field. The tool refuses a version it does not know. | Must |
| T3 | A validator checks the file and names the exact field of every error. | Must |
| T4 | Saving is atomic and keeps numbered earlier versions, so any change can be undone. This is the "save the project" request. | Must |
| T5 | Claude Code can read and edit the file directly. The tool offers commands that print a summary, validate, and show frames. | Must |
| T6 | A change made on the page and a change made in the file at the same time never overwrite each other silently. | Must |

**A - Automatic assembly (step 2)**

| ID | Requirement | Priority |
|----|-------------|----------|
| A1 | Place the clips in recording order. | Must |
| A2 | Find the sync clap in each clip and cut it out of picture and sound. | Must |
| A3 | Find the stretches in which the camera does not move and cut out the movement. | Must |
| A4 | A clip with several steady scenes becomes one segment per scene. | Must |
| A5 | Between segments, the sound fades out and in with a crossfade of settable length. | Must |
| A6 | The film starts with a fade-in of picture and sound and ends with a fade-out of both. | Must |
| A7 | Write a report per clip: clap found or not, scenes found, what was left out and why. A clip needing the owner's attention is marked. | Must |

**L - Look and sound (step 3)**

| ID | Requirement | Priority |
|----|-------------|----------|
| L1 | Make side-by-side stills of the same frames of several real clips with several candidate LUTs, to choose one. | Must |
| L2 | Apply one LUT to the whole film with tetrahedral interpolation. | Must |
| L3 | Apply the base conversion from Blackmagic Film before the LUT, unless the working copies already hold converted colour (D2). | Must |
| L4 | Bring every segment to the same loudness, then bring the film to the target loudness. | Must |
| L5 | Keep the true peak of the film under the limit. | Must |

**E - Preview and export (steps 2 and 3)**

| ID | Requirement | Priority |
|----|-------------|----------|
| E1 | Render a small preview of the film or a time range from the timeline file. | Must |
| E2 | Render the final film from the working copies, never from the preview. | Must |
| E3 | Export to MP4 (H.264 video, AAC audio) at a custom resolution. | Must |
| E4 | Write a Shotcut project (MLT XML) from the timeline file. The export is one-way. | Must |
| E5 | Write a render report: what was shortened, capped or missing. | Should |

**U - Web page (steps 2 and 3)**

| ID | Requirement | Priority |
|----|-------------|----------|
| U1 | Play the preview in the page. | Must |
| U2 | Simple trim controls per segment: move the start or end by one frame or one second, remove a segment, restore it, reorder. | Must |
| U3 | Show the segment list with film time, length and label. | Should |

**W - Words (step 4)**

| ID | Requirement | Priority |
|----|-------------|----------|
| W1 | English transcript with times, from the H2N audio, only where speech is detected. | Must |
| W2 | Subtitles built from the transcript and mapped onto the segments. Text is editable by hand. | Must |
| W3 | Titles by hand (in the file) or by prompt (Claude Code writes them). | Must |
| W4 | Burn subtitles and titles into the film, and write an SRT file. Which of the two is used is D13. | Must |
| W5 | Search the transcript by words. | Should |

**P - Prompt edits (step 5)**

| ID | Requirement | Priority |
|----|-------------|----------|
| P1 | Build an index of every clip: labelled contact sheets (grids of stills with times) and the transcript. | Must |
| P2 | Claude Code finds scenes by description through the index and changes the timeline file. | Must |
| P3 | Commands give Claude Code feedback: validate, summary, frames at given times, preview of a range. | Must |
| P4 | Each prompt edit is one saved version of the timeline file and can be undone. | Must |

**S - Vertical shorts (step 6)**

| ID | Requirement | Priority |
|----|-------------|----------|
| S1 | Build a second timeline file for a 9:16 short from a 16:9 film and a target length. | Must |
| S2 | Shorten scenes to reach the length and keep the most important moments. | Must |
| S3 | Cut the 9:16 window from the 4K source, with a crop position per segment. | Must |
| S4 | Lay out the subtitles for the 9:16 frame and scale them to fit its safe area. | Must |
| S5 | Export the short to MP4 at a custom resolution (default 1080 x 1920). | Must |

**R - Resolve option (kept only if D4 says so)**

| ID | Requirement | Priority |
|----|-------------|----------|
| R1 | Copy the matched WAV next to its video under the video's name, with undo. (Old F8, F14.) | Could |
| R2 | Push the ordered timeline into DaVinci Resolve. (Old F11, F12.) | Could |

## 10. Non-Functional Requirements

- **Originals untouched.** The tool never modifies or deletes `.braw` and WAV originals (decision, kept from v1.1). It opens them read-only. It writes only into the work directory on the SSD, the timeline file, `project.json` and the export folders the user names.
- **Derived files are allowed.** v1.1 forbade any re-encoding. That rule is replaced: the tool makes working copies, previews and renders from the originals. Every derived file can be made again from the originals.
- **Recoverability.** Every change to the timeline file is saved as a numbered version. A working copy that is lost or damaged is made again by the convert step.
- **Local only.** At run time the tool makes no network calls apart from `localhost`. One-time setup (Homebrew packages, the BRAW plugin, the Whisper model) is done by the owner and listed in `SPECS.md`.
- **Frame and sample accuracy.** Cuts fall on exact frames. Audio is placed to the sample. Picture and sound in the finished film stay aligned to the precision of the matched offset (about 1 ms, [measured] in v1.1) plus rounding to one audio sample.
- **Transparency.** Every automatic decision (clap position, scene boundaries, gains, shortened crossfades) is written to a report or into the timeline file, so the user can audit it.
- **Disk.** Nothing large goes to the internal disk. Generated files go to the SSD.
- **Speed.** No time limits are set before they are measured. Steps 1 and 2 record the times; the owner then sets limits.
- **Dependencies.** `numpy`, `scipy`, `tqdm`, `vapoursynth`, the BRAW plugin and `ffmpeg-full`. The convert step needs Python 3.12 or newer [documented, Src1]; what that means for the rest of the tool is D3. Any other package needs the owner's approval first (D3). `ffmpeg-full` is GPL; the tool runs it as a separate program and does not bundle it [inference: this keeps the tool's MIT licence unaffected; not legal advice].
- **Public repository.** No footage, no LUT files, no Blackmagic runtime and no paths from the owner's machine go into the repository. `project.json` and the timeline file are listed in `.gitignore`.
- **Security.** The server listens on `127.0.0.1` only and keeps the Host and Origin checks of v1.1. The new file-serving endpoints serve only files inside the work directory.

## 11. Success Metrics

- Every clip of the owner's first shoot has a working copy that passes check C5.
- Matching stays correct: all pairs of a typical shoot are matched as in v1.1.
- The sha256 hashes of all originals are the same before and after any run of the tool.
- The film's integrated loudness is within 0.5 LU of the target, and its true peak is at or under -1 dBTP [starting values, to be confirmed by the owner].
- In a test film with a known marker, picture and sound stay within 1 ms (`SPECS.md`, check 2.10).
- Times for conversion, assembly and render are measured and recorded in steps 1 to 3. No limit is set before that.
- **Goal check by the owner:** the next film is finished without opening DaVinci Resolve.

## 12. Key Decisions and Rationale

"Owner" marks a decision the owner settled. "Design" marks a choice made in the specification that the owner can change. "Kept" marks a row that still holds from v1.1.

| Decision | Status | Alternative considered | Why |
|----------|--------|------------------------|-----|
| The tool edits the film; Resolve is not used for these films. | Owner | Keep Resolve; buy Resolve Studio with its built-in Claude connection. | The free Resolve cannot be driven from outside, and version 21.1 moved Python scripting to Studio [documented, Src8; reports differ on what the free edition keeps]. Studio's price was not checked. Studio 21.1 can connect to Claude Code [documented, Src8]; it is the paid alternative. |
| Convert every clip once to 10-bit ProRes at full size on an external SSD. | Owner | Edit on small proxies and re-decode BRAW at the end; convert on demand. | ffmpeg cannot decode BRAW [measured]. ProRes holds whole frames, so cuts are exact and decoding is fast [inference]. Cost: large files (section 5). |
| Decode with `vapoursynth-brawsource`; the Blackmagic RAW SDK is the alternative. Step 1 decides. | Owner | Write an SDK wrapper first; buy BRAW Toolbox. | The plugin is MIT, one person's project with a first release in 2026 [documented, Src1]. A real clip tests it before anything depends on it. |
| One timeline file (JSON) holds the edit. | Owner | A browser timeline editor (months of work [inference]); a native Swift app (more [inference]); a generated Shotcut project as the main file. | An agent edits a small JSON file more reliably than MLT XML [inference]. Shotcut and Kdenlive project files are not stable interfaces [documented, Src6, Src7]. |
| The tool renders preview and film itself. | Owner | Render with Shotcut's engine (`melt`). | Owner decision. The specification builds on `ffmpeg-full` (Design). |
| A Shotcut project is written last and never read back. | Owner | Kdenlive; a reader for Shotcut edits. | Reading edits back needs a reader for MLT XML [inference]. |
| One LUT for the whole film, chosen from side-by-side stills; tetrahedral interpolation. | Owner | Per-clip grading. | Fits static ambient films; one choice, easy to repeat. |
| 4K films; shorts cut from the 4K source. | Owner | 1080p. | A 9:16 window of a 1080p frame is 608 pixels wide and would be enlarged to 1080 [arithmetic]. |
| English speech only. | Owner | Several languages. | Fixes the Whisper model class and the subtitle rules. |
| Duration-first matching with an order-preserving alignment. | Kept | Pure audio correlation of all pairs. | Camera microphone against H2N gave weak scores (8 to 18 percent) and many extractions; durations are cheap and near-identical for pairs [reported in the v1.1 PRD]. |
| Correlation only as tiebreaker and for the offset. | Kept | Always correlate. | Keeps the common case fast. |
| A single-file browser page on `http.server`, plain JavaScript. | Kept | Electron, Tkinter, Streamlit. | No extra dependency, easy to inspect. It now also plays video. |
| The timeline refers to the original WAV with an offset. No copy of the WAV is made for the tool's own render. | Design | Copy and rename the WAV next to the video (v1.1). | The copy existed to feed Resolve's auto-sync by filename. Kept only if D4 keeps the Resolve option. |
| Analysis uses `numpy` and `scipy`, no OpenCV. | Design | OpenCV, PySceneDetect. | No new packages without approval (D3). A prototype on synthetic video separated still from moving frames with `numpy` only [measured, synthetic]. |
| Replaced rows from v1.1: "Resolve script run from inside Resolve", "Copy and rename audio instead of muxing", "Output defaults to the video folder", "No re-encoding". | Replaced | - | Their reasons applied to the Resolve workflow. |

## 13. Risks

| Risk | What it depends on | Handling |
|------|--------------------|----------|
| The BRAW decode path rests on a young one-person plugin (first release 2026, one GitHub star [measured]). | Step 1 | Step 1 tests it on real clips first. The SDK is the second decoder (C7). |
| No decode or encode speed is known for real clips on this machine. All timings are estimates [inference]. | Step 1 | Step 1 measures and reports. |
| Disk space. The internal disk is 96 percent full. | D1 | All generated files go to the SSD; C6 refuses to start without space. |
| Colour: a LUT made for one input state looks wrong on another. The clips' colour science generation is unknown. | D2 | Step 1 reads the generation; step 3 shows stills before any choice. |
| Blackmagic's own `.cube` files come from the local Resolve install. Their redistribution terms were not found [unverified]. | Step 3 | They are used from the local install and never committed. |
| Steady-scene thresholds are starting values. A subject that fills the frame can mislead the movement measure [inference]. | Step 2 | Thresholds are settings. The report lists every cut. Calibrate on the real clips. |
| Clock drift between camera and recorder over a 271-second clip. The offset is measured near the start only. | Step 2 | Step 2 measures the offset again near the end of the longest clips and reports the drift. |
| Whisper invents text on silence and ambient noise [inference]. | Step 4 | Voice-activity detection; no subtitles for non-speech regions. |
| Shotcut project files may change between versions; the Shotcut annotations were not tested [documented, Src6]. | Step 3 | The writer targets one tested Shotcut version. Edits made in Shotcut do not return. |
| A change on the page and a change by Claude Code collide. | T6 | Hash check on every page write; numbered versions. |
| Python 3.12 is needed for the VapourSynth wheel [documented, Src1]; v1.1 said 3.9. | D3 | A dedicated environment, or a new minimum for the whole tool. |
| Scene search by description depends on Claude Code reading still images correctly. | Step 5 | The owner checks the result in the preview; every edit is one version that can be undone. |
| If the Resolve option stays, Resolve 21.1 limits scripting to Studio; reports differ on the free edition [documented, Src8]. | D4 | Decide D4 first. |

## 14. Open Decisions

Nothing here is decided. Where the owner has said something, it is quoted. The current plan in `SPECS.md` is a technical default, not a decision. Each question is needed before the step named.

**Any time**

- **D4. Does the Resolve push stay as an option?** The owner said: "no longer needs DaVinci Resolve for these films". Open: keep the Resolve push, the audio copy step (R1, R2) and their tests, or remove them. Until answered, they stay in the code and are not extended.

**Before step 1**

- **D1. SSD path, free space and ProRes profile.** The owner said: "on an external SSD". Open: which drive and which folder, and how much space. Open: ProRes 422 LT, 422 or 422 HQ (sizes in section 5). The one clip checked runs at 25 frames per second; the frame rate of the other clips is read in step 1.
- **D2. Colour.** The owner said: "one LUT for the whole film (after the base conversion from Blackmagic Film)". Open: which Blackmagic colour science generation the clips carry (read from the clip metadata in step 1), and which colour state the working copies hold: Blackmagic Film as recorded, with the base conversion applied at render, or already converted to Rec.709 by the decoder. Step 1 makes stills of each so the owner can see.
- **D3. Python and packages.** The VapourSynth wheel needs Python 3.12 or newer [documented, Src1]; v1.1 documents say 3.9. Open: may the whole tool require 3.12, or should only the convert step run in its own environment? Open: may packages beyond `numpy`, `scipy` and `tqdm` be added (for example OpenCV)? The specification avoids them.

**Before step 2**

- **D5. Frame shape of the 4K film.** One checked clip is 4128 x 2176 [measured]. A 16:9 4K film is 3840 x 2160. Open: crop a 3840 x 2160 window without scaling (loses about 7 percent of the width), or crop 16:9 at full height and scale down slightly, or deliver another shape. The sizes of the other clips are not checked.
- **D6. The clap.** Open: where in the clip it is (only at the start? also a clap at the end?), how many seconds after the clap each segment should start (starting value 0.5 s), and what to do when no clap is found.
- **D7. What "most stable scene" means.** The owner said: "detect the most stable scene and cut out camera movement" and "split a clip with several static scenes into one clip per scene". Open: does the tool keep every steady scene and drop only the movement between them (the plan), or keep only the steadiest scene of each clip? Open: the shortest scene to keep (starting value 2 s), and what to do with a clip that has no steady scene (starting plan: leave it out and list it in the report).
- **D8. Picture and fades.** The owner asked for "smooth audio fade out and in between scenes" and for fades at the film's start and end. Nothing was said about the picture between scenes. Open: hard cut (the plan) or a dissolve. Open: fade lengths. Starting values: 1.0 s in, 2.0 s out, 0.5 s audio crossfade [inference].

**Before step 3**

- **D9. Loudness target.** The owner said: "levelise all audio loudness". Open: the number. YouTube is described as targeting about -14 LUFS integrated with a -1 dBTP peak; the sources are secondary and the official upload page gives no loudness number [documented, secondary, Src12]. Open: is every segment levelled to the same loudness (the plan), or only the film as a whole?
- **D10. LUT sources.** The owner asked for "state-of-the-art LUTs". Open: which LUT collection the owner trusts and where it lives on the machine. Free packs exist; their licences were not read [documented, secondary; found in the research report, not in Appendix B].
- **D11. Audio in the Shotcut project.** Open: separate audio clips per segment, already shifted by the offset (the plan), or one premixed audio track.

**Before step 4**

- **D12. Whisper model.** Open: which model size and where it is downloaded from. The choice follows a timing test on a two-minute sample in step 4.
- **D13. Subtitle form.** Open: burned into the picture, as an SRT file next to the film, or both. Open: the font for subtitles and titles.

**Before step 5**

- **D14. Prompt edits.** The owner's example is "please cut out the scene where I am frying a sausage". In the owner's other requests, "cut out" means remove (clap, camera movement); the plan reads the example the same way. Open: confirm, or also support "keep only this scene". Open: how scenes are found. The plan: Claude Code reads contact sheets and the transcript, which needs no install. The alternative, a local image-search model, needs installs the owner has not allowed.

**Before step 6**

- **D15. Default length of a vertical short.** The owner said: "of a desired length". Open: the default when none is given. YouTube Shorts may be up to 3 minutes since 2024-10-15 [documented, secondary, Src14].
- **D16. Crop and importance.** Open: where the 9:16 window sits in each segment (centre by default, set by hand or by prompt, or found by subject tracking, which needs installs the owner has not allowed), and which signals mark an important moment (speech, sound events, pinned scenes, a prompt).

## 15. Changes Against v1.1

**Removed or replaced**

- Goal: "timeline open in DaVinci Resolve" is replaced by section 2.
- "No re-encoding" is replaced by the originals-untouched rule (section 10).
- Out of scope in v1.1, now in scope: scene detection within a clip, grading with a LUT, final render and export, subtitles.
- The audio copy next to the video (old F8, F14) and the Resolve push (old F11, F12) become the optional requirements R1 and R2.
- Per-scene trims in seconds from the start and the end (`trim_start`, `trim_end`, `selection_trims`) are replaced by segment in-points and out-points in the timeline file. The owner's `project.json` has an empty selection and no trims, so nothing needs to move [measured].
- The v1.1 open item "ship `resolve_import.py`" depends on D4.

**Carried over as future work**

- Show the "resolved by correlation" decisions in the page.
- Re-pair a clip by hand in the page.
- Package the app as a double-clickable file.
