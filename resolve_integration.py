"""
DaVinci Resolve integration.
Imports matched video + audio pairs into a Resolve project with a timeline.
Video goes on V1, the matching audio on A2, aligned by the detected offset
and cut by the user's trims.

build_timeline_plan() is pure (no Resolve imports) so it can be unit-tested
and reused from an in-Resolve script (SPECS section 6).

REQUIREMENTS:
  - DaVinci Resolve must be running.
  - External scripting (this CLI/app) may require Resolve Studio and
    Preferences > System > General > External scripting using: Local.
  - On the free edition, run the same logic from inside Resolve via
    Workspace > Scripts.
"""

import math
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
        "  1. DaVinci Resolve is running\n"
        "  2. External scripting is enabled:\n"
        "     Preferences > System > General > External scripting using: Local\n"
        "     (External scripting from outside Resolve may require the Studio\n"
        "     edition. On the free edition use the in-app route instead:\n"
        "     Workspace > Scripts.)\n"
        "  3. Set PYTHONPATH to Resolve's scripting modules:\n"
        "     macOS: export PYTHONPATH=\"/Library/Application Support/Blackmagic Design/"
        "DaVinci Resolve/Developer/Scripting/Modules/\"\n"
    )


def _frames(seconds: float, fps: float) -> int:
    return int(round(seconds * fps))


def build_timeline_plan(scenes, fps: float) -> list:
    """
    Pure timeline layout. No Resolve imports.

    Args:
        scenes: ordered SceneMatch-like objects with trims applied.
            trim_start / trim_end are seconds cut from the start / end.
            offset > 0: the audio content is LATER in the audio file than in
            the video (recorder started `offset` s before the camera), so the
            audio source in-point is advanced by `offset`. offset < 0: the
            audio is placed |offset| s later on the timeline.
        fps: timeline frame rate.

    Returns:
        One dict per scene:
          {"index", "video": clipInfo, "audio": clipInfo or None}
        where clipInfo = {startFrame, endFrame, recordFrame, trackIndex,
        mediaType}. startFrame/endFrame are source frames (endFrame is
        treated as exclusive); recordFrame is relative to the timeline start
        (0-based). Scenes are laid back-to-back.
    """
    plan = []
    cursor = 0
    for scene in scenes:
        v_total = max(0, int(math.floor(scene.video_duration * fps + 1e-6)))
        a_total = max(0, int(math.floor(scene.audio_duration * fps + 1e-6)))
        v_start = max(0, _frames(scene.trim_start, fps))
        v_end = min(v_total, v_total - _frames(scene.trim_end, fps))
        v_end = max(v_end, v_start)
        length = v_end - v_start

        video = {
            "startFrame": v_start, "endFrame": v_end, "recordFrame": cursor,
            "trackIndex": 1, "mediaType": 1,
        }

        # Audio: source position corresponding to the video's in-point.
        a_start = v_start + _frames(scene.offset, fps)
        delay = 0
        if a_start < 0:
            delay = -a_start       # no audio exists before the file begins
            a_start = 0
        delay = min(delay, length)
        a_end = min(a_total, a_start + (length - delay))
        audio = None
        if a_end > a_start:
            audio = {
                "startFrame": a_start, "endFrame": a_end,
                "recordFrame": cursor + delay,
                "trackIndex": 2, "mediaType": 2,
            }

        plan.append({"index": scene.index, "video": video, "audio": audio})
        cursor += length
    return plan


def resolve_media_paths(project: Project, scenes) -> list:
    """
    Per scene return (video_path, audio_path or None). Prefers the renamed
    sibling audio (<video_stem><audio suffix>) in the video folder or in
    project.output_dir, falling back to the original audio_path.
    """
    result = []
    for scene in scenes:
        vp = Path(scene.video_path)
        if not vp.exists():
            raise FileNotFoundError(f"Video not found: {vp}")
        suffix = Path(scene.audio_path).suffix
        candidates = [vp.parent / (vp.stem + suffix)]
        if project.output_dir:
            candidates.append(Path(project.output_dir) / (vp.stem + suffix))
        candidates.append(Path(scene.audio_path))
        audio = next((c for c in candidates if c.exists()), None)
        if audio is None:
            print(f"  Warning: audio not found for {vp.name}, skipping audio")
        result.append((str(vp.resolve()), str(audio.resolve()) if audio else None))
    return result


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


def _unique_timeline_name(proj, name: str) -> str:
    existing = set()
    try:
        count = int(proj.GetTimelineCount() or 0)
    except (TypeError, ValueError):
        count = 0
    for i in range(1, count + 1):
        tl = proj.GetTimelineByIndex(i)
        if tl:
            existing.add(tl.GetName())
    if name not in existing:
        return name
    n = 2
    while f"{name} {n}" in existing:
        n += 1
    return f"{name} {n}"


def _parse_fps(value, default: float = 24.0) -> float:
    try:
        f = float(value)
        return f if f > 0 else default
    except (TypeError, ValueError):
        return default


def apply_plan(resolve, project_name, timeline_name, scenes, plan=None,
               media_paths=None, fps=None) -> dict:
    """
    Do the Resolve API calls.

    Args:
        scenes: ordered scenes (trims applied).
        plan: output of build_timeline_plan; if None it is built once the
            timeline frame rate is known.
        media_paths: list of (video_path, audio_path or None) per scene
            (see resolve_media_paths).
        fps: optional override for the frame rate (set on the project
            before the timeline is created).
    """
    if media_paths is None:
        media_paths = [(s.video_path, s.audio_path) for s in scenes]

    pm = resolve.GetProjectManager()
    proj = pm.LoadProject(project_name)
    if proj:
        print(f"  Opened existing project: {project_name}")
    else:
        proj = pm.CreateProject(project_name)
        if not proj:
            raise RuntimeError(f"Failed to create project '{project_name}'.")
        print(f"  Created project: {project_name}")

    if fps:
        # Only takes effect before the project's first timeline exists.
        proj.SetSetting("timelineFrameRate", str(fps))

    media_pool = proj.GetMediaPool()
    all_paths = []
    for v, a in media_paths:
        for p in (v, a):
            if p and p not in all_paths:
                all_paths.append(p)

    print(f"\nImporting {len(all_paths)} files into Media Pool...")
    imported = media_pool.ImportMedia(all_paths) or []
    items = list(imported)
    try:
        items += list(media_pool.GetRootFolder().GetClipList() or [])
    except Exception:
        pass
    lookup = {}
    for it in items:
        fp = it.GetClipProperty("File Path")
        if fp:
            lookup.setdefault(_norm(fp), it)
    if not lookup:
        raise RuntimeError("Failed to import media.")

    timeline_name = _unique_timeline_name(proj, timeline_name)
    print(f"\nCreating timeline: {timeline_name}")
    timeline = media_pool.CreateEmptyTimeline(timeline_name)
    if not timeline:
        raise RuntimeError("Failed to create timeline.")
    proj.SetCurrentTimeline(timeline)

    while int(timeline.GetTrackCount("audio") or 0) < 2:
        if not timeline.AddTrack("audio", "stereo"):
            break

    if not fps:
        fps = _parse_fps(timeline.GetSetting("timelineFrameRate")
                         or proj.GetSetting("timelineFrameRate"))
    if plan is None:
        plan = build_timeline_plan(scenes, fps)

    base = int(timeline.GetStartFrame() or 0)
    clip_infos = []
    skipped = []
    for entry, (vpath, apath) in zip(plan, media_paths):
        for key, path in (("video", vpath), ("audio", apath)):
            info = entry.get(key)
            if not info:
                continue
            item = lookup.get(_norm(path)) if path else None
            if item is None:
                skipped.append(path or f"(no {key} for scene {entry['index']})")
                continue
            ci = dict(info)
            ci["mediaPoolItem"] = item
            ci["recordFrame"] = base + info["recordFrame"]
            clip_infos.append(ci)

    if clip_infos:
        media_pool.AppendToTimeline(clip_infos)
    print(f"\nTimeline '{timeline_name}' created ({len(clip_infos)} clips, {fps} fps).")
    if skipped:
        print(f"  Skipped (not found in Media Pool): {skipped}")
    print("\nSwitch to DaVinci Resolve to start editing.")
    return {
        "project_name": project_name,
        "timeline_name": timeline_name,
        "clips_imported": len(clip_infos),
        "fps": fps,
        "skipped": skipped,
    }


def create_resolve_project(
    project: Project,
    project_name: str = "Ambient Video Project",
    timeline_name: str = "Main Timeline",
    fps: Optional[float] = None,
) -> dict:
    """
    Create a DaVinci Resolve project with matched video (V1) and audio (A2)
    on a timeline, honouring trims and offsets.

    Args:
        fps: optional frame rate override (default: Resolve's timeline rate).
    """
    scenes = project.get_ordered_scenes()
    if not scenes:
        raise ValueError("No scenes selected.")
    media_paths = resolve_media_paths(project, scenes)
    print(f"Preparing {len(scenes)} scenes for Resolve...")
    print("\nConnecting to DaVinci Resolve...")
    resolve = _get_resolve()
    print("  Connected.")
    return apply_plan(resolve, project_name, timeline_name, scenes,
                      media_paths=media_paths, fps=fps)


def list_resolve_projects() -> list:
    """List all projects in the current Resolve database."""
    resolve = _get_resolve()
    pm = resolve.GetProjectManager()
    projects = pm.GetProjectListInCurrentFolder()
    return list(projects) if projects else []
