#!/usr/bin/env python3
"""
Dual-System Audio Video Automation
====================================
Automates the BMPCC 4K + Zoom H2N ambient video workflow:

  1. match   — Audio-fingerprint match video files to audio files
  2. rename  — Give matched pairs consistent names
  3. select  — Visual HTML interface to pick & order scenes
  4. sync    — Replace scratch audio with H2N, trim, export clips for DaVinci

Usage:
  # Step 1: Match video files to audio files
  python main.py match --video ./video/ --audio ./audio/

  # Step 2 (optional): Rename matched pairs
  python main.py rename

  # Step 3: Open visual scene selector
  python main.py select

  # Step 4: Sync and export clips (after selecting in the browser)
  python main.py sync --selection '{"selection":[3,1,5,2]}'
  # or
  python main.py sync --selection-file selection.json

  # Or specify output directory
  python main.py sync --output ./synced_clips/
"""

import argparse
import json
import sys
import webbrowser
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
    print("STEP 1: Matching video files to audio recordings")
    print("=" * 60)

    matches = match_files(
        video_dir=video_dir,
        audio_dir=audio_dir,
        min_confidence=args.min_confidence,
    )

    if not matches:
        print("\nNo matches found. Try lowering --min-confidence.")
        sys.exit(1)

    # Create project
    project = Project(
        video_dir=video_dir,
        audio_dir=audio_dir,
        output_dir=args.output or "synced_output",
        matches=matches,
    )

    # Extract thumbnails
    thumb_dir = Path(args.output or ".") / "thumbnails"
    print(f"\nExtracting thumbnails...")
    project = extract_all_thumbnails(project, str(thumb_dir))

    # Save project
    project_file = args.project or config.PROJECT_FILE
    project.save(project_file)
    print(f"\nProject saved to: {project_file}")
    print(f"Next step: python main.py select")


def cmd_rename(args):
    """Rename matched file pairs to share a common name."""
    from renamer import rename_matches, undo_renames

    if args.undo:
        log_path = args.undo
        undo_renames(log_path, dry_run=args.dry_run)
        return

    project_file = args.project or config.PROJECT_FILE
    if not Path(project_file).exists():
        print(f"Error: Project file not found: {project_file}")
        print("Run 'python main.py match' first.")
        sys.exit(1)

    project = Project.load(project_file)

    print("=" * 60)
    print("STEP 2: Renaming matched file pairs")
    print("=" * 60)

    rename_matches(
        project=project,
        pattern=args.pattern,
        dry_run=args.dry_run,
    )

    if not args.dry_run:
        project.save(project_file)
        print(f"\nProject updated: {project_file}")


def cmd_select(args):
    """Generate and open the visual scene selector."""
    from thumbnails import generate_selector_html

    project_file = args.project or config.PROJECT_FILE
    if not Path(project_file).exists():
        print(f"Error: Project file not found: {project_file}")
        print("Run 'python main.py match' first.")
        sys.exit(1)

    project = Project.load(project_file)

    print("=" * 60)
    print("STEP 3: Visual scene selector")
    print("=" * 60)

    html_path = generate_selector_html(
        project=project,
        output_path=args.html or config.SELECTOR_HTML,
    )

    # Open in browser
    abs_path = Path(html_path).resolve()
    url = f"file://{abs_path}"
    print(f"\nOpening in browser: {url}")
    print("Drag scenes to arrange your timeline, set trim points,")
    print("then click 'Export Selection' and copy the JSON.")
    print(f"\nNext step: python main.py sync --selection '<paste JSON here>'")

    if not args.no_open:
        webbrowser.open(url)


def cmd_sync(args):
    """Sync audio to video for selected scenes and export clips."""
    from syncer import sync_all_scenes

    project_file = args.project or config.PROJECT_FILE
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
        print("Run 'python main.py select' first, then pass the exported JSON:")
        print("  python main.py sync --selection '{\"selection\":[0,2,1]}'")
        print("  python main.py sync --selection-file selection.json")
        sys.exit(1)

    # Apply selection to project
    project.selection = selection_data.get("selection", [])
    project.selection_trims = selection_data.get("selection_trims", {})

    print("=" * 60)
    print("STEP 4: Syncing audio and exporting clips")
    print("=" * 60)
    print(f"Selection: {project.selection}")
    if project.selection_trims:
        print(f"Trims: {project.selection_trims}")

    output_dir = args.output or project.output_dir or "synced_output"
    outputs = sync_all_scenes(project, output_dir)

    # Save selection back to project
    project.save(project_file)

    print(f"\n{'=' * 60}")
    print(f"Done! {len(outputs)} synced clips saved to: {output_dir}/")
    print(f"Import them into DaVinci Resolve in numbered order,")
    print(f"or run: python main.py resolve --clips {output_dir}")
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

    # Load our project data
    if not Path(project_file).exists():
        print(f"Warning: Project file not found: {project_file}")
        print("Continuing with clips directory only.\n")
        proj = Project(output_dir=args.clips or "synced_output")
    else:
        proj = Project.load(project_file)

    clips_dir = args.clips or proj.output_dir or "synced_output"

    print("=" * 60)
    print("STEP 5: Creating DaVinci Resolve project")
    print("=" * 60)

    try:
        result = create_resolve_project(
            project=proj,
            project_name=args.name or "Ambient Video Project",
            timeline_name=args.timeline or "Main Timeline",
            frame_rate=args.fps,
            width=args.width,
            height=args.height,
            clips_dir=clips_dir,
        )
    except ConnectionError as e:
        print(f"\nError: {e}")
        sys.exit(1)
    except RuntimeError as e:
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
        help="Output directory for synced clips (default: synced_output)")
    p_match.add_argument("--min-confidence", type=float, default=None,
        help=f"Minimum match confidence (default: {config.MATCH_MIN_CONFIDENCE})")
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

    # ── select ──
    p_select = subparsers.add_parser("select",
        help="Open visual scene selector in browser")
    p_select.add_argument("--html",
        help=f"Output HTML file path (default: {config.SELECTOR_HTML})")
    p_select.add_argument("--no-open", action="store_true",
        help="Don't auto-open in browser")
    p_select.set_defaults(func=cmd_select)

    # ── sync ──
    p_sync = subparsers.add_parser("sync",
        help="Sync audio and export clips for selected scenes")
    p_sync.add_argument("--selection", "-s",
        help='Selection JSON from the visual selector (e.g. \'{"selection":[0,2,1]}\')')
    p_sync.add_argument("--selection-file", "-f",
        help="Path to a selection JSON file")
    p_sync.add_argument("--output", "-o",
        help="Output directory for synced clips (default: synced_output)")
    p_sync.set_defaults(func=cmd_sync)

    # ── resolve ──
    p_resolve = subparsers.add_parser("resolve",
        help="Create a DaVinci Resolve project with clips on a timeline")
    p_resolve.add_argument("--clips", "-c",
        help="Directory containing synced clips (default: synced_output)")
    p_resolve.add_argument("--name", "-n",
        help='Resolve project name (default: "Ambient Video Project")')
    p_resolve.add_argument("--timeline", "-t",
        help='Timeline name (default: "Main Timeline")')
    p_resolve.add_argument("--fps", type=float,
        help="Project frame rate (default: auto-detect)")
    p_resolve.add_argument("--width", type=int,
        help="Project width in pixels (default: auto-detect)")
    p_resolve.add_argument("--height", type=int,
        help="Project height in pixels (default: auto-detect)")
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
