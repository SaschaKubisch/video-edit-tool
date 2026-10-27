# Product Requirements Document - Ambient Video Auto

**Version:** 2.5 (draft for the owner's review)
**Date:** 2026-10-28
**Owner:** Sascha Kubisch
**Status:** Matching and sync (requirements M1 to M6) are built and released as v1.1.0. Step 1, the conversion test, is done: its results are in section 5 and in `SPECS.md` section 6. Of the rest, step 2 is built on the branch `editor-step2` (the `convert` command, the timeline file's loader and validator, the clap finder, the steady-scene finder, the assembly, the film preview and the Film tab with trim controls); the drift measurement (`analyse --drift`) is built too; of step 3 the loudness levelling, the creative LUT, the final film from the `.braw` files with its presets and the ProRes master, and the Shotcut project with its read-back are built on the same branch, of step 4 the text box per scene, the cues and titles with their rules, Whisper, the SRT files, text drawn into the preview and the final film, and the translations are built on the same branch (`SPECS.md` section 9 says what is not), of step 5 the scene index, the still sheets, the commands `scenes remove`, `keep`, `restore` and `cut` and the `CLAUDE.md` section are built on the same branch (D14 is settled), and of step 6 the vertical short is built on the same branch (`short`, `scenes pin`, `scenes crop`; the owner has not watched a short yet). Section 3 records the owner's decisions of 2026-10-26 on storage, colour, frame, Python and export. Section 2 replaces the goal of v1.1. Version 2.2 records the owner's decisions of 2026-10-26 on the clap (D6), steady scenes (D7, including the clip with no steady scene), transitions (D8), reading cuts back from Shotcut (D11) and the encoder (D17); step 2 is being built. Version 2.3 records the owner's answer of 2026-10-27 on which creative LUTs to install (D10): "slay Film + Portrait". Version 2.4 records the owner's answers of 2026-10-27 on the Whisper model (D12), the subtitle form and font (D13), how subtitles are written (a text box per scene), the one subtitle style and the translations; step 4 is built on the branch `editor-step2`. Version 2.5 records the owner's answers of 2026-10-27 on the Resolve push (D4), the audio form in the Shotcut project (D11), prompt edits (D14), the default length of a vertical short (D15), crop and importance (D16), the Portuguese speech of three clips and the deleted ProRes test files; no decision is open. The Resolve push and the audio copy step are removed, and step 6, the vertical short, is built on the branch `editor-step2`.

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
- **ProRes**: Apple's editing video format. It stores every frame whole, so a cut at any frame is exact. "10-bit" means 1024 levels per colour channel. In this plan it is used only for the optional master file.
- **Preview copy**: the small video file (MP4) the tool makes once from each `.braw` clip. It is 2048 pixels wide, has the base LUT applied, and carries the camera's audio. Analysis, the film preview in the tool's page and Shotcut read the preview copies. The final render does not: it reads the `.braw` files.
- **Film preview**: the small rendering of the whole film from the timeline file that the tool's page plays.
- **Decoder**: software that turns BRAW data into pictures.
- **SSD**: the external drive that holds the preview copies and everything else the tool makes.
- **H.264, MP4, AAC**: H.264 is the video format that YouTube and most players use. MP4 is the file type that holds H.264 video with AAC audio. **Mbit/s** (megabits per second) is the data rate of a video file.
- **DCI 4K**: the cinema 4K frame, 4096 x 2160 pixels. **UHD** is the television 4K frame, 3840 x 2160. **Letterbox**: black bars above and below a picture that is wider than the frame.
- **Timecode**: the clock the camera writes into each clip, as hours:minutes:seconds:frames.
- **OpenCV**: a free library for image analysis in Python.
- **LUT**: look-up table, a file that maps each input colour to an output colour. The common text form is `.cube`. "Tetrahedral interpolation" is the method that computes colours between the table's entries.
- **Blackmagic Film**: the flat colour curve and colour range the camera records. The **base conversion** turns it into normal display colours (Rec.709, the standard colour space for HD video).
- **LUFS**: the unit of perceived loudness. **LU** is a difference of one LUFS. **dBTP** is the true-peak level, the highest level of the reconstructed sound wave.
- **Fade**: a change of picture or sound from or to nothing. **Crossfade**: one sound fades out while the next fades in at the same time.
- **Shotcut**: a free video editor. Its project file is MLT XML. MLT is the video engine inside it.
- **Whisper**: a speech-to-text model. **SRT**: a plain subtitle file.
- **Vertical short**: a 9:16 video for phones, cut from the 16:9 film.
- **Claude Code**: the AI agent the owner uses in the terminal. It edits files and runs commands.
- **Step**: one of the six parts of the work in section 7.

Tags on facts. **[measured]**: checked on the owner's machine or files on 2026-10-26. **[documented]**: a source states it; the sources are in `SPECS.md`, Appendix B (written Src1, Src2 and so on). **[arithmetic]**: computed from stated numbers. **[inference]**: reasoning that was not checked. **[unverified]**: a design statement that only a test can confirm. A statement with no tag is a requirement or a definition. Speeds were measured while other jobs loaded the machine; each measured speed names the load average (the number of busy processes, averaged over one minute) where it is known.

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

Resolve reads BRAW without conversion. No open-source editor and no ffmpeg build does [documented, Src4]. Leaving Resolve therefore starts with turning BRAW into a format other tools read. Step 1 tested a decoder that does this (section 5).

## 2. Goal

**Owner decision:** The owner no longer needs DaVinci Resolve for these films. After matching video and audio, the tool edits the film itself.

The tool does all automatic work on one timeline file. It renders a preview and the final film itself. It writes a Shotcut project for rare hand work, done last.

## 3. Settled Decisions (Owner)

These decisions are settled. This document builds on them and does not reopen them. Each is also a row in section 12 or a requirement in section 9. D1, D2, D3 and D5 were open decisions in version 2.0; the owner answered them on 2026-10-26 (section 14 lists what remains open). D6, D7, D8, D11 and D17 were answered later the same day; the last part of D7 (a clip with no steady scene) was answered after that. D9 (loudness), D10 (creative LUT sources) and the question about the offsets of two clips were answered after that, also on 2026-10-26. The owner's choice of creative LUT packs (D10) was answered on 2026-10-27. D12, D13, the subtitle input, the subtitle style and the translations were answered on 2026-10-27, too. D4, the audio form of D11, D14, D15 and D16 were answered on 2026-10-27 as well, with the words quoted in the rows below.

| Area | Decision |
|------|----------|
| Editing | The tool does all automatic work on one timeline file (JSON) that Claude Code can edit. The tool renders a preview and the final film itself. The tool's web page gets a small preview with simple trim controls. The tool also writes a Shotcut project (MLT XML) for rare hand work, done last. After the hand work the tool reads the cut points and the order back from the Shotcut project (D11, section 3). |
| Footage | All clips are BRAW from a BMPCC 4K. Audio comes from a Zoom H2N as WAV. ffmpeg cannot decode BRAW video. |
| Storage (D1) | **Option A.** The owner asked: "can we have it smaller", then decided: "okay lets do A". The tool keeps no full-resolution working copies. It makes only small preview copies (H.264, 2048 pixels wide) for analysis, the film preview in the tool's page and Shotcut. The final 4K render decodes the original `.braw` files directly: best quality, one encode. The ProRes working copies of version 2.0 are dropped. |
| Decoder | Decoding goes through `vapoursynth-brawsource` (a plugin for the VapourSynth video framework, using the Apple GPU through Metal). Step 1 tested it on two real clips and it works (section 5). The Blackmagic RAW SDK stays the second decoder and is written only if the plugin fails later. |
| Colour (D2, D10 in part) | The clips are Blackmagic colour science generation 5 and are recorded as Blackmagic Design Film (gamma and gamut) [measured]. The owner chose the Blackmagic "Gen 5 Film to Video" LUT, panel d of the step-1 comparison, as the default look. It is the base conversion from Blackmagic Film to Rec.709, applied with tetrahedral interpolation (`lut3d`, `interp=tetrahedral`) on the full-range RGB picture. The preview copies carry it baked in. The final render applies it to the RAW decode. LUT files stay on the owner's Mac (Resolve's LUT folder, `/Library/Application Support/Blackmagic Design/DaVinci Resolve/LUT/`) and are never committed. A creative LUT on top of the base conversion is optional; where such LUTs come from is settled in the row "Creative LUT sources (D10)" below. |
| Frame (D5) | **DCI 4K, 4096 x 2160**, as the owner decided: "DCI 4K, bars for wide". The wider clips (4096 x 1720) keep their full picture and are letterboxed: black bars above and below. Nothing is cropped from them. |
| Python (D3) | **Python 3.12 and OpenCV allowed**, as the owner decided: "Yes, 3.12 + OpenCV". The tool moves to Python 3.12 and runs in its own environment. It may use OpenCV. |
| Export | **Default final export:** MP4, H.264 video, DCI 4K 4096 x 2160, about 45 Mbit/s, AAC audio. YouTube's recommended range for 4K at 30 frames per second is 35 to 45 Mbit/s [documented, Src11]. **Master:** a ProRes 422 HQ `.mov` file is made only when the owner ticks it; it needs about 130 GB per 20 minutes at DCI 4K [estimate given with the decision; `SPECS.md` section 5.6 gives the arithmetic], so the free space is checked first. **Vertical shorts:** MP4, H.264, 1080 x 1920. The owner first said: "for the final export we can do quick time movie or another best practice format", then chose this. |
| Clap (D6) | **"Only at the start".** Each clip has one clap, at its beginning. The tool cuts everything up to 0.5 s after the clap (starting value, changeable). A clip without a clap is kept from its first steady frame and is listed in the report. |
| Steady scenes (D7) | **"Every steady scene".** The tool keeps each steady part of a clip as its own scene and drops only the movement between them. A scene shorter than 2 s is dropped (starting value, changeable). A clip with no steady scene of that length: the owner answered **"Leave it out, list it"**. The clip is not put on the timeline, the report names it, and the owner can add it back by hand. |
| Transitions (D8) | **"Hard cut"** for the picture. The sound crossfades for 0.5 s at every join. The film fades in over 1 s and out over 2 s (starting values, changeable). |
| Shotcut read-back (D11) | **"Tool reads cuts back".** After hand work in Shotcut, the tool reads the cut points and the order from the Shotcut project back into its timeline and renders the 4K film from the `.braw` files. Effects made only in Shotcut do not come back. Built in step 3. This answers the part of D11 about the preview size. The audio form is the next row. |
| Answers of 2026-10-27 (D4, D11 audio form, D14, D15, D16) | The owner said on 2026-10-27: **"i will not validate anything for now. keep on finishing all open tasks"**. Later the same day he answered the open questions with: **"Yes, delete the old test files and for the other decisions apply your recommended answers"**. The recommended answers are the rows that follow. The recommendation is the main session's; the owner accepted it with these words and did not write the details himself. |
| Resolve push removed (D4) | Recommended answer, accepted with the words above: remove the Resolve push, the audio copy step (R1, R2) and their tests. The owner had said the tool **"no longer needs DaVinci Resolve for these films"**. The git history keeps the code. |
| Audio form in the Shotcut project (D11) | Recommended answer, accepted with the words above: separate audio clips per segment, already shifted by the offset (the plan's default). Reason: the tool reads the cuts back from Shotcut, and the audio of a segment moves with its picture when the owner re-cuts there; one premixed audio track would go out of sync. |
| Prompt edits (D14) | Recommended answer, accepted with the words above: "cut out" means remove; "keep only" is also supported (built in step 5 as `scenes keep`). Claude Code finds the scenes from still sheets and the scene text; no image-search model is installed. |
| Default length of a short (D15) | Recommended answer, accepted with the words above: 60 s when `--length` is not given. `--length` overrides it. The plan had made `--length` required; this row replaces that. |
| Crop and importance of a short (D16) | Recommended answer, accepted with the words above: the 9:16 window is centred by default. A horizontal offset per scene can be set by hand or by prompt and is stored in the timeline file. There is no subject tracking. Importance, highest first: pinned scenes and scenes named in a prompt; scenes with scene text or subtitles; the rest. |
| Shortening a film to a short (details of D15, D16) | The same words apply. The short gets about one kept scene per 4 s of its length. Each kept scene gets time in proportion to its length in the film, at least 2.0 s (2.25 s for the last scene, because of the film's fade-out), at most 8 s. The short copies the film's fades (1 s in, 2 s out). These numbers are the main session's recommendation, not words of the owner. |
| Portuguese speech in three clips | The clips `A002_01060447_C065`, `A002_01060459_C067` and `A002_01060503_C069` (scenes s114 to s117) hold Portuguese speech. Same words: no special handling. The automatic subtitles are set aside (row above), so the subtitles of these scenes come from the scene text box like every other scene. |
| Deleted test files | Same words: the two ProRes test files in `/Volumes/Untitled/video-edit-tool-work/step1/prores` (5.7 GB) were deleted. No code or document referenced them. `step1/logs`, `step1/scripts` and `step1/stills` (26 MB) are kept. |
| Encoder (D17) | **"Hardware (fast)".** The default encoder of the final export is `h264_videotoolbox` (Apple's hardware encoder) at 45 Mbit/s. `libx264` (software) stays selectable. |
| Offsets of two clips | The clap report lines up the claps of two clips whose stored offsets have no shared sound: `A002_01011851_C047` (stored -5.027 s; a sync of +0.02 s would line up its claps) and `A002_01060813_C101` (stored 1.282 s; +0.18 s would) [inference from the clap report, not checked by ear]. The owner was asked whether to use the lined-up offsets or leave the stored ones and set them by hand, and answered **"Leave them, I set them"**. The stored offsets stay as they are. The owner types an offset per clip in the Film tab (`SPECS.md` section 7.6). |
| Loudness (D9) | **"Each scene, then film".** Each scene is levelled to the same loudness, then the whole film is levelled to -14 LUFS integrated, with a true peak of at most -1 dBTP. These two numbers are the ones shown to the owner with the question. In the timeline file this is the loudness mode `segment_then_film`. Built in step 3. |
| Creative LUT sources (D10) | **"Research free packs".** A research step finds free LUT packs whose licence allows use in the owner's videos, and shows stills of the owner's footage with each look. The owner picks which packs to install. The base look stays Gen 5 Film to Video. Built in step 3. **Pick, 2026-10-27: "slay Film + Portrait".** The two LUTs are `slay-04-film.cube` and `slay-05-portrait.cube` from the slay pack (github.com/slaycamera/luts, licence CC BY 4.0, which asks for the credit line `LUTs: [slay](https://www.slaycamera.com/?utm_source=github&utm_medium=repo&utm_campaign=luts) (CC BY 4.0)` wherever work made with them is published). They are installed on the owner's drive, with the licence text and a `CREDIT.txt`, in the folder that `VET_LUT_DIR` names, and are not committed. The creative LUT is applied after the base look, on Rec.709. No creative LUT is applied unless the owner chooses one. When a film uses a slay LUT, the export writes the credit line into a text file next to the film, for the YouTube description. |
| Delivery | 4K films. Vertical 9:16 shorts are cut from the 4K source. |
| Speech | English. This covers subtitles and transcript search. |
| Whisper model (D12) | **"large-v3-turbo"**, run with whisper.cpp (2026-10-27). The `whisper-cli` program in Shotcut's app folder is run as a command-line program only; Shotcut itself is not started. The model file `ggml-large-v3-turbo.bin` is downloaded once from the whisper.cpp repository on Hugging Face into the work folder, checked against the published sha256 and named by the setting `VET_WHISPER_MODEL`. A timing test on a two-minute sample of the real film came first (`SPECS.md` section 9.2 has the numbers). The model is not trained to translate; the subtitle language is English. |
| Subtitle form (D13) | **"This should be configurable actually."** (2026-10-27). The form is a setting of each export: `srt` (an SRT file next to the film), `burned` (drawn into the picture) or `both`. Starting values, not decisions: the main film `srt`, a vertical short `burned`. The setting is changed in the export settings, on the command line and in the Film tab. |
| Subtitle font (D13) | **"Inter"** (2026-10-27). Inter (SIL Open Font License 1.1) from github.com/rsms/inter, kept in a folder on the SSD with its licence file, not installed on the system and not committed. |
| Subtitle language | **"English"** (2026-10-27, asked again for the text box). Version 1 of the timeline file supports English only. |
| How subtitles are written | The owner (2026-10-27): **"Maybe it's best to just have a text input where I use my local Superwhisper model and then just fill a text box with text. And then in the background we will split this text up into subtitles that we then spread across the video wherever it fits. And then I as a user can also move these sections myself."** Follow-up answers: **"One box per scene"**; placement **"Across the whole scene"**; suggestion for the text **"Local cleanup rules"**; language **"English"**. Superwhisper is the owner's own dictation program; the tool records no audio. The automatic Whisper subtitles (W1, W2) stay as a second way; the next row puts them aside for now. |
| Automatic subtitles put aside | The owner (2026-10-27): **"You can for now ignore the automatic subtitles. We just get the feature where we have a text box and you convert it into subtitles and spread them across the film. But we don't need a voice detection feature yet."** The automatic Whisper subtitles (`subtitles auto`, W1, W2) stay in the code as built, with their tests, but are not part of the owner's workflow for now. No further work on them, and no voice-detection model (`VET_WHISPER_VAD_MODEL`) is to be added. The text box per scene is the way subtitles are written. |
| Subtitle style | One style for every film and every export, defined once in the tool's configuration (Inter, size, weight, colour, outline, line spacing, bottom position in the safe area, centred), never per film. Text is wrapped by its measured width with the real font and never leaves the safe area; a cue that does not fit is split into more cues, never drawn smaller. The owner asked for the common readability practice: about 42 characters per line, at most 2 lines, reading speed at most about 17 characters per second, cues of about 1 to 7 s, a small gap between cues, line breaks at natural phrase points, a bottom-heavy 2-line layout. The numbers and their sources are in `SPECS.md` section 9.3. |
| Subtitle translations | The owner (2026-10-27): **"Would be good to also translate the subtitles into Portuguese, Chinese, Thai and German. To have them as exportable files that I can then use for the YouTube subtitles and so on."** Then: **"also japanese and russian"**. Translator: **"openrouter cheap model"**. Portuguese: **"Brazilian (pt-BR)"**. Chinese: **"Simplified (zh-Hans)"**. So the six target languages are pt-BR, zh-Hans, th, de, ja and ru; the files are SRT files, one per language, next to the film. |
| Installs allowed | `ffmpeg-full` (Homebrew, GPL, keg-only, installed beside the existing ffmpeg), VapourSynth with the BRAW plugin in the tool's own Python 3.12 environment, OpenCV, and Shotcut (installed on 2026-10-26, version 26.9.28 [measured]). |
| Originals | The tool never modifies or deletes the `.braw` and WAV originals. (Kept from v1.1.) |

## 4. Users

One user, the filmmaker, running the tool locally on macOS with Apple Silicon. A second operator is Claude Code, which edits the timeline file and runs the tool's commands at the user's request. No multi-user, login or cloud requirements.

## 5. Facts About the Footage and the Machine

| Fact | Tag |
|------|-----|
| The owner's camera is a BMPCC 4K; all clips are BRAW; audio comes from a Zoom H2N. | Owner statement |
| The first shoot has 105 matched clips of 3.5 s to 271.1 s, 73.1 minutes in total. | [measured] |
| The folder `Recordings_261003` holds 116 video clips, 76.6 minutes in total: 51 clips with the codec tag `brlt` (23.8 min) and 65 with `brhq` (52.8 min). All 116 run at 30000/1001 frames per second (29.97). | [measured, step 1] |
| The two kinds differ in frame shape. `brlt`: container 4128 x 2176, picture decoded by the BRAW SDK 4096 x 2160 (DCI 4K). `brhq`: container 4128 x 1736, decoded 4096 x 1720, wider than DCI 4K. Decoding at half size gives 2048 x 1080 and 2048 x 860. | [measured, step 1] |
| Other folders may differ. One clip from a later shoot was probed earlier: 4128 x 2176 (container size) at 25 frames per second, with PCM scratch audio (24-bit, 48 kHz, stereo) and two timed-metadata tracks of about 1025 samples per second each. What the metadata tracks hold was not read. | [measured] |
| The test clip `A002_01060615_C083` carries colour science generation 5, gamma "Blackmagic Design Film", gamut "Blackmagic Design", ISO 500, white balance 4000 K, camera firmware 7.9.1. The other clips are probed when `convert` runs (`SPECS.md` check 1.9). | [measured] |
| One checked H2N file is stereo, 24-bit, 96 kHz WAV. | [measured] |
| In the first shoot, 96 of 105 offsets lie between 0.09 and 0.37 s. Nine lie outside: -5.03, -1.70, 0.63, 0.64, 0.66, 0.83, 0.89, 1.28 and 1.94 s. Whether these nine are correct was not checked. | [measured] |
| The volume that holds the originals (`/Volumes/Untitled`) has 264 GiB free of 932 GiB. It holds the folders of three shoots. The step-1 test files are in `/Volumes/Untitled/video-edit-tool-work/`. The path of the tool's own work folder is a setting (`work_dir`). | [measured] |
| The internal disk has 9.0 GB free of 228 GB. | [measured] |
| Apple M4, 16 GB RAM, macOS 26. Python 3.9.6 is the system Python; Python 3.12 is installed with Homebrew; `uv` is installed. | [measured] |
| VapourSynth R81 with `vapoursynth-brawsource` 0.5.0 runs in a Python 3.12 virtual environment. The plugin carries the Blackmagic RAW runtime 6.0 and decodes on the GPU through Metal. | [measured, step 1] |
| The installed ffmpeg 8.1 has no libass, no freetype and no zscale. It cannot draw text or burn subtitles. `ffmpeg-full` has them and can run Whisper. The installed ffmpeg 8.1 does have `lut3d` and the encoders `libx264`, `h264_videotoolbox`, `hevc_videotoolbox` and `prores_videotoolbox`, and all of them ran in step 1. | [measured] |
| DaVinci Resolve 18.6.3 (free edition) is installed. Its LUT folder holds the Blackmagic conversion files, among them "Blackmagic Gen 5 Film to Video.cube". | [measured] |
| ffmpeg has no BRAW decoder. | [measured] |
| Shotcut 26.9.28 is installed (`/Applications/Shotcut.app`). Its `melt` program (version 7.41.0) is at `/Applications/Shotcut.app/Contents/MacOS/melt`. The same folder holds `ffmpeg` (version 9.0.2, with libx264, libx265 and zimg), `ffprobe` and `whisper-cli`. That `ffmpeg` finds no decoder for the video of a `.braw` clip (a read-only test), so Shotcut can use only the preview copies. | [measured] |
| ProRes 422 LT at 4096 x 2160 and 25 frames per second needs about 164 GB per hour; ProRes 422 needs 236 GB and 422 HQ needs 354 GB. At 29.97 frames per second the figures are 1.2 times larger. | [documented, Src10], arithmetic mine |

### 5.1 Results of step 1

Step 1 was a test by hand, with scripts, on two clips of `Recordings_261003`: `A002_01060615_C083` (`brlt`, 26.9 s, 805 frames) and `A002_01011826_C040` (`brhq`, 121.3 s, 3635 frames). The scripts and logs are on the SSD in `/Volumes/Untitled/video-edit-tool-work/step1/` and `.../step2-preview/`. They are not in the repository. Other jobs loaded the machine during the test: the load average was 21 to 73 in the conversion test, 6 to 25 in the preview-copy check. Speeds on an idle machine would be higher [inference]. The detail, the commands and the checks are in `SPECS.md` section 6.1.

All numbers below are [measured] on 2026-10-26.

| Part | Result |
|------|--------|
| Conversion test (the original plan: 4096-wide ProRes 422 LT copies) | The decoder works on both clips. Every frame of C083 was compared with the decoded picture: 805 of 805, Y PSNR 52.1 to 54.3 dB. Full-size ProRes conversion ran at 5.6 to 18.4 fps for C083 (load 21 to 73) and 20.9 fps for C040. The copies were 1.27 GB (C083, 2.8 GB per minute) and 4.85 GB (C040, 2.4 GB per minute): about 194 GB for the 76.6 minutes of the folder [arithmetic]. The owner chose option A after seeing this size. |
| Preview-copy check (the addendum): half-size decode, LUT d, H.264 | Size 31.7 MB for C083 (9.4 Mbit/s, 71 MB per minute) and 74.3 MB for C040 (4.9 Mbit/s, 37 MB per minute). About 3.6 GB for all 116 clips [arithmetic]. Speed 54 fps (C083) and 62 fps (C040), which is 1.8 and 2.1 times real time, at load 16 to 19. About 39 minutes for all clips, one after the other [arithmetic]; a repeat at a load of 9 to 15 ran at 2.5 times real time, about 31 minutes. Peak memory: Python 0.75 to 0.93 GB, ffmpeg 0.2 GB. |
| What the preview copies were checked for | Frame count equal to the source (805 and 3635), frame rate 29.97, timecode kept (06:15:34:16 and 18:26:12:24), a key frame every 30 frames, H.264 High profile, `yuv420p`, `moov` box at the start of the file, camera audio as AAC. macOS's AVFoundation (the engine of QuickTime Player) reports both files playable and decodes every frame. A frame matches the step-1 still d: the mean colour differs by less than 1 of 255, PSNR 36.2 dB at the same size, 47.4 dB after a blur that removes fine detail. |
| Shotcut and `melt` | `melt` reads both preview copies (2048 x 1080 and 2048 x 860, 29.97 fps, timecode read) and rendered a 2 s piece of each in 1.7 s. Shotcut was not opened. |
| Final-render check (option A): 5 s of C083 decoded from the `.braw` at full size, LUT d, to DCI 4K | Decode alone 37 fps (load 6). Decode, LUT and colour conversion without an encoder 17.6 to 19.2 fps (load 17). With `h264_videotoolbox` at 45 Mbit/s: 13.9 to 15.2 fps, 0.46 to 0.51 times real time (load 6 to 26). With `hevc_videotoolbox` 10-bit: 16 fps. With `libx264` preset slow at 45 Mbit/s: 4.1 to 4.4 fps, 0.14 to 0.15 times real time. A letterboxed clip (C040, 4096 x 1720 into 4096 x 2160): 20.6 fps with `h264_videotoolbox`. A 20-minute film at 29.97 fps would take about 40 minutes with `h264_videotoolbox` and about 2.4 hours with `libx264` slow [arithmetic from these speeds]. A repeat at a lighter load (load 3 to 5, after the other jobs had ended) gave 27.3 fps without an encoder, 26.1 fps with `h264_videotoolbox` (0.87 times real time, about 23 minutes for a 20-minute film) and 6.6 fps with `libx264` slow (0.22 times real time, about 1.5 hours). |

## 6. Core User Journey

1. The user starts the app (`python3 main.py`). A browser tab opens at `localhost:8765`.
2. **Match.** The user picks the folders with the video and the audio and clicks Match. The tool pairs each clip with its audio and finds the offset. (Built.)
3. **Convert.** The tool makes one preview copy of every clip on the SSD: half-size decode, base LUT applied, H.264, camera audio. The page shows progress. A clip with a complete copy is not converted again. For the first shoot this is about 3.6 GB and about 39 minutes (section 5.1).
4. **Assemble.** The tool cuts the clap off each clip, finds the steady scenes, drops the camera movement, puts the scenes in recording order, adds the fades, writes the timeline file and renders a film preview from the preview copies. The user watches the preview in the page and trims with simple controls.
5. **Look and sound.** The default look is the Blackmagic Gen 5 Film to Video LUT. If the user wants a creative LUT on top, the tool shows side-by-side stills of real clips with candidate LUTs and the user picks one. The tool levels the loudness and exports the 4K film (MP4, H.264, DCI 4K, about 45 Mbit/s) from the `.braw` files. A ProRes 422 HQ master is made only if the user ticks it.
6. **Words.** The tool writes English subtitles from the speech. The user adds titles by hand or asks Claude Code for them.
7. **Prompt edits.** The user tells Claude Code what to change ("cut out the scene where I am frying a sausage"). Claude Code edits the timeline file, the tool checks it, and the preview shows the result.
8. **Shotcut, last.** For rare hand work the tool writes a Shotcut project that uses the preview copies. After the hand work the tool reads the cut points and the order from the Shotcut project back into the timeline file and renders the 4K film from the `.braw` files. Effects made only in Shotcut do not come back.
9. **Shorts.** The user asks for a vertical short of a given length. The tool builds a second timeline file from the film.

## 7. Steps (Order of Work)

Each step is usable on its own. A later step starts only when the step before it works.

| Step | Name | What the owner can do when it is done | Needed before it starts |
|------|------|---------------------------------------|-------------------------|
| 1 | Conversion test. **Done** on 2026-10-26: the conversion test, and its addendum, the preview-copy check. Results in section 5.1. | The owner knows how long one real clip takes to convert, how large the copies are and that the colour is right. Two preview copies exist and open in QuickTime-compatible players and in Shotcut's `melt`. | - |
| 2 | Preview copies, timeline file and assembly. **Built** on the branch `editor-step2` (the owner has not yet watched a preview). | Get preview copies of all clips (the `convert` command, requirements C1 to C7), then a rough film in recording order, without clap and camera movement, with audio crossfades and film fades. Watch it in the page and trim it. | Step 1 result; D6, D7 and D8 are settled (section 3) |
| 3 | Look and sound | Get a levelled, graded DCI 4K film in MP4 (H.264, about 45 Mbit/s), rendered from the `.braw` files. Choose a creative LUT from stills if wanted. Get the Shotcut project and read its cuts back, and the ProRes master if ticked. D9, D10 and D11 are settled (section 3) |
| 4 | Words | Get English subtitles and titles, burned in or as a file, and SRT files in six more languages. **Built** on the branch `editor-step2` (the owner has not yet read the subtitles on a rendered film). | D12 (Whisper model) and D13 (subtitle form and font) are settled (section 3) |
| 5 | Prompt edits. **Built** on the branch `editor-step2` (the owner has not yet tried it). | Change the film by describing scenes to Claude Code. | D14 is settled (section 3) |
| 6 | Vertical shorts. **Built** on the branch `editor-step2` (the owner has not yet watched a short). | Get a 9:16 short of a chosen length from the 16:9 film. | D15 (default length) and D16 (crop and importance) are settled (section 3) |

No decision is open (section 14). D1 to D17 are answered (section 3).

## 8. Scope

**In scope**

- Matching and sync of camera and recorder files (built).
- One-time conversion of BRAW clips to preview copies.
- The timeline file, automatic assembly, film preview, final render from the `.braw` files, export.
- A web page with a preview player and simple trim controls.
- A Shotcut project written from the timeline file. It uses the preview copies. The tool reads the cut points and the order back from it (D11).
- English transcript, subtitles and titles.
- Scene search by description through Claude Code.
- Vertical shorts.

**Out of scope**

- A full timeline editor in the browser. The page has playback and simple trim controls only.
- Reading Shotcut effects back into the timeline file: filters, transitions and other changes made only in Shotcut. Only cut points and order come back (D11).
- Editing at full resolution in Shotcut. Shotcut cannot read BRAW and works on the preview copies (D11).
- Correcting camera movement. The owner films static; movement is cut out, not corrected.
- Grading beyond one LUT per film: no per-clip correction, no scopes.
- Music, sound effects, voice recording.
- Speech in languages other than English.
- HDR output. Output is SDR Rec.709 [inference: the Blackmagic Film-to-Video conversions target it].
- Several cameras or several recorders. Windows and Linux.
- Upload to YouTube or any other service. Any use of cloud services at run time.
- Transitions between pictures other than a hard cut (D8).

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
| Titles and subtitles manually or automatically via a prompt | W1 to W8 | 4 |
| Prompt-driven edits via Claude Code that find scenes by description ("please cut out the scene where I am frying a sausage") | P1 to P4 | 5 |
| Fade in video and audio at the start and fade out at the end of the film | A6 | 2 |
| Save the project | T4 | 2 |
| Set the sync offset of a clip by hand (the owner's answer "Leave them, I set them") | A8 | 2 |
| Export to the most common video format at a custom resolution | E3, E6 | 3 |
| Automatically convert a 16:9 film with subtitles into a vertical short of a desired length, shortening scenes while keeping the most important moments, and scaling subtitles to fit | S1 to S5 | 6 |

### 9.2 Requirements

Priority: Must, Should, Could. "Built" means released in v1.1.0.

**Built on the branch `editor-step2`** [measured by the tests of the branch, 2026-10-26; `SPECS.md` section 7 says how]: C1 to C6 and the interface of C7; T1 to T3; T4 (atomic saves, numbered snapshots, `timeline undo` and `timeline history`); T5 (`timeline validate`, `timeline summary` and `timeline frames`); T6 (hash check on every page edit); A1 to A8 (the drift has a report of its own, `analyse --drift`); E1; U1 to U3; M7 and M9. Of step 3 these are built too: L2 to L5, E2 to E7 (the film from the `.braw` files, the presets, the render report, the ProRes master, the Shotcut project and its read-back). Of step 4 these are built too: W1 to W8 (the search of the transcript by words, W5, is `subtitles search`). Of step 5 these are built too: P1 to P4 (the scene index and the still sheets are `scenes index`, `scenes sheets` and `scenes frames`; the edits are `scenes remove`, `keep`, `restore` and `cut`; D14 is open and the plan's default is built). **Not built:** M8 (thumbnails from the preview copies), L1 (the comparison stills), burned-in subtitles in Chinese, Japanese and Thai, subtitles and titles in the Shotcut project, and the requirements of step 6.

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
| M8 | One thumbnail per clip, taken from its preview copy. (Old F5; `.braw` had no thumbnail.) | Should | Step 2 |
| M9 | Never modify or delete the `.braw` and WAV originals. Derived files go to the work directory on the SSD. No exception is left: the audio copy of R1 was removed (D4). | Must | Step 2 |
| M10 | A command-line subcommand for every step. (Old F13.) | Should | All |

**C - Preview copies (the `convert` command, built first in step 2)**

Step 1 tested this method on two clips (section 5.1). These requirements describe the command that applies it to every clip.

| ID | Requirement | Priority |
|----|-------------|----------|
| C1 | Make one preview copy of each `.braw` clip, once, on the SSD: MP4, H.264 (High profile, 8-bit 4:2:0), half-size decode (2048 pixels wide: 2048 x 1080 for `brlt`, 2048 x 860 for `brhq`), the clip's frame rate, the base LUT (default Gen 5 Film to Video) applied with tetrahedral interpolation, the clip's camera audio as AAC, the source timecode kept, a key frame every second, the `moov` box at the start of the file. | Must |
| C2 | Skip clips whose preview copy is complete. Resume after an interrupted run. A copy counts as complete only after it passed the check in C5. | Must |
| C3 | Store per clip: decoded frame size, frame rate, frame count, start timecode, colour science generation, and the hash of the LUT baked into the copy. | Must |
| C4 | Report the speed (frames per second), the file size and the elapsed time for each clip and in total. | Must |
| C5 | Check each copy after writing: codec, pixel format, frame size, frame rate and frame count equal what the source says; an audio stream is present; the timecode equals the source's. | Must |
| C6 | Refuse to start when the SSD is missing or has less free space than the estimate plus a margin. The estimate uses the measured size per minute of the first converted clip of each kind. | Must |
| C7 | Support a second decoder behind the same interface, so the decoder can change if the plugin fails. | Should |

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
| A8 | The owner can type a sync offset per clip in the Film tab ("Leave them, I set them", section 3). The offset from matching stays in the file and stays visible beside it, and the owner can go back to it. A changed offset changes the sound of that clip's scenes only: no picture is rendered again. A value that cannot be right is refused; a WAV that then holds no sound for a scene gives a warning, not a failure. | Must |

**L - Look and sound (step 3)**

| ID | Requirement | Priority |
|----|-------------|----------|
| L1 | Make side-by-side stills of the same frames of several real clips with several candidate creative LUTs, to choose one (D10). The candidates come from the research step of D10: free packs whose licence allows use in the owner's videos. The default look needs no choice: it is the Gen 5 Film to Video LUT. | Must |
| L2 | Apply the LUTs of the whole film with tetrahedral interpolation. | Must |
| L3 | Apply the base LUT (default: Blackmagic Gen 5 Film to Video) in the final render to the RAW decode, before any creative LUT. The preview copies already carry the base LUT. | Must |
| L4 | Bring every segment to the same loudness, then bring the film to the target loudness: -14 LUFS integrated (D9, "Each scene, then film"). | Must |
| L5 | Keep the true peak of the film under the limit. | Must |

**E - Preview and export (steps 2 and 3)**

| ID | Requirement | Priority |
|----|-------------|----------|
| E1 | Render a film preview of the film or a time range from the timeline file, from the preview copies. | Must |
| E2 | Render the final film from the original `.braw` files, decoded at full size, never from the preview copies or the film preview. | Must |
| E3 | Export the default final film: MP4, H.264, 4096 x 2160, about 45 Mbit/s, AAC audio. Other sizes of the same shape are allowed. | Must |
| E4 | Write a Shotcut project (MLT XML) from the timeline file. It uses the preview copies. The cut points and the order can be read back (E7). | Must |
| E5 | Write a render report: what was shortened, capped or missing. | Should |
| E6 | Make a ProRes 422 HQ master (`.mov`) only when the owner ticks it. Before it starts, check the free space against the estimate and refuse if it is too small. | Should |
| E7 | Read the cut points (`in`, `out`) and the order of the segments from a Shotcut project written by E4 back into the timeline file. Effects made only in Shotcut are not read; the report lists them. The 4K film is then rendered from the `.braw` files (E2). | Must |

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
| W4 | Burn subtitles and titles into the film, and write an SRT file. Which of the two is used is a setting of each export (D13, section 3): `srt`, `burned` or `both`. Titles are always drawn into the picture. | Must |
| W5 | Search the transcript by words. | Should |
| W6 | A text box per scene. The owner types or dictates the words of the scene; the tool cleans the text locally, splits it into cues by the readability rules and spreads them across the scene. The owner can move and resize each cue. | Must |
| W7 | One subtitle style for all films, with the readability limits of section 3 (Inter; wrapped by measured width; two lines at most; never outside the safe area). | Must |
| W8 | Translate the English cues into pt-BR, zh-Hans, th, de, ja and ru and write one SRT file per language, with the times of the English cues. | Should |

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
| S5 | Export the short to MP4, H.264, at a custom resolution (default 1080 x 1920). | Must |

Built on the branch `editor-step2`, in `SPECS.md` section 11 and by the command `short`: S1 (the plan is a timeline file of kind `short` next to the output), S2 (one scene per equal part of the film, importance by tiers, time in proportion to the length of the scene with a floor and a cap, exact to the frame), S3 (the window is cut from the `.braw` picture, centred or set per scene with `scenes crop`), S4 (the subtitles are laid out again inside the safe area of the 9:16 frame), S5 (MP4, H.264, 8 Mbit/s, AAC, any vertical size). The owner has not watched a short yet.

**R - Resolve option (removed)**

The requirements R1 (copy the matched WAV next to its video under the video's name, with undo; old F8, F14) and R2 (push the ordered timeline into DaVinci Resolve; old F11, F12) are removed (D4, 2026-10-27). The git history keeps the code.

## 10. Non-Functional Requirements

- **Originals untouched.** The tool never modifies or deletes `.braw` and WAV originals (decision, kept from v1.1). It opens them read-only. It writes only into the work directory on the SSD, the timeline file, `project.json` and the export folders the user names.
- **Derived files are allowed.** v1.1 forbade any re-encoding. That rule is replaced: the tool makes preview copies, film previews and renders from the originals. Every derived file can be made again from the originals.
- **Recoverability.** Every change to the timeline file is saved as a numbered version. A preview copy that is lost or damaged is made again by the convert step.
- **Local only.** At run time the tool makes no network calls apart from `localhost`. One-time setup (Homebrew packages, the BRAW plugin, the Whisper model) is done by the owner and listed in `SPECS.md`.
- **Frame and sample accuracy.** Cuts fall on exact frames. Audio is placed to the sample. Picture and sound in the finished film stay aligned to the precision of the matched offset (about 1 ms, [measured] in v1.1) plus rounding to one audio sample.
- **Transparency.** Every automatic decision (clap position, scene boundaries, gains, shortened crossfades) is written to a report or into the timeline file, so the user can audit it.
- **Disk.** Nothing large goes to the internal disk (9.0 GB free). Generated files go to the SSD. The preview copies of the first shoot take about 3.6 GB. A ProRes 422 HQ master takes about 130 GB per 20 minutes and needs the free-space check of E6.
- **Speed.** The owner sets no time limits yet. Measured so far (section 5.1): preview copies at 1.8 to 2.1 times real time, the final render at 0.5 times real time with `h264_videotoolbox` and 0.14 times with `libx264` slow, at the loads named there. Steps 2 and 3 record the times; the owner then sets limits.
- **Dependencies.** The whole tool runs on Python 3.12 in its own virtual environment (D3, settled). Packages: `numpy`, `scipy`, `tqdm`, `vapoursynth` with `vapoursynth-brawsource`, `budoux`, and `ffmpeg-full`. OpenCV (`opencv-python`) is allowed; the specification does not need it yet. Any other package needs the owner's approval first. `budoux` (Apache-2.0, pure Python, from Google) cuts Japanese, Chinese and Thai subtitle lines at phrase boundaries (`SPECS.md` 9.9); it was added on 2026-10-27 with the fix of those line breaks. The owner was told that it was added; he has not confirmed it explicitly. `ffmpeg-full` is GPL; the tool runs it as a separate program and does not bundle it [inference: this keeps the tool's MIT licence unaffected; not legal advice].
- **Public repository.** No footage, no LUT files, no Blackmagic runtime and no paths from the owner's machine go into the repository. `project.json` and the timeline file are listed in `.gitignore`.
- **Security.** The server listens on `127.0.0.1` only and keeps the Host and Origin checks of v1.1. The new file-serving endpoints serve only files inside the work directory.

## 11. Success Metrics

- Every clip of the owner's first shoot has a preview copy that passes check C5.
- Matching stays correct: all pairs of a typical shoot are matched as in v1.1.
- The sha256 hashes of all originals are the same before and after any run of the tool.
- The film's integrated loudness is within 0.5 LU of the target of -14 LUFS, and its true peak is at or under -1 dBTP (D9, settled; the 0.5 LU tolerance is a starting value).
- In a test film with a known marker, picture and sound stay within 1 ms (`SPECS.md`, check 2.10).
- Times for conversion, assembly and render are measured and recorded in steps 1 to 3. Step 1 is done (section 5.1). No limit is set before step 3.
- **Goal check by the owner:** the next film is finished without opening DaVinci Resolve.

## 12. Key Decisions and Rationale

"Owner" marks a decision the owner settled. "Design" marks a choice made in the specification that the owner can change. "Kept" marks a row that still holds from v1.1.

| Decision | Status | Alternative considered | Why |
|----------|--------|------------------------|-----|
| The tool edits the film; Resolve is not used for these films. | Owner | Keep Resolve; buy Resolve Studio with its built-in Claude connection. | The free Resolve cannot be driven from outside, and version 21.1 moved Python scripting to Studio [documented, Src8; reports differ on what the free edition keeps]. Studio's price was not checked. Studio 21.1 can connect to Claude Code [documented, Src8]; it is the paid alternative. |
| The Resolve push, the audio copy step (R1, R2) and their tests are removed. (D4) | Owner | Keep them as an option: the code stays and is not extended. | The owner said the tool "no longer needs DaVinci Resolve for these films" and accepted the recommendation (section 3). The tool renders the film itself, and nothing in its flow uses the push or the copy. The git history keeps the code. |
| In the Shotcut project, each segment has its own audio clip, already shifted by the offset. (D11) | Owner | One premixed audio track. | The tool reads the cuts back from Shotcut. When the owner re-cuts there, the audio clip of a segment moves with its picture; a premixed track would go out of sync [inference]. |
| "Cut out" means remove; "keep only" is also supported. Claude Code finds the scenes from still sheets and the scene text. (D14) | Owner | Support "cut out" only; find scenes with a local image-search model. | In the owner's other requests "cut out" means remove (the clap, camera movement). `scenes keep` was built in step 5. A local image-search model needs installs the owner has not allowed, and none is installed. |
| A short is 60 s when `--length` is not given. (D15) | Owner | `--length` required. | The owner said "of a desired length" earlier; the recommended default was accepted. The platforms allow at least 3 minutes (YouTube Shorts since 2024-10-15 [documented, secondary, Src14]); rule V22 warns above 180 s. |
| The 9:16 window of a short is centred unless set per scene; there is no subject tracking; importance goes pinned or named in a prompt, then scene text or subtitles, then the rest. (D16) | Owner | Subject tracking; scores for sound events and picture activity. | Subject tracking needs installs the owner has not allowed. A wide clip (4096 x 1720) shows 23.6 percent of its width in the window, so the position per scene matters [arithmetic]; the owner or Claude Code sets it after looking at a still. Sound events and picture activity are not measured by any part of the tool yet. |
| A short keeps about one scene per 4 s, gives each scene time in proportion to its length (at least 2.0 s, 2.25 s for the last, at most 8 s), and copies the film's fades. | Design (accepted with the owner's words of 2026-10-27) | Cut the lowest-scoring seconds from the edges of scenes until the length fits (`SPECS.md` 11.4 of version 2.4); the same time for every kept scene. | A run of the edge-cutting rule on the first shoot (171 scenes in 3514.7 s, speech-only scores) ended with 18 scenes of 2.1 to 2.9 s each [measured by simulation]: a fast montage whose coverage of the film depends on the tie rule. Keeping fewer scenes and giving long scenes more time shows the whole film in order. The last scene needs 2.25 s because its share of the 0.5 s join is 0.25 s and the fade-out is 2.0 s (rule V11). |
| Keep no full-resolution working copies. Make small H.264 preview copies once; render the final film from the `.braw` files. (D1, option A) | Owner | Convert every clip once to ProRes 422 LT at full size on the SSD (the plan of version 2.0); edit on small proxies; convert on demand. | The owner said "can we have it smaller", then "okay lets do A". The ProRes copies measured 2.4 to 2.8 GB per minute, about 194 GB for the 76.6 minutes of the folder [measured, arithmetic]. The preview copies measured 37 to 71 MB per minute, about 3.6 GB. The final render decodes the `.braw` files directly, so it has the best quality and one encode. Cost: the final render is slow (0.5 times real time at best, section 5.1) and every analysis and preview works on the half-size picture. ffmpeg cannot decode BRAW [measured], so the preview copies are also what Shotcut and the page can play. |
| Decode with `vapoursynth-brawsource`; the Blackmagic RAW SDK is the alternative. | Owner | Write an SDK wrapper first; buy BRAW Toolbox. | The plugin is MIT, one person's project with a first release in 2026 [documented, Src1]. Step 1 tested it on two real clips: it decodes all frames, at full and half size, on the GPU [measured]. The SDK stays the fallback. |
| The default look is the Blackmagic "Gen 5 Film to Video" LUT (panel d of the step-1 comparison). The preview copies carry it baked in; the final render applies it to the RAW decode. (D2, D10 in part) | Owner | The Pocket 4K Film to Video v4 LUT (panel c); the "Extended Video" variants (panels e and f); the decoder's own Rec.709 conversion (panel b); a base conversion applied only at render, with unconverted copies. | The clips are generation 5 and record Blackmagic Design Film [measured]. The owner picked panel d from the side-by-side stills. Baking it into the copies makes the film preview and the analysis show the final colours. Changing the base LUT later means making all copies again (about 39 minutes, section 5.1). |
| The film frame is DCI 4K, 4096 x 2160. Wider clips are letterboxed. (D5) | Owner | A 3840 x 2160 UHD frame with a crop (loses up to 7 percent of the width) or a slight scale; a crop of the wide clips to fill the frame. | The owner said "DCI 4K, bars for wide". The decoded `brlt` picture is already 4096 x 2160, so it needs no crop and no scale; the `brhq` picture (4096 x 1720) keeps all its pixels, with 220-pixel bars above and below [measured sizes, arithmetic]. |
| The tool moves to Python 3.12 in its own environment and may use OpenCV. (D3) | Owner | Python 3.12 only for the convert step; no new packages. | The owner said "Yes, 3.12 + OpenCV". The VapourSynth wheel needs 3.12 [documented, Src1]. |
| Default export: MP4, H.264, 4096 x 2160, about 45 Mbit/s, AAC. A ProRes 422 HQ `.mov` master only on the owner's tick. Shorts: MP4, H.264, 1080 x 1920. | Owner | QuickTime movie (ProRes) as the default final export; MP4 with CRF instead of a bitrate. | The owner said "for the final export we can do quick time movie or another best practice format", then chose this. YouTube recommends 35 to 45 Mbit/s for 4K at 30 frames per second [documented, Src11]. The ProRes master is about 130 GB per 20 minutes [estimate], so it is off by default. The encoder is `h264_videotoolbox` by default (D17, next rows); `libx264` stays an option. |
| One timeline file (JSON) holds the edit. | Owner | A browser timeline editor (months of work [inference]); a native Swift app (more [inference]); a generated Shotcut project as the main file. | An agent edits a small JSON file more reliably than MLT XML [inference]. Shotcut and Kdenlive project files are not stable interfaces [documented, Src6, Src7]. |
| The tool renders preview and film itself. | Owner | Render with Shotcut's engine (`melt`). | Owner decision. The specification builds on `ffmpeg-full` and the decoder (Design). `melt` reads the preview copies and renders from them, and Shotcut's own ffmpeg cannot decode BRAW [measured]. |
| A Shotcut project is written last. After hand work the tool reads the cut points and the order back; effects made only in Shotcut do not come back. (D11) | Owner | Never read back (version 2.1); a reader for all Shotcut edits, filters included; full-size ProRes copies for Shotcut. | The owner answered "Tool reads cuts back". Cut points and order are plain numbers in the MLT XML (`in` and `out` of each playlist entry) [inference]. The preview copy and the `.braw` file have the same frame numbers, so the 4K render can use them. A reader for effects would need a model of every Shotcut filter [inference]. The MLT format is documented as unstable [documented, Src6], so the reader targets one tested Shotcut version. |
| One clap per clip, at the start. Cut up to 0.5 s after it. A clip without a clap starts at its first steady frame and is listed. (D6) | Owner | A clap at the end as well; search the whole clip. | The owner answered "Only at the start". The 0.5 s guard is a starting value that the owner can change. |
| Keep every steady scene of a clip; drop only the movement between them; drop scenes shorter than 2 s. A clip with no steady scene is left out and listed. (D7) | Owner | Keep only the steadiest scene of each clip. Keep the whole clip after the clap when it has no steady scene. | The owner answered "Every steady scene". 2 s is a starting value. For a clip with no scene of 2 s the owner answered "Leave it out, list it": the clip is not put on the timeline, the report names it, and the owner can add it back by hand. |
| Hard cut for the picture. Sound crossfade 0.5 s. Film fade-in 1 s, fade-out 2 s. (D8) | Owner | A dissolve between scenes. | The owner answered "Hard cut" for the picture. The lengths are starting values that the owner can change. |
| The default encoder of the final export is `h264_videotoolbox` at 45 Mbit/s; `libx264` stays selectable. (D17) | Owner | `libx264` preset slow as the default. | The owner answered "Hardware (fast)". Measured on 5 s of a `brlt` clip at 4096 x 2160 (section 5.1): `h264_videotoolbox` ran at 13.9 to 15.2 fps and gave 45.7 Mbit/s; `libx264` preset slow ran at 4.1 to 4.4 fps and gave 49.1 Mbit/s with the same 45 Mbit/s setting. A 20-minute film takes about 40 minutes against about 2.4 hours at the loads of that test [arithmetic]. Picture quality was compared on one frame only (PSNR against the step-1 still: 39.1 dB and 39.3 dB). Check 3.8 compares both on the whole first shoot. |
| One LUT for the whole film, chosen from side-by-side stills; tetrahedral interpolation. | Owner | Per-clip grading. | Fits static ambient films; one choice, easy to repeat. |
| The stored offsets of `A002_01011851_C047` and `A002_01060813_C101` stay; the owner types offsets per clip in the Film tab. | Owner | Use the lined-up offsets +0.02 s and +0.18 s from the clap report. | The owner answered "Leave them, I set them". The lined-up values are an inference from two sounds that the camera and the recorder each heard once; they were not checked by ear [inference]. A hand offset is stored in the timeline file next to the offset from matching, so the original stays visible and can be restored (`SPECS.md` section 3.2, `audio_offset_hand_s`). |
| Loudness: level each scene to the same loudness, then the whole film to -14 LUFS integrated with a true peak of at most -1 dBTP. (D9) | Owner | Level the film as a whole only (mode `film_only`, which keeps the differences between scenes). | The owner answered "Each scene, then film". The numbers are those shown with the question; YouTube is described as targeting about -14 LUFS with a -1 dBTP peak, from secondary sources only [documented, secondary, Src12]. |
| Creative LUTs come from free packs found by a research step, and the owner picks which to install. (D10) | Owner | Name one LUT collection now; bundle a pack with the tool. | The owner answered "Research free packs". The licences of free packs were not read before this answer [documented, secondary]; the research step reads them, and a pack whose licence does not allow use in the owner's videos is not offered. The research step read the licence of the slay pack (CC BY 4.0, free for personal and commercial use with credit [documented, the pack's README and LICENSE]). The owner then picked "slay Film + Portrait" (2026-10-27). The credit is the condition of the licence, so the export writes it next to the film. |
| The Whisper model is large-v3-turbo, run by whisper.cpp's `whisper-cli` from Shotcut's folder. (D12) | Owner | A smaller or English-only model; `mlx-whisper`; the `whisper` filter of `ffmpeg-full`. | The owner answered "large-v3-turbo". The installed ffmpeg 8.1 has no `whisper` filter [measured]; `whisper-cli` 1.8.3 in Shotcut's app folder is a universal binary with Metal and runs from the command line [measured]. The model file is 1.6 GB and was checked against the sha256 that Hugging Face publishes. |
| The subtitle form is a setting of each export: `srt`, `burned` or `both`. (D13) | Owner | One form for all exports. | The owner answered "This should be configurable actually." The starting values (film `srt`, short `burned`) are the plan's guess, not an answer. |
| Subtitles and titles are drawn with Inter. (D13) | Owner | A system font. | The owner answered "Inter". The installed ffmpeg has no `libass`, `freetype` or `drawtext` [measured], so the tool draws the text with Pillow and lays it over the picture with ffmpeg's `overlay` filter. |
| Subtitles come mainly from a text box per scene; automatic Whisper subtitles are the second way. | Owner | Whisper only. | The owner wants to dictate with Superwhisper and fill a box. On the first shoot, Whisper heard "Thank you." and "*Pain*" in ambient sound, where nobody speaks [measured, `SPECS.md` 9.2], so its text needs a person's check. |
| One subtitle style for all films, in the configuration. | Owner | A style per film. | The owner asked for one consistent style. |
| Subtitle translations go through OpenRouter with a cheap model, one SRT file per language. | Owner | A local translation model; DeepL. | The owner answered "openrouter cheap model". |
| 4K films; shorts cut from the 4K source. | Owner | 1080p. | A 9:16 window of a 1080p frame is 608 pixels wide and would be enlarged to 1080 [arithmetic]. |
| English speech only. | Owner | Several languages. | Fixes the Whisper model class and the subtitle rules. |
| Duration-first matching with an order-preserving alignment. | Kept | Pure audio correlation of all pairs. | Camera microphone against H2N gave weak scores (8 to 18 percent) and many extractions; durations are cheap and near-identical for pairs [reported in the v1.1 PRD]. |
| Correlation only as tiebreaker and for the offset. | Kept | Always correlate. | Keeps the common case fast. |
| A single-file browser page on `http.server`, plain JavaScript. | Kept | Electron, Tkinter, Streamlit. | No extra dependency, easy to inspect. It now also plays video. |
| The timeline refers to the original WAV with an offset. No copy of the WAV is made for the tool's own render. | Design | Copy and rename the WAV next to the video (v1.1). | The copy existed to feed Resolve's auto-sync by filename. It was removed with the Resolve option (D4). |
| Analysis is written with `numpy` and `scipy`. OpenCV is allowed but not required. | Design | OpenCV (`cv2.phaseCorrelate` could replace the hand-written phase correlation [inference]), PySceneDetect. | A prototype on synthetic video separated still from moving frames with `numpy` only [measured, synthetic]. The owner allowed OpenCV (D3), so the choice can change when the code is written. |
| Replaced rows from v1.1: "Resolve script run from inside Resolve", "Copy and rename audio instead of muxing", "Output defaults to the video folder", "No re-encoding". | Replaced | - | Their reasons applied to the Resolve workflow. |

## 13. Risks

| Risk | What it depends on | Handling |
|------|--------------------|----------|
| The BRAW decode path rests on a young one-person plugin (first release 2026, one GitHub star [measured]). Step 1 showed that it decodes two real clips completely [measured]. The final render depends on it for every film. | Step 3 | The SDK is the second decoder (C7). The `convert` command checks every clip (C5, check 1.9). |
| Speeds were measured while other jobs loaded the machine (section 5.1). The final render of a 20-minute film takes about 40 minutes with `h264_videotoolbox` and about 2.4 hours with `libx264` slow at the measured loads, and about 23 minutes and 1.5 hours at a lighter load [arithmetic]. The speed of a render with several segments is not measured. | Step 3 | Step 3 measures a whole film (`SPECS.md` check 3.8). The owner sets time limits after that. |
| Disk space. The internal disk has 9.0 GB free. The preview copies of the first shoot take about 3.6 GB; a ProRes master about 130 GB per 20 minutes. | Step 2, E6 | All generated files go to the SSD; C6 and E6 refuse to start without space. |
| Colour: a LUT made for one input state looks wrong on another. The default LUT matches generation 5, which the test clip carries [measured]; the other clips are probed when `convert` runs. How QuickTime and YouTube display the BT.709-tagged result was not checked [unverified]. | Step 2, step 3 | `convert` reads the generation of every clip and warns when it is not 5. Check 3.8: the owner compares the render with the chosen stills in the players used. |
| Changing the base LUT after the preview copies exist means making all copies again (about 39 minutes). | Step 2 | `conversion.json` stores the hash of the baked LUT; the timeline check warns when it differs from `look.base`. |
| Shotcut cannot read BRAW and works on the preview copies. A film exported from Shotcut is preview-sized (2048 pixels wide), not 4K. The reader (E7) depends on the Shotcut file format, which is documented as unstable [documented, Src6]. | D11 | The tool reads the cut points and the order back and renders the 4K film from the `.braw` files. The reader targets one tested Shotcut version and ignores what it does not know. |
| Blackmagic's own `.cube` files come from the local Resolve install. Their redistribution terms were not found [unverified]. | Step 3 | They are used from the local install and never committed. |
| Steady-scene thresholds are starting values. A subject that fills the frame can mislead the movement measure [inference]. | Step 2 | Thresholds are settings. The report lists every cut. Calibrate on the real clips. |
| Clock drift between camera and recorder over a 271-second clip. The offset is measured near the start only. | Step 2 | Step 2 measures the offset again near the end of the longest clips and reports the drift. Measured on 2026-10-26 on the five clips of 120 s or more: at most 1.9 ms against a limit of half a frame (16.7 ms); the other 100 clips are shorter and are not measured (`SPECS.md` 7.3). |
| Whisper invents text on silence and ambient noise [measured on the first shoot: 329 of 384 phrases were dropped by the filter of `SPECS.md` 9.2, and the 55 that were kept include the model's English rendering of Portuguese speech]. | Step 4 | A filter (labels in brackets, stock phrases, loops, low probability, sound at the noise floor) and a person's check; a voice-activity model is a setting (`VET_WHISPER_VAD_MODEL`) that the owner can add. The text box per scene is the main way to write subtitles. |
| Shotcut project files may change between versions; the Shotcut annotations were not tested [documented, Src6]. | Step 3 | The writer targets one tested Shotcut version. Edits made in Shotcut do not return. |
| A change on the page and a change by Claude Code collide. | T6 | Hash check on every page write; numbered versions. |
| v1.1 was written and tested on Python 3.9; the tool now moves to 3.12 (D3, settled). Whether the 60 tests of v1.1 pass on 3.12 was not run [unverified]. | Step 2 | The first task of step 2 runs them on 3.12 in the new environment and fixes what breaks. |
| Scene search by description depends on Claude Code reading still images correctly. | Step 5 | The owner checks the result in the preview; every edit is one version that can be undone. |

## 14. Open Decisions

No decision is open. Where the owner said something, section 3 quotes it.

Answered on 2026-10-26 and moved to section 3 (settled decisions) and section 12 (rationale): **D1** storage (option A), **D2** colour, **D3** Python and packages, **D5** frame shape, the default LUT of **D10**, **D6** the clap, **D7** steady scenes (including the clip with no steady scene: "Leave it out, list it"), **D8** transitions and fades, **D9** loudness ("Each scene, then film"), the rest of **D10** ("Research free packs"), **D17** the encoder and the preview-size part of **D11**. Answered on 2026-10-27: **D12** (Whisper model: "large-v3-turbo"), **D13** (subtitle form: "This should be configurable actually."; font: "Inter"), and with the words "Yes, delete the old test files and for the other decisions apply your recommended answers" **D4** (the Resolve push is removed), the audio form of **D11** (separate audio clips per segment), **D14** ("cut out" means remove, "keep only" also works), **D15** (a short is 60 s unless `--length` says otherwise) and **D16** (centred window, per-scene offset by hand or prompt, no subject tracking, importance order). The question about the offsets of `A002_01011851_C047` and `A002_01060813_C101` ("Leave them, I set them") was also answered; it has no number. These numbers are not reused. **D17** came from the speed measurements of step 1.

One confirmation is still missing, and it is not a decision of the plan: the package `budoux` (section 10, Dependencies). The owner was told that it was added; he has not confirmed it explicitly.

## 15. Changes Against v1.1

**Removed or replaced**

- Goal: "timeline open in DaVinci Resolve" is replaced by section 2.
- "No re-encoding" is replaced by the originals-untouched rule (section 10).
- Out of scope in v1.1, now in scope: scene detection within a clip, grading with a LUT, final render and export, subtitles.
- The audio copy next to the video (old F8, F14) and the Resolve push (old F11, F12) became the optional requirements R1 and R2. Both were removed in version 2.5 (D4).
- Per-scene trims in seconds from the start and the end (`trim_start`, `trim_end`, `selection_trims`) are replaced by segment in-points and out-points in the timeline file. The owner's `project.json` has an empty selection and no trims, so nothing needs to move [measured].
- The v1.1 open item "ship `resolve_import.py`" is closed: the Resolve push was removed (D4).

**Changed in version 2.5 against version 2.4 (2026-10-28)**

- Decisions moved from open to settled, with the owner's words of 2026-10-27 ("Yes, delete the old test files and for the other decisions apply your recommended answers"): D4 (remove the Resolve push, the audio copy step R1 and R2, and their tests; done: the commands `sync` and `resolve`, the modules `syncer.py` and `resolve_integration.py`, `/api/sync` and `/api/resolve`, and the Sync & Export tab of the page are gone, and the page has three tabs), the audio form of D11 (separate audio clips per segment, shifted by the offset), D14 ("cut out" means remove, "keep only" also works), D15 (60 s when `--length` is not given) and D16 (centred window, per-scene offset by hand or prompt, no subject tracking, importance order). Section 3 has the rows, section 12 the reasons, section 14 has no open decision left.
- New rows in section 3 for the shortening details (about one kept scene per 4 s, time in proportion to the length of the scene, floor 2.0 s and 2.25 s for the last scene, cap 8 s, the film's fades), for the Portuguese speech of three clips (no special handling) and for the deleted ProRes test files.
- Step 6, the vertical short, is built on the branch `editor-step2` (`SPECS.md` section 11): the command `short`, the marks `scenes pin` and `scenes crop`, the preset `short`. The owner has not watched a short yet.
- Fixed in step 6: the `lower_third` title lay inside the bottom 35 percent of a 9:16 frame; `SPECS.md` 11.1 and check 6.3 named ASS files and libass, which the tool does not have.
- The package `budoux`: the owner was told that it was added; he has not confirmed it explicitly (section 10).

**Changed in version 2.4 against version 2.3 (2026-10-27)**

- Decisions moved from open to settled: D12 ("large-v3-turbo") and D13 (form: "This should be configurable actually."; font: "Inter"). Section 3 has new rows for them and for the subtitle language, the text box per scene, the one subtitle style and the translations; section 12 has the reasons; section 14 loses D12 and D13.
- New requirements W6 (text box per scene), W7 (one style) and W8 (translations).

**Changed in version 2.2 against version 2.1**

- Decisions moved from open to settled: D6 ("Only at the start"), D7 ("Every steady scene"), D8 ("Hard cut"), D17 ("Hardware (fast)") and the preview-size part of D11 ("Tool reads cuts back"). Section 3 has new rows, section 12 new rows, section 14 shrinks.
- Reading cut points and order back from Shotcut is now in scope (new requirement E7, step 3). It was out of scope in version 2.1. E4 no longer says "one-way".
- Not answered and still open: the audio form in the Shotcut project (D11).
- Step 2 is built on the branch `editor-step2`. Where the build found the plan wrong, `SPECS.md` says "changed in the build": OpenCV measures blocks of the picture and fits one camera motion (so people who move in a still shot are not camera movement); a film fade that does not fit the first or last scene is shortened and reported; a linear sound crossfade keeps the level of two parts of the same sound, not of two different sounds; the page does not start a preview job by itself after a change.
- Added later on 2026-10-26: the owner answered D9 (loudness, "Each scene, then film", -14 LUFS integrated, true peak at most -1 dBTP), the open part of D10 (creative LUT sources, "Research free packs") and the question about the offsets of `A002_01011851_C047` and `A002_01060813_C101` ("Leave them, I set them"). Section 3 has new rows, section 12 new rows, section 14 loses D9 and D10.
- Added on 2026-10-27: the owner picked the creative LUTs to install (D10): **"slay Film + Portrait"** (`slay-04-film.cube` and `slay-05-portrait.cube`, CC BY 4.0). Section 3 and section 12 say how the tool uses them and what credit the export writes.
- Added later on 2026-10-26: the owner answered the last open part of D7, what to do with a clip that has no steady scene: **"Leave it out, list it"**. The clip is not put on the timeline, the report names it, and the owner can add it back by hand. The plan's setting that would keep the whole clip instead is dropped.

**Changed in version 2.1 against version 2.0**

- Decisions moved from open to settled: D1 (option A), D2 (colour), D3 (Python 3.12 and OpenCV), D5 (DCI 4K, letterbox), the default LUT of D10, and the export format (new row in section 3).
- The ProRes working copies are replaced by H.264 preview copies. The final render decodes the `.braw` files. Requirements C1 to C7, E1 to E4, L1 to L3, M8 and M9 changed; E6 is new.
- Step 1 is marked done. The `convert` command (C1 to C7) moves to the start of step 2.
- Facts updated with the step-1 and preview-copy measurements (section 5, section 5.1). The frame rate of `Recordings_261003` is 29.97, not 25.
- D17 (encoder of the default export) is new. D11 gained the question about Shotcut's preview-sized output.

**Carried over as future work**

- Show the "resolved by correlation" decisions in the page.
- Re-pair a clip by hand in the page.
- Package the app as a double-clickable file.
