"""
File renaming for matched video + audio pairs.
Gives matched pairs consistent names and creates a backup mapping.
"""

import shutil
from pathlib import Path

import config
from project import Project, SceneMatch


def rename_matches(
    project: Project,
    pattern: str = None,
    dry_run: bool = False,
) -> list[dict]:
    """
    Rename matched video + audio file pairs to share a common name.

    Creates a backup mapping file (rename_log.json) so renames can be undone.

    Args:
        project: Project with matched scenes.
        pattern: Naming pattern with {index} and optional {label}.
                 Default: "scene_{index:02d}"
        dry_run: If True, just print what would happen without renaming.

    Returns:
        List of rename operations performed.
    """
    pattern = pattern or config.RENAME_PATTERN
    operations = []

    print("Rename plan:" if dry_run else "Renaming files...")

    for match in project.matches:
        m = match if isinstance(match, SceneMatch) else SceneMatch(**match)

        video_path = Path(m.video_path)
        audio_path = Path(m.audio_path)

        # Build new base name
        base_name = pattern.format(index=m.index, label=m.label or "")

        new_video_name = base_name + video_path.suffix
        new_audio_name = base_name + audio_path.suffix

        new_video_path = video_path.parent / new_video_name
        new_audio_path = audio_path.parent / new_audio_name

        op = {
            "index": m.index,
            "video_old": str(video_path),
            "video_new": str(new_video_path),
            "audio_old": str(audio_path),
            "audio_new": str(new_audio_path),
        }
        operations.append(op)

        prefix = "[DRY RUN] " if dry_run else ""
        print(f"  {prefix}[{m.index}]")
        print(f"    Video: {video_path.name} -> {new_video_name}")
        print(f"    Audio: {audio_path.name} -> {new_audio_name}")

        if not dry_run:
            # Rename files
            if video_path != new_video_path:
                video_path.rename(new_video_path)
            if audio_path != new_audio_path:
                audio_path.rename(new_audio_path)

            # Update project paths
            m.video_path = str(new_video_path)
            m.audio_path = str(new_audio_path)

    if not dry_run:
        # Update matches in project with new paths
        project.matches = [
            m if isinstance(m, SceneMatch) else SceneMatch(**m)
            for m in project.matches
        ]
        # Save backup log
        import json
        log_path = Path(project.video_dir) / "rename_log.json"
        with open(log_path, "w") as f:
            json.dump(operations, f, indent=2)
        print(f"\n  Rename log saved to: {log_path}")
        print("  (Use this to undo renames if needed)")

    return operations


def undo_renames(log_path: str, dry_run: bool = False):
    """
    Undo renames using a rename_log.json file.

    Args:
        log_path: Path to the rename log JSON.
        dry_run: If True, just print what would happen.
    """
    import json
    with open(log_path) as f:
        operations = json.load(f)

    print("Undo plan:" if dry_run else "Undoing renames...")

    for op in operations:
        prefix = "[DRY RUN] " if dry_run else ""

        video_new = Path(op["video_new"])
        video_old = Path(op["video_old"])
        audio_new = Path(op["audio_new"])
        audio_old = Path(op["audio_old"])

        print(f"  {prefix}[{op['index']}]")
        print(f"    Video: {video_new.name} -> {video_old.name}")
        print(f"    Audio: {audio_new.name} -> {audio_old.name}")

        if not dry_run:
            if video_new.exists() and video_new != video_old:
                video_new.rename(video_old)
            if audio_new.exists() and audio_new != audio_old:
                audio_new.rename(audio_old)

    if not dry_run:
        print("  Renames undone.")
