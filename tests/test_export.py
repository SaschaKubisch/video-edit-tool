"""Check 3.6: the final film (SPECS 5.3, 5.6, 5.7, 8.5). A synthetic film whose clips are
decoded by a decoder that makes flat frames: frame i of a clip is a grey that tells which frame
it is, so a cut that is one frame off, a missing letterbox bar or a LUT in the wrong order is
seen in the pixels of the finished file. Nothing here reads a .braw file."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import export
import loudness
import timeline
import tools
from decoder import ClipInfo, Decoder
from tests import media

W, H = 320, 180
FPS = (30000, 1001)
RATE = 48000

CUBE_IDENTITY = "LUT_3D_SIZE 2\n" + "\n".join(
    f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)) + "\n"
CUBE_INVERT = "LUT_3D_SIZE 2\n" + "\n".join(
    f"{1 - r} {1 - g} {1 - b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)) + "\n"
CUBE_HALVE = "LUT_3D_SIZE 2\n" + "\n".join(
    f"{r / 2} {g / 2} {b / 2}" for b in (0, 1) for g in (0, 1) for r in (0, 1)) + "\n"


class FlatDecoder(Decoder):
    """Frame i of every clip is one flat grey, `media.frame_value(i)`, in 16-bit RGB. Frames
    are made from the absolute frame number, so the segment that is asked for is visible."""
    name = "flat"

    def __init__(self, clips, short_by=0, fail_on=None, interrupt_on=None):
        self.clips = clips
        self.short_by = short_by
        self.fail_on = fail_on
        self.interrupt_on = interrupt_on
        self.calls = []

    def probe(self, path):
        return self.clips[path]

    def write_frames(self, *a, **k):
        raise NotImplementedError

    def write_audio(self, *a, **k):
        return None

    def write_frame_range(self, path, scale, sink, first, last):
        self.calls.append((path, first, last))
        if self.fail_on == path:
            raise RuntimeError("the decoder crashed")
        info = self.clips[path]
        n = last - first - self.short_by
        for k, i in enumerate(range(first, first + n)):
            if self.interrupt_on and self.interrupt_on == (path, first) and k == 5:
                raise KeyboardInterrupt()
            v = int(round(media.frame_value(i) / 255 * 65535))
            sink.write(np.full((info.height, info.width), v, "<u2").tobytes() * 3)
        return n


def listing(folder):
    out = {}
    for p in sorted(Path(folder).rglob("*")):
        if p.is_file():
            out[str(p.relative_to(folder))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class ExportFixture(unittest.TestCase):
    """Two clips: A is W x H, B is W x 136 (a wide picture, letterboxed in the frame)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name).resolve()
        cls.root = root
        cls.originals = root / "originals"
        cls.originals.mkdir()
        cls.work = root / "work"
        cls.out = root / "renders"
        cls.luts = root / "luts"
        cls.luts.mkdir()
        for name, text in (("identity", CUBE_IDENTITY), ("invert", CUBE_INVERT), ("halve", CUBE_HALVE)):
            (cls.luts / f"{name}.cube").write_text(text)
        cls.clips = {}
        cls.sources = {}
        for name, h in (("A", H), ("B", 136)):
            braw = cls.originals / f"{name}.braw"
            braw.write_bytes(b"not really a braw file")
            wav = cls.originals / f"{name}.wav"
            media.sine_wav(wav, 12, 48000, 440 if name == "A" else 660, 0.3)
            cls.clips[str(braw)] = ClipInfo(width=W, height=h, fps_num=FPS[0], fps_den=FPS[1], frames=100)
            cls.sources[name] = {
                "original_video": str(braw), "preview_copy": str(root / f"{name}.mp4"),
                "audio": str(wav), "audio_offset_s": 0.1, "frames": 100,
                "fps": {"num": FPS[0], "den": FPS[1]}, "size": {"width": W, "height": h},
                "copy_lut_sha256": "0" * 64}
        cls.originals_before = listing(cls.originals)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    @classmethod
    def entry(cls, name="film-x264", codec="libx264", width=W, height=H):
        video = {"codec": "libx264", "preset": "veryfast", "rate_control": {"mode": "crf", "crf": 8}}
        if codec == "h264_videotoolbox":
            video = {"codec": codec, "rate_control": {"mode": "bitrate", "mbps": 8}}
        return {"name": name, "output": str(cls.out / f"{name}.mp4"), "width": width, "height": height,
                "video": video,
                "audio": {"codec": "aac", "bitrate_kbps": 384, "sample_rate": 48000}}

    @classmethod
    def make_tl(cls, segments=None, fade_in=0.3, fade_out=0.3, join=0.25, loud=True, base=None,
                lut=None, exports=None, offsets=None):
        segments = segments or [("A", 10, 70), ("B", 5, 65), ("A", 40, 100)]
        sources = json.loads(json.dumps(cls.sources))
        for k, v in (offsets or {}).items():
            sources[k]["audio_offset_s"] = v
        look = {"base": {"mode": "none"}}
        if base:
            look["base"] = {"mode": "lut", "path": str(base), "sha256": hashlib.sha256(Path(base).read_bytes()).hexdigest()}
        if lut:
            look["lut"] = {"path": str(lut), "sha256": hashlib.sha256(Path(lut).read_bytes()).hexdigest(),
                           "interp": "tetrahedral"}
        loudness_block = {"mode": "segment_then_film", "target_lufs": -14.0, "true_peak_dbtp": -1.0,
                          "max_gain_db": 20.0} if loud else {"mode": "off"}
        return timeline.Timeline({
            "version": 1, "kind": "film",
            "film": {"fps": {"num": FPS[0], "den": FPS[1]}, "frame": {"width": W, "height": H},
                     "fit": "fit", "fade_in_s": fade_in, "fade_out_s": fade_out,
                     "audio_join": {"duration_s": join, "curve": "equal_power"}},
            "sources": sources,
            "segments": [{"id": f"s{i + 1:03d}", "source": s, "in": a, "out": b}
                         for i, (s, a, b) in enumerate(segments)],
            "look": look,
            "audio": {"sample_rate": RATE, "loudness": loudness_block},
            "exports": exports if exports is not None else [cls.entry()]})

    def run_export(self, tl, name="film-x264", decoder=None, work=None, **kw):
        work = work or self.work
        decoder = decoder or FlatDecoder(self.clips)
        return export.render_export(tl, name, str(work), decoder, log=lambda s: None,
                                    protected=[str(self.originals)], **kw)

    def frames_of(self, path, w=W, h=H):
        return media.read_yuv_frames(path, w, h)


class FinalFilmTests(ExportFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tl = cls.make_tl()
        cls.decoder = FlatDecoder(cls.clips)
        cls.result = export.render_export(cls.tl, "film-x264", str(cls.work), cls.decoder,
                                          log=lambda s: None, protected=[str(cls.originals)])
        cls.path = cls.result.path
        cls.yuv = media.read_yuv_frames(cls.path, W, H)
        cls.probe = media.probe(cls.path)

    def test_the_render_passes_its_own_checks(self):
        self.assertTrue(self.result.ok, self.result.problems)
        self.assertEqual(self.result.problems, [])
        self.assertTrue(os.path.isfile(self.path))
        self.assertFalse(os.path.exists(self.path + ".partial"))

    def test_the_file_is_h264_high_420_at_the_export_size_with_every_frame_of_the_film(self):
        v = [s for s in self.probe["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((v["codec_name"], v["profile"], v["pix_fmt"]), ("h264", "High", "yuv420p"))
        self.assertEqual((v["width"], v["height"]), (W, H))
        self.assertEqual(len(self.yuv), 60 + 60 + 60)
        self.assertEqual(self.result.frames, 180)

    def test_colour_tags_timecode_faststart_and_aac(self):
        v = [s for s in self.probe["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((v["color_space"], v["color_transfer"], v["color_primaries"], v["color_range"]),
                         ("bt709", "bt709", "bt709", "tv"))
        self.assertEqual(v["tags"]["timecode"], "00:00:00:00")
        a = [s for s in self.probe["streams"] if s["codec_type"] == "audio"][0]
        self.assertEqual((a["codec_name"], int(a["sample_rate"]), a["channels"]), ("aac", 48000, 2))
        import convert
        order = convert.mp4_box_order(self.path)
        self.assertLess(order.index("moov"), order.index("mdat"))

    def test_each_frame_is_the_frame_the_timeline_asks_for(self):
        expect = [("A", i) for i in range(10, 70)] + [("B", i) for i in range(5, 65)] + \
                 [("A", i) for i in range(40, 100)]
        fade_in, fade_out = 9, 9               # 0.3 s of frames at 29.97 fps
        for film_frame in range(len(expect)):
            if film_frame < fade_in + 1 or film_frame > len(expect) - fade_out - 2:
                continue                       # the fades change the grey on purpose
            src, i = expect[film_frame]
            y = self.yuv[film_frame][0]
            rows = y[:, :] if src == "A" else y[22:158, :]
            self.assertAlmostEqual(float(rows.mean()), media.limited_luma(media.frame_value(i)),
                                   delta=2.5, msg=f"film frame {film_frame}: {src} frame {i}")

    def test_the_wide_clip_has_black_bars_at_video_level_and_an_untouched_picture(self):
        # B is 136 high in a 180 frame: bars of 22 rows above and below.
        for film_frame in (70, 90, 110):
            y = self.yuv[film_frame][0]
            self.assertAlmostEqual(float(y[:22].mean()), 16.0, delta=1.0)
            self.assertAlmostEqual(float(y[-22:].mean()), 16.0, delta=1.0)
            self.assertTrue(abs(float(y[22:158].mean()) - 16.0) > 10)
            u, v = self.yuv[film_frame][1], self.yuv[film_frame][2]
            self.assertAlmostEqual(float(u[:11].mean()), 128.0, delta=1.0)
            self.assertAlmostEqual(float(v[:11].mean()), 128.0, delta=1.0)

    def test_the_film_fades_in_from_black_and_out_to_black(self):
        first, last = self.yuv[0][0], self.yuv[-1][0]
        self.assertAlmostEqual(float(first.mean()), 16.0, delta=1.5)
        self.assertAlmostEqual(float(last.mean()), 16.0, delta=1.5)
        mid = self.yuv[30][0]
        self.assertGreater(float(mid.mean()), 40)

    def test_the_sound_has_the_final_loudness_and_peak(self):
        got = loudness.measure_file(self.path)
        self.assertAlmostEqual(got.lufs, -14.0, delta=0.5)
        self.assertLessEqual(got.peak_dbtp, -1.0)
        self.assertAlmostEqual(self.result.output_loudness["lufs"], got.lufs, delta=0.05)

    def test_the_sound_is_as_long_as_the_picture(self):
        a = media.read_audio_f32(self.path)
        self.assertAlmostEqual(len(a) / RATE, 180 * 1001 / 30000, delta=0.034)

    def test_the_report_names_the_loudness_the_speed_and_the_checks(self):
        report = json.loads((self.work / "reports" / "render-report.json").read_text())
        row = report["renders"][-1]
        self.assertEqual(row["export"], "film-x264")
        self.assertTrue(row["ok"])
        self.assertEqual(row["loudness"]["mode"], "segment_then_film")
        self.assertAlmostEqual(row["loudness"]["film"]["after"]["lufs"], -14.0, delta=0.5)
        self.assertAlmostEqual(row["loudness"]["output_file"]["lufs"], -14.0, delta=0.5)
        self.assertIn("video_fps", row["speed"])
        self.assertEqual(row["frames"], 180)
        self.assertIn("ffmpeg", row["tools"]["ffmpeg"].lower())
        self.assertTrue(row["checks"]["file"])

    def test_the_decoder_was_asked_for_exactly_the_frames_of_each_segment(self):
        calls = [(Path(p).name, a, b) for p, a, b in self.decoder.calls]
        self.assertEqual(calls, [("A.braw", 10, 70), ("B.braw", 5, 65), ("A.braw", 40, 100)])

    def test_the_originals_were_not_touched_and_no_scratch_folder_is_left(self):
        self.assertEqual(listing(self.originals), self.originals_before)
        left = [p.name for p in self.work.iterdir() if p.name.startswith("render-")]
        self.assertEqual(left, [])
        self.assertFalse((self.work / ".render.lock").exists())


class HardwareEncoderTests(ExportFixture):
    def test_the_default_encoder_h264_videotoolbox_makes_a_valid_file(self):
        tl = self.make_tl(exports=[self.entry("film-vt", "h264_videotoolbox")])
        r = self.run_export(tl, "film-vt")
        self.assertTrue(r.ok, r.problems)
        v = [s for s in media.probe(r.path)["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((v["codec_name"], v["pix_fmt"], v["color_space"]), ("h264", "yuv420p", "bt709"))
        self.assertEqual(r.frames, 180)


class LutChainTests(ExportFixture):
    def luma_of_first_segment(self, tl, name):
        tl.data["exports"] = [self.entry(name)]
        r = self.run_export(tl, name)
        self.assertTrue(r.ok, r.problems)
        y = self.frames_of(r.path)
        return float(y[30][0].mean())              # film frame 30: source frame 40 of clip A

    def grey(self, frame=40):
        return media.frame_value(frame)

    def test_no_lut_leaves_the_grey_as_it_is(self):
        tl = self.make_tl(segments=[("A", 10, 70)], loud=False)
        self.assertAlmostEqual(self.luma_of_first_segment(tl, "plain"),
                               media.limited_luma(self.grey(40)), delta=2.5)

    def test_the_base_lut_acts_on_the_decoded_picture(self):
        tl = self.make_tl(segments=[("A", 10, 70)], loud=False, base=self.luts / "invert.cube")
        self.assertAlmostEqual(self.luma_of_first_segment(tl, "base"),
                               media.limited_luma(255 - self.grey(40)), delta=3)

    def test_the_creative_lut_comes_after_the_base_lut(self):
        # base halves, creative inverts: 255 - v/2. The other order would give (255 - v)/2.
        tl = self.make_tl(segments=[("A", 10, 70)], loud=False, base=self.luts / "halve.cube",
                          lut=self.luts / "invert.cube")
        got = self.luma_of_first_segment(tl, "order")
        v = self.grey(40)
        self.assertAlmostEqual(got, media.limited_luma(255 - v / 2), delta=3)
        self.assertGreater(abs(got - media.limited_luma((255 - v) / 2)), 20)

    def test_the_letterbox_bars_come_after_the_luts_and_stay_black(self):
        tl = self.make_tl(segments=[("B", 5, 65)], loud=False, base=self.luts / "invert.cube")
        tl.data["exports"] = [self.entry("bars")]
        r = self.run_export(tl, "bars")
        y = self.frames_of(r.path)[30][0]
        self.assertAlmostEqual(float(y[:22].mean()), 16.0, delta=1.0)       # not 235 (an inverted black)
        self.assertAlmostEqual(float(y[22:158].mean()),
                               media.limited_luma(255 - media.frame_value(35)), delta=3)


class CreditTests(ExportFixture):
    def test_a_film_with_a_credited_lut_gets_a_credit_file_next_to_it(self):
        tl = self.make_tl(segments=[("A", 10, 40)], loud=False, lut=self.luts / "identity.cube")
        tl.data["look"]["lut"]["credit"] = "LUTs: [slay](https://example.org/x) (CC BY 4.0)"
        tl.data["exports"] = [self.entry("credited")]
        r = self.run_export(tl, "credited")
        self.assertTrue(r.ok, r.problems)
        credit = Path(self.out / "credited.credit.txt")
        self.assertEqual(credit.read_text(), "LUTs: slay (https://example.org/x) (CC BY 4.0)\n")
        self.assertEqual(r.credit_file, str(credit))

    def test_a_film_without_a_credit_gets_no_file(self):
        tl = self.make_tl(segments=[("A", 10, 40)], loud=False, lut=self.luts / "identity.cube")
        tl.data["exports"] = [self.entry("uncredited")]
        r = self.run_export(tl, "uncredited")
        self.assertIsNone(r.credit_file)
        self.assertFalse((self.out / "uncredited.credit.txt").exists())


class OtherSizeTests(ExportFixture):
    def test_another_size_of_the_same_shape_works_and_keeps_the_bars(self):
        tl = self.make_tl(segments=[("A", 10, 40), ("B", 5, 35)], loud=False)
        tl.data["exports"] = [self.entry("small", width=160, height=90)]
        r = self.run_export(tl, "small")
        self.assertTrue(r.ok, r.problems)
        v = [s for s in media.probe(r.path)["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((v["width"], v["height"]), (160, 90))
        frames = self.frames_of(r.path, 160, 90)
        self.assertEqual(len(frames), 60)
        y = frames[45][0]                               # a frame of clip B
        self.assertAlmostEqual(float(y[:10].mean()), 16.0, delta=1.5)
        self.assertAlmostEqual(float(y[-10:].mean()), 16.0, delta=1.5)

    def test_another_shape_is_refused_with_the_rule(self):
        tl = self.make_tl(segments=[("A", 10, 40)], loud=False)
        tl.data["exports"] = [self.entry("wrong", width=160, height=160)]
        with self.assertRaisesRegex(export.ExportError, "V5"):
            self.run_export(tl, "wrong")
        self.assertFalse((self.out / "wrong.mp4").exists())


class RefusalTests(ExportFixture):
    def test_a_disabled_export_is_not_rendered(self):
        e = self.entry("off")
        e["enabled"] = False
        tl = self.make_tl(segments=[("A", 10, 40)], exports=[e])
        with self.assertRaises(export.ExportDisabled):
            self.run_export(tl, "off")
        self.assertFalse((self.out / "off.mp4").exists())

    def test_an_unknown_export_is_named(self):
        tl = self.make_tl(segments=[("A", 10, 40)])
        with self.assertRaisesRegex(export.ExportError, "film-x264"):
            self.run_export(tl, "nope")

    def test_too_little_free_space_stops_before_anything_is_written(self):
        tl = self.make_tl(segments=[("A", 10, 40)])
        tl.data["exports"] = [self.entry("space")]
        work = self.root / "work-space"
        with self.assertRaisesRegex(export.NotEnoughSpace, "free"):
            self.run_export(tl, "space", work=work, free_bytes=lambda p: 1000)
        self.assertFalse(work.exists())
        self.assertFalse((self.out / "space.mp4").exists())

    def test_an_output_inside_the_originals_is_refused(self):
        e = self.entry("inside")
        e["output"] = str(self.originals / "film.mp4")
        tl = self.make_tl(segments=[("A", 10, 40)], exports=[e])
        with self.assertRaises(export.ExportError):
            self.run_export(tl, "inside")
        self.assertEqual(listing(self.originals), self.originals_before)

    def test_a_clip_that_decodes_at_another_size_stops_the_render(self):
        tl = self.make_tl(segments=[("A", 10, 40)])
        tl.data["exports"] = [self.entry("mismatch")]
        tl.data["sources"]["A"]["size"] = {"width": 640, "height": 360}
        with self.assertRaisesRegex(export.ExportError, "decodes at"):
            self.run_export(tl, "mismatch")

    def test_a_missing_original_stops_the_render_and_names_the_clip(self):
        tl = self.make_tl(segments=[("A", 10, 40)])
        tl.data["exports"] = [self.entry("missing")]
        tl.data["sources"]["A"]["original_video"] = str(self.originals / "gone.braw")
        with self.assertRaisesRegex(export.ExportError, "clip A"):
            self.run_export(tl, "missing")


class FailureTests(ExportFixture):
    def test_a_decoder_that_gives_too_few_frames_fails_the_check_and_keeps_the_partial_file(self):
        tl = self.make_tl(segments=[("A", 10, 70), ("B", 5, 65)], loud=False)
        tl.data["exports"] = [self.entry("short")]
        r = self.run_export(tl, "short", decoder=FlatDecoder(self.clips, short_by=2))
        self.assertFalse(r.ok)
        self.assertTrue(any("video frames" in p for p in r.problems), r.problems)
        self.assertTrue(os.path.isfile(r.path + ".partial"))
        self.assertFalse(os.path.exists(r.path))
        report = json.loads((self.work / "reports" / "render-report.json").read_text())
        self.assertFalse(report["renders"][-1]["ok"])
        self.assertTrue(report["renders"][-1]["problems"])
        os.unlink(r.path + ".partial")

    def test_a_decoder_that_crashes_leaves_nothing_behind(self):
        tl = self.make_tl(segments=[("A", 10, 70), ("B", 5, 65)], loud=False)
        tl.data["exports"] = [self.entry("crash")]
        bad = str(self.originals / "B.braw")
        with self.assertRaisesRegex(RuntimeError, "crashed"):
            self.run_export(tl, "crash", decoder=FlatDecoder(self.clips, fail_on=bad))
        self.assertFalse(os.path.exists(str(self.out / "crash.mp4.partial")))
        self.assertFalse((self.work / ".render.lock").exists())
        self.assertEqual([p for p in self.work.iterdir() if p.name.startswith("render-crash")], [])

    def test_an_interrupt_removes_the_partial_file_the_scratch_folder_and_the_lock(self):
        tl = self.make_tl(segments=[("A", 10, 70), ("B", 5, 65)], loud=False)
        tl.data["exports"] = [self.entry("stop")]
        dec = FlatDecoder(self.clips, interrupt_on=(str(self.originals / "B.braw"), 5))
        with self.assertRaises(KeyboardInterrupt):
            self.run_export(tl, "stop", decoder=dec)
        self.assertFalse(os.path.exists(str(self.out / "stop.mp4.partial")))
        self.assertFalse((self.work / ".render.lock").exists())
        self.assertEqual([p for p in self.work.iterdir() if p.name.startswith("render-stop")], [])
        out = subprocess.run(["pgrep", "-f", "render-stop"], capture_output=True, text=True).stdout
        self.assertEqual(out.strip(), "")

    def test_a_second_render_in_the_same_work_folder_is_refused(self):
        tl = self.make_tl(segments=[("A", 10, 40)])
        tl.data["exports"] = [self.entry("busy")]
        lock = self.work / ".render.lock"
        self.work.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(os.getpid()))
        try:
            with self.assertRaises(export.AlreadyRunning):
                self.run_export(tl, "busy")
        finally:
            lock.unlink()

    def test_a_lock_of_a_dead_process_is_taken_over(self):
        tl = self.make_tl(segments=[("A", 10, 40)])
        tl.data["exports"] = [self.entry("stale")]
        self.work.mkdir(parents=True, exist_ok=True)
        (self.work / ".render.lock").write_text("999999")
        r = self.run_export(tl, "stale")
        self.assertTrue(r.ok, r.problems)


class SoundSyncTests(ExportFixture):
    def test_a_click_in_the_wav_lands_on_its_film_time_in_the_file(self):
        # A click at 5.0 s in the WAV, offset 0.5 s: it belongs to picture time 4.5 s of the clip.
        # The segment starts at frame 120 (4.004 s), so the click is 0.496 s into the film.
        wav = self.originals / "click.wav"
        x = np.zeros((12 * RATE, 2), np.float32)
        x[5 * RATE:5 * RATE + 20, :] = 0.8
        media.write_wav(wav, x, RATE)
        tl = self.make_tl(segments=[("A", 0, 150)], loud=False, fade_in=0.0, fade_out=0.0, join=0.0)
        tl.data["sources"]["A"].update(audio=str(wav), audio_offset_s=0.5, frames=150)
        self.clips[str(self.originals / "A.braw")] = ClipInfo(width=W, height=H, fps_num=FPS[0],
                                                              fps_den=FPS[1], frames=150)
        try:
            tl.data["segments"][0]["in"] = 120
            tl.data["exports"] = [self.entry("sync")]
            r = self.run_export(tl, "sync")
            self.assertTrue(r.ok, r.problems)
        finally:
            self.clips[str(self.originals / "A.braw")] = ClipInfo(width=W, height=H, fps_num=FPS[0],
                                                                  fps_den=FPS[1], frames=100)
        a = media.read_audio_f32(r.path)
        at = int(np.argmax(np.abs(a[:, 0]) > 0.3))
        self.assertAlmostEqual(at / RATE, 5.0 - 0.5 - 120 * 1001 / 30000, delta=0.003)   # AAC smears a little


if __name__ == "__main__":
    unittest.main()


class AudioFileTests(unittest.TestCase):
    """The sound file of an AAC export is encoded, read back, and limited again when the
    decoded true peak is over the limit (SPECS 5.3, order of the work)."""

    def fakes(self, offset_db):
        """An encoder that keeps what it was given and a reader that reports a true peak
        `offset_db` above the sample peak, as a sound with sharp transients has."""
        kept = {}

        def encode(x, path, kbps, rate):
            kept[path] = x

        def measure(path):
            peak = float(np.abs(kept[path]).max())
            return loudness.Level(-14.0, 20 * np.log10(peak) + offset_db)

        return encode, measure

    def run_it(self, samples, offset_db, mode="segment_then_film"):
        encode, measure = self.fakes(offset_db)
        warnings = []
        entry = {"audio": {"codec": "aac", "bitrate_kbps": 384, "sample_rate": 48000}}
        with tempfile.TemporaryDirectory() as scratch:
            path, level = export.make_audio_file(
                samples, entry, {"mode": mode, "true_peak_dbtp": -1.0}, 48000, scratch, "ffmpeg",
                warn=warnings.append, encode=encode, measure_file=measure,
                limiter=lambda x, c: np.clip(x, -10 ** (c / 20), 10 ** (c / 20)))
        return level, warnings

    def test_a_sound_whose_true_peak_is_above_its_sample_peak_gets_a_ceiling_under_the_sample_peak(self):
        x = np.zeros((48000, 2), np.float32)
        x[100, :] = 0.7746                                    # -2.2 dBFS sample peak
        level, warnings = self.run_it(x, 1.3)                 # a true peak of -0.9 dBTP
        self.assertLessEqual(level.peak_dbtp, -1.0)
        self.assertEqual(len([w for w in warnings if "limiter" in w]), 1)
        self.assertFalse(any("above the limit" in w for w in warnings), warnings)

    def test_a_sound_under_the_limit_is_encoded_once_and_not_limited(self):
        x = np.zeros((48000, 2), np.float32)
        x[100, :] = 0.5
        level, warnings = self.run_it(x, 1.0)
        self.assertEqual(warnings, [])

    def test_a_sound_that_cannot_be_brought_down_is_reported(self):
        encode, measure = self.fakes(0.0)
        x = np.zeros((48000, 2), np.float32)
        x[100, :] = 0.9
        warnings = []
        entry = {"audio": {"codec": "aac"}}
        with tempfile.TemporaryDirectory() as scratch:
            path, level = export.make_audio_file(
                x, entry, {"mode": "segment_then_film", "true_peak_dbtp": -1.0}, 48000, scratch,
                "ffmpeg", warn=warnings.append, encode=encode, measure_file=measure,
                limiter=lambda s, c: s)                      # a limiter that does nothing
        self.assertTrue(any("above the limit" in w for w in warnings), warnings)

    def test_with_the_mode_off_the_peak_is_not_touched(self):
        x = np.zeros((48000, 2), np.float32)
        x[100, :] = 0.95
        level, warnings = self.run_it(x, 1.0, mode="off")
        self.assertEqual(warnings, [])

    def test_pcm_is_written_as_raw_float_without_a_check(self):
        x = np.random.default_rng(1).standard_normal((4800, 2)).astype(np.float32) * 0.1
        with tempfile.TemporaryDirectory() as scratch:
            path, level = export.make_audio_file(x, {"audio": {"codec": "pcm_s24le"}}, {"mode": "off"},
                                                 48000, scratch, "ffmpeg")
            self.assertIsNone(level)
            np.testing.assert_array_equal(np.fromfile(path, "<f4").reshape(-1, 2), x)


class VolumeTests(ExportFixture):
    def test_an_output_on_a_volume_that_is_not_mounted_is_an_export_error(self):
        e = self.entry("nowhere")
        e["output"] = "/Volumes/not-mounted-for-sure/film.mp4"
        tl = self.make_tl(segments=[("A", 10, 40)], exports=[e])
        with self.assertRaisesRegex(export.ExportError, "not mounted"):
            self.run_export(tl, "nowhere")
