"""The example of SPECS.md section 3.3, read from the specification itself, so
that the example and the validator cannot drift apart."""

import copy
import json
import re
from pathlib import Path

SPECS = Path(__file__).resolve().parent.parent / "SPECS.md"


def _load():
    text = SPECS.read_text(encoding="utf-8")
    start = text.index("### 3.3 Example")
    block = re.search(r"```json\n(.*?)\n```", text[start:], re.S).group(1)
    return json.loads(block)


_EXAMPLE = _load()


def example():
    """A fresh deep copy of the section 3.3 example."""
    return copy.deepcopy(_EXAMPLE)


def short_example():
    """A valid short built from the example: kind short, fill frame, derived_from."""
    d = example()
    d["kind"] = "short"
    d["film"]["frame"] = {"width": 1080, "height": 1920}
    d["film"]["fit"] = "fill"
    d["subtitles"]["cues"][0]["text"] = "The pan is hot."        # 9:16 lines hold 24 characters
    d["derived_from"] = {"film": "/Volumes/SSD/film/timeline.json",
                         "target_length_s": 60, "settings": {"x_free": 1}}
    d["exports"] = [{"name": "short-vertical",
                     "output": "/Volumes/SSD/film/renders/short.mp4",
                     "width": 1080, "height": 1920,
                     "video": {"codec": "h264_videotoolbox",
                               "rate_control": {"mode": "bitrate", "mbps": 8}},
                     "audio": {"codec": "aac", "bitrate_kbps": 384, "sample_rate": 48000}}]
    return d
