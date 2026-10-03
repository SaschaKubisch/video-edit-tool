"""
File renaming for matched video + audio pairs.
Gives matched pairs consistent names and creates a backup mapping.
"""

import json
from pathlib import Path

import config
from project import Project, SceneMatch


def _check_collisions(pairs):
    """
    pairs: list of (source, target) paths. Raise FileExistsError, before any
    file is touched, if a target already exists (other than the source
    itself) or if two renames share the same target.
    """
    seen = {}
    problems = []
    for src, dst in pairs:
        src, dst = Path(src), Path(dst)
        if src == dst:
            continue
        if dst.exists():
            problems.append(f"{dst} already exists (would be overwritten by {src.name})")
        if dst in seen:
            problems.append(f"{seen[dst].name} and {src.name} both map to {dst}")
        seen[dst] = src
    if problems:
        raise FileExistsError(
            "Rename aborted, nothing was changed:\n  " + "\n  ".join(problems)
        )


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
    scenes = []

    for match in project.matches:
        m = match if isinstance(match, SceneMatch) else SceneMatch(**match)
        scenes.append(m)

        video_path = Path(m.video_path)
        audio_path = Path(m.audio_path)

        # Build new base name
        base_name = pattern.format(index=m.index, label=m.label or "")

        operations.append({
            "index": m.index,
            "video_old": str(video_path),
            "video_new": str(video_path.parent / (base_name + video_path.suffix)),
            "audio_old": str(audio_path),
            "audio_new": str(audio_path.parent / (base_name + audio_path.suffix)),
        })

    # Abort before touching any file if a target would clobber something.
    _check_collisions(
        [(op["video_old"], op["video_new"]) for op in operations]
        + [(op["audio_old"], op["audio_new"]) for op in operations]
    )

    print("Rename plan:" if dry_run else "Renaming files...")

    for m, op in zip(scenes, operations):
        video_path, new_video_path = Path(op["video_old"]), Path(op["video_new"])
        audio_path, new_audio_path = Path(op["audio_old"]), Path(op["audio_new"])

        prefix = "[DRY RUN] " if dry_run else ""
        print(f"  {prefix}[{m.index}]")
        print(f"    Video: {video_path.name} -> {new_video_path.name}")
        print(f"    Audio: {audio_path.name} -> {new_audio_path.name}")

        if not dry_run:
            if video_path != new_video_path:
                video_path.rename(new_video_path)
            if audio_path != new_audio_path:
                audio_path.rename(new_audio_path)

            # Update project paths
            m.video_path = str(new_video_path)
            m.audio_path = str(new_audio_path)

    if not dry_run:
        # Update matches in project with new paths
        project.matches = scenes
        # Save backup log
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
    with open(log_path) as f:
        operations = json.load(f)

    # Only renames that can still happen (source exists) can collide.
    _check_collisions([
        (op[f"{kind}_new"], op[f"{kind}_old"])
        for op in operations for kind in ("video", "audio")
        if Path(op[f"{kind}_new"]).exists()
    ])

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
