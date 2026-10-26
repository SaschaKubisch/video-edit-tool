"""Synthetic media for the render tests: videos whose frames can be told apart by
their grey value, WAV files, and readers that measure what ffmpeg wrote."""

import json
import subprocess
import wave
from fractions import Fraction
from pathlib import Path

import numpy as np

import tools

FPS = Fraction(30000, 1001)


def frame_value(i: int) -> int:
    """The grey value of source frame i: neighbours differ by 7, so a cut that is one
    frame off is seen even after lossy coding."""
    return 20 + (7 * i) % 210


def write_ramp_mp4(path, frames: int, w: int, h: int, bf: bool = True, gop: int = 30,
                   white_frame: int = None, value=frame_value) -> None:
    """H.264 4:2:0 MP4, 30000/1001 fps, every frame one flat grey value. `white_frame`
    makes that frame white (for the sync marker)."""
    raw = bytearray()
    for i in range(frames):
        v = 235 if i == white_frame else value(i)
        raw += np.full((h, w), v, np.uint8).tobytes()
    cmd = [tools.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", "30000/1001", "-i", "-",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p",
           "-g", str(gop), "-bf", "2" if bf else "0", "-color_primaries", "bt709",
           "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
           "-movflags", "+faststart", str(path)]
    subprocess.run(cmd, input=bytes(raw), check=True)


def write_wav(path, samples: np.ndarray, rate: int) -> None:
    """16-bit PCM WAV; `samples` is (n, channels) in -1..1."""
    x = np.clip(np.asarray(samples, np.float64), -1, 1)
    if x.ndim == 1:
        x = x[:, None]
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())


def sine_wav(path, seconds: float, rate: int, freq: float = 440.0, level: float = 0.3,
             channels: int = 2) -> None:
    t = np.arange(int(seconds * rate)) / rate
    x = level * np.sin(2 * np.pi * freq * t)
    write_wav(path, np.stack([x] * channels, axis=-1), rate)


def probe(path) -> dict:
    r = subprocess.run([tools.ffprobe_path(), "-v", "error", "-show_streams", "-show_format",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def video_frames(path) -> int:
    r = subprocess.run([tools.ffprobe_path(), "-v", "error", "-count_packets", "-select_streams",
                        "v:0", "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0",
                        str(path)], capture_output=True, text=True, check=True)
    return int(r.stdout.strip().split(",")[0])


def read_luma(path, w: int, h: int) -> np.ndarray:
    """All frames of the video as an array (frames, h, w) of luma, at the file's own size."""
    r = subprocess.run([tools.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-an",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(-1, h, w)


def read_audio(path, rate: int = 48000) -> np.ndarray:
    """The audio of a file as float32 (samples, 2) at `rate`."""
    r = subprocess.run([tools.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-vn",
                        "-f", "f32le", "-ac", "2", "-ar", str(rate), "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.float32).reshape(-1, 2)


def duration(info: dict, kind: str) -> float:
    for s in info["streams"]:
        if s["codec_type"] == kind:
            return float(s.get("duration") or info["format"]["duration"])
    raise KeyError(kind)
