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
    run_server(port=args.port)


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
    try:
        summary = conv.run(clips, dry_run=args.dry_run)
    except (safety.OriginalsProtectedError, convert.WorkDirUnavailable,
            convert.NotEnoughSpace, convert.AlreadyRunning) as e:
        fail(str(e))
    sys.exit(summary.exit_code)


def cmd_timeline(args):
    """Work with the timeline file (SPECS 3, 4.3)."""
    import timeline

    path = args.file or "timeline.json"
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
        sys.exit(1 if timeline.has_errors(findings) else 0)


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

    # ── timeline ──
    p_timeline = subparsers.add_parser("timeline",
        help="Work with the timeline file")
    tl_sub = p_timeline.add_subparsers(dest="timeline_command", required=True)
    p_validate = tl_sub.add_parser("validate",
        help="Check the timeline file; print the findings as JSON; exit 1 on errors")
    p_validate.add_argument("--file", "-f",
        help="Timeline file (default: timeline.json)")
    p_validate.add_argument("--no-file-checks", action="store_true",
        help="Skip the checks that read the disk (files, hashes)")
    p_timeline.set_defaults(func=cmd_timeline)

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
