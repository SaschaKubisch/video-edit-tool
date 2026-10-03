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

Usage:
  python main.py
  python main.py match --video ./video/ --audio ./audio/
  python main.py sync
  python main.py resolve --project-name "My Project" --timeline "Cut 1"
"""

import argparse
import json
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


def main():
    parser = argparse.ArgumentParser(
        description="Dual-System Audio Video Automation (BMPCC 4K + Zoom H2N)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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

    args = parser.parse_args()

    # Default to "app" if no command given
    if args.command is None:
        args = parser.parse_args(["app"])

    args.func(args)


if __name__ == "__main__":
    main()
