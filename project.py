"""
Project file management.
Stores match results, offsets, selections, and scene metadata in a JSON file.
"""

import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

import config


@dataclass
class SceneMatch:
    """A matched video + audio pair."""
    index: int
    video_path: str
    audio_path: str
    video_duration: float           # seconds
    audio_duration: float           # seconds
    offset: float                   # seconds — audio offset relative to video
    confidence: float               # 0.0 - 1.0
    thumbnail_path: str = ""        # path to extracted thumbnail
    label: str = ""                 # optional user label
    trim_start: float = 0.0        # seconds to trim from start
    trim_end: float = 0.0          # seconds to trim from end

    @property
    def effective_duration(self) -> float:
        """Duration after trimming."""
        return max(0, min(self.video_duration, self.audio_duration)
                   - self.trim_start - self.trim_end)

    def fmt_duration(self, seconds: float = None) -> str:
        s = seconds if seconds is not None else self.effective_duration
        m, sec = divmod(s, 60)
        return f"{int(m):02d}:{sec:05.2f}"


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
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

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
        proj.matches = [SceneMatch(**m) for m in data.get("matches", [])]
        return proj

    def get_match(self, index: int) -> Optional[SceneMatch]:
        for m in self.matches:
            m_obj = m if isinstance(m, SceneMatch) else SceneMatch(**m)
            if m_obj.index == index:
                return m_obj
        return None

    def get_ordered_scenes(self) -> list:
        """Return SceneMatch objects in the user's selected order."""
        result = []
        for idx in self.selection:
            m = self.get_match(idx)
            if m:
                # Apply per-scene trims from selection
                trims = self.selection_trims.get(str(idx), {})
                m.trim_start = trims.get("start", m.trim_start)
                m.trim_end = trims.get("end", m.trim_end)
                result.append(m)
        return result
