#!/usr/bin/env python3
"""
Dual-System Audio Video Automation
====================================
Automates the BMPCC 4K + Zoom H2N ambient video workflow.

Commands:
  app      Web app (default): match, pick/order/trim scenes, sync, Resolve
  match    Audio-fingerprint match video files to audio files
  sync     Copy audio renamed to the video's stem (into the video folder
           by default), using the selection saved in project.json;
           `sync --undo` removes those copies again
  resolve  Push the ordered timeline (V1 video, A2 audio) into DaVinci Resolve
  rename   (legacy) rename both files of each pair to scene_NN.*, with undo
  convert  make a small preview copy of every matched .braw clip in the work folder
  analyse  find the sync clap at the start of each clip, find the steady scenes (reports in the work folder)
  assemble build the timeline file: clap cut, steady scenes, fades
  preview  render the film preview from the preview copies
  timeline validate or summarise the timeline file, list its versions (history), go back (undo),
           write stills at film times (frames)

Usage:
  python main.py
  python main.py match --video ./video/ --audio ./audio/
  python main.py sync
  python main.py resolve --project-name "My Project" --timeline "Cut 1"
"""

import argparse
import json
import os
import sys
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
        output_dir=args.output or "",
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


def cmd_sync(args):
    """Copy the matched audio next to the video, renamed to the video's stem."""
    from syncer import sync_all_scenes, remove_copied_audio

    project_file = args.project or config.PROJECT_FILE

    if args.undo:
        out_dir = args.output
        if not out_dir:
            if not Path(project_file).exists():
                print(f"Error: Project file not found: {project_file}")
                print("Pass --output DIR to name the folder holding the copies.")
                sys.exit(1)
            proj = Project.load(project_file)
            out_dir = proj.output_dir or proj.video_dir
        removed = remove_copied_audio(out_dir)
        for path in removed:
            print(f"  Removed: {path}")
        print(f"Removed {len(removed)} copied audio file(s) from {out_dir}")
        return

    if not Path(project_file).exists():
        print(f"Error: Project file not found: {project_file}")
        print("Run 'python main.py match' first.")
        sys.exit(1)

    project = Project.load(project_file)

    # Load selection from argument or file
    selection_data = None
    if args.selection:
        selection_data = json.loads(args.selection)
    elif args.selection_file:
        with open(args.selection_file) as f:
            selection_data = json.load(f)
    elif project.selection:
        # Use previously saved selection
        selection_data = {
            "selection": project.selection,
            "selection_trims": project.selection_trims,
        }
    else:
        print("Error: No selection provided.")
        print("Pick scenes in the app first (python main.py app), or pass:")
        print("  python main.py sync --selection '{\"selection\":[0,2,1]}'")
        print("  python main.py sync --selection-file selection.json")
        sys.exit(1)

    # Apply selection to project
    project.selection = selection_data.get("selection", [])
    project.selection_trims = selection_data.get("selection_trims", {})

    print("=" * 60)
    print("Copying audio renamed to the video names")
    print("=" * 60)
    print(f"Selection: {project.selection}")
    if project.selection_trims:
        print(f"Trims: {project.selection_trims}")

    output_dir = args.output or project.output_dir or None
    outputs = sync_all_scenes(project, output_dir)
    shown_dir = output_dir or project.video_dir

    # Save selection back to project
    project.save(project_file)

    print(f"\n{'=' * 60}")
    print(f"Done! {len(outputs)} renamed audio files saved to: {shown_dir}/")
    print(f"Next: python main.py resolve")
    print(f"{'=' * 60}")


def cmd_resolve(args):
    """Create a DaVinci Resolve project with synced clips on a timeline."""
    from resolve_integration import create_resolve_project, list_resolve_projects

    project_file = args.project or config.PROJECT_FILE

    # List projects mode
    if args.list:
        print("Projects in current Resolve database:")
        try:
            projects = list_resolve_projects()
            for p in projects:
                print(f"  - {p}")
            if not projects:
                print("  (none)")
        except ConnectionError as e:
            print(f"Error: {e}")
        return

    if not Path(project_file).exists():
        print(f"Error: Project file not found: {project_file}")
        print("Run 'python main.py match' (or the app) first.")
        sys.exit(1)
    proj = Project.load(project_file)

    print("=" * 60)
    print("Creating DaVinci Resolve project")
    print("=" * 60)

    try:
        result = create_resolve_project(
            project=proj,
            project_name=args.project_name or "Ambient Video Project",
            timeline_name=args.timeline or "Main Timeline",
            fps=args.fps,
        )
    except ConnectionError as e:
        print(f"\nError: {e}")
        sys.exit(1)
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"\nError: {e}")
        sys.exit(1)


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

    if not (args.clap or args.scenes):
        fail("Say what to analyse: --clap (the sync clap) or --scenes (the steady scenes).")
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
    """List the versions of the timeline file with time and a one-line change (SPECS 4.3)."""
    import store
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
        return
    print(f"{name}: {len(snapshots)} earlier version(s) kept in {store.history_dir(path)}")
    print(f"{'no.':8} {'time':19}  {'hash':8}  change from the version before")
    for r in rows:
        number = "current" if r["current"] else f"{r['number']:04d}"
        print(f"{number:8} {store.format_time(r['time']):19}  {r['hash'][:8]}  {r['change']}")


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
    p_match.add_argument("--output", "-o",
        help="Directory for renamed audio copies (default: the video folder)")
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

    # ── sync ──
    p_sync = subparsers.add_parser("sync",
        help="Copy matched audio next to the video, renamed to the video's stem")
    p_sync.add_argument("--selection", "-s",
        help='Selection JSON (default: the selection saved in project.json)')
    p_sync.add_argument("--selection-file", "-f",
        help="Path to a selection JSON file")
    p_sync.add_argument("--output", "-o",
        help="Directory for renamed audio copies (default: the video folder)")
    p_sync.add_argument("--undo", action="store_true",
        help="Delete the audio copies made by a previous sync (listed in "
             "ambient_audio_copies.json) and exit")
    p_sync.set_defaults(func=cmd_sync)

    # ── resolve ──
    p_resolve = subparsers.add_parser("resolve",
        help="Create a Resolve project with video + audio on a timeline")
    p_resolve.add_argument("--project-name", "-n", dest="project_name",
        help='Resolve project name (default: "Ambient Video Project")')
    p_resolve.add_argument("--timeline", "-t",
        help='Timeline name (default: "Main Timeline")')
    p_resolve.add_argument("--fps", type=float,
        help="Override timeline frame rate (default: Resolve's timeline rate)")
    p_resolve.add_argument("--list", action="store_true",
        help="List existing projects in Resolve and exit")
    p_resolve.set_defaults(func=cmd_resolve)

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
    p_analyse.add_argument("--work", "-w",
        help="Work folder on the SSD (default: work_dir of the project file)")
    p_analyse.add_argument("--limit", type=int,
        help="Analyse only the first N matched clips")
    p_analyse.add_argument("--only",
        help="With --scenes: analyse only the clips with these names (comma separated, "
             "no extension)")
    p_analyse.add_argument("--report-name",
        help="Name of the report files in <work>/reports (default: clap-report, "
             "scenes-report)")
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
