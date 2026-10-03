"""
Sync step: copy H2N audio files into the video folder, renamed to match
their video counterpart. No re-encoding. Both files then get pushed to
DaVinci Resolve with the detected offset.

Every copy is recorded in a manifest (ambient_audio_copies.json) in the
output folder so the step can be undone with remove_copied_audio().
"""

import json
import os
import shutil
from pathlib import Path

from project import Project, SceneMatch

MANIFEST_NAME = "ambient_audio_copies.json"


def _manifest_path(output_dir) -> Path:
    return Path(output_dir) / MANIFEST_NAME


def _read_manifest(output_dir) -> list:
    """Return the list of copied file names (empty if no/invalid manifest)."""
    path = _manifest_path(output_dir)
    if not path.exists():
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return [str(n) for n in data.get("copies", [])]
    except (OSError, ValueError, AttributeError):
        print(f"  Warning: could not read {path}, ignoring it")
        return []


def _write_manifest(output_dir, names) -> None:
    path = _manifest_path(output_dir)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as f:
        json.dump({"copies": sorted(set(names))}, f, indent=2)
    os.replace(tmp, path)


def sync_all_scenes(
    project: Project,
    output_dir: str = None,
) -> list:
    """
    For each matched scene in the user's selected order:
      - Copy the H2N .WAV file into the output directory
      - Rename it to match the video filename (e.g. IMG_2231.WAV next to IMG_2231.MOV)

    No re-encoding, no merging. The video files stay where they are.
    Both video + renamed audio get pushed to DaVinci Resolve with the offset.
    An existing, differently sized file at the destination is overwritten
    with a warning. Copies are listed in ambient_audio_copies.json in the
    output directory (merged with an existing manifest).

    Args:
        project: Project with matches and selection.
        output_dir: Directory to copy renamed audio files into.
                    Defaults to the video directory.

    Returns:
        List of dicts with video_path, audio_copy_path, offset per scene.
    """
    scenes = project.get_ordered_scenes()

    if not scenes:
        raise ValueError("No scenes selected. Run the selector first.")

    # Default output to the video directory
    out_dir = Path(output_dir) if output_dir else Path(project.video_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Copying and renaming {len(scenes)} audio files...")
    results = []
    copied = []

    for order_num, scene in enumerate(scenes, 1):
        video_path = Path(scene.video_path)
        audio_path = Path(scene.audio_path)

        # New audio filename = video stem + audio extension
        # e.g. IMG_2231.MOV -> IMG_2231.WAV
        new_audio_name = video_path.stem + audio_path.suffix
        new_audio_path = out_dir / new_audio_name

        print(f"  [{order_num}/{len(scenes)}] {audio_path.name} -> {new_audio_name}")
        print(f"    Matched to: {video_path.name}")
        print(f"    Offset: {scene.offset:+.3f}s")

        # Copy audio file with new name
        if new_audio_path != audio_path:
            if (new_audio_path.exists()
                    and os.path.getsize(new_audio_path) != os.path.getsize(audio_path)):
                print(f"    Warning: {new_audio_path} exists with a different size; "
                      f"overwriting it")
            shutil.copy2(str(audio_path), str(new_audio_path))
            copied.append(new_audio_name)
        else:
            print(f"    (already named correctly)")

        results.append({
            "order": order_num,
            "scene_index": scene.index,
            "video_path": str(video_path),
            "audio_original": str(audio_path),
            "audio_copy": str(new_audio_path),
            "offset": scene.offset,
            "trim_start": scene.trim_start,
            "trim_end": scene.trim_end,
        })

    if copied:
        _write_manifest(out_dir, _read_manifest(out_dir) + copied)

    print(f"\nDone! {len(results)} audio files copied to: {out_dir}/")
    print("Each .WAV now shares a name with its matching .MOV.")

    return results


def remove_copied_audio(output_dir: str) -> list:
    """
    Undo sync_all_scenes: delete only the files listed in the manifest in
    `output_dir`, then the manifest itself.

    Returns:
        List of removed file paths (strings). Files already gone are skipped.
    """
    out_dir = Path(output_dir)
    removed = []
    for name in _read_manifest(out_dir):
        # Manifest holds plain file names; never follow paths out of the folder.
        if os.path.basename(name) != name:
            print(f"  Skipping suspicious manifest entry: {name}")
            continue
        target = out_dir / name
        if target.is_file():
            target.unlink()
            removed.append(str(target))
    manifest = _manifest_path(out_dir)
    if manifest.exists():
        manifest.unlink()
    return removed
