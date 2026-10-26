"""Real-data checks of the `convert` command (SPECS 6.10, checks 1.6 and 1.7).

They need the owner's machine. Each test is skipped, with its reason printed,
when what it needs is missing:

  VET_REAL_BRAW   one real .braw clip
  VET_WORK_DIR    a work folder on the SSD (the test writes a small copy there)

The base LUT is found in Resolve's LUT folder at run time.
"""

import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

import cv2
import numpy as np

import convert

REAL = os.environ.get("VET_REAL_BRAW", "")
WORK = os.environ.get("VET_WORK_DIR", "")
FRAMES = 90     # three seconds


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


def _listing(folder):
    return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                  for p in Path(folder).iterdir())


def _psnr(a, b):
    mse = np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2)
    return 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


@unittest.skipIf(SKIP, SKIP)
class RealClipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import decoder
        cls.decoder = decoder.BrawSourceDecoder()
        cls.lut = convert.find_base_lut()
        cls.work = tempfile.mkdtemp(prefix="vet-real-", dir=WORK)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work, ignore_errors=True)

    def test_probe_reports_the_facts_of_the_clip(self):
        info = self.decoder.probe(REAL)
        self.assertEqual(info.width, 4096)
        self.assertIn(info.height, (2160, 1720))
        self.assertGreater(info.frames, FRAMES)
        self.assertGreater(info.fps_num, 0)
        self.assertGreater(info.fps_den, 0)
        self.assertRegex(info.timecode_start, r"^\d\d:\d\d:\d\d:\d\d$")
        self.assertTrue(info.colour_science_gen)
        self.assertIsInstance(info.has_sidecar, bool)

    def test_container_tracks_are_listed(self):
        tracks = convert_tracks(REAL)
        self.assertTrue(any(t["codec_type"] == "video" for t in tracks), tracks)

    def test_a_short_piece_converts_checks_and_leaves_the_originals_alone(self):
        folder = Path(REAL).parent
        before = _listing(folder)
        clip_before = convert.sha256_file(REAL)
        lines = []
        conv = convert.Converter(
            work_dir=os.path.join(self.work, "short"), decoder=self.decoder,
            settings=convert.PreviewSettings(), lut_path=self.lut,
            originals_folders=[str(folder)], log=lines.append)
        summary = conv.run([convert.ClipRef(stem=Path(REAL).stem, path=REAL)],
                           max_frames=FRAMES)
        self.assertEqual(summary.failed, 0, "\n".join(lines))
        self.assertEqual(summary.converted, 1)
        self.assertEqual(_listing(folder), before)
        self.assertEqual(convert.sha256_file(REAL), clip_before)

        info = self.decoder.probe(REAL)
        copy = Path(self.work) / "short" / "copies" / (Path(REAL).stem + ".mp4")
        self.assertTrue(copy.is_file())
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,nb_read_packets,pix_fmt,codec_name",
             "-of", "csv=p=0", str(copy)], capture_output=True, text=True).stdout.strip()
        codec, w, h, pix, packets = probe.split(",")
        self.assertEqual((codec, int(w), int(h), pix, int(packets)),
                         ("h264", info.width // 2, info.height // 2, "yuv420p", FRAMES))

    def test_a_frame_of_the_copy_matches_the_raw_decode_through_the_same_lut(self):
        """Check 1.7. The first frame of the copy against the first frame of the RAW
        decode through the same LUT, in 8-bit RGB at the copy's size.

        The unblurred PSNR is dominated by the fine detail that the half-size
        decode, 4:2:0 and H.264 lose (32.8 to 36.8 dB on 8 frames of one clip,
        measured), so it only has to stay above 30 dB. Colour and tone are
        judged on the mean colour (step 1: below 1 of 255) and on the PSNR after
        a Gaussian blur of sigma 4 that removes the fine detail (step 1: 47.4 dB;
        48.8 to 49.7 dB measured on the same 8 frames)."""
        info = self.decoder.probe(REAL)
        w, h = info.width // 2, info.height // 2
        conv = convert.Converter(
            work_dir=os.path.join(self.work, "psnr"), decoder=self.decoder,
            settings=convert.PreviewSettings(), lut_path=self.lut,
            originals_folders=[str(Path(REAL).parent)], log=lambda line: None)
        conv.run([convert.ClipRef(stem=Path(REAL).stem, path=REAL)], max_frames=FRAMES)
        copy = Path(self.work) / "psnr" / "copies" / (Path(REAL).stem + ".mp4")

        # Reference: the same frames, same chain, no encoder, 8-bit RGB.
        ref_cmd = ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gbrp16le",
                   "-s", f"{w}x{h}", "-r", f"{info.fps_num}/{info.fps_den}", "-i", "-",
                   "-vf", f"lut3d=file={convert.escape_filter_path(self.lut)}:interp=tetrahedral,"
                          f"format=rgb24",
                   "-f", "rawvideo", "-"]
        # Only the first frame is needed: ask the decoder for one frame.
        p = subprocess.Popen(ref_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        out = []
        reader = threading.Thread(target=lambda: out.append(p.stdout.read()))
        reader.start()
        self.decoder.write_frames(REAL, 2, p.stdin, max_frames=1)
        p.stdin.close()
        reader.join()
        p.wait()
        p.stdout.close()
        ref = np.frombuffer(out[0], dtype=np.uint8)
        self.assertEqual(ref.size, w * h * 3)

        got = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(copy), "-frames:v", "1",
             "-vf", "scale=in_color_matrix=bt709:in_range=tv:out_range=pc:"
                    "flags=accurate_rnd+full_chroma_int+bicubic,format=rgb24",
             "-f", "rawvideo", "-"], capture_output=True).stdout
        got = np.frombuffer(got, dtype=np.uint8)
        self.assertEqual(got.size, w * h * 3)
        got = got.reshape(h, w, 3)
        ref = ref.reshape(h, w, 3)
        self.assertGreaterEqual(_psnr(got, ref), 30.0)
        for c in range(3):
            self.assertLess(abs(float(got[..., c].mean()) - float(ref[..., c].mean())), 1.5)
        blurred = _psnr(cv2.GaussianBlur(got, (0, 0), 4), cv2.GaussianBlur(ref, (0, 0), 4))
        self.assertGreaterEqual(blurred, 40.0)


def convert_tracks(path):
    import decoder
    return decoder.container_tracks(path)


if __name__ == "__main__":
    unittest.main()
