"""Test helpers: a fake decoder that makes small synthetic clips, and an identity LUT."""

import numpy as np

from decoder import AudioInfo, ClipInfo, Decoder

IDENTITY_CUBE = "LUT_3D_SIZE 2\n" + "\n".join(
    f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)) + "\n"


def small_clip(frames=12, width=128, height=72, tc="01:02:03:04", gen="5", **kw):
    return ClipInfo(width=width, height=height, fps_num=30000, fps_den=1001,
                    frames=frames, timecode_start=tc, colour_science_gen=gen,
                    gamma="Blackmagic Design Film", gamut="Blackmagic Design",
                    iso=500, white_balance_k=4000, **kw)


class FakeDecoder(Decoder):
    """Returns the ClipInfo it was given for a path and writes synthetic frames
    (a moving gradient, so no frame is constant) and a sine wave as audio.

    `short_by` makes write_frames deliver fewer frames than probe reports, to
    provoke a failed check. `fail_on` names paths whose write_frames raises."""
    name = "fake"

    def __init__(self, clips, audio=True, short_by=0, fail_on=(), audio_short_by=0):
        self.clips = dict(clips)
        self.audio = audio
        self.short_by = short_by
        self.audio_short_by = audio_short_by      # frames of audio missing against the picture
        self.fail_on = set(fail_on)
        self.calls = []

    def probe(self, path):
        self.calls.append(("probe", path))
        if path not in self.clips:
            raise RuntimeError(f"cannot open {path}")
        return self.clips[path]

    def write_frames(self, path, scale, sink, max_frames=None):
        self.calls.append(("frames", path))
        if path in self.fail_on:
            raise RuntimeError("decoder crashed")
        info = self.clips[path]
        w, h = info.width // scale, info.height // scale
        n = info.frames if max_frames is None else min(max_frames, info.frames)
        n -= self.short_by
        xs = np.linspace(0, 60000, w, dtype=np.float64)[None, :].repeat(h, axis=0)
        for i in range(n):
            g = ((xs + i * 2000) % 65535).astype("<u2")
            b = ((xs[::-1] + i * 1000) % 65535).astype("<u2")
            r = np.full((h, w), (i * 3000) % 65535, dtype="<u2")
            sink.write(g.tobytes() + b.tobytes() + r.tobytes())
        return n

    def write_frame_range(self, path, scale, sink, first, last):
        self.calls.append(("range", path, first, last))
        if path in self.fail_on:
            raise RuntimeError("decoder crashed")
        info = self.clips[path]
        if not 0 <= first < last <= info.frames:
            raise ValueError(f"frames {first} to {last} are outside the {info.frames} frames")
        w, h = info.width // scale, info.height // scale
        n = last - first - self.short_by
        xs = np.linspace(0, 60000, w, dtype=np.float64)[None, :].repeat(h, axis=0)
        for i in range(first, first + n):
            g = ((xs + i * 2000) % 65535).astype("<u2")
            b = ((xs[::-1] + i * 1000) % 65535).astype("<u2")
            r = np.full((h, w), (i * 3000) % 65535, dtype="<u2")
            sink.write(g.tobytes() + b.tobytes() + r.tobytes())
        return n

    def write_audio(self, path, dest, max_frames=None):
        self.calls.append(("audio", path))
        if not self.audio:
            return None
        info = self.clips[path]
        n_frames = info.frames if max_frames is None else min(max_frames, info.frames)
        samples = round((n_frames - self.audio_short_by) * 48000 * info.fps_den / info.fps_num)
        t = np.arange(samples) / 48000.0
        wave = (np.sin(2 * np.pi * 440 * t) * 0.3 * (2 ** 31)).astype("<i4")
        stereo = np.stack([wave, wave], axis=-1)
        with open(dest, "wb") as f:
            f.write(stereo.tobytes())
        return AudioInfo(sample_rate=48000, channels=2, samples=samples)


import tempfile
from pathlib import Path


class Workspace:
    """A temporary 'originals' folder with fake .braw files, an empty work
    folder, an identity LUT and a FakeDecoder that knows the clips."""

    def __init__(self, testcase, names=("A001_C001", "A001_C002"), **clip_kw):
        tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.src = self.root / "Recordings"
        self.src.mkdir()
        self.work = self.root / "work"
        self.lut = self.root / "id.cube"
        self.lut.write_text(IDENTITY_CUBE)
        self.refs = []
        clips = {}
        for i, name in enumerate(names):
            path = self.src / f"{name}.braw"
            path.write_bytes(b"fake braw %d " % i * 100)
            kw = dict(clip_kw)
            clips[str(path)] = small_clip(**kw)
            self.refs.append((name, str(path)))
        self.decoder = FakeDecoder(clips)

    def listing(self, folder=None):
        """(relative name, size, mtime_ns, sha256) of every file below `folder`."""
        import hashlib
        folder = Path(folder or self.src)
        out = []
        for p in sorted(folder.rglob("*")):
            st = p.stat()
            digest = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""
            out.append((str(p.relative_to(folder)), st.st_size, st.st_mtime_ns, digest))
        return out
