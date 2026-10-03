"""
Project file management.
Stores match results, offsets, selections, and scene metadata in a JSON file.
"""

import dataclasses
import json
import os
import tempfile
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

import config


@dataclass
class SceneMatch:
    """A matched video + audio pair.

    offset contract: offset > 0 means the audio recording's content is LATER
    in the audio file than in the video, i.e. the recorder started `offset`
    seconds BEFORE the camera. To align, the audio clip's source in-point is
    advanced by `offset` seconds. If offset < 0, the audio clip is instead
    placed |offset| seconds later on the timeline relative to its video.

    trim_start / trim_end are seconds cut from the start / end of the clip.
    Trims are only stored here; they are applied in Resolve.
    """
    index: int
    video_path: str
    audio_path: str
    video_duration: float           # seconds
    audio_duration: float           # seconds
    offset: float                   # seconds, see the contract below
    confidence: float               # 0.0 - 1.0
    thumbnail_path: str = ""        # path to extracted thumbnail
    label: str = ""                 # optional user label
    trim_start: float = 0.0        # seconds cut from the START (0.0 = none)
    trim_end: float = 0.0          # seconds cut from the END (0.0 = none)

    @property
    def effective_duration(self) -> float:
        """Duration after trimming."""
        return max(0, min(self.video_duration, self.audio_duration)
                   - self.trim_start - self.trim_end)

    def fmt_duration(self, seconds: float = None) -> str:
        s = seconds if seconds is not None else self.effective_duration
        m, sec = divmod(s, 60)
        return f"{int(m):02d}:{sec:05.2f}"


def _num(value, default):
    return default if value is None else float(value)


def _scene_from_dict(d: dict) -> SceneMatch:
    """Build a SceneMatch, ignoring unknown keys (older/newer project files)."""
    known = {f.name for f in dataclasses.fields(SceneMatch)}
    return SceneMatch(**{k: v for k, v in d.items() if k in known})


@dataclass
class Project:
    """Full project state."""
    video_dir: str = ""
    audio_dir: str = ""
    output_dir: str = ""
    matches: list = field(default_factory=list)   # list of SceneMatch dicts
    selection: list = field(default_factory=list)  # ordered list of scene indices
    selection_trims: dict = field(default_factory=dict)  # {index: {start, end}}

    def save(self, path: str = None):
        path = path or config.PROJECT_FILE
        data = {
            "video_dir": self.video_dir,
            "audio_dir": self.audio_dir,
            "output_dir": self.output_dir,
            "matches": [asdict(m) if isinstance(m, SceneMatch) else m for m in self.matches],
            "selection": self.selection,
            "selection_trims": self.selection_trims,
        }
        # Atomic write: temp file in the same directory, then replace.
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".project-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str = None) -> "Project":
        path = path or config.PROJECT_FILE
        with open(path) as f:
            data = json.load(f)

        proj = cls(
            video_dir=data.get("video_dir", ""),
            audio_dir=data.get("audio_dir", ""),
            output_dir=data.get("output_dir", ""),
            selection=data.get("selection", []),
            selection_trims=data.get("selection_trims", {}),
        )
        proj.matches = [_scene_from_dict(m) for m in data.get("matches", [])]
        return proj

    def get_match(self, index: int) -> Optional[SceneMatch]:
        for m in self.matches:
            m_obj = m if isinstance(m, SceneMatch) else _scene_from_dict(m)
            if m_obj.index == index:
                return m_obj
        return None

    def get_ordered_scenes(self) -> list:
        """Return copies of SceneMatch objects in the user's selected order,
        with per-scene trims applied. Stored matches are never mutated.
        selection_trims may be keyed by int or str index."""
        result = []
        for idx in self.selection:
            m = self.get_match(idx)
            if m is None:
                continue
            trims = self.selection_trims.get(str(idx))
            if trims is None:
                trims = self.selection_trims.get(idx, {})
            result.append(dataclasses.replace(
                m,
                trim_start=_num(trims.get("start"), m.trim_start),
                trim_end=_num(trims.get("end"), m.trim_end),
            ))
        return result
