"""
The decoder interface (SPECS 6.2, requirement C7).

A decoder turns a .braw clip into pictures and camera audio. `convert.py`
and, later, `render.py` use only this interface, so a second decoder (the
Blackmagic RAW SDK) can replace the first without changes elsewhere.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ClipInfo:
    """Facts about one clip, as the decoder reports them."""
    width: int                  # decoded picture size at full scale (SDK size)
    height: int
    fps_num: int
    fps_den: int
    frames: int
    timecode_start: str = ""    # "hh:mm:ss:ff", empty when the clip has none
    colour_science_gen: str = ""
    gamma: str = ""
    gamut: str = ""
    iso: int = 0
    white_balance_k: int = 0
    has_sidecar: bool = False
    camera_type: str = ""


@dataclass
class AudioInfo:
    """The camera audio written by Decoder.write_audio (raw s32le, interleaved)."""
    sample_rate: int
    channels: int
    samples: int                # per channel


class Decoder(ABC):
    name = "decoder"

    def check(self) -> None:
        """Raise RuntimeError with install steps if the decoder cannot run."""

    @abstractmethod
    def probe(self, path: str) -> ClipInfo:
        """Open the clip and report its facts. Decodes no picture."""

    @abstractmethod
    def write_frames(self, path: str, scale: int, sink, max_frames=None) -> int:
        """Write raw 16-bit RGB frames (ffmpeg pixel format gbrp16le: planes in
        the order green, blue, red) of the clip, decoded at 1/scale of the full
        size, to the binary file object `sink`. Returns the frames written."""

    @abstractmethod
    def write_audio(self, path: str, dest: str, max_frames=None):
        """Write the camera audio to the file `dest` as raw s32le (24 bit audio in
        the upper bits). Returns an AudioInfo, or None when the clip has no audio.
        `max_frames` limits the audio to the duration of that many frames."""


def container_tracks(path: str) -> list:
    """All tracks of the .braw container as ffprobe reports them: index, type,
    codec name and tag, handler name. The two timed-metadata tracks of the
    camera show up as type "data" (SPECS 6.8)."""
    import json
    import subprocess

    import tools
    cmd = [tools.ffprobe_path(), "-v", "error", "-show_entries",
           "stream=index,codec_type,codec_name,codec_tag_string:stream_tags=handler_name",
           "-of", "json", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {r.stderr.strip()}")
    out = []
    for s in json.loads(r.stdout).get("streams", []):
        out.append({"index": s.get("index"), "codec_type": s.get("codec_type", ""),
                    "codec_name": s.get("codec_name", ""),
                    "codec_tag": s.get("codec_tag_string", ""),
                    "handler": (s.get("tags") or {}).get("handler_name", "")})
    return out


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)


class BrawSourceDecoder(Decoder):
    """Decoder 1: vapoursynth-brawsource (MIT) with the Blackmagic RAW runtime.

    Opens the clip with the plugin's `braw.Source`, on the GPU through Metal. The
    picture is handed out as the camera recorded it (gamma "Blackmagic Design
    Film", gamut "Blackmagic Design"); no colour override is applied (SPECS 6.5).
    VapourSynth gives the colour planes in the order red, green, blue; ffmpeg's
    gbrp16le wants green, blue, red, so the planes are reordered.
    """

    def __init__(self, pipeline="metal", cache_mb=1024):
        self.pipeline = pipeline
        self.cache_mb = cache_mb
        self._vs = None

    @property
    def name(self) -> str:
        try:
            from importlib.metadata import version
            return f"vapoursynth-brawsource {version('vapoursynth-brawsource')}"
        except Exception:
            return "vapoursynth-brawsource"

    def check(self):
        try:
            import vapoursynth
        except ImportError as e:
            raise RuntimeError(
                "VapourSynth is not installed. Set up the environment: "
                "uv venv --python 3.12 .venv && "
                "uv pip install --python .venv/bin/python -r requirements.txt, "
                "and run the tool with .venv/bin/python.") from e
        if not hasattr(vapoursynth.core, "braw"):
            raise RuntimeError(
                "The VapourSynth plugin 'braw' (vapoursynth-brawsource) is not loaded. "
                "Install it: uv pip install --python .venv/bin/python vapoursynth-brawsource")

    def _core(self):
        if self._vs is None:
            import vapoursynth
            self._vs = vapoursynth
            vapoursynth.core.max_cache_size = self.cache_mb
        return self._vs.core

    def probe(self, path):
        core = self._core()
        c = core.braw.Source(str(path), pipeline=self.pipeline, scale=1)
        props = c.get_frame(0).props
        ident = lambda key: _text(props.get(key, ""))
        return ClipInfo(
            width=c.width, height=c.height,
            fps_num=c.fps.numerator, fps_den=c.fps.denominator,
            frames=c.num_frames,
            timecode_start=ident("BRAWTimecode"),
            colour_science_gen=ident("BRAWColorScienceGen"),
            gamma=ident("BRAWGamma"), gamut=ident("BRAWGamut"),
            iso=int(props.get("BRAWISO", 0) or 0),
            white_balance_k=int(props.get("BRAWWhiteBalanceKelvin", 0) or 0),
            has_sidecar=bool(props.get("BRAWSidecarAttached", 0)),
            camera_type=ident("BRAWCameraType"))

    def write_frames(self, path, scale, sink, max_frames=None):
        vs = self._vs or (self._core() and self._vs)
        core = vs.core
        c = core.braw.Source(str(path), pipeline=self.pipeline, scale=scale)
        if max_frames:
            c = c[:max_frames]
        n = c.num_frames
        c = core.std.ShufflePlanes(c, planes=[1, 2, 0], colorfamily=vs.RGB)
        c.output(sink, y4m=False, prefetch=0, backlog=-1)
        return n

    def write_audio(self, path, dest, max_frames=None):
        import numpy as np
        vs = self._vs or (self._core() and self._vs)
        core = vs.core
        try:
            au = core.braw.AudioSource(str(path))
            rate, total, channels = au.sample_rate, au.num_samples, au.num_channels
        except vs.Error:
            return None
        if not channels or not total:
            return None
        want = total
        if max_frames:
            fps = core.braw.Source(str(path), pipeline=self.pipeline, scale=8).fps
            want = min(total, round(max_frames * rate * fps.denominator / fps.numerator))
        done = 0
        with open(dest, "wb") as f:
            for fr in au.frames(prefetch=0, backlog=-1, close=True):
                # (samples, channels) of unsigned 32-bit values with the 24-bit
                # audio in the upper bits: this is s32le.
                x = np.stack([np.asarray(fr[ch]) for ch in range(channels)], axis=-1)
                take = min(len(x), want - done)
                if take <= 0:
                    break
                f.write(x[:take].astype("<u4").tobytes())
                done += take
        return AudioInfo(sample_rate=rate, channels=channels, samples=done)
