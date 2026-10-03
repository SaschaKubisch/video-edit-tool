"""
Sync step: copy H2N audio files into the video folder, renamed to match
their video counterpart. No re-encoding. Both files then get pushed to
DaVinci Resolve with the detected offset.
"""

import shutil
from pathlib import Path

from project import Project, SceneMatch


def sync_all_scenes(
    project: Project,
    output_dir: str = None,
) -> list[dict]:
    """
    For each matched scene in the user's selected order:
      - Copy the H2N .WAV file into the output directory
      - Rename it to match the video filename (e.g. IMG_2231.WAV next to IMG_2231.MOV)

    No re-encoding, no merging. The video files stay where they are.
    Both video + renamed audio get pushed to DaVinci Resolve with the offset.

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
            shutil.copy2(str(audio_path), str(new_audio_path))
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

    print(f"\nDone! {len(results)} audio files copied to: {out_dir}/")
    print("Each .WAV now shares a name with its matching .MOV.")

    return results
