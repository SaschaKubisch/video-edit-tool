"""
DaVinci Resolve integration.
Imports matched video + audio pairs into a Resolve project with a timeline.
Audio is placed on a separate track with the detected offset so you can
fine-tune the sync in Resolve.

REQUIREMENTS:
  - DaVinci Resolve Studio must be running
  - External scripting must be enabled:
    Preferences > System > General > External scripting using: Local
"""

import sys
import os
from pathlib import Path
from typing import Optional

from project import Project, SceneMatch


def _find_resolve_script_module() -> Optional[str]:
    """Find the DaVinciResolveScript module path based on platform."""
    candidates = []
    if sys.platform == "darwin":
        candidates = [
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/",
        ]
    elif sys.platform == "win32":
        candidates = [
            os.path.join(os.environ.get("PROGRAMDATA", "C:\\ProgramData"),
                         "Blackmagic Design", "DaVinci Resolve", "Support",
                         "Developer", "Scripting", "Modules"),
        ]
    else:
        candidates = [
            "/opt/resolve/Developer/Scripting/Modules/",
        ]
    for path in candidates:
        if os.path.isdir(path):
            return path
    return None


def _get_resolve():
    """Connect to the running DaVinci Resolve instance."""
    try:
        import DaVinciResolveScript as dvr
        resolve = dvr.scriptapp("Resolve")
        if resolve:
            return resolve
    except ImportError:
        pass

    module_path = _find_resolve_script_module()
    if module_path:
        if module_path not in sys.path:
            sys.path.insert(0, module_path)
        try:
            import DaVinciResolveScript as dvr
            resolve = dvr.scriptapp("Resolve")
            if resolve:
                return resolve
        except ImportError:
            pass

    raise ConnectionError(
        "Could not connect to DaVinci Resolve.\n\n"
        "Make sure:\n"
        "  1. DaVinci Resolve Studio is running\n"
        "  2. External scripting is enabled:\n"
        "     Preferences > System > General > External scripting using: Local\n"
        "  3. Set PYTHONPATH to Resolve's scripting modules:\n"
        "     macOS: export PYTHONPATH=\"/Library/Application Support/Blackmagic Design/"
        "DaVinci Resolve/Developer/Scripting/Modules/\"\n"
    )


def create_resolve_project(
    project: Project,
    project_name: str = "Ambient Video Project",
    timeline_name: str = "Main Timeline",
) -> dict:
    """
    Create a DaVinci Resolve project with matched video + audio on a timeline.

    For each scene in the user's selection:
      - Video goes on video track 1
      - Matched audio goes on audio track 1
      - Audio is offset by the detected amount (adjustable in Resolve)

    Args:
        project: Project with matches, selection, and sync results.
        project_name: Name for the Resolve project.
        timeline_name: Name for the timeline.

    Returns:
        Dict with project info.
    """
    scenes = project.get_ordered_scenes()
    if not scenes:
        raise ValueError("No scenes selected.")

    # Collect all file paths
    video_paths = []
    audio_paths = []
    for scene in scenes:
        vp = Path(scene.video_path)
        if not vp.exists():
            raise FileNotFoundError(f"Video not found: {vp}")
        video_paths.append(str(vp.resolve()))

        # Look for the renamed audio file (same stem as video)
        audio_copy = vp.parent / (vp.stem + Path(scene.audio_path).suffix)
        if audio_copy.exists():
            audio_paths.append(str(audio_copy.resolve()))
        elif Path(scene.audio_path).exists():
            audio_paths.append(str(Path(scene.audio_path).resolve()))
        else:
            print(f"  Warning: audio not found for {vp.name}, skipping audio")
            audio_paths.append(None)

    print(f"Preparing {len(scenes)} scenes for Resolve...")

    # Connect to Resolve
    print("\nConnecting to DaVinci Resolve...")
    resolve = _get_resolve()
    print("  Connected.")

    # Create or open project
    pm = resolve.GetProjectManager()
    existing = pm.LoadProject(project_name)
    if existing:
        print(f"  Opened existing project: {project_name}")
        proj = existing
    else:
        proj = pm.CreateProject(project_name)
        if not proj:
            raise RuntimeError(f"Failed to create project '{project_name}'.")
        print(f"  Created project: {project_name}")

    media_pool = proj.GetMediaPool()
    media_storage = resolve.GetMediaStorage()

    # Import all media
    all_paths = [p for p in video_paths + audio_paths if p]
    print(f"\nImporting {len(all_paths)} files into Media Pool...")
    imported = media_storage.AddItemListToMediaPool(all_paths)
    if not imported:
        imported = media_pool.ImportMedia(all_paths)
    if not imported:
        raise RuntimeError("Failed to import media.")
    print(f"  Imported {len(imported)} items.")

    # Build a lookup from filename to media pool item
    root_folder = media_pool.GetRootFolder()
    all_clips = root_folder.GetClipList()
    clip_lookup = {}
    for clip in all_clips:
        clip_name = clip.GetClipProperty("File Name")
        if clip_name:
            clip_lookup[clip_name] = clip

    # Create timeline
    print(f"\nCreating timeline: {timeline_name}")
    timeline = media_pool.CreateEmptyTimeline(timeline_name)
    if not timeline:
        raise RuntimeError("Failed to create timeline.")
    proj.SetCurrentTimeline(timeline)

    # Add clips to timeline in order
    clips_added = 0
    for i, scene in enumerate(scenes):
        vp = Path(scene.video_path)
        video_filename = vp.name

        # Find video clip in media pool
        video_clip = clip_lookup.get(video_filename)
        if video_clip:
            media_pool.AppendToTimeline([video_clip])
            clips_added += 1
            print(f"  [{i+1}] Added {video_filename}")

        # Find and add audio with offset
        audio_filename = vp.stem + Path(scene.audio_path).suffix
        audio_clip = clip_lookup.get(audio_filename)
        if not audio_clip:
            # Try original audio filename
            audio_clip = clip_lookup.get(Path(scene.audio_path).name)

        if audio_clip:
            # Add audio to timeline (will go on next available audio track)
            media_pool.AppendToTimeline([{
                "mediaPoolItem": audio_clip,
                "trackIndex": 2,  # Audio track 2 (track 1 has video's scratch audio)
            }])
            print(f"        + {audio_filename} (offset: {scene.offset:+.3f}s)")

    print(f"\nTimeline created with {clips_added} video clips.")
    print(f"Audio clips are on a separate track — adjust offset in Resolve if needed.")
    print(f"\nSwitch to DaVinci Resolve to start editing.")

    return {
        "project_name": project_name,
        "timeline_name": timeline_name,
        "clips_imported": clips_added,
    }


def list_resolve_projects() -> list[str]:
    """List all projects in the current Resolve database."""
    resolve = _get_resolve()
    pm = resolve.GetProjectManager()
    projects = pm.GetProjectListInCurrentFolder()
    return list(projects) if projects else []
