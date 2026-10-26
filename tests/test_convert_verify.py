"""Check 1.2: the check on a finished preview copy (C5, SPECS 6.6)."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import convert

FPS = "testsrc2=size=640x360:rate=30000/1001"


def make_clip(path, video=FPS, seconds=2, audio=True, timecode="01:00:00:00",
              faststart=True, pix_fmt=None, extra=(), audio_seconds=None):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", video]
    if audio:
        cmd += ["-f", "lavfi", "-i",
                "sine" if audio_seconds is None else f"sine=duration={audio_seconds}"]
    cmd += ["-t", str(seconds), "-c:v", "libx264"]
    if pix_fmt:
        cmd += ["-pix_fmt", pix_fmt]
    if audio:
        cmd += ["-c:a", "aac", "-ar", "48000"]
    else:
        cmd += ["-an"]
    if timecode:
        cmd += ["-timecode", timecode]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd += list(extra) + [str(path)]
    subprocess.run(cmd, check=True)
    return path


def expected(**kw):
    base = dict(width=640, height=360, fps_num=30000, fps_den=1001, frames=60,
                audio_rate=48000, has_audio=True, timecode="01:00:00:00",
                colour_tags=False)
    base.update(kw)
    return convert.CopyExpectation(**base)


class VerifyCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.good = make_clip(root / "good.mp4")
        cls.no_audio = make_clip(root / "no_audio.mp4", audio=False)
        cls.no_timecode = make_clip(root / "no_tc.mp4", timecode="")
        cls.moov_at_end = make_clip(root / "slow.mp4", faststart=False)
        cls.yuv444 = make_clip(root / "yuv444.mp4", pix_fmt="yuv444p")
        cls.black = make_clip(root / "black.mp4",
                              video="color=c=black:s=640x360:r=30000/1001")
        cls.other_tc = make_clip(root / "tc.mp4", timecode="02:00:00:00")
        # picture 2 s, sound 1 s: the source itself can have less audio than picture
        cls.short_audio = make_clip(root / "short_audio.mp4", audio_seconds=1)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_a_good_copy_passes_and_reports_its_facts(self):
        r = convert.verify_copy(str(self.good), expected())
        self.assertTrue(r.ok, r.problems)
        self.assertEqual(r.problems, [])
        self.assertEqual(r.details, {"codec": "h264", "pix_fmt": "yuv420p",
                                     "frames": 60, "audio": "aac",
                                     "timecode": "01:00:00:00"})

    def test_wrong_frame_count_is_rejected(self):
        r = convert.verify_copy(str(self.good), expected(frames=61))
        self.assertFalse(r.ok)
        self.assertTrue(any("frames" in p for p in r.problems), r.problems)

    def test_wrong_frame_size_is_rejected(self):
        r = convert.verify_copy(str(self.good), expected(width=1280, height=720))
        self.assertFalse(r.ok)
        self.assertTrue(any("size" in p for p in r.problems), r.problems)

    def test_wrong_frame_rate_is_rejected(self):
        r = convert.verify_copy(str(self.good), expected(fps_num=25, fps_den=1))
        self.assertFalse(r.ok)
        self.assertTrue(any("frame rate" in p for p in r.problems), r.problems)

    def test_wrong_pixel_format_is_rejected(self):
        r = convert.verify_copy(str(self.yuv444), expected())
        self.assertFalse(r.ok)
        self.assertTrue(any("pixel format" in p for p in r.problems), r.problems)

    def test_missing_audio_is_rejected(self):
        r = convert.verify_copy(str(self.no_audio), expected())
        self.assertFalse(r.ok)
        self.assertTrue(any("audio" in p for p in r.problems), r.problems)

    def test_a_clip_that_has_no_audio_may_have_none(self):
        r = convert.verify_copy(str(self.no_audio), expected(has_audio=False))
        self.assertTrue(r.ok, r.problems)

    def test_audio_much_shorter_than_the_picture_is_rejected_by_default(self):
        r = convert.verify_copy(str(self.short_audio), expected())
        self.assertFalse(r.ok)
        self.assertTrue(any("differ" in p for p in r.problems), r.problems)

    def test_audio_is_compared_with_the_length_of_the_source_audio_when_given(self):
        # Found on a real clip: the .braw file holds 4.000 s of audio and 4.87 s of picture.
        # The copy carries what the source has; that is not a failure of the copy.
        r = convert.verify_copy(str(self.short_audio), expected(audio_seconds=1.0))
        self.assertTrue(r.ok, r.problems)

    def test_audio_of_the_wrong_length_is_rejected_when_the_source_length_is_given(self):
        r = convert.verify_copy(str(self.good), expected(audio_seconds=1.0))
        self.assertFalse(r.ok)
        self.assertTrue(any("source" in p for p in r.problems), r.problems)

    def test_wrong_audio_sample_rate_is_rejected(self):
        r = convert.verify_copy(str(self.good), expected(audio_rate=44100))
        self.assertFalse(r.ok)
        self.assertTrue(any("sample rate" in p for p in r.problems), r.problems)

    def test_missing_timecode_is_rejected(self):
        r = convert.verify_copy(str(self.no_timecode), expected())
        self.assertFalse(r.ok)
        self.assertTrue(any("timecode" in p for p in r.problems), r.problems)

    def test_other_timecode_is_rejected(self):
        r = convert.verify_copy(str(self.other_tc), expected())
        self.assertFalse(r.ok)
        self.assertTrue(any("timecode" in p for p in r.problems), r.problems)

    def test_a_clip_without_timecode_needs_none(self):
        r = convert.verify_copy(str(self.no_timecode), expected(timecode=""))
        self.assertTrue(r.ok, r.problems)

    def test_moov_box_at_the_end_is_rejected(self):
        r = convert.verify_copy(str(self.moov_at_end), expected())
        self.assertFalse(r.ok)
        self.assertTrue(any("moov" in p for p in r.problems), r.problems)

    def test_constant_black_picture_is_rejected(self):
        r = convert.verify_copy(str(self.black), expected())
        self.assertFalse(r.ok)
        self.assertTrue(any("constant" in p for p in r.problems), r.problems)

    def test_missing_colour_tags_are_rejected_when_required(self):
        r = convert.verify_copy(str(self.good), expected(colour_tags=True))
        self.assertFalse(r.ok)
        self.assertTrue(any("colour" in p for p in r.problems), r.problems)

    def test_unreadable_file_is_rejected(self):
        bad = Path(self._tmp.name) / "bad.mp4"
        bad.write_bytes(b"not a video")
        r = convert.verify_copy(str(bad), expected())
        self.assertFalse(r.ok)
        self.assertTrue(r.problems)

    def test_mp4_box_order(self):
        self.assertEqual(convert.mp4_box_order(str(self.good))[:2], ["ftyp", "moov"])
        order = convert.mp4_box_order(str(self.moov_at_end))
        self.assertLess(order.index("mdat"), order.index("moov"))


if __name__ == "__main__":
    unittest.main()
