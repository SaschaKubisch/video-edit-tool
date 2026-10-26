"""Check 2.10: picture and sound stay together through the whole chain. A fixture clip
has a white frame at frame 100; its WAV (96 kHz, so the mix resamples it) has a click
at 100 / fps + offset. After building a timeline for it and rendering the film preview,
the white frame and the click lie within 1 ms of each other in the mix and in the file."""

import tempfile
import unittest
from pathlib import Path

import numpy as np

import render
import timeline
from tests import media

W, H = 320, 180
OFFSET = 0.276
FPS = media.FPS


class SyncMarkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.work = root / "work"
        (cls.work / "copies").mkdir(parents=True)
        cls.copy = cls.work / "copies" / "M.mp4"
        media.write_ramp_mp4(cls.copy, 200, W, H, white_frame=100, value=lambda i: 60)
        rate = 96000
        x = np.zeros((12 * rate, 2))
        click_at = round((100 / FPS + OFFSET) * rate)
        burst = np.random.default_rng(5).uniform(-0.9, 0.9, (96, 2))
        x[click_at:click_at + 96] = burst
        cls.wav = root / "M.wav"
        media.write_wav(cls.wav, x, rate)
        cls.tl = timeline.Timeline({
            "version": 1, "kind": "film",
            "film": {"fps": {"num": 30000, "den": 1001}, "frame": {"width": W, "height": H},
                     "fit": "fit", "fade_in_s": 0, "fade_out_s": 0,
                     "audio_join": {"duration_s": 0, "curve": "equal_power"}},
            "sources": {"M": {"original_video": "/o/M.braw", "preview_copy": str(cls.copy),
                              "audio": str(cls.wav), "audio_offset_s": OFFSET, "frames": 200,
                              "fps": {"num": 30000, "den": 1001},
                              "size": {"width": W * 2, "height": H * 2},
                              "copy_lut_sha256": "0" * 64}},
            "segments": [{"id": "s001", "source": "M", "in": 50, "out": 150}],
            "look": {"base": {"mode": "none"}},
            "audio": {"sample_rate": 48000, "loudness": {"mode": "off"}}})

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_click_in_the_mix_lies_at_the_film_time_of_the_white_frame(self):
        plan = render.make_plan(self.tl, preview_width=W)
        mix = render.build_mix(self.tl, plan, lambda p: render.load_wav(p, 48000))
        mono = np.abs(mix.samples).max(axis=1)
        first = int(np.argmax(mono > 0.3 * mono.max()))
        want = round(50 * 1001 / 30000 * 48000)          # film frame 50, in samples
        self.assertAlmostEqual(first, want, delta=48)    # within 1 ms (48 samples)

    def test_the_white_frame_and_the_click_in_the_file_lie_within_1_ms(self):
        result = render.render_preview(self.tl, str(self.work), settings=render.PreviewSettings(
            width=W, encoder="libx264"), log=lambda s: None)
        luma = media.read_luma(result.path, W, H)
        bright = np.nonzero(luma[:, H // 2, W // 2] > 200)[0]
        self.assertEqual(list(bright), [50])
        t_video = 50 * 1001 / 30000
        audio = media.read_audio(result.path, 48000)
        mono = np.abs(audio).max(axis=1)
        t_audio = int(np.argmax(mono > 0.3 * mono.max())) / 48000
        self.assertAlmostEqual(t_audio, t_video, delta=0.001)


if __name__ == "__main__":
    unittest.main()
