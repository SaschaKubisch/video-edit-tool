"""
Thumbnail extraction.
Grabs a representative frame from each video with ffmpeg. The web app
(server.py) and the CLI match command (main.py) both use it.
"""

import subprocess
from pathlib import Path

import config
from project import Project, SceneMatch


def extract_thumbnail(
    video_path: str,
    output_path: str,
    time_sec: float = None,
    width: int = None,
) -> Path:
    """
    Extract a single frame from a video as a thumbnail image.

    Args:
        video_path: Path to the video file.
        output_path: Where to save the thumbnail.
        time_sec: Time offset in seconds to grab the frame.
        width: Output width (height scales proportionally).

    Returns:
        Path to the thumbnail image.
    """
    time_sec = time_sec if time_sec is not None else config.THUMBNAIL_TIME_SEC
    width = width or config.THUMBNAIL_WIDTH

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(time_sec),
        "-i", str(video_path),
        "-vframes", "1",
        "-vf", f"scale={width}:-1",
        "-q:v", str(max(1, min(31, 32 - int(config.THUMBNAIL_QUALITY / 3.2)))),
        str(out),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Thumbnail extraction failed for {video_path}:\n{result.stderr[-500:]}")

    return out


def extract_all_thumbnails(
    project: Project,
    output_dir: str,
) -> Project:
    """
    Extract thumbnails for all matched scenes.

    Args:
        project: Project with matched scenes.
        output_dir: Directory to save thumbnails.

    Returns:
        Updated project with thumbnail paths.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Extracting thumbnails...")
    for i, match in enumerate(project.matches):
        m = match if isinstance(match, SceneMatch) else SceneMatch(**match)

        thumb_path = out_dir / f"thumb_{m.index:03d}.{config.THUMBNAIL_FORMAT}"
        try:
            extract_thumbnail(m.video_path, str(thumb_path))
            m.thumbnail_path = str(thumb_path)
            project.matches[i] = m
            print(f"  [{m.index}] {Path(m.video_path).name} -> {thumb_path.name}")
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the rest
            print(f"  [{m.index}] Failed: {e}")

    return project
