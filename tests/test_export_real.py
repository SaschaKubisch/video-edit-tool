"""Real-data checks of the final film (SPECS 8.8, check 3.8, in small). They need the owner's
machine. Each test is skipped, with its reason printed, when what it needs is missing:

  VET_REAL_BRAW   one real .braw clip
  VET_WORK_DIR    a work folder on the SSD (the tests write a small film there)

The base LUT is found in Resolve's LUT folder at run time. The film is a few seconds of the
clip at DCI 4K with the hardware encoder; the sound is a synthetic tone, because what is
checked here is the picture path: the frames the real decoder gives for a range, the base LUT
on the RAW decode and the finished file."""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import convert
import export
import timeline
import tools
from tests import media

REAL = os.environ.get("VET_REAL_BRAW", "")
WORK = os.environ.get("VET_WORK_DIR", "")


def _need():
    if not REAL or not Path(REAL).is_file():
        return "VET_REAL_BRAW is not set to a .braw file"
    if not WORK or not Path(WORK).is_dir():
        return "VET_WORK_DIR is not set to a folder (on the SSD)"
    try:
        import vapoursynth  # noqa: F401
    except ImportError:
        return "vapoursynth is not installed"
    try:
        convert.find_base_lut()
    except FileNotFoundError as e:
        return f"base LUT not found: {e}"
    return ""


SKIP = _need()


@unittest.skipIf(SKIP, SKIP)
class RealFilmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import decoder
        cls.decoder = decoder.BrawSourceDecoder()
        cls.info = cls.decoder.probe(REAL)
        cls.dir = Path(tempfile.mkdtemp(prefix="vet-real-export-", dir=WORK))
        cls.lut = convert.find_base_lut()
        (cls.dir / "audio").mkdir()
        wav = cls.dir / "audio" / "tone.wav"
        media.sine_wav(wav, 12, 48000, 330, 0.2)
        h = cls.info.height
        cls.tl = timeline.Timeline({
            "version": 1, "kind": "film",
            "film": {"fps": {"num": cls.info.fps_num, "den": cls.info.fps_den},
                     "frame": {"width": 4096, "height": 2160}, "fit": "fit",
                     "fade_in_s": 0.0, "fade_out_s": 0.0},
            "sources": {"C": {"original_video": REAL, "preview_copy": "/none.mp4", "audio": str(wav),
                              "audio_offset_s": 0.0, "frames": cls.info.frames,
                              "fps": {"num": cls.info.fps_num, "den": cls.info.fps_den},
                              "size": {"width": cls.info.width, "height": h},
                              "copy_lut_sha256": "0" * 64}},
            "segments": [{"id": "s001", "source": "C", "in": 100, "out": 130},
                         {"id": "s002", "source": "C", "in": 300, "out": 330}],
            "look": {"base": {"mode": "lut", "path": cls.lut, "sha256": convert.sha256_file(cls.lut)}},
            "audio": {"sample_rate": 48000, "loudness": {"mode": "off"}},
            "exports": [export.preset_entry("film-4k", str(cls.dir / "out"), {"width": 4096, "height": 2160})]})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_a_frame_range_is_the_same_frames_as_the_start_of_the_clip_gives(self):
        scale = 8                                   # 512 wide: quick
        w, h = self.info.width // scale, self.info.height // scale
        size = w * h * 6
        whole = io.BytesIO()
        self.decoder.write_frames(REAL, scale, whole, max_frames=25)
        part = io.BytesIO()
        n = self.decoder.write_frame_range(REAL, scale, part, 10, 20)
        self.assertEqual(n, 10)
        self.assertEqual(part.getvalue(), whole.getvalue()[10 * size:20 * size])

    def test_a_range_beyond_the_clip_is_refused(self):
        with self.assertRaises(ValueError):
            self.decoder.write_frame_range(REAL, 8, io.BytesIO(), self.info.frames - 2, self.info.frames + 5)

    def test_the_film_is_dci_4k_h264_with_the_frames_of_the_two_segments(self):
        r = export.render_export(self.tl, "film-4k", str(self.dir / "work"), self.decoder,
                                 protected=[str(Path(REAL).parent)], log=lambda s: None)
        self.assertTrue(r.ok, r.problems)
        streams = media.probe(r.path)["streams"]
        v = [s for s in streams if s["codec_type"] == "video"][0]
        self.assertEqual((v["codec_name"], v["width"], v["height"]), ("h264", 4096, 2160))
        self.assertEqual(r.frames, 60)
        # The picture is the RAW decode through the base LUT: its mean colour is close to what
        # the same frame has in the step-1 chain (convert's filter) at the same size.
        cmd = [tools.ffmpeg_path(), "-v", "error", "-i", r.path, "-vf", "select=eq(n\\,5),scale=512:-2",
               "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        got = np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.uint8)
        got = got.reshape(-1, 512, 3).astype(float).mean(axis=(0, 1))
        buf = io.BytesIO()
        self.decoder.write_frame_range(REAL, 8, buf, 105, 106)
        ref = subprocess.run(
            [tools.ffmpeg_path(), "-v", "error", "-f", "rawvideo", "-pix_fmt", "gbrp16le",
             "-s", f"{self.info.width // 8}x{self.info.height // 8}", "-i", "-",
             "-vf", convert.build_filter(self.lut) + ",scale=512:-2,format=rgb24",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            input=buf.getvalue(), capture_output=True, check=True).stdout
        want = np.frombuffer(ref, np.uint8).reshape(-1, 512, 3).astype(float).mean(axis=(0, 1))
        self.assertLess(float(np.abs(got - want).max()), 6.0, (got, want))


if __name__ == "__main__":
    unittest.main()
