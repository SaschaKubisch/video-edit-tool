"""Check 2.12 and the checks of SPECS 5.7 on the film preview: the segment cache, the
joined file, the frame count, the duration, the sound, the film fades, the letterbox.
Sources are synthetic: every frame of a preview copy has its own grey value."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import render
import timeline
from tests import media

W, H = 320, 180
FPS_NUM, FPS_DEN = 30000, 1001
RATE = 48000


def frame_secs(n):
    return n * FPS_DEN / FPS_NUM


class PreviewFixture(unittest.TestCase):
    """A film of three segments from two clips. Clip A is 320 x 180 (a 640 x 360 picture),
    clip B is 320 x 136 (a 640 x 272 picture), so B is letterboxed in the 320 x 180 frame."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.work = root / "work"
        (cls.work / "copies").mkdir(parents=True)
        cls.copy_a = cls.work / "copies" / "A.mp4"
        cls.copy_b = cls.work / "copies" / "B.mp4"
        media.write_ramp_mp4(cls.copy_a, 100, W, H)
        media.write_ramp_mp4(cls.copy_b, 100, W, 136)
        cls.wav_a = root / "A.wav"
        cls.wav_b = root / "B.wav"
        media.sine_wav(cls.wav_a, 12, 48000, 440, 0.3)
        media.sine_wav(cls.wav_b, 12, 96000, 660, 0.3)         # 96 kHz: the mix resamples it
        cls.settings = render.PreviewSettings(width=W, encoder="libx264")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def make_tl(self, segments=None, fade_in=0.5, fade_out=0.5, join=0.25):
        src = lambda copy_path, wav, h: {
            "original_video": "/o/x.braw", "preview_copy": str(copy_path), "audio": str(wav),
            "audio_offset_s": 0.1, "frames": 100, "fps": {"num": FPS_NUM, "den": FPS_DEN},
            "size": {"width": W * 2, "height": h * 2}, "copy_lut_sha256": "0" * 64}
        segments = segments or [("A", 10, 70), ("B", 5, 65), ("A", 40, 100)]
        return timeline.Timeline({
            "version": 1, "kind": "film",
            "film": {"fps": {"num": FPS_NUM, "den": FPS_DEN}, "frame": {"width": W, "height": H},
                     "fit": "fit", "fade_in_s": fade_in, "fade_out_s": fade_out,
                     "audio_join": {"duration_s": join, "curve": "equal_power"}},
            "sources": {"A": src(self.copy_a, self.wav_a, H), "B": src(self.copy_b, self.wav_b, 136)},
            "segments": [{"id": f"s{i + 1:03d}", "source": s, "in": a, "out": b}
                         for i, (s, a, b) in enumerate(segments)],
            "look": {"base": {"mode": "none"}},
            "audio": {"sample_rate": RATE, "loudness": {"mode": "off"}}})

    render_dir = None       # where the previews go; the class work folder unless a test sets its own

    def render(self, tl, name="preview", **kw):
        return render.render_preview(tl, str(self.render_dir or self.work), name=name,
                                     settings=self.settings, log=lambda s: None, **kw)


class FilmPreviewTests(PreviewFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tl = cls.make_tl(cls)
        cls.result = cls.render(cls, cls.tl)
        cls.path = Path(cls.result.path)
        cls.info = media.probe(cls.path)

    def test_the_file_exists_in_the_previews_folder(self):
        self.assertEqual(self.path, self.work / "previews" / "preview.mp4")
        self.assertTrue(self.path.is_file())
        self.assertGreater(self.path.stat().st_size, 0)

    def test_it_has_as_many_frames_as_the_film(self):
        self.assertEqual(self.tl.film_length_frames(), 180)
        self.assertEqual(self.result.frames, 180)
        self.assertEqual(media.video_frames(self.path), 180)

    def test_the_duration_matches_the_timeline_within_one_frame(self):
        want = frame_secs(180)
        one_frame = frame_secs(1)
        self.assertAlmostEqual(media.duration(self.info, "video"), want, delta=one_frame)
        self.assertAlmostEqual(float(self.info["format"]["duration"]), want, delta=one_frame)

    def test_the_sound_is_there_and_as_long_as_the_picture_within_one_frame(self):
        audio = [s for s in self.info["streams"] if s["codec_type"] == "audio"]
        self.assertEqual(len(audio), 1)
        self.assertEqual(audio[0]["codec_name"], "aac")
        self.assertEqual(int(audio[0]["sample_rate"]), RATE)
        self.assertAlmostEqual(media.duration(self.info, "audio"),
                               media.duration(self.info, "video"), delta=frame_secs(1))

    def test_the_picture_is_the_film_frame_at_the_film_frame_rate(self):
        v = [s for s in self.info["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((v["width"], v["height"]), (W, H))
        self.assertEqual(v["r_frame_rate"], "30000/1001")
        self.assertEqual(v["codec_name"], "h264")
        self.assertEqual(v["pix_fmt"], "yuv420p")

    def test_the_moov_box_is_at_the_front_so_a_browser_can_play_it_while_it_loads(self):
        head = self.path.read_bytes()[:4096]
        self.assertLess(head.find(b"moov"), head.find(b"mdat") if b"mdat" in head else 10 ** 9)

    def test_every_frame_is_the_frame_the_timeline_asks_for(self):
        luma = media.read_luma(self.path, W, H)
        order = self.tl.film_order()
        starts = self.tl.segment_film_starts()
        checked = 0
        for seg in order:
            start = starts[seg["id"]]
            for j in range(seg["out"] - seg["in"]):
                film_frame = start + j
                if film_frame < 16 or film_frame >= 180 - 16:       # inside the film fades
                    continue
                want = media.frame_value(seg["in"] + j)
                got = int(luma[film_frame, H // 2, W // 2])
                self.assertAlmostEqual(got, want, delta=4, msg=f"film frame {film_frame}")
                checked += 1
        self.assertGreater(checked, 100)

    def test_the_picture_fades_in_from_black_and_out_to_black(self):
        luma = media.read_luma(self.path, W, H)
        mid = media.frame_value(10 + 40)
        first, last = int(luma[0, H // 2, W // 2]), int(luma[-1, H // 2, W // 2])
        self.assertLessEqual(first, 20)                       # black is 16
        self.assertLessEqual(last, 40)
        self.assertGreater(int(luma[40, H // 2, W // 2]), 0.8 * mid)
        # the fade-in rises: the frame after the first is brighter
        self.assertGreater(int(luma[8, H // 2, W // 2]), first + 8)

    def test_the_sound_fades_in_and_out(self):
        audio = media.read_audio(self.path, RATE)
        rms = lambda a: float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))
        mid = rms(audio[RATE:2 * RATE])
        self.assertGreater(mid, 0.1)
        self.assertLess(rms(audio[:RATE // 50]), 0.1 * mid)               # first 20 ms
        self.assertLess(rms(audio[-RATE // 50:]), 0.1 * mid)              # last 20 ms

    def test_the_wide_clip_is_letterboxed_and_the_picture_is_not_touched(self):
        luma = media.read_luma(self.path, W, H)
        f = 80                                    # inside s002 (film frames 60 to 119), clip B
        bar = 22                                  # (180 - 136) / 2
        self.assertLessEqual(int(luma[f, 3, W // 2]), 20)
        self.assertLessEqual(int(luma[f, H - 3, W // 2]), 20)
        want = media.frame_value(5 + (f - 60))
        self.assertAlmostEqual(int(luma[f, H // 2, W // 2]), want, delta=4)
        self.assertAlmostEqual(int(luma[f, bar + 3, W // 2]), want, delta=4)
        self.assertAlmostEqual(int(luma[f, H - bar - 4, W // 2]), want, delta=4)

    def test_the_render_names_the_checks_it_ran_and_they_pass(self):
        self.assertEqual(self.result.problems, [])
        self.assertEqual(self.result.rendered, ["s001", "s002", "s003"])
        self.assertEqual(self.result.cached, [])


class SegmentCacheTests(PreviewFixture):
    def setUp(self):
        # each test starts with an empty segment cache
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.render_dir = Path(tmp.name)

    def test_a_second_preview_of_an_unchanged_film_renders_no_segment(self):
        tl = self.make_tl()
        first = self.render(tl, name="cache1")
        self.assertEqual(len(first.rendered), 3)
        second = self.render(tl, name="cache1")
        self.assertEqual(second.rendered, [])
        self.assertEqual(second.cached, ["s001", "s002", "s003"])
        self.assertEqual(media.video_frames(second.path), 180)

    def test_a_trim_of_one_segment_renders_exactly_that_segment(self):
        tl = self.make_tl()
        self.render(tl, name="cache2")
        trimmed = copy.deepcopy(tl.data)
        trimmed["segments"][1]["in"] += 3                    # the middle segment
        result = self.render(timeline.Timeline(trimmed), name="cache2")
        self.assertEqual(result.rendered, ["s002"])
        self.assertEqual(result.cached, ["s001", "s003"])
        self.assertEqual(result.frames, 177)
        self.assertEqual(media.video_frames(result.path), 177)

    def test_a_change_of_the_film_fades_renders_only_the_segments_they_touch(self):
        tl = self.make_tl()
        self.render(tl, name="cache3")
        d = copy.deepcopy(tl.data)
        d["film"]["fade_out_s"] = 0.25                       # touches the last segment only
        result = self.render(timeline.Timeline(d), name="cache3")
        self.assertEqual(result.rendered, ["s003"])

    def test_the_cache_key_covers_the_preview_settings(self):
        tl = self.make_tl()
        self.render(tl, name="cache4")
        other = render.PreviewSettings(width=W, encoder="libx264", quality=40)
        result = render.render_preview(tl, str(self.render_dir), name="cache4", settings=other,
                                       log=lambda s: None)
        self.assertEqual(len(result.rendered), 3)

    def test_a_range_renders_only_the_segments_that_touch_it(self):
        tl = self.make_tl()
        result = self.render(tl, name="range", film_range=(1.0, 3.0))   # s001 (0-2 s) and s002 (2-4 s)
        self.assertEqual(result.frames, 120)
        self.assertEqual(media.video_frames(result.path), 120)
        self.assertEqual(result.segments, ["s001", "s002"])

    def test_the_meta_file_records_the_timeline_hash_and_the_segments(self):
        tl = self.make_tl()
        result = self.render(tl, name="meta", timeline_hash="abc123")
        meta = json.loads((self.render_dir / "previews" / "meta.json").read_text())
        self.assertEqual(meta["timeline_hash"], "abc123")
        self.assertEqual(meta["frames"], 180)
        self.assertEqual(meta["file"], "meta.mp4")
        self.assertEqual(meta["segments"], ["s001", "s002", "s003"])
        self.assertEqual(result.timeline_hash, "abc123")


class DefaultEncoderTests(PreviewFixture):
    def test_the_default_encoder_is_the_hardware_encoder_and_the_preview_passes_its_checks(self):
        import config
        self.assertEqual(config.FILM_PREVIEW_ENCODER, "h264_videotoolbox")
        self.assertEqual(render.PreviewSettings().encoder, "h264_videotoolbox")
        try:
            result = render.render_preview(self.make_tl(), str(self.work), name="vt",
                                           settings=render.PreviewSettings(width=W),
                                           log=lambda s: None)
        except render.RenderError as e:
            if "videotoolbox" in str(e).lower():
                self.skipTest(f"h264_videotoolbox is not available here: {e}")
            raise
        self.assertEqual(result.problems, [])
        self.assertEqual(media.video_frames(result.path), 180)
        v = [s for s in media.probe(result.path)["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((v["codec_name"], v["pix_fmt"], v["r_frame_rate"]),
                         ("h264", "yuv420p", "30000/1001"))


class RefusalTests(PreviewFixture):
    def test_a_missing_preview_copy_stops_the_render_and_names_the_clip(self):
        tl = self.make_tl()
        d = copy.deepcopy(tl.data)
        d["sources"]["B"]["preview_copy"] = str(self.work / "copies" / "gone.mp4")
        with self.assertRaises(render.RenderError) as cm:
            self.render(timeline.Timeline(d), name="gone")
        self.assertIn("B", str(cm.exception))
        self.assertIn("convert", str(cm.exception))

    def test_an_invalid_timeline_is_refused_with_the_findings(self):
        tl = self.make_tl()
        d = copy.deepcopy(tl.data)
        d["segments"][0]["out"] = 5000
        with self.assertRaises(render.RenderError) as cm:
            self.render(timeline.Timeline(d), name="invalid")
        self.assertIn("V9", str(cm.exception))

    def test_nothing_is_written_inside_a_folder_that_holds_originals(self):
        import safety
        tl = self.make_tl()
        with self.assertRaises(safety.OriginalsProtectedError):
            render.render_preview(tl, str(self.work), name="x", settings=self.settings,
                                  log=lambda s: None, protected=[str(self.work)])


if __name__ == "__main__":
    unittest.main()
