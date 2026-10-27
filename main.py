#!/usr/bin/env python3
"""
Dual-System Audio Video Automation
====================================
Automates the BMPCC 4K + Zoom H2N ambient video workflow.

Commands:
  app      Web app (default): match, pick and order scenes, the film
  match    Audio-fingerprint match video files to audio files
  rename   (legacy) rename both files of each pair to scene_NN.*, with undo
  convert  make a small preview copy of every matched .braw clip in the work folder
  analyse  find the sync clap at the start of each clip, find the steady scenes (reports in the work folder)
  assemble build the timeline file: clap cut, steady scenes, fades
  preview  render the film preview from the preview copies
  timeline validate or summarise the timeline file, list its versions (history), go back (undo),
           write stills at film times (frames)
  loudness set or show the loudness of the film (each scene, then the film)
  look     list creative LUTs, choose the creative or the base LUT
  export   list, add, switch on and off the exports (presets) of the timeline file
  render   render the final film from the .braw files (levelled sound, LUTs, DCI 4K)
  subtitles list, write, import and change the subtitles; a text box per scene; Whisper; translations
  title    add, change, remove and import titles
  scenes   prompt edits: the scene index and sheets to read; remove, keep, cut and restore scenes; pin, crop
  short    make a vertical 9:16 short of a chosen length from the film

Usage:
  python main.py
  python main.py match --video ./video/ --audio ./audio/
"""

import argparse
import json
import os
import re
import sys
import time
from fractions import Fraction
from pathlib import Path

import config
from project import Project


def cmd_app(args):
    """Launch the web app (default command)."""
    from server import run_server
    if args.project:
        config.PROJECT_FILE = args.project
    run_server(port=args.port, open_browser=not args.no_browser,
               timeline_path=args.timeline, work_dir_path=args.work)


def cmd_match(args):
    """Match video files to audio files by audio fingerprinting."""
    from matcher import match_files
    from thumbnails import extract_all_thumbnails

    video_dir = args.video
    audio_dir = args.audio

    # Validate directories
    if not Path(video_dir).is_dir():
        print(f"Error: Video directory not found: {video_dir}")
        sys.exit(1)
    if not Path(audio_dir).is_dir():
        print(f"Error: Audio directory not found: {audio_dir}")
        sys.exit(1)

    print("=" * 60)
    print("Matching video files to audio recordings")
    print("=" * 60)

    matches = match_files(
        video_dir=video_dir,
        audio_dir=audio_dir,
    )

    if not matches:
        print("\nNo matches found.")
        sys.exit(1)

    # Create project
    project = Project(
        video_dir=video_dir,
        audio_dir=audio_dir,
        matches=matches,
    )

    # Extract thumbnails
    project_file = args.project or config.PROJECT_FILE
    thumb_dir = Path(project_file).resolve().parent / "thumbnails"
    print(f"\nExtracting thumbnails...")
    project = extract_all_thumbnails(project, str(thumb_dir))

    # Save project
    project.save(project_file)
    print(f"\nProject saved to: {project_file}")
    print(f"Next step: python main.py app  (pick and order scenes)")


def cmd_rename(args):
    """Rename matched file pairs to share a common name."""
    from renamer import rename_matches, undo_renames

    if args.undo:
        log_path = args.undo
        try:
            undo_renames(log_path, dry_run=args.dry_run)
        except FileExistsError as e:
            print(f"Error: {e}")
            sys.exit(1)
        return

    project_file = args.project or config.PROJECT_FILE
    if not Path(project_file).exists():
        print(f"Error: Project file not found: {project_file}")
        print("Run 'python main.py match' first.")
        sys.exit(1)

    project = Project.load(project_file)

    print("=" * 60)
    print("Renaming matched file pairs")
    print("=" * 60)

    try:
        rename_matches(
            project=project,
            pattern=args.pattern,
            dry_run=args.dry_run,
        )
    except FileExistsError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if not args.dry_run:
        project.save(project_file)
        print(f"\nProject updated: {project_file}")


def install_graceful_stop():
    """Make `kill PID` (SIGTERM) stop a long run the clean way: it raises
    KeyboardInterrupt, so the partial file is removed, the lock released and the
    clip left as 'partial' for the next run. (A job started in the background of
    a shell ignores SIGINT, so SIGTERM is the way to stop it.)"""
    import signal

    def handler(signum, frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, handler)


def cmd_convert(args, decoder=None):
    """Make the preview copies of the matched .braw clips (SPECS 6)."""
    import convert
    import safety
    from decoder import BrawSourceDecoder, container_tracks

    def fail(message):
        print(f"Error: {message}")
        sys.exit(2)

    decoder = decoder or BrawSourceDecoder()
    try:
        decoder.check()
    except RuntimeError as e:
        fail(str(e))

    if args.probe:
        info = decoder.probe(args.probe)
        data = dict(info.__dict__)
        try:
            data["tracks"] = container_tracks(args.probe)
        except (RuntimeError, FileNotFoundError) as e:
            data["tracks"] = []
            data["tracks_error"] = str(e)
        print(json.dumps(data, indent=2))
        return

    try:
        lut = convert.find_base_lut(args.lut)
    except FileNotFoundError as e:
        fail(str(e))
    settings = convert.PreviewSettings(encoder=args.encoder or config.PREVIEW_ENCODER)

    # One clip, the first seconds of it, into <work>/test (its own conversion file).
    if args.test:
        if not args.work:
            fail("--test needs --work DIR.")
        info = decoder.probe(args.test)
        frames = None if args.seconds is None else \
            max(1, round(args.seconds * info.fps_num / info.fps_den))
        folder = str(Path(args.test).resolve().parent)
        conv = convert.Converter(os.path.join(args.work, "test"), decoder, settings, lut, [folder])
        try:
            summary = conv.run([convert.ClipRef(Path(args.test).stem, args.test)],
                               max_frames=frames)
        except (safety.OriginalsProtectedError, convert.WorkDirUnavailable,
                convert.NotEnoughSpace, convert.AlreadyRunning) as e:
            fail(str(e))
        sys.exit(summary.exit_code)

    project_file = args.project or config.PROJECT_FILE
    if not Path(project_file).exists():
        fail(f"Project file not found: {project_file}\nRun 'python main.py match' first.")
    project = Project.load(project_file)
    work_dir = args.work or project.work_dir
    if not work_dir:
        fail("No work directory. Pass --work DIR (a folder on the SSD) or set "
             "work_dir in the project file.")

    clips = [convert.ClipRef(Path(m.video_path).stem, m.video_path) for m in project.matches]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        clips = [c for c in clips if c.stem in wanted]
        missing = wanted - {c.stem for c in clips}
        if missing:
            fail(f"No matched clip named: {', '.join(sorted(missing))}")
    if args.limit:
        clips = clips[:args.limit]
    if not clips:
        fail("The project has no matched clips.")

    conv = convert.Converter(work_dir, decoder, settings, lut,
                             safety.originals_folders(project))
    install_graceful_stop()
    try:
        summary = conv.run(clips, dry_run=args.dry_run)
    except (safety.OriginalsProtectedError, convert.WorkDirUnavailable,
            convert.NotEnoughSpace, convert.AlreadyRunning) as e:
        fail(str(e))
    except KeyboardInterrupt:
        print("Interrupted. Run the same command again to continue; finished copies are kept.")
        sys.exit(130)
    sys.exit(summary.exit_code)


def cmd_analyse(args):
    """Analyse the clips of the project (SPECS 7): the sync clap and the steady scenes."""
    import safety

    def fail(message):
        print(f"Error: {message}")
        sys.exit(2)

    if not (args.clap or args.scenes or args.drift):
        fail("Say what to analyse: --clap (the sync clap), --scenes (the steady scenes) or "
             "--drift (the clock drift of camera and recorder).")
    project_file = args.project or config.PROJECT_FILE
    if not Path(project_file).exists():
        fail(f"Project file not found: {project_file}\nRun 'python main.py match' first.")
    project = Project.load(project_file)
    work_dir = args.work or project.work_dir
    if not work_dir:
        fail("No work directory. Pass --work DIR (a folder on the SSD) or set "
             "work_dir in the project file.")
    only = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else None
    if args.clap:
        import clap
        name = args.report_name or "clap-report"
        try:
            rows = clap.analyse_project(project, work_dir, limit=args.limit, report_name=name)
        except safety.OriginalsProtectedError as e:
            fail(str(e))
        print()
        print(clap.format_report(rows))
        print(f"Report: {os.path.join(work_dir, 'reports', name)}.json / .txt")
    if args.scenes:
        import stable
        name = args.report_name or "scenes-report"
        if args.clap and args.report_name:
            name = f"{args.report_name}-scenes"
        try:
            rows = stable.analyse_project(project, work_dir, limit=args.limit, only=only,
                                          report_name=name)
        except safety.OriginalsProtectedError as e:
            fail(str(e))
        print()
        print(stable.format_report(rows))
        print(f"Report: {os.path.join(work_dir, 'reports', name)}.json / .txt")
    if args.drift:
        import clap
        name = args.report_name or "drift-report"
        if (args.clap or args.scenes) and args.report_name:
            name = f"{args.report_name}-drift"
        try:
            rows = clap.analyse_drift_project(project, work_dir, limit=args.limit, only=only,
                                              report_name=name)
        except safety.OriginalsProtectedError as e:
            fail(str(e))
        print()
        print(clap.format_drift_report(rows))
        print(f"Report: {os.path.join(work_dir, 'reports', name)}.json / .txt")


def cmd_assemble(args):
    """Assemble the timeline file from the clips and their analysis (SPECS 7.5)."""
    import assemble
    import safety

    def fail(message):
        print(f"Error: {message}")
        sys.exit(2)

    project_file = args.project or config.PROJECT_FILE
    if not Path(project_file).exists():
        fail(f"Project file not found: {project_file}\nRun 'python main.py match' first.")
    project = Project.load(project_file)
    work_dir = args.work or project.work_dir
    if not work_dir:
        fail("No work directory. Pass --work DIR (a folder on the SSD) or set "
             "work_dir in the project file.")
    output = args.output or str(Path(project_file).resolve().parent / config.TIMELINE_FILE)
    frame = config.FILM_FRAME
    if args.frame:
        try:
            w, h = args.frame.lower().split("x")
            frame = (int(w), int(h))
        except ValueError:
            fail(f"--frame must look like 4096x2160, not {args.frame!r}")
    st = assemble.AssembleSettings(
        frame=frame,
        fit=args.fit or config.FILM_FIT,
        fade_in_s=config.FILM_FADE_IN_SEC if args.fade_in is None else args.fade_in,
        fade_out_s=config.FILM_FADE_OUT_SEC if args.fade_out is None else args.fade_out,
        join_s=config.FILM_JOIN_SEC if args.join is None else args.join,
        min_run_sec=config.STABLE_MIN_RUN_SEC if args.min_scene is None else args.min_scene,
        clap_guard_sec=config.CLAP_GUARD_SEC if args.clap_guard is None else args.clap_guard,
        title=args.title or "")
    only = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else None
    try:
        assemble.assemble_project(project, work_dir, output, replace=args.replace,
                                  limit=args.limit, only=only, settings=st,
                                  report_name=args.report_name,
                                  file_checks=not args.no_file_checks)
    except assemble.TimelineExists as e:
        fail(str(e))
    except (assemble.AssemblyError, safety.OriginalsProtectedError) as e:
        fail(str(e))
    print(f"Timeline: {output}")
    print(f"Report:   {os.path.join(work_dir, 'reports', args.report_name)}.json / .txt")
    print(f"Next:     python main.py timeline validate --file {output}")


def default_timeline_path(args) -> str:
    """The timeline file named by --file, else timeline.json next to the project file."""
    explicit = getattr(args, "file", None)
    if explicit:
        return explicit
    project_file = args.project or config.PROJECT_FILE
    return str(Path(project_file).resolve().parent / config.TIMELINE_FILE)


def cmd_timeline(args):
    """Work with the timeline file (SPECS 3, 4.3)."""
    import timeline

    path = default_timeline_path(args)
    if args.timeline_command == "summary":
        import edits
        try:
            tl = timeline.Timeline.load(path)
        except FileNotFoundError:
            print(f"Error: timeline file not found: {path}", file=sys.stderr)
            sys.exit(2)
        except timeline.TimelineError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(edits.format_summary(edits.summary(tl, file_hash=timeline.file_hash(path)),
                                   name=os.path.basename(path)), end="")
        return
    if args.timeline_command == "validate":
        try:
            tl = timeline.Timeline.load(path)
        except FileNotFoundError:
            print(f"Error: timeline file not found: {path}", file=sys.stderr)
            sys.exit(2)
        except timeline.TimelineError as e:
            print(json.dumps([{"level": "error", "rule": "V1" if isinstance(
                e, timeline.UnknownVersion) else "V0", "path": "", "message": str(e),
                "hint": ""}], indent=2))
            sys.exit(1)
        findings = tl.validate(file_checks=not args.no_file_checks)
        print(json.dumps([f.as_dict() for f in findings], indent=2))
        if not timeline.has_errors(findings):
            # SPECS 4.1 rule 3: a good check keeps the version, so a direct edit can be undone
            import store
            made = store.snapshot_if_changed(path)
            if made:
                print(f"snapshot {os.path.basename(made)[:-5]} of the valid file kept in "
                      f"{store.history_dir(path)}", file=sys.stderr)
        sys.exit(1 if timeline.has_errors(findings) else 0)
    if args.timeline_command == "history":
        return cmd_timeline_history(args, path)
    if args.timeline_command == "undo":
        return cmd_timeline_undo(args, path)
    if args.timeline_command == "frames":
        return cmd_timeline_frames(args, path)


def cmd_timeline_history(args, path):
    """List the versions of the timeline file with time and a one-line change (SPECS 4.3). Only the snapshots made
    from this file are its versions; the others in the shared history folder are named apart. Writes nothing."""
    import store
    import timeline
    if not os.path.isfile(path):
        print(f"Error: timeline file not found: {path}", file=sys.stderr)
        sys.exit(2)
    rows = store.history(path)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    name = os.path.basename(path)
    snapshots = [r for r in rows if not r["current"]]
    if not snapshots:
        print(f"{name}: no earlier version is kept yet. A version is kept when the file is "
              "saved over, or when `timeline validate` finds it valid.")
    else:
        print(f"{name}: {len(snapshots)} earlier version(s) kept in {store.history_dir(path)}")
        print(f"{'no.':8} {'time':19}  {'hash':8}  change from the version before")
        for r in rows:
            number = "current" if r["current"] else f"{r['number']:04d}"
            print(f"{number:8} {store.format_time(r['time']):19}  {r['hash'][:8]}  {r['change']}")
    others = store.other_snapshots(path)
    if not others["other_files"] and not others["unowned"]:
        return
    print()
    print("Not shown (the history folder is shared by the timeline files of this folder; each snapshot belongs to the "
          "file it was made from, and `timeline undo` uses only the snapshots of the file named by --file):")
    for owner, snaps in others["other_files"].items():
        numbers = ", ".join(f"{store._number(s):04d}" for s in snaps)
        print(f"  {len(snaps)} snapshot(s) of {owner}: {numbers}")
    if others["unowned"]:
        print(f"  {len(others['unowned'])} snapshot(s) without a recorded file. They were made before snapshots "
              "recorded the file they belong to, so no command can tell whose they are and none restores them. "
              "To use one, copy it by hand to a file name of your own.")
        print(f"  {'no.':8} {'time':19}  {'hash':8}  holds")
        for s in others["unowned"]:
            print(f"  {store._number(s):04d}     {store.format_time(os.path.getmtime(s)):19}  "
                  f"{timeline.file_hash(s)[:8]}  {store.describe_snapshot(s)}")


def cmd_timeline_undo(args, path):
    """Go back to the previous version of the timeline file (SPECS 4.3 step 6)."""
    import store
    import timeline
    try:
        replaced = store._read_data(path)
        result = store.undo(path)
    except FileNotFoundError:
        print(f"Error: timeline file not found: {path}", file=sys.stderr)
        sys.exit(2)
    except store.NothingToUndo as e:
        print(f"Nothing to undo: {e}", file=sys.stderr)
        sys.exit(1)
    restored = store._read_data(path)
    print(f"Restored version {result['restored']:04d} of {os.path.basename(path)} "
          f"(written {store.format_time(os.path.getmtime(path))}).")
    if replaced is not None and restored is not None:
        print(f"Changes made by the undo: {store.describe_change(replaced, restored)}")
    print(f"The version that was replaced is kept in {result['undone']}.")


def cmd_timeline_frames(args, path):
    """Write stills at film times or source times and print their paths (SPECS 4.3 step 5)."""
    import frames
    import render
    import safety
    import timeline

    def fail(message, code=2):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    if bool(args.film_s) == bool(args.source):
        fail("Say what to show: --film-s T1,T2 (film times in seconds) or --source CLIP --at "
             "T1,T2 (times in a clip), not both.")
    if args.source and not args.at:
        fail("--source needs --at T1,T2 (times in seconds in the clip).")
    if args.at and not args.source:
        fail("--at belongs to --source CLIP.")
    try:
        times = frames.parse_times(args.film_s or args.at)
    except ValueError:
        fail("the times must be numbers of seconds separated by commas, for example 12.0,48.5")
    try:
        tl = timeline.Timeline.load(path)
    except FileNotFoundError:
        fail(f"timeline file not found: {path}")
    except timeline.TimelineError as e:
        fail(str(e), 1)
    work_dir = args.work
    if not work_dir:
        project_file = args.project or config.PROJECT_FILE
        if Path(project_file).exists():
            work_dir = Project.load(project_file).work_dir
    if not work_dir:
        fail("No work directory. Pass --work DIR (a folder on the SSD) or set "
             "work_dir in the project file.")
    findings = tl.validate(file_checks=not args.no_file_checks)
    if timeline.has_errors(findings):
        print(json.dumps([f.as_dict() for f in findings], indent=2))
        fail("The timeline is not valid, so no still was written.", 1)
    originals = sorted({str(Path(s[k]).parent) for s in tl.data["sources"].values()
                        for k in ("original_video", "audio")})
    try:
        safety.assert_not_inside(work_dir, originals)
        if args.film_s:
            paths = frames.film_stills(tl, times, work_dir)
        else:
            paths = frames.source_stills(tl, args.source, times, work_dir)
    except safety.OriginalsProtectedError as e:
        fail(str(e))
    except frames.FrameError as e:
        fail(str(e), 1)
    for p in paths:
        print(p)


def cmd_preview(args):
    """Render the film preview from the preview copies (SPECS 5.5)."""
    import render
    import safety
    import timeline

    def fail(message, code=2):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    path = default_timeline_path(args)
    try:
        tl = timeline.Timeline.load(path)
    except FileNotFoundError:
        fail(f"timeline file not found: {path}")
    except timeline.TimelineError as e:
        fail(str(e), 1)
    work_dir = args.work
    if not work_dir:
        project_file = args.project or config.PROJECT_FILE
        if Path(project_file).exists():
            work_dir = Project.load(project_file).work_dir
    if not work_dir:
        fail("No work directory. Pass --work DIR (a folder on the SSD) or set "
             "work_dir in the project file.")
    film_range = None
    if args.range:
        try:
            a, b = (float(x) for x in args.range.split(":"))
            if not 0 <= a < b:
                raise ValueError
            film_range = (a, b)
        except ValueError:
            fail(f"--range must look like 60:90 (film seconds, from:to), not {args.range!r}")
    findings = tl.validate(file_checks=not args.no_file_checks)
    if timeline.has_errors(findings):
        print(json.dumps([f.as_dict() for f in findings], indent=2))
        print("The timeline is not valid, so no preview was rendered.", file=sys.stderr)
        sys.exit(1)
    settings = render.PreviewSettings(width=args.width or config.FILM_PREVIEW_WIDTH,
                                      encoder=args.encoder or config.FILM_PREVIEW_ENCODER)
    originals = sorted({str(Path(s[k]).parent) for s in tl.data["sources"].values()
                        for k in ("original_video", "audio")})
    try:
        result = render.render_preview(tl, work_dir, name=args.name, settings=settings,
                                       film_range=film_range, timeline_hash=timeline.file_hash(path),
                                       protected=originals)
    except (render.RenderError, safety.OriginalsProtectedError) as e:
        fail(str(e), 1)
    print(f"Preview: {result.path}")
    print(f"  {result.frames} frames, {result.seconds:.2f} s; "
          f"{len(result.rendered)} rendered, {len(result.cached)} reused; "
          f"{result.elapsed_s:.1f} s")
    for w in result.warnings:
        print(f"  warning: {w}")


def change_timeline(args, change, file_checks=False):
    """Load the timeline file, apply `change(data) -> new data`, validate, and save it with a
    snapshot of the old file (SPECS 4.1). Exits with 2 when the file is missing, 1 when it or the
    result breaks a rule. Returns the Timeline that was written."""
    import store
    import timeline

    path = default_timeline_path(args)
    try:
        tl = timeline.Timeline.load(path)
    except FileNotFoundError:
        print(f"Error: timeline file not found: {path}", file=sys.stderr)
        sys.exit(2)
    except timeline.TimelineError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        new = timeline.Timeline(change(tl.data), tl.duplicates)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    findings = new.validate(file_checks=file_checks)
    if timeline.has_errors(findings):
        print(json.dumps([f.as_dict() for f in findings], indent=2))
        print("The change breaks a rule of the timeline file; nothing was saved.", file=sys.stderr)
        sys.exit(1)
    store.save(new, path)
    return new


def cmd_loudness(args):
    """Set or show the loudness of the film (decision D9; SPECS 8.4)."""
    import edits
    import timeline

    if args.loudness_command == "show":
        path = default_timeline_path(args)
        try:
            tl = timeline.Timeline.load(path)
        except FileNotFoundError:
            print(f"Error: timeline file not found: {path}", file=sys.stderr)
            sys.exit(2)
        except timeline.TimelineError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        block = (tl.data.get("audio") or {}).get("loudness") or {}
        print(json.dumps(block, indent=2))
        return
    change_timeline(args, lambda d: edits.set_loudness(
        d, args.mode, target_lufs=args.target, true_peak_dbtp=args.peak, max_gain_db=args.max_gain),
        file_checks=not args.no_file_checks)
    print(f"Loudness set: mode {args.mode}"
          + ("" if args.mode == "off" else f", target {args.target if args.target is not None else config.LOUDNESS_TARGET_LUFS:g} LUFS"))


def timeline_originals(data: dict) -> list:
    """The folders that hold the originals a timeline file uses (nothing is written there)."""
    return sorted({str(Path(s[k]).parent) for s in (data.get("sources") or {}).values()
                   for k in ("original_video", "audio") if isinstance(s.get(k), str)})


def cmd_look(args):
    """List the creative LUTs, or choose the base or the creative LUT of the film (SPECS 8.2)."""
    import look
    import safety

    def fail(message, code=1):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    folder = args.luts or look.lut_dir()
    if args.look_command == "list":
        rows = look.list_luts(folder)
        if not rows:
            fail(f"no .cube files found{'' if folder else ': set VET_LUT_DIR or pass --luts DIR'}"
                 f"{' in ' + folder if folder else ''}", 1)
        for r in rows:
            note = r.get("problem") or f"{r['size']} points" + (f", {r['title']}" if r.get("title") else "")
            credit = f"  credit: {look.plain_credit(r['credit'])}" if r.get("credit") else ""
            print(f"{r['id']:40} {note}{credit}")
        return

    chosen = [bool(args.lut), bool(args.no_lut), bool(args.base)]
    if sum(chosen) != 1:
        fail("give exactly one of --lut, --no-lut and --base", 2)
    work_dir = args.work
    if not work_dir and args.lut:
        project_file = args.project or config.PROJECT_FILE
        if Path(project_file).exists():
            work_dir = Project.load(project_file).work_dir
        if not work_dir:
            fail("No work directory. Pass --work DIR (a folder on the SSD) or set "
                 "work_dir in the project file.", 2)

    def change(data):
        try:
            if args.no_lut:
                return look.with_creative_lut(data, None)
            if args.base:
                return look.with_base_lut(data, args.base)
            path = args.lut if Path(args.lut).is_file() else look.find_lut(folder, args.lut)
            return look.with_creative_lut(
                data, look.install(path, work_dir, protected=timeline_originals(data)))
        except (look.LutError, safety.OriginalsProtectedError) as e:
            raise ValueError(str(e))

    new = change_timeline(args, change, file_checks=not args.no_file_checks)
    lut = new.data["look"].get("lut")
    if args.base:
        print(f"Base look set: {args.base}")
        print("The preview copies carry the old base look; run convert to make them again.")
    elif lut:
        print(f"Creative LUT set: {lut.get('name', lut['path'])} (after the base look)")
        if lut.get("credit"):
            print(f"Credit to publish with the film: {look.plain_credit(lut['credit'])}")
    else:
        print("No creative LUT: the film has the base look only.")


def _work_dir_of(args, fail):
    work_dir = getattr(args, "work", None)
    if not work_dir:
        project_file = args.project or config.PROJECT_FILE
        if Path(project_file).exists():
            work_dir = Project.load(project_file).work_dir
    if not work_dir:
        fail("No work directory. Pass --work DIR (a folder on the SSD) or set "
             "work_dir in the project file.")
    return work_dir


def cmd_export(args):
    """List, add, switch on and off the exports of the timeline file (SPECS 5.6, 8.5)."""
    import export
    import timeline

    def fail(message, code=2):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    path = default_timeline_path(args)
    if args.export_command == "list":
        try:
            tl = timeline.Timeline.load(path)
        except FileNotFoundError:
            fail(f"timeline file not found: {path}")
        except timeline.TimelineError as e:
            fail(str(e), 1)
        frames = tl.film_length_frames()
        work_dir = args.work or (Project.load(args.project or config.PROJECT_FILE).work_dir
                                 if Path(args.project or config.PROJECT_FILE).exists() else "")
        rows = tl.data.get("exports") or []
        if not rows:
            print("The timeline file has no exports. Add one: python main.py export add --preset film-4k")
        for e in rows:
            last = export.latest_render(work_dir, e["name"]) if work_dir else None
            state = "on " if e.get("enabled", True) else "off"
            size = export.estimate_bytes(e, frames, tl.fps) / 1e9
            done = "" if not last else f"  last render: {'ok' if last['ok'] else 'FAILED'} " \
                f"{last['finished_at']}, {last['speed']['video_fps']} fps"
            print(f"{e['name']:16} {state} {e['width']}x{e['height']}  {e['video']['codec']:20} "
                  f"about {size:.1f} GB  subtitles {export.subtitle_form(e, tl.data['kind'])}  "
                  f"{e['output']}{done}")
        return
    if args.export_command == "set":
        import edits
        change_timeline(args, lambda d: edits.apply_operations(
            d, [{"op": "set_export_subtitles", "export": args.name, "form": args.subtitles}]))
        print(f"Export {args.name}: subtitles {args.subtitles}")
        return
    if args.export_command in ("enable", "disable"):
        on = args.export_command == "enable"
        change_timeline(args, lambda d: export.with_export_enabled(d, args.name, on))
        print(f"Export {args.name}: {'on' if on else 'off'}")
        return
    if args.export_command == "add":
        work_dir = _work_dir_of(args, fail) if not args.output_dir else None
        out_dir = args.output_dir or os.path.join(work_dir, "renders")

        made = {}

        def add(data):
            made["entry"] = export.preset_entry(args.preset, out_dir, data["film"]["frame"],
                                                name=args.name, width=args.width, height=args.height)
            if args.subtitles:
                made["entry"]["subtitles"] = args.subtitles
            return export.with_export(data, made["entry"], replace=args.replace)

        change_timeline(args, add)
        e = made["entry"]
        state = f"off (switch on with: export enable {e['name']})" if e.get("enabled") is False else "on"
        print(f"Export {e['name']} added: {e['width']}x{e['height']}, {e['video']['codec']}, {state}")
        print(f"File: {e['output']}")


def cmd_render(args, decoder=None):
    """Render the final film from the original .braw files (SPECS 5.3, 8.5)."""
    import export
    import safety
    import timeline
    from decoder import BrawSourceDecoder

    def fail(message, code=2):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    if bool(args.export) == bool(args.all):
        fail("give --export NAME or --all")
    path = default_timeline_path(args)
    try:
        tl = timeline.Timeline.load(path)
    except FileNotFoundError:
        fail(f"timeline file not found: {path}")
    except timeline.TimelineError as e:
        fail(str(e), 1)
    work_dir = _work_dir_of(args, fail)
    names = [e["name"] for e in tl.data.get("exports") or [] if e.get("enabled", True)] if args.all \
        else [args.export]
    if not names:
        fail("the timeline file has no export that is switched on; add one with: "
             "python main.py export add --preset film-4k")
    if args.detach:
        argv = [sys.executable, os.path.abspath(__file__)] + [a for a in sys.argv[1:] if a != "--detach"]
        tag = names[0] if len(names) == 1 else "all"
        log_path = os.path.join(work_dir, "reports", f"render-{tag}.log")
        pid_path = os.path.join(work_dir, "reports", f"render-{tag}.pid")
        try:
            pid = export.spawn_detached(argv, log_path, pid_path)
        except export.AlreadyRunning as e:
            fail(str(e), 1)
        print(f"Render started as process {pid}, in a session of its own.")
        print(f"  Log:     {log_path}")
        print(f"  Process: {pid_path}")
        print(f"  Watch:   tail -f '{log_path}'")
        print(f"  Stop:    kill {pid}   (SIGTERM: the render removes its files and exits)")
        print(f"  Restart: run the same command again; a long film goes on with its finished chunks, a short "
              "one starts from the beginning (--fresh starts a long one over).")
        return
    decoder = decoder or BrawSourceDecoder()
    try:
        decoder.check()
    except RuntimeError as e:
        fail(str(e))
    findings = tl.validate(file_checks=False)
    if timeline.has_errors(findings):
        print(json.dumps([f.as_dict() for f in findings], indent=2))
        fail("The timeline is not valid, so nothing was rendered.", 1)
    originals = timeline_originals(tl.data)
    install_graceful_stop()
    failed = False
    try:
        for name in names:
            form = args.subtitles
            if args.lang and args.lang != config.SUBTITLE_LANGUAGE and not form:
                # asking for a language means drawing it: the form of the export when it draws, else burned
                entry = next((e for e in tl.data.get("exports") or [] if e.get("name") == name), {})
                own = export.subtitle_form(entry, tl.data["kind"])
                form = own if own in ("burned", "both") else "burned"
            try:
                result = export.render_export(
                    tl, name, work_dir, decoder, timeline_hash=timeline.file_hash(path),
                    protected=originals, force=args.force, subtitles=form, subtitle_lang=args.lang,
                    chunk_seconds=args.chunk_seconds, fresh=args.fresh,
                    log=lambda s: print(f"{time.strftime('%H:%M:%S')}  {s}", flush=True))
            except (export.ExportError, safety.OriginalsProtectedError) as e:
                print(f"Error: {e}", file=sys.stderr, flush=True)
                failed = True
                continue
            for w in result.warnings:
                print(f"  warning: {w}", flush=True)
            if result.srt_file:
                print(f"  subtitles file: {result.srt_file}", flush=True)
            if result.credit_file:
                print(f"  credit for the video description: {result.credit_file}", flush=True)
            failed = failed or not result.ok
    except KeyboardInterrupt:
        print("Interrupted. The file that was being written was removed. Run the same command again: a long film goes "
              "on with the chunks that are finished, a short one starts over.", flush=True)
        sys.exit(130)
    sys.exit(1 if failed else 0)


def _id_list(text):
    """'s003, s004' -> ['s003', 's004']"""
    return [p for p in str(text or "").replace(",", " ").split() if p]


def cmd_short(args, decoder=None):
    """Make a vertical short from the film (SPECS 11): plan it, save the plan next to the output, render it from the
    .braw files, and optionally write a still of each scene. A timeline file of kind short is rendered as it is."""
    import export
    import safety
    import shorts
    import store
    import timeline
    from decoder import BrawSourceDecoder

    def fail(message, code=1):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    tl, path = _load_timeline_or_exit(args)
    findings = tl.validate(file_checks=False)
    if timeline.has_errors(findings):
        print(json.dumps([f.as_dict() for f in findings], indent=2))
        fail("The timeline is not valid, so nothing was done.", 1)
    originals = timeline_originals(tl.data)
    log = lambda s: print(f"{time.strftime('%H:%M:%S')}  {s}", flush=True)

    if tl.data["kind"] == "short":
        given = [n for n, v in (("--length", args.length), ("--size", args.size), ("--prefer", args.prefer),
                                ("--only", args.only), ("--exclude", args.exclude), ("--output", args.output),
                                ("--subtitles", args.subtitles)) if v]
        if given:
            fail(f"{path} is a short that is already planned, and a plan is rendered as it is. {', '.join(given)} "
                 "belong to a new short: give the film file instead. To change this short, edit the plan file "
                 "(its scenes, crops and subtitles) or use the `scenes` and `subtitles` commands on it.", 2)
        plan = None
        short_tl = tl
        entry = next((e for e in short_tl.data.get("exports") or [] if e.get("enabled", True)), None)
        if entry is None:
            fail("the short has no export that is switched on", 1)
        out_path, plan_path = entry["output"], path
        print(shorts.format_plan(_plan_from_file(short_tl), os.path.basename(path), plan_path), end="")
        if args.dry_run or args.no_render:
            print("Dry run: nothing was written." if args.dry_run else "The plan is as it is; nothing to write.")
            return
    else:
        size = parse_size(args.size) if args.size else config.SHORT_FRAME
        work = None
        if args.output is None or not (args.dry_run or args.no_render):
            work = _work_dir_of(args, lambda m: fail(m, 2))
        length = args.length if args.length is not None else config.SHORT_DEFAULT_LENGTH_SEC
        out_path = os.path.abspath(args.output) if args.output else os.path.join(
            work, "exports", f"short-{length:g}.mp4")
        try:
            plan = shorts.plan_short(tl.data, film_path=os.path.abspath(path), length_s=length, size=size,
                                     prefer=_id_list(args.prefer), only=_id_list(args.only),
                                     exclude=_id_list(args.exclude), output=out_path,
                                     film_hash=timeline.file_hash(path), subtitles=args.subtitles or "burned")
        except shorts.ShortError as e:
            fail(str(e))
        print(shorts.format_plan(plan, os.path.basename(path)), end="")
        try:
            safety.assert_not_inside(out_path, originals)
        except safety.OriginalsProtectedError as e:
            fail(str(e), 2)
        if args.dry_run:
            print("Dry run: nothing was written.")
            return
        plan_path = plan.plan_path
        os.makedirs(os.path.dirname(plan_path), exist_ok=True)
        short_tl = timeline.Timeline(plan.data)
        existed = os.path.isfile(plan_path)
        store.save(short_tl, plan_path)
        if existed:
            snapshot = os.path.basename(store.list_snapshots(plan_path)[-1])[:-5]
            print(f"The plan file existed; the version before is snapshot {snapshot} (`timeline undo --file {plan_path}`).")
        print(f"Plan saved: {plan_path}")
        if args.no_render:
            print(f"Render it with: python main.py short --file {plan_path} --work <work folder>")
            return

    work = _work_dir_of(args, lambda m: fail(m, 2))
    name = next(e for e in short_tl.data["exports"] if e.get("enabled", True))["name"]
    if args.detach:
        argv = [sys.executable, os.path.abspath(__file__), "short", "--file", plan_path, "--work", work]
        if args.stills:
            argv.append("--stills")
        if args.lang:
            argv += ["--lang", args.lang]
        log_path = os.path.join(work, "reports", f"short-{name}.log")
        pid_path = os.path.join(work, "reports", f"short-{name}.pid")
        try:
            pid = export.spawn_detached(argv, log_path, pid_path)
        except export.AlreadyRunning as e:
            fail(str(e), 1)
        print(f"Render started as process {pid}, in a session of its own.")
        print(f"  Log:     {log_path}")
        print(f"  Watch:   tail -f '{log_path}'")
        print(f"  Stop:    kill {pid}   (SIGTERM: the render removes its files and exits)")
        return
    decoder = decoder or BrawSourceDecoder()
    try:
        decoder.check()
    except RuntimeError as e:
        fail(str(e), 2)
    install_graceful_stop()
    started = time.time()
    try:
        result = export.render_export(short_tl, name, work, decoder, timeline_hash=timeline.file_hash(plan_path),
                                      protected=originals, report_name="short-report", subtitles=args.subtitles,
                                      subtitle_lang=args.lang, log=log)
    except (export.ExportError, safety.OriginalsProtectedError) as e:
        fail(str(e))
    except KeyboardInterrupt:
        print("Interrupted. The files of the render were removed. Run the same command again to start over.", flush=True)
        sys.exit(130)
    for w in result.warnings:
        print(f"  warning: {w}", flush=True)
    if result.srt_file:
        print(f"  subtitles file: {result.srt_file}", flush=True)
    if result.credit_file:
        print(f"  credit for the video description: {result.credit_file}", flush=True)
    if not result.ok:
        print("The file failed its checks: " + "; ".join(result.problems), file=sys.stderr)
        sys.exit(1)
    print(f"Short: {result.path}  {result.frames} frames, {result.seconds:.3f} s, {result.bytes / 1e6:.1f} MB, "
          f"{result.mbps} Mbit/s, rendered in {time.time() - started:.0f} s")
    if args.stills:
        try:
            made = shorts.scene_stills(short_tl.data, result.path)
        except shorts.ShortError as e:
            fail(str(e))
        print(f"Stills: {made['folder']}  (one per scene)")
        print(f"Contact sheet: {made['contact']}")


def _plan_from_file(short_tl):
    """A ShortPlan to print for a short that is read from its file (nothing is planned again)."""
    import shorts
    data = short_tl.data
    f = data["film"]["fps"]
    fps = Fraction(f["num"], f["den"])
    scenes, at = [], 0
    for seg in data["segments"]:
        if not seg.get("enabled", True):
            continue
        n = seg["out"] - seg["in"]
        scenes.append({"id": seg["id"], "film_id": seg.get("x_film_segment", "?"), "source": seg["source"],
                       "size": data["sources"][seg["source"]]["size"],
                       "film_in_s": seg.get("x_film_in_s", 0.0), "film_out_s": seg.get("x_film_out_s", 0.0),
                       "short_in_s": round(float(Fraction(at) / fps), 3),
                       "short_out_s": round(float(Fraction(at + n) / fps), 3), "frames": n, "scene_frames": n,
                       "crop": seg.get("crop"), "reason": seg.get("note", ""), "tier": 0,
                       "cues": {"kept": [c["id"] for c in (data.get("subtitles") or {}).get("cues", [])
                                         if c["anchor"] == seg["id"]], "dropped": [], "pieces": 0}})
        at += n
    target = round(data["derived_from"]["target_length_s"] * fps)
    return shorts.ShortPlan(data=data, plan_path="", target_frames=int(target), frames=at, fps=fps, scenes=scenes,
                            pool_scenes=len(scenes))


def _words_change(args, ops, file_checks=False):
    """Apply operations of words.py through the validator and save with a snapshot. Prints what the
    operations noted. Returns (Timeline before, Timeline after)."""
    import edits
    import timeline
    import words
    before = {}
    notes = []

    def change(data):
        before["data"] = data
        try:
            return edits.apply_operations(data, ops, notes)
        except words.NeedsConfirmation as e:
            raise ValueError(f"{e} Cues: {', '.join(e.cues)}. Run the command again with --replace-adjusted "
                             "to replace them, or leave the scene as it is.")

    new = change_timeline(args, change, file_checks=file_checks)
    for n in notes:
        print(f"  note: {n}")
    return timeline.Timeline(before["data"]), new


def _new_ids(before, after, key):
    old = {x["id"] for x in (before.data.get(key) if key == "titles" else (before.data.get("subtitles") or {}).get("cues")) or []}
    now = after.data.get(key) if key == "titles" else (after.data.get("subtitles") or {}).get("cues")
    return [x["id"] for x in now or [] if x["id"] not in old]


def _load_timeline_or_exit(args):
    import timeline
    path = default_timeline_path(args)
    try:
        return timeline.Timeline.load(path), path
    except FileNotFoundError:
        print(f"Error: timeline file not found: {path}", file=sys.stderr)
        sys.exit(2)
    except timeline.TimelineError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_subtitles(args):
    """The subtitles of the film (SPECS 9): list, write by hand, a text box per scene, Whisper, import,
    SRT, translations."""
    import edits
    import export
    import timeline
    import words

    def fail(message, code=1):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    sub = args.subtitles_command
    if sub == "list":
        tl, path = _load_timeline_or_exit(args)
        rows = words.cue_spans(tl.data)
        shown = {r["id"] for r in rows}
        off = [c for c in (tl.data.get("subtitles") or {}).get("cues", []) if c["id"] not in shown]
        if args.json:
            print(json.dumps([{"id": r["id"], "segment": r["segment"], "start_s": round(r["start_s"], 3),
                               "end_s": round(r["end_s"], 3), "text": r["text"],
                               "origin": r["cue"].get("origin", "manual"), "locked": bool(r["cue"].get("locked"))}
                              for r in rows], indent=2))
            return
        if not rows and not off:
            print("The film has no subtitles. Write them: python main.py subtitles text SCENE --text \"...\", "
                  "or run: python main.py subtitles auto")
        for r in rows:
            flag = ("locked " if r["cue"].get("locked") else "") + r["cue"].get("origin", "manual")
            print(f"{r['id']}  {edits.clock(r['start_s'])} - {edits.clock(r['end_s'])}  {r['segment']}  "
                  f"{flag:12} {r['text'].replace(chr(10), ' / ')}")
        for c in off:
            print(f"{c['id']}  (scene {c['anchor']} is not in the film)  {c['text'].replace(chr(10), ' / ')}")
        return
    if sub == "auto":
        return cmd_subtitles_auto(args)
    if sub == "translate":
        return cmd_subtitles_translate(args)
    if sub == "search":
        import transcribe
        tl, path = _load_timeline_or_exit(args)
        work = _work_dir_of(args, fail)
        try:
            hits = transcribe.search(tl, work, args.words, include_dropped=args.all)
        except transcribe.WhisperError as e:
            fail(str(e), 2)
        if not hits:
            print("Nothing found. (Search reads the transcripts that `subtitles auto` made; add --all to include the "
                  "phrases it dropped.)")
        for h in hits:
            where = f"{edits.clock(h['film_s'])} in the film ({h['segment']})" if h["film_s"] is not None \
                else "not in the film"
            print(f"{h['clip']}  {edits.clock(h['wav_s'])} in the sound  {where}  {h['text']}"
                  + (f"  [dropped: {h['dropped']}]" if h["dropped"] else ""))
        sys.exit(0 if hits else 1)
    if sub == "check":
        return cmd_subtitles_check(args)
    if sub == "export":
        tl, path = _load_timeline_or_exit(args)
        out = args.out
        if not out:
            exports = tl.data.get("exports") or []
            entry = next((e for e in exports if e["name"] == args.export), None) if args.export else \
                (exports[0] if exports else None)
            if entry is None:
                fail("name the file with --out, or an export of the timeline file with --export", 2)
            out = export.srt_path(entry["output"])
        spans = words.cue_spans(tl.data)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        import fsutil
        fsutil.write_text_atomic(out, words.srt_text(spans, words.RULES_FACTORY(tl.data)))
        print(f"{len(spans)} subtitle(s) written to {out}")
        return

    if sub == "add":
        end = args.end if args.end is not None else (args.at + args.length if args.length else None)
        ops = [{"op": "add_cue", "film_start_s": args.at, "film_end_s": end, "text": args.text}]
    elif sub == "edit":
        op = {"op": "edit_cue", "cue": args.cue}
        for key, value in (("text", args.text), ("film_start_s", args.start), ("film_end_s", args.end)):
            if value is not None:
                op[key] = value
        ops = [op]
    elif sub == "remove":
        ops = [{"op": "remove_cue", "cues": args.cues}]
    elif sub == "shift":
        ops = [{"op": "shift_cues", "seconds": args.seconds,
                "cues": args.cues.split(",") if args.cues else None,
                "from_s": args.from_s, "to_s": args.to_s}]
    elif sub == "sort":
        ops = [{"op": "sort_cues"}]
    elif sub == "text":
        if args.text_file == "-":
            text = sys.stdin.read()
        elif args.text_file:
            try:
                text = Path(args.text_file).read_text(encoding="utf-8")
            except OSError as e:
                fail(str(e), 2)
        elif args.text is not None:
            text = args.text
        else:
            fail("give --text, or --text-file FILE (- for standard input)", 2)
        ops = [{"op": "set_scene_text", "segment": args.segment, "text": text,
                "replace_adjusted": args.replace_adjusted}]
    elif sub == "import":
        try:
            raw = Path(args.file_to_import).read_text(encoding="utf-8")
            if args.file_to_import.lower().endswith(".srt"):
                items = words.parse_srt(raw)
            else:
                data = json.loads(raw)
                items = data.get("cues") if isinstance(data, dict) else data
        except (OSError, ValueError, words.WordsError) as e:
            fail(f"cannot read {args.file_to_import}: {e}")
        ops = [{"op": "import_cues", "cues": items, "replace": args.replace}]
    before, after = _words_change(args, ops)
    if sub in ("add", "import", "text"):
        made = _new_ids(before, after, "cues")
        if sub == "text":
            scene = [c for c in after.data["subtitles"]["cues"] if c["anchor"] == args.segment]
            print(f"Scene {args.segment}: {len(scene)} cues from the text")
        else:
            print(f"{len(made)} cue(s) added: {', '.join(made)}")
    else:
        print({"edit": "Cue changed", "remove": "Cue removed", "shift": "Cues moved", "sort": "Cues sorted"}[sub])


def cmd_subtitles_check(args):
    """Which cues do not fit the safe area at the size of the film, and the readability warnings of the
    validator (SPECS 9.3)."""
    import render
    import textlayer
    import textlayout
    import timeline
    tl, path = _load_timeline_or_exit(args)
    problems = []
    plan = render.make_plan(tl, preview_width=None)
    count = len((tl.data.get("subtitles") or {}).get("cues", []))
    text = textlayer.build(tl.data, plan.frame, 0, plan.film_frames, render.segment_rects(plan), burn_titles=False)
    problems += text.warnings
    for f in tl.validate(file_checks=False):
        if f.rule in ("V19", "V24") and f.level == "warning":
            problems.append(f"{f.rule} {f.path}: {f.message}")
    if not textlayout.fonts_available():
        print("note: no Inter font (VET_FONT_DIR), so widths are estimated, not measured")
    print(f"{count} cue(s) checked.")
    for p in problems:
        print(f"  {p}")
    sys.exit(1 if problems else 0)


def cmd_subtitles_auto(args):
    """Whisper subtitles for the scenes of the film (SPECS 9.2)."""
    import fsutil
    import store
    import timeline
    import transcribe
    tl, path = _load_timeline_or_exit(args)
    work = _work_dir_of(args, lambda m, code=2: (print(f"Error: {m}", file=sys.stderr), sys.exit(code)))
    before = timeline.file_hash(path)
    only = [s for s in args.only.split(",") if s] if args.only else None
    try:
        data, report = transcribe.auto_subtitles(
            tl, work, only=only, redo=args.redo, stock=not args.keep_stock_phrases,
            log=lambda s: print(f"{time.strftime('%H:%M:%S')}  {s}", flush=True))
    except transcribe.WhisperError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    new = timeline.Timeline(data, tl.duplicates)
    findings = new.validate(file_checks=False)
    if timeline.has_errors(findings):
        print(json.dumps([f.as_dict() for f in findings], indent=2))
        print("The cues break a rule of the timeline file; nothing was saved.", file=sys.stderr)
        sys.exit(1)
    try:
        store.save(new, path, expected_hash=before)
    except store.StaleHash as e:
        print(f"Error: {e} The transcripts are kept; run the command again.", file=sys.stderr)
        sys.exit(1)
    report_path = os.path.join(work, "reports", f"{args.report_name}.json")
    fsutil.write_json_atomic(report_path, report)
    print(f"{report['cues']} cue(s) made from {len(report['clips'])} clip(s) in {report['seconds']:.0f} s"
          + (f" ({report['transcribe_s']:.0f} s of Whisper for {report['audio_s'] / 60:.1f} min of sound, "
             f"{report.get('seconds_per_minute_of_audio', 0):.1f} s per minute)" if report["audio_s"] else
             " (the transcripts of an earlier run were used)"))
    print(f"{len(report['dropped'])} phrase(s) dropped as not speech or not trusted; the report lists them and the "
          f"reasons: {report_path}")


def cmd_subtitles_translate(args):
    """Translate the English cues into more languages and write one SRT file per language (SPECS 9.9)."""
    import translate
    return translate.cli(args, default_timeline_path(args), _work_dir_of)


def cmd_title(args):
    """Titles drawn into the picture (SPECS 9.7): add, change, remove, list, import."""
    import edits
    import timeline
    import words

    sub = args.title_command
    if sub == "list":
        tl, path = _load_timeline_or_exit(args)
        titles = tl.data.get("titles") or []
        if not titles:
            print("The film has no titles. Add one: python main.py title add --text \"Sunday kitchen\" --start 1 --length 4")
        for x in titles:
            print(f"{x['id']}  {x['anchor']:5} {x['start_s']:g} - {x['end_s']:g} s  "
                  f"{x.get('template', 'center'):11} {x['text']}")
        return
    if sub == "add":
        op = {"op": "add_title", "text": args.text, "start_s": args.start, "anchor": args.anchor}
        for key, value in (("end_s", args.end), ("length_s", args.length), ("template", args.template),
                           ("size_pct", args.size), ("fade_s", args.fade)):
            if value is not None:
                op[key] = value
        ops = [op]
    elif sub == "edit":
        op = {"op": "edit_title", "title": args.title}
        for key, value in (("text", args.text), ("start_s", args.start), ("end_s", args.end),
                           ("length_s", args.length), ("anchor", args.anchor), ("template", args.template),
                           ("size_pct", args.size), ("fade_s", args.fade)):
            if value is not None:
                op[key] = value
        ops = [op]
    elif sub == "remove":
        ops = [{"op": "remove_title", "title": args.title}]
    elif sub == "import":
        try:
            data = json.loads(Path(args.file_to_import).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"Error: cannot read {args.file_to_import}: {e}", file=sys.stderr)
            sys.exit(1)
        ops = [{"op": "import_titles", "titles": data.get("titles") if isinstance(data, dict) else data,
                "replace": args.replace}]
    before, after = _words_change(args, ops)
    if sub in ("add", "import"):
        print(f"{len(_new_ids(before, after, 'titles'))} title(s) added: {', '.join(_new_ids(before, after, 'titles'))}")
    else:
        print({"edit": "Title changed", "remove": "Title removed"}[sub])



def _originals_of(tl) -> list:
    return sorted({str(Path(src[k]).parent) for src in tl.data["sources"].values()
                   for k in ("original_video", "audio")})


def cmd_scenes(args):
    """Prompt edits (SPECS 10): the scene index and sheets Claude Code reads, and the commands that
    remove, keep, cut and restore scenes."""
    if args.scenes_command in ("remove", "keep", "restore", "cut"):
        return cmd_scenes_edit(args)
    if args.scenes_command in ("pin", "unpin", "crop"):
        return cmd_scenes_mark(args)
    return cmd_scenes_read(args)


def cmd_scenes_edit(args):
    """remove, keep, restore and cut (SPECS 10.4). Each goes through the validator and keeps the old file as one
    snapshot; --dry-run prints what would change and writes nothing."""
    import sceneedit
    import store
    import timeline

    def fail(message, code=1):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    sub = args.scenes_command
    tl, path = _load_timeline_or_exit(args)
    notes = []
    try:
        if sub == "cut":
            ids = [args.scene]
            new_data = sceneedit.cut(tl.data, args.scene, args.from_s, args.to_s, notes)
            command = f"scenes cut {args.scene} --from {args.from_s:g} --to {args.to_s:g}"
        else:
            ids = sceneedit.parse_ids(args.scene_ids)
            new_data = getattr(sceneedit, sub)(tl.data, ids, notes)
            command = f"scenes {sub} {','.join(ids)}"
    except sceneedit.SceneEditError as e:
        fail(str(e), 2 if str(e).startswith("name at least one") else 1)
    new = timeline.Timeline(new_data, tl.duplicates)
    findings = new.validate(file_checks=False)
    if timeline.has_errors(findings):
        print(json.dumps([f.as_dict() for f in findings], indent=2))
        fail("The change breaks a rule of the timeline file; nothing was saved." if not args.dry_run else
             "The change would break a rule of the timeline file, so it would be refused. Nothing was written.")
    rep = sceneedit.report(tl, new, notes, sceneedit.new_warnings(tl.validate(file_checks=False), findings))
    print(sceneedit.format_report(rep, command, args.dry_run), end="")
    if rep["no_change"] or args.dry_run:
        return
    store.save(new, path)
    snapshot = os.path.basename(store.list_snapshots(path)[-1])[:-5]
    print(f"Saved {os.path.basename(path)}. The version before is snapshot {snapshot}: "
          f"`python main.py timeline undo` takes this change back.")
    print("Next: `python main.py timeline validate`; the film times after this change have moved, so run "
          "`python main.py scenes index` again before reading it.")


def cmd_scenes_mark(args):
    """pin, unpin and crop (SPECS 11.3, 11.5): the marks the shorts read. The film does not change. Each goes through the
    validator and makes one snapshot; --dry-run writes nothing. `crop --still` draws a picture of the window on a frame."""
    import sceneedit
    import shorts
    import store
    import timeline
    import words

    def fail(message, code=1):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    sub = args.scenes_command
    tl, path = _load_timeline_or_exit(args)
    notes = []
    try:
        if sub == "crop":
            ids = [args.scene]
            if args.still and not (args.x is not None or args.from_x is not None or args.to_x is not None or args.clear):
                new_data = tl.data                              # only look at the window as it is
            else:
                new_data = sceneedit.crop(tl.data, args.scene, x=args.x, x_from=args.from_x, x_to=args.to_x,
                                          clear=args.clear, notes=notes)
            what = "--clear" if args.clear else f"--x {args.x:g}" if args.x is not None else \
                f"--from {args.from_x:g} --to {args.to_x:g}" if args.from_x is not None else "--still"
            command = f"scenes crop {args.scene} {what}"
        else:
            ids = sceneedit.parse_ids(args.scene_ids)
            new_data = getattr(sceneedit, sub)(tl.data, ids, notes)
            command = f"scenes {sub} {','.join(ids)}"
    except sceneedit.SceneEditError as e:
        fail(str(e), 2 if str(e).startswith(("name at least one", "say where")) else 1)
    new = timeline.Timeline(new_data, tl.duplicates)
    findings = new.validate(file_checks=False)
    if timeline.has_errors(findings):
        print(json.dumps([f.as_dict() for f in findings], indent=2))
        fail("The change breaks a rule of the timeline file; nothing was saved." if not args.dry_run else
             "The change would break a rule of the timeline file, so it would be refused. Nothing was written.")
    segs_before = {s["id"]: s for s in tl.data["segments"]}
    segs_after = {s["id"]: s for s in new.data["segments"]}
    changed = tl.dumps() != new.dumps()
    lines = [command + ("   [dry run: nothing was written]" if args.dry_run else "")]
    rows = {r["seg"]["id"]: r for r in words.film_segments(new.data)}
    for sid in ids:
        before, after = segs_before[sid], segs_after[sid]
        row = rows.get(sid)
        where = f"film {edits_clock(row['start_s'])} - {edits_clock(row['start_s'] + row['length_s'])}" if row \
            else "removed from the film"
        if sub == "crop":
            show = lambda s: "centre" if not s.get("crop") else (f"x {s['crop']['x']:g}" if "x" in s["crop"] else
                                                                  f"pan from x {s['crop']['x_from']:g} to x {s['crop']['x_to']:g}")
            lines.append(f"  {sid}  {where}  clip {after['source']}  crop: {show(before)} -> {show(after)}")
            info = shorts.window_info(new.data["sources"][after["source"]]["size"], _short_size(args), after.get("crop"))
            lines.append(f"      {shorts.describe_window(info)}")
        else:
            was = before.get("priority")
            if sub == "pin":
                state = "pinned (it was marked drop)" if was == "drop" else "pinned"
            else:
                state = {"must": "no longer pinned", "drop": "no longer marked drop"}.get(was, "had no mark")
            lines.append(f"  {sid}  {where}  clip {after['source']}  {state}"
                         + ("" if row else "  (removed from the film)"))
    if sub in ("pin", "unpin"):
        lines.append("The film itself does not change. A short made from it keeps the pinned scenes." if sub == "pin"
                     else "The film itself does not change. Shorts treat these scenes like any other.")
    if notes:
        lines.append("Notes:")
        lines += [f"  {n}" for n in notes]
    print("\n".join(lines))
    if sub == "crop" and args.still:
        work = _work_dir_of(args, lambda m: fail(m, 2))
        try:
            made = shorts.draw_window_still(new, args.scene, work, _short_size(args), at_s=args.at)
        except shorts.ShortError as e:
            fail(str(e))
        print(f"Still: {made['path']}")
    if not changed or args.dry_run:
        return
    store.save(new, path)
    snapshot = os.path.basename(store.list_snapshots(path)[-1])[:-5]
    print(f"Saved {os.path.basename(path)}. The version before is snapshot {snapshot}: "
          f"`python main.py timeline undo` takes this change back.")
    if sub == "crop":
        print("Next: `python main.py short --dry-run` shows the short; `python main.py scenes crop SCENE --still` "
              "shows the window on a frame.")


def edits_clock(seconds: float) -> str:
    import edits
    return edits.clock(seconds)


def _short_size(args):
    text = getattr(args, "size", None)
    if not text:
        return config.SHORT_FRAME
    return parse_size(text)


def parse_size(text: str):
    """'1080x1920' -> (1080, 1920). Exits with 2 for anything else."""
    try:
        w, h = (int(v) for v in str(text).lower().replace("*", "x").split("x"))
    except ValueError:
        print(f"Error: the size must be WIDTHxHEIGHT, for example 1080x1920, not {text!r}", file=sys.stderr)
        sys.exit(2)
    return w, h


def cmd_scenes_read(args):
    """index, sheets and frames: what Claude Code reads to find a scene."""
    import edits
    import safety
    import sceneindex

    def fail(message, code=1):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    sub = args.scenes_command
    tl, path = _load_timeline_or_exit(args)
    work = _work_dir_of(args, lambda m: fail(m, 2))
    try:
        safety.assert_not_inside(work, _originals_of(tl))
    except safety.OriginalsProtectedError as e:
        fail(str(e), 2)

    if sub == "frames":
        try:
            every = sceneindex.parse_step(args.every)
        except ValueError as e:
            fail(str(e), 2)
        try:
            result = sceneindex.draw_frames(tl, path, work, args.scene, every, from_s=args.from_s, to_s=args.to_s)
        except sceneindex.SheetError as e:
            fail(str(e))
        for w in result["warnings"]:
            print(f"warning: {w}", file=sys.stderr)
        for p in result["paths"]:
            print(p)
        return

    if sub == "sheets" or (sub == "index" and args.sheets):
        try:
            report = sceneindex.draw_sheets(tl, path, work, log=lambda m: print(m, file=sys.stderr))
        except sceneindex.SheetError as e:
            fail(str(e))
        for w in report["warnings"]:
            print(f"warning: {w}", file=sys.stderr)
        if sub == "sheets":
            n = len(report["drawn"]) + len(report["reused"])
            pages = report["overview"]["drawn"] + report["overview"]["reused"]
            print(f"Sheets of {n} scenes in {report['folder']}/sheets: {len(report['drawn'])} drawn, "
                  f"{len(report['reused'])} kept, {len(report['deleted'])} deleted, {report['seconds']:.1f} s.")
            print(f"Overview pages: {len(pages)} ({len(report['overview']['drawn'])} drawn): "
                  f"{os.path.join(report['folder'], pages[0])} ... {os.path.join(report['folder'], pages[-1])}"
                  if pages else "Overview pages: none")
    index = sceneindex.write_index(tl, path, work)
    print(f"Index of {os.path.basename(path)} (hash {index['timeline_hash'][:8]}): {index['scenes_in_film']} scenes "
          f"in the film, {index['scenes_removed']} removed, film {edits.clock(index['film_seconds'])}.")
    print(f"  {index['paths']['markdown']}")
    print(f"  {index['paths']['json']}")
    drawn = sum(1 for r in index["scenes"] if r["sheet"]["exists"])
    if drawn < len(index["scenes"]):
        print(f"  {drawn} of {len(index['scenes'])} sheets are drawn. Draw the rest: "
              f"python main.py scenes sheets --file {path} --work {work}")
    if index["clips_without_transcript"]:
        print(f"  {len(index['clips_without_transcript'])} clip(s) have no transcript, so their scenes have no "
              f"'heard' lines.")


def cmd_export_shotcut(args):
    """Write the Shotcut project of the timeline file (SPECS 8.6)."""
    import export_shotcut
    import safety
    import timeline

    def fail(message, code=2):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    path = default_timeline_path(args)
    try:
        tl = timeline.Timeline.load(path)
    except FileNotFoundError:
        fail(f"timeline file not found: {path}")
    except timeline.TimelineError as e:
        fail(str(e), 1)
    work_dir = _work_dir_of(args, fail)
    out = args.out or os.path.join(work_dir, "film.mlt")
    langs = [] if args.lang.strip().lower() == "none" else [x for x in re.split(r"[,\s]+", args.lang) if x]
    by_lower = {c.lower(): c for c in config.SHOTCUT_SUBTITLE_TRACKS}
    bad = [x for x in langs if x.lower() not in by_lower]
    if bad:
        fail(f"unknown language {', '.join(bad)}. The languages are: {', '.join(config.SHOTCUT_SUBTITLE_TRACKS)}, or none.", 1)
    langs = [by_lower[x.lower()] for x in langs]
    try:
        result = export_shotcut.write_project(tl, work_dir, out, protected=timeline_originals(tl.data),
                                              check=not args.no_check, subtitle_langs=langs,
                                              log=lambda s: print(s, flush=True))
    except (export_shotcut.ShotcutError, safety.OriginalsProtectedError) as e:
        fail(str(e), 1)
    print(f"Shotcut project: {result.path}  ({result.segments} scenes, profile "
          f"{result.profile[0]}x{result.profile[1]})")
    if result.subtitle_tracks:
        print(f"Subtitle tracks: {', '.join(result.subtitle_tracks)} (the first is drawn over the picture; they are "
              "not read back by import-shotcut)")
    for w in result.warnings:
        print(f"  warning: {w}")
    print("Open it in Shotcut for hand work, save it, then: python main.py import-shotcut")


def cmd_import_shotcut(args):
    """Read the cut points and the order back from a saved Shotcut project (SPECS 8.6)."""
    import import_shotcut
    import timeline

    def fail(message, code=2):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    path = default_timeline_path(args)
    if not os.path.isfile(path):
        fail(f"timeline file not found: {path}")
    mlt = args.mlt or os.path.join(_work_dir_of(args, fail), "film.mlt")
    if not os.path.isfile(mlt):
        fail(f"Shotcut project not found: {mlt}")
    try:
        report = import_shotcut.read_back(mlt, path, dry_run=args.dry_run)
    except (import_shotcut.ShotcutImportError, timeline.TimelineError) as e:
        fail(str(e), 1)
    verb = "Would change" if args.dry_run else "Changed"
    if report.nothing_changed:
        print("The cut points and the order in the Shotcut project are those of the timeline file; "
              "nothing was changed.")
    for c in report.changed:
        print(f"  {c['id']}: in {c['in'][0]} -> {c['in'][1]}, out {c['out'][0]} -> {c['out'][1]}")
    if report.moved:
        print(f"  new order for: {', '.join(report.moved)}")
    if report.added:
        print(f"  new scenes: {', '.join(report.added)}")
    if report.removed:
        print(f"  removed (enabled: false): {', '.join(report.removed)}")
    if not report.nothing_changed:
        print(f"{verb} the timeline file {path}." + ("" if args.dry_run else
              " The old file is kept as a snapshot (python main.py timeline undo)."))
    if report.ignored:
        print("Not read (made in Shotcut, they do not come back):")
        for line in report.ignored:
            print(f"  - {line}")
    if report.subtitles:
        print("Subtitles in the project (never read back; the film's subtitles are in the timeline file):")
        for line in report.subtitles:
            print(f"  - {line}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Dual-System Audio Video Automation (BMPCC 4K + Zoom H2N)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {config.VERSION}")
    parser.add_argument("--project", "-p", help=f"Project file path (default: {config.PROJECT_FILE})")

    subparsers = parser.add_subparsers(dest="command")

    # ── app (default) ──
    p_app = subparsers.add_parser("app",
        help="Launch the web app (default)")
    p_app.add_argument("--port", type=int, default=config.SELECTOR_PORT,
        help=f"Port to run on (default: {config.SELECTOR_PORT})")
    p_app.add_argument("--timeline", metavar="FILE",
        help=f"Timeline file for the Film tab (default: {config.TIMELINE_FILE} next to the project file)")
    p_app.add_argument("--work", metavar="DIR",
        help="Work folder on the SSD (default: work_dir of the project file)")
    p_app.add_argument("--no-browser", action="store_true",
        help="Do not open a browser window")
    p_app.set_defaults(func=cmd_app)

    # ── match ──
    p_match = subparsers.add_parser("match",
        help="Match video files to audio files by audio fingerprinting")
    p_match.add_argument("--video", "-v", required=True,
        help="Directory containing BMPCC video files")
    p_match.add_argument("--audio", "-a", required=True,
        help="Directory containing Zoom H2N audio files")
    p_match.set_defaults(func=cmd_match)

    # ── rename ──
    p_rename = subparsers.add_parser("rename",
        help="Rename matched file pairs to share a common name")
    p_rename.add_argument("--pattern",
        help=f'Naming pattern (default: "{config.RENAME_PATTERN}")')
    p_rename.add_argument("--dry-run", action="store_true",
        help="Show what would be renamed without doing it")
    p_rename.add_argument("--undo",
        help="Path to rename_log.json to undo previous renames")
    p_rename.set_defaults(func=cmd_rename)

    # ── convert ──
    p_convert = subparsers.add_parser("convert",
        help="Make a small preview copy of every matched .braw clip (work folder on the SSD)")
    p_convert.add_argument("--work", "-w",
        help="Work folder on the SSD (default: work_dir of the project file). "
             "Never a folder that holds the originals.")
    p_convert.add_argument("--lut",
        help="Base LUT (.cube). Default: Blackmagic Gen 5 Film to Video, "
             "found in Resolve's LUT folder, or the file named by VET_BASE_LUT")
    p_convert.add_argument("--encoder", choices=["h264_videotoolbox", "libx264"],
        help=f"Encoder of the copies (default: {config.PREVIEW_ENCODER})")
    p_convert.add_argument("--limit", type=int,
        help="Convert only the first N matched clips")
    p_convert.add_argument("--only",
        help="Convert only the clips with these names (comma separated, no extension)")
    p_convert.add_argument("--dry-run", action="store_true",
        help="Show what would be converted and the estimated size, write nothing")
    p_convert.add_argument("--probe", metavar="BRAW",
        help="Print the facts of one clip as JSON and exit")
    p_convert.add_argument("--test", metavar="BRAW",
        help="Convert the first --seconds of one clip into <work>/test")
    p_convert.add_argument("--seconds", type=float,
        help="Length of the --test piece in seconds (default: the whole clip)")
    p_convert.set_defaults(func=cmd_convert)

    # ── analyse ──
    p_analyse = subparsers.add_parser("analyse",
        help="Analyse the clips: find the sync clap, find the steady scenes")
    p_analyse.add_argument("--clap", action="store_true",
        help="Find the sync clap at the start of each clip (camera audio and WAV)")
    p_analyse.add_argument("--scenes", action="store_true",
        help="Find the steady scenes of each clip in its preview copy "
             "(only copies whose status in conversion.json is done)")
    p_analyse.add_argument("--drift", action="store_true",
        help="Measure the clock drift of camera and recorder: the offset again on the last "
             "minute of every clip of 120 s or more, against the stored offset (SPECS 7.3)")
    p_analyse.add_argument("--work", "-w",
        help="Work folder on the SSD (default: work_dir of the project file)")
    p_analyse.add_argument("--limit", type=int,
        help="Analyse only the first N matched clips")
    p_analyse.add_argument("--only",
        help="With --scenes or --drift: analyse only the clips with these names (comma "
             "separated, no extension)")
    p_analyse.add_argument("--report-name",
        help="Name of the report files in <work>/reports (default: clap-report, "
             "scenes-report, drift-report)")
    p_analyse.set_defaults(func=cmd_analyse)

    # ── assemble ──
    p_assemble = subparsers.add_parser("assemble",
        help="Assemble the timeline file: clap cut, steady scenes, fades (needs convert "
             "and analyse --clap --scenes first)")
    p_assemble.add_argument("--work", "-w",
        help="Work folder on the SSD (default: work_dir of the project file)")
    p_assemble.add_argument("--output", "-o",
        help=f"Timeline file to write (default: {config.TIMELINE_FILE} next to the project file)")
    p_assemble.add_argument("--replace", action="store_true",
        help="Overwrite an existing timeline file; the old one is kept as a snapshot")
    p_assemble.add_argument("--limit", type=int, help="Use only the first N matched clips")
    p_assemble.add_argument("--only",
        help="Use only the clips with these names (comma separated, no extension)")
    p_assemble.add_argument("--frame", help=f"Film frame, WxH (default: "
        f"{config.FILM_FRAME[0]}x{config.FILM_FRAME[1]})")
    p_assemble.add_argument("--fit", choices=["fit", "fill"],
        help=f"Place the picture whole with bars (fit) or fill the frame (default: {config.FILM_FIT})")
    p_assemble.add_argument("--fade-in", type=float,
        help=f"Film fade-in in seconds (default: {config.FILM_FADE_IN_SEC:g})")
    p_assemble.add_argument("--fade-out", type=float,
        help=f"Film fade-out in seconds (default: {config.FILM_FADE_OUT_SEC:g})")
    p_assemble.add_argument("--join", type=float,
        help=f"Sound crossfade at every join in seconds (default: {config.FILM_JOIN_SEC:g})")
    p_assemble.add_argument("--min-scene", type=float,
        help=f"Shortest scene in seconds (default: {config.STABLE_MIN_RUN_SEC:g})")
    p_assemble.add_argument("--clap-guard", type=float,
        help=f"Seconds cut after the clap (default: {config.CLAP_GUARD_SEC:g})")
    p_assemble.add_argument("--title", help="Name of the film")
    p_assemble.add_argument("--report-name", default="assembly-report",
        help="Name of the report files in <work>/reports (default: assembly-report)")
    p_assemble.add_argument("--no-file-checks", action="store_true",
        help="Do not read the files while validating (tests only)")
    p_assemble.set_defaults(func=cmd_assemble)

    # ── timeline ──
    p_timeline = subparsers.add_parser("timeline",
        help="Work with the timeline file")
    tl_sub = p_timeline.add_subparsers(dest="timeline_command", required=True)
    p_summary = tl_sub.add_parser("summary",
        help="Print the film as a table: segments, film times, sources, frames")
    p_summary.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_validate = tl_sub.add_parser("validate",
        help="Check the timeline file; print the findings as JSON; exit 1 on errors")
    p_validate.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_validate.add_argument("--no-file-checks", action="store_true",
        help="Skip the checks that read the disk (files, hashes)")
    p_history = tl_sub.add_parser("history",
        help="List the versions of the timeline file with their time and what changed")
    p_history.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_history.add_argument("--json", action="store_true", help="Print the rows as JSON")
    p_undo = tl_sub.add_parser("undo",
        help="Go back to the previous version of the timeline file; the version that is "
             "replaced is kept in timeline.history/undone/")
    p_undo.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_frames = tl_sub.add_parser("frames",
        help="Write PNG stills at film times or clip times into <work>/stills and print the paths")
    p_frames.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_frames.add_argument("--work", "-w",
        help="Work folder on the SSD (default: work_dir of the project file)")
    p_frames.add_argument("--film-s", metavar="T1,T2",
        help="Film times in seconds, for example 12.0,48.5")
    p_frames.add_argument("--source", metavar="CLIP",
        help="A clip of the timeline file (its id); use with --at")
    p_frames.add_argument("--at", metavar="T1,T2",
        help="Times in seconds in the clip named by --source")
    p_frames.add_argument("--no-file-checks", action="store_true",
        help="Do not read the files while validating the timeline")
    p_timeline.set_defaults(func=cmd_timeline)

    # ── preview ──
    p_preview = subparsers.add_parser("preview",
        help="Render the film preview from the preview copies (cuts, crossfades, fades)")
    p_preview.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_preview.add_argument("--work", "-w",
        help="Work folder on the SSD (default: work_dir of the project file)")
    p_preview.add_argument("--name", default="preview",
        help="Name of the preview file in <work>/previews (default: preview)")
    p_preview.add_argument("--range", metavar="A:B",
        help="Render only the segments that touch film seconds A to B")
    p_preview.add_argument("--encoder", choices=["h264_videotoolbox", "libx264"],
        help=f"Encoder of the segment files (default: {config.FILM_PREVIEW_ENCODER})")
    p_preview.add_argument("--width", type=int,
        help=f"Width of the preview in pixels (default: {config.FILM_PREVIEW_WIDTH})")
    p_preview.add_argument("--no-file-checks", action="store_true",
        help="Do not read the files while validating the timeline")
    p_preview.set_defaults(func=cmd_preview)

    # ── export ──
    p_export = subparsers.add_parser("export",
        help="List, add, switch on and off the exports of the timeline file")
    ex_sub = p_export.add_subparsers(dest="export_command", required=True)
    for cmd_name, text in (("list", "List the exports with their size estimate and last render"),
                           ("enable", "Switch an export on (the ProRes master is off until you do)"),
                           ("disable", "Switch an export off"),
                           ("set", "Change a setting of an export: the form of the subtitles"),
                           ("add", "Add an export from a preset")):
        sp = ex_sub.add_parser(cmd_name, help=text)
        sp.add_argument("--file", "-f",
            help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
        sp.add_argument("--work", "-w", help="Work folder (default: work_dir of the project file)")
        if cmd_name in ("enable", "disable", "set"):
            sp.add_argument("name", help="Name of the export")
    ex_add = ex_sub.choices["add"]
    ex_add.add_argument("--preset", required=True, choices=["film-4k", "film-4k-x264", "film-master"],
        help="film-4k: MP4, H.264, hardware encoder, 45 Mbit/s; film-4k-x264: the same with libx264; "
             "film-master: ProRes 422 HQ, off until switched on")
    ex_add.add_argument("--name", help="Name of the export (default: the preset's, or film-<height>p)")
    ex_add.add_argument("--width", type=int, help="Custom width (needs --height; the shape of the film)")
    ex_add.add_argument("--height", type=int, help="Custom height")
    ex_add.add_argument("--output-dir", help="Folder of the file (default: <work>/renders)")
    ex_add.add_argument("--replace", action="store_true", help="Replace an export of the same name")
    ex_add.add_argument("--subtitles", choices=config.SUBTITLE_FORMS,
        help="The form of the subtitles in this export (D13): srt, burned, both or none "
             "(default: srt for a film, burned for a short)")
    ex_sub.choices["set"].add_argument("--subtitles", required=True, choices=config.SUBTITLE_FORMS,
        help="srt: a file next to the film; burned: drawn into the picture; both; none")
    p_export.set_defaults(func=cmd_export)

    # ── subtitles ──
    p_sub = subparsers.add_parser("subtitles",
        help="The subtitles: write them by hand or in a text box per scene, let Whisper listen, import, "
             "write SRT files, translate")
    sb = p_sub.add_subparsers(dest="subtitles_command", required=True)

    def sub_cmd(name, text, work=False):
        sp = sb.add_parser(name, help=text)
        sp.add_argument("--file", "-f",
            help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
        if work:
            sp.add_argument("--work", "-w", help="Work folder (default: work_dir of the project file)")
        return sp

    sp = sub_cmd("list", "List the cues with their film times")
    sp.add_argument("--json", action="store_true", help="Print the cues as JSON")
    sp = sub_cmd("add", "Add a cue at a film time")
    sp.add_argument("--at", type=float, required=True, help="Film time of the start, in seconds")
    sp.add_argument("--end", type=float, help="Film time of the end (default: the time the text needs)")
    sp.add_argument("--length", type=float, help="Length in seconds, instead of --end")
    sp.add_argument("--text", required=True, help="The text; a line feed breaks a line")
    sp = sub_cmd("edit", "Change the text and/or the film times of a cue")
    sp.add_argument("cue", help="Cue id, for example c003")
    sp.add_argument("--text")
    sp.add_argument("--start", type=float, help="New film time of the start")
    sp.add_argument("--end", type=float, help="New film time of the end")
    sp = sub_cmd("remove", "Remove cues")
    sp.add_argument("cues", nargs="+", help="Cue ids")
    sp = sub_cmd("shift", "Move cues earlier (negative) or later in the film")
    sp.add_argument("seconds", type=float)
    sp.add_argument("--cues", help="Only these cue ids, comma separated")
    sp.add_argument("--from", dest="from_s", type=float, help="Only cues that start at or after this film time")
    sp.add_argument("--to", dest="to_s", type=float, help="Only cues that start at or before this film time")
    sub_cmd("sort", "Put the cues in film order")
    sp = sub_cmd("text", "Set the text of a scene: it is cleaned, split into cues and spread across the scene")
    sp.add_argument("segment", help="Scene id, for example s003")
    sp.add_argument("--text", help="The words of the scene")
    sp.add_argument("--text-file", metavar="FILE", help="Read the words from a file (- for standard input)")
    sp.add_argument("--replace-adjusted", action="store_true",
        help="Also replace cues of the scene that were made or moved by hand")
    sp = sub_cmd("import", "Add cues from a JSON file (film seconds) or an SRT file")
    sp.add_argument("file_to_import", metavar="FILE", help="A .json file with a list of {start_s, end_s, text} or "
                    "{\"cues\": [...]}, or an .srt file")
    sp.add_argument("--replace", action="store_true", help="Remove all cues first")
    sp = sub_cmd("export", "Write the SRT file of the film")
    sp.add_argument("--out", help="The file (default: <film>.en.srt next to the export)")
    sp.add_argument("--export", metavar="NAME", help="Name of the export whose film the file belongs to")
    sub_cmd("check", "Check that every cue fits the safe area and the readability limits")
    sp = sub_cmd("search", "Search the words Whisper heard (the transcripts of subtitles auto)", work=True)
    sp.add_argument("words", help="Words to look for; a phrase has to contain all of them")
    sp.add_argument("--all", action="store_true", help="Also the phrases that were dropped as not speech")
    sp = sub_cmd("auto", "Let Whisper listen to the sound and make cues", work=True)
    sp.add_argument("--only", help="Only these scenes, comma separated (for example s001,s002)")
    sp.add_argument("--redo", action="store_true", help="Run Whisper again even where a transcript is kept")
    sp.add_argument("--keep-stock-phrases", action="store_true",
        help="Keep phrases such as \"Thank you.\" that Whisper writes for sound without speech")
    sp.add_argument("--report-name", default="subtitles-auto-report", help="Name of the report in <work>/reports")
    sp = sub_cmd("translate", "Translate the cues and write one SRT file per language", work=True)
    sp.add_argument("--lang", help=f"Languages, comma separated (default: {','.join(config.TRANSLATE_LANGUAGES)})")
    sp.add_argument("--export", metavar="NAME", help="The export whose film the files belong to (default: the first)")
    sp.add_argument("--model", help="The OpenRouter model (default: VET_TRANSLATE_MODEL)")
    sp.add_argument("--dry-run", action="store_true", help="Count what would be sent and the cost; send nothing")
    sp.add_argument("--no-shorten", action="store_true",
        help="Do not ask the model for a shorter text where a translation does not fit its cue")
    sp.add_argument("--save-raw", metavar="DIR",
        help="Write the text of every answer of the model that cannot be read to a file in DIR (never a header or the "
             "key), and name the files in the warnings")
    p_sub.set_defaults(func=cmd_subtitles)

    # ── title ──
    p_title = subparsers.add_parser("title", help="Titles drawn into the picture")
    tt = p_title.add_subparsers(dest="title_command", required=True)

    def title_cmd(name, text):
        sp = tt.add_parser(name, help=text)
        sp.add_argument("--file", "-f",
            help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
        return sp

    def title_args(sp, start_required=False):
        sp.add_argument("--start", type=float, required=start_required,
            help="Start in seconds (film time, or from the start of the scene with --anchor)")
        sp.add_argument("--end", type=float, help="End in seconds")
        sp.add_argument("--length", type=float, help="Length in seconds, instead of --end (default 4)")
        sp.add_argument("--anchor", help="film (default) or a scene id")
        sp.add_argument("--template", choices=list(config.TITLE_TEMPLATES), help="center, lower_third or opening")
        sp.add_argument("--size", type=float, help="Font size in percent of the frame height (1 to 30)")
        sp.add_argument("--fade", type=float, help=f"Fade in and out, in seconds (default {config.TITLE_FADE_SEC})")

    title_cmd("list", "List the titles")
    sp = title_cmd("add", "Add a title")
    sp.add_argument("--text", required=True)
    title_args(sp, start_required=True)
    sp.set_defaults(anchor="film")
    sp = title_cmd("edit", "Change a title")
    sp.add_argument("title", help="Title id, for example t002")
    sp.add_argument("--text")
    title_args(sp)
    sp = title_cmd("remove", "Remove a title")
    sp.add_argument("title", help="Title id")
    sp = title_cmd("import", "Add titles from a JSON file")
    sp.add_argument("file_to_import", metavar="FILE", help="A .json file with a list of {text, start_s, end_s} or "
                    "{\"titles\": [...]}")
    sp.add_argument("--replace", action="store_true", help="Remove all titles first")
    p_title.set_defaults(func=cmd_title)


    # ── scenes ──
    p_scenes = subparsers.add_parser("scenes",
        help="Prompt edits: the scene index and sheets to read, and commands to remove, keep, cut and restore scenes")
    sc = p_scenes.add_subparsers(dest="scenes_command", required=True)

    def scenes_cmd(name, text, work=True):
        sp = sc.add_parser(name, help=text)
        sp.add_argument("--file", "-f",
            help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
        if work:
            sp.add_argument("--work", "-w", help="Work folder on the SSD (default: work_dir of the project file)")
        return sp

    sp = scenes_cmd("index", "Write the scene index (index.json and index.md) into <work>/index/<timeline name>/")
    sp.add_argument("--sheets", action="store_true", help="Draw the sheets first (the same as `scenes sheets`)")
    scenes_cmd("sheets", "Draw one still sheet per scene and the overview pages; sheets that are up to date are kept")
    sp = scenes_cmd("frames", "A finer look inside one scene: a still every N seconds, on pages")
    sp.add_argument("scene", help="Scene id, for example s017")
    sp.add_argument("--every", default="1s", help="Step between stills: 1s, 0.5s or 500ms (default 1s)")
    sp.add_argument("--from", dest="from_s", type=float, help="Start of the stretch, in film seconds (default: the scene's start)")
    sp.add_argument("--to", dest="to_s", type=float, help="End of the stretch, in film seconds (default: the scene's end)")

    def edit_cmd(name, text):
        sp = scenes_cmd(name, text, work=False)
        sp.add_argument("--dry-run", action="store_true",
            help="Print what would change (scenes, film length before and after, subtitles) and write nothing")
        return sp

    sp = edit_cmd("remove", "Take scenes out of the film (they stay in the file; `scenes restore` brings them back)")
    sp.add_argument("scene_ids", nargs="+", metavar="ID", help="Scene ids, for example s017 or s017,s018")
    sp = edit_cmd("keep", "Keep only these scenes: every other scene is taken out of the film")
    sp.add_argument("scene_ids", nargs="+", metavar="ID", help="Scene ids, for example s017,s018,s030")
    sp = edit_cmd("restore", "Put removed scenes back into the film")
    sp.add_argument("scene_ids", nargs="+", metavar="ID", help="Scene ids, for example s017 or s017,s018")
    sp = edit_cmd("cut", "Take a moment out of one scene by splitting it (film seconds)")
    sp.add_argument("scene", help="Scene id, for example s017")
    sp.add_argument("--from", dest="from_s", type=float, required=True, help="Film second where the cut moment starts")
    sp.add_argument("--to", dest="to_s", type=float, required=True, help="Film second where the cut moment ends")
    sp = edit_cmd("pin", "Pin scenes: a short made from the film always keeps them (the film does not change)")
    sp.add_argument("scene_ids", nargs="+", metavar="ID", help="Scene ids, for example s017 or s017,s018")
    sp = edit_cmd("unpin", "Take the pin (or a drop mark) off scenes")
    sp.add_argument("scene_ids", nargs="+", metavar="ID", help="Scene ids, for example s017 or s017,s018")
    sp = scenes_cmd("crop", "Where the 9:16 window of a short sits in a scene (the film does not change)")
    sp.add_argument("scene", help="Scene id, for example s017")
    sp.add_argument("--x", type=float, help="Place of the window: 0 left edge, 0.5 centre, 1 right edge")
    sp.add_argument("--from", dest="from_x", type=float, help="Start of a slow pan (with --to)")
    sp.add_argument("--to", dest="to_x", type=float, help="End of a slow pan (with --from)")
    sp.add_argument("--clear", action="store_true", help="Take the crop away: the window is centred")
    sp.add_argument("--dry-run", action="store_true", help="Print what would change and write nothing")
    sp.add_argument("--still", action="store_true",
        help="Draw a picture of the window on a frame of the scene into <work>/stills/ (also with --dry-run, to try a place)")
    sp.add_argument("--at", type=float, help="Film second of the frame for --still (default: the middle of the scene)")
    sp.add_argument("--size", help=f"Size of the short for the window, WIDTHxHEIGHT (default {config.SHORT_FRAME[0]}x{config.SHORT_FRAME[1]})")
    p_scenes.set_defaults(func=cmd_scenes)

    # ── short ──
    p_short = subparsers.add_parser("short",
        help="Make a vertical 9:16 short from the film (or render a short that is already planned)")
    p_short.add_argument("--file", "--from", "-f", dest="file",
        help=f"The film (default: {config.TIMELINE_FILE} next to the project file), or a short that is already planned")
    p_short.add_argument("--work", "-w", help="Work folder (default: work_dir of the project file)")
    p_short.add_argument("--length", "-l", type=float, metavar="SECONDS",
        help=f"Length of the short in seconds, exact to the frame (default {config.SHORT_DEFAULT_LENGTH_SEC:g})")
    p_short.add_argument("--output", "-o", metavar="FILE.mp4",
        help="The MP4 to write; the plan file sits next to it with the extension .json (default: "
             "<work>/exports/short-<length>.mp4)")
    p_short.add_argument("--size", metavar="WxH",
        help=f"The frame of the short, a vertical size (default {config.SHORT_FRAME[0]}x{config.SHORT_FRAME[1]})")
    p_short.add_argument("--prefer", metavar="IDS", help="Scenes the short must keep, for example s012,s040")
    p_short.add_argument("--only", metavar="IDS", help="Make the short only from these scenes")
    p_short.add_argument("--exclude", metavar="IDS", help="Do not use these scenes")
    p_short.add_argument("--subtitles", choices=config.SUBTITLE_FORMS,
        help="The form of the subtitles: srt, burned, both or none (default for a short: burned)")
    p_short.add_argument("--lang", choices=config.TRANSLATE_LANGUAGES + (config.SUBTITLE_LANGUAGE,), metavar="LANG",
        help="Render the short with its subtitles in this language, from the translations that `subtitles translate` "
             "made for the film, into <short>.<LANG>.mp4 (the plan stays English; Japanese, Chinese and Thai need "
             "VET_NOTO_FONT_DIR)")
    p_short.add_argument("--dry-run", action="store_true",
        help="Print the plan (kept scenes, film and short times, kept and dropped subtitles) and write nothing")
    p_short.add_argument("--no-render", action="store_true", help="Write the plan file and stop")
    p_short.add_argument("--stills", action="store_true",
        help="After the render, write one still per scene and a contact sheet next to the MP4")
    p_short.add_argument("--detach", action="store_true",
        help="Render in the background, in a session of its own, with a log and a pid file in <work>/reports")
    p_short.set_defaults(func=cmd_short)

    # ── render ──
    p_render = subparsers.add_parser("render",
        help="Render the final film from the .braw files (levelled sound, LUTs, DCI 4K)")
    p_render.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_render.add_argument("--work", "-w", help="Work folder (default: work_dir of the project file)")
    p_render.add_argument("--export", metavar="NAME", help="Render this export")
    p_render.add_argument("--all", action="store_true", help="Render every export that is switched on")
    p_render.add_argument("--force", action="store_true", help="Render an export that is switched off")
    p_render.add_argument("--subtitles", choices=config.SUBTITLE_FORMS,
        help="The form of the subtitles for this render only: srt, burned, both or none (default: the "
             "setting of the export; change that with: export set NAME --subtitles FORM)")
    p_render.add_argument("--lang", choices=config.TRANSLATE_LANGUAGES + (config.SUBTITLE_LANGUAGE,), metavar="LANG",
        help="Burn the subtitles in this language (" + ", ".join(config.TRANSLATE_LANGUAGES) + "), from the translations "
             "that `subtitles translate` made, into <film>.<LANG>.<ext>; the form is burned unless you say both "
             "(Japanese, Chinese and Thai need VET_NOTO_FONT_DIR)")
    p_render.add_argument("--chunk-seconds", type=float, metavar="S",
        help=f"A film longer than this is rendered in chunks of whole scenes that are kept when the render stops, so that "
             f"the same command goes on where it was (default {config.EXPORT_CHUNK_SEC:g}; 0 renders in one process)")
    p_render.add_argument("--fresh", action="store_true",
        help="Throw away the chunks of an earlier run of this export and render everything again")
    p_render.add_argument("--detach", action="store_true",
        help="Run in the background, in a session of its own, with a log and a pid file in <work>/reports")
    p_render.set_defaults(func=cmd_render)

    # ── export-shotcut, import-shotcut ──
    p_xs = subparsers.add_parser("export-shotcut",
        help="Write the Shotcut project (film.mlt) of the timeline file, for hand work")
    p_xs.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_xs.add_argument("--work", "-w", help="Work folder (default: work_dir of the project file)")
    p_xs.add_argument("--out", metavar="FILE", help="The project file to write (default: <work>/film.mlt)")
    p_xs.add_argument("--no-check", action="store_true", help="Do not read the project with melt")
    p_xs.add_argument("--lang", default=config.SUBTITLE_LANGUAGE, metavar="LANGS",
        help="The subtitle tracks of the project, a list such as en,ja (default: en; none for no track). The first "
             "is drawn over the picture; a translation must have been made with `subtitles translate`")
    p_xs.set_defaults(func=cmd_export_shotcut)
    p_is = subparsers.add_parser("import-shotcut",
        help="Read the cut points and the order back from the saved Shotcut project")
    p_is.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_is.add_argument("--work", "-w", help="Work folder (default: work_dir of the project file)")
    p_is.add_argument("--mlt", metavar="FILE", help="The Shotcut project to read (default: <work>/film.mlt)")
    p_is.add_argument("--dry-run", action="store_true", help="Show what would change; write nothing")
    p_is.set_defaults(func=cmd_import_shotcut)

    # ── look ──
    p_look = subparsers.add_parser("look",
        help="List creative LUTs, choose the base or the creative LUT of the film")
    lk_sub = p_look.add_subparsers(dest="look_command", required=True)
    p_lk_list = lk_sub.add_parser("list", help="List the .cube files in the LUT folder")
    p_lk_list.add_argument("--luts", metavar="DIR",
        help="Folder with the .cube files (default: the environment variable VET_LUT_DIR)")
    p_lk_set = lk_sub.add_parser("set",
        help="Choose the creative LUT (after the base look) or the base look of the film")
    p_lk_set.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_lk_set.add_argument("--work", "-w",
        help="Work folder on the SSD: the LUT is copied to <work>/luts (default: work_dir of the project file)")
    p_lk_set.add_argument("--luts", metavar="DIR",
        help="Folder with the .cube files (default: the environment variable VET_LUT_DIR)")
    p_lk_set.add_argument("--lut", metavar="ID_OR_FILE",
        help="The creative LUT: a file, or an id from `look list`, for example slay/slay-04-film.cube")
    p_lk_set.add_argument("--no-lut", action="store_true", help="No creative LUT: the base look only")
    p_lk_set.add_argument("--base", metavar="FILE",
        help="Change the base look (the preview copies then have to be made again)")
    p_lk_set.add_argument("--no-file-checks", action="store_true",
        help="Do not read the files while validating (tests)")
    p_look.set_defaults(func=cmd_look)

    # ── loudness ──
    p_loud = subparsers.add_parser("loudness",
        help="Set or show the loudness of the film (each scene, then the film)")
    ld_sub = p_loud.add_subparsers(dest="loudness_command", required=True)
    p_ld_set = ld_sub.add_parser("set",
        help="Write the loudness mode and numbers into the timeline file")
    p_ld_set.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_ld_set.add_argument("--mode", required=True, choices=["off", "segment_then_film", "film_only"],
        help="off: no levelling; segment_then_film: every scene to the same loudness, then the "
             "film to the target (decision D9); film_only: only the film")
    p_ld_set.add_argument("--target", type=float,
        help=f"Integrated loudness of the film in LUFS (default {config.LOUDNESS_TARGET_LUFS:g})")
    p_ld_set.add_argument("--peak", type=float,
        help=f"True-peak limit in dBTP (default {config.LOUDNESS_TRUE_PEAK_DBTP:g})")
    p_ld_set.add_argument("--max-gain", type=float,
        help=f"Most a quiet scene may be raised, in dB (default {config.LOUDNESS_MAX_GAIN_DB:g})")
    p_ld_set.add_argument("--no-file-checks", action="store_true",
        help="Do not read the files while validating (tests)")
    p_ld_show = ld_sub.add_parser("show", help="Print the loudness settings of the timeline file")
    p_ld_show.add_argument("--file", "-f",
        help=f"Timeline file (default: {config.TIMELINE_FILE} next to the project file)")
    p_loud.set_defaults(func=cmd_loudness)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Default to "app" if no command given
    if args.command is None:
        args = parser.parse_args(["app"])

    args.func(args)


if __name__ == "__main__":
    main()
