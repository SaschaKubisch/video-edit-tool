"""Check 6.4: a short, planned and rendered from the originals by the renderer of the film (SPECS 11.7). The clips
are synthetic (320 x 180 and a wide 320 x 136); the decoder makes a horizontal gradient from black on the left to
white on the right, so the mean grey of a frame of the short tells which part of the picture the 9:16 window cut. Nothing
here reads a .braw file."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image

import config
import export
import main
import shorts
import textlayout
import timeline
from tests import media
from tests.fonts import builtin_font
from tests.test_export import ExportFixture, FlatDecoder, W, H
from tests.test_shorts import FPS, frames

SIZE = (180, 320)                       # a small vertical frame: the layout takes the shape, not the size


class GradientDecoder(FlatDecoder):
    """Every frame is a horizontal gradient: column x has the grey x / (width - 1)."""
    name = "gradient"

    def write_frame_range(self, path, scale, sink, first, last):
        self.calls.append((path, first, last))
        info = self.clips[path]
        row = np.round(np.linspace(0, 65535, info.width)).astype("<u2")
        frame = np.tile(row, (info.height, 1))
        data = np.stack([frame, frame, frame]).tobytes()          # gbrp16le: three planes
        for _ in range(last - first):
            sink.write(data)
        return last - first


class ShortFixture(ExportFixture):
    """A film of 12 scenes, 100 frames each, from the clips A (320 x 180) and B (320 x 136)."""

    PATTERN = "AABBABBAABAB"            # the clip of each scene: a short of 6 scenes takes scenes 1, 3, 5, 7, 9, 11

    @classmethod
    def make_film(cls, cues=()):
        segs = [(cls.PATTERN[i], 0, 100) for i in range(12)]
        tl = cls.make_tl(segments=segs, fade_in=1.0, fade_out=2.0, join=0.5, exports=[
            dict(cls.entry("film-x264"), video={"codec": "libx264", "preset": "veryfast",
                                                  "rate_control": {"mode": "bitrate", "mbps": 45}})])
        for i, s in enumerate(tl.data["segments"]):
            s["origin"] = "assembly"
        if cues:
            tl.data["subtitles"] = {"language": "en", "cues": [dict(c) for c in cues]}
        return tl

    @classmethod
    def short(cls, length_s=20, cues=(), name="short-test", **kw):
        film = cls.make_film(cues)
        plan = shorts.plan_short(film.data, film_path=str(cls.root / "film.json"), length_s=length_s, size=SIZE,
                                 output=str(cls.out / f"{name}.mp4"), **kw)
        return film, plan

    @classmethod
    def render(cls, plan, decoder=None, name="short-test", **kw):
        tl = timeline.Timeline(plan.data)
        return export.render_export(tl, name, str(cls.work), decoder or GradientDecoder(cls.clips),
                                    log=lambda s: None, protected=[str(cls.originals)], report_name="short-report", **kw)


class RenderTests(ShortFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.film, cls.plan = cls.short(20)
        data = cls.plan.data
        n = len(data["segments"])
        data["segments"][0]["crop"] = {"x": 0.0}
        data["segments"][1]["crop"] = {"x": 1.0}
        data["segments"][2]["crop"] = {"x_from": 0.0, "x_to": 1.0}
        cls.result = cls.render(cls.plan)
        cls.path = cls.result.path
        cls.yuv = media.read_yuv_frames(cls.path, SIZE[0], SIZE[1])
        cls.probe = media.probe(cls.path)
        cls.starts = []
        at = 0
        for s in data["segments"]:
            cls.starts.append(at)
            at += s["out"] - s["in"]

    def luma(self, film_frame):
        return self.yuv[film_frame][0].astype(float)

    def test_the_render_passes_its_own_checks(self):
        self.assertTrue(self.result.ok, self.result.problems)
        self.assertEqual(self.result.problems, [])
        self.assertFalse(os.path.exists(self.path + ".partial"))

    def test_the_file_is_h264_in_the_size_of_the_short_with_exactly_the_frames_of_the_plan(self):
        v = [s for s in self.probe["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((v["codec_name"], v["profile"], v["pix_fmt"]), ("h264", "High", "yuv420p"))
        self.assertEqual((v["width"], v["height"]), SIZE)
        self.assertEqual(len(self.yuv), self.plan.frames)
        self.assertEqual(self.plan.frames, frames(20))
        self.assertEqual(v["avg_frame_rate"], "30000/1001")

    def test_the_sound_is_aac_at_48_khz_and_as_long_as_the_picture_within_a_frame(self):
        a = [s for s in self.probe["streams"] if s["codec_type"] == "audio"][0]
        v = [s for s in self.probe["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((a["codec_name"], int(a["sample_rate"]), a["channels"]), ("aac", 48000, 2))
        self.assertLess(abs(float(a["duration"]) - float(v["duration"])), 1 / 29.97)

    def test_the_length_is_the_length_asked_for_to_the_frame(self):
        self.assertAlmostEqual(float(self.probe["format"]["duration"]), frames(20) / 29.97003, delta=0.04)

    def test_the_loudness_is_the_films_target_and_the_true_peak_stays_under_the_limit(self):
        out = self.result.output_loudness
        self.assertAlmostEqual(out["lufs"], -14.0, delta=1.0)
        self.assertLessEqual(out["true_peak_dbtp"], -1.0 + 0.1)

    def test_the_window_of_the_first_scene_is_at_the_left_the_second_at_the_right(self):
        left = self.luma(self.starts[0] + 50).mean()               # after the fade-in of 30 frames
        right = self.luma(self.starts[1] + 50).mean()
        self.assertLess(left, 70)
        self.assertGreater(right, 170)

    def test_a_pan_moves_the_window_from_the_left_to_the_right_over_the_scene(self):
        n = self.plan.data["segments"][2]["out"] - self.plan.data["segments"][2]["in"]
        first = self.luma(self.starts[2] + 1).mean()
        last = self.luma(self.starts[2] + n - 2).mean()
        middle = self.luma(self.starts[2] + n // 2).mean()
        self.assertLess(first, 70)
        self.assertGreater(last, 170)
        self.assertTrue(first + 40 < middle < last - 40, (first, middle, last))

    def test_a_centred_window_shows_the_middle_of_the_picture(self):
        for k in range(3, len(self.plan.data["segments"]) - 1):
            if self.plan.data["segments"][k].get("crop"):
                continue
            frame = self.starts[k] + 30
            self.assertAlmostEqual(self.luma(frame).mean(), 126, delta=20, msg=f"scene {k}")

    def test_no_black_bar_is_in_the_picture_of_a_wide_clip(self):
        # B is 320 x 136, a letterboxed clip in the film. The window has its full height, so no row is a bar.
        wide = [k for k, s in enumerate(self.plan.data["segments"]) if s["source"] == "B" and k >= 3]
        self.assertTrue(wide)
        for k in wide:
            rows = self.luma(self.starts[k] + 30).mean(axis=1)
            self.assertGreater(rows.min(), 60, f"scene {k}")
            self.assertLess(rows.max(), 200, f"scene {k}")

    def test_the_short_is_cut_from_the_originals_with_the_window_in_each_call(self):
        calls = GradientDecoder(self.clips)
        tl_data = json.loads(json.dumps(self.plan.data))
        tl_data["exports"][0]["output"] = str(self.out / "short-calls.mp4")
        tl_data["exports"][0]["name"] = "short-calls"
        export.render_export(timeline.Timeline(tl_data), "short-calls", str(self.work), calls, log=lambda s: None,
                             protected=[str(self.originals)], report_name="short-report")
        self.assertEqual(len(calls.calls), len(tl_data["segments"]))
        for (path, first, last), seg in zip(calls.calls, tl_data["segments"]):
            self.assertEqual((first, last), (seg["in"], seg["out"]))

    def test_the_stills_and_the_contact_sheet(self):
        made = shorts.scene_stills(self.plan.data, self.path)
        self.assertEqual(len(made["stills"]), len(self.plan.data["segments"]))
        self.assertTrue(all(os.path.isfile(p) for p in made["stills"]))
        self.assertTrue(made["contact"].endswith(".contact.jpg"))
        img = Image.open(made["contact"])
        self.assertLessEqual(max(img.size), 2000)
        first = np.asarray(Image.open(made["stills"][0]).convert("L"), dtype=float)
        self.assertEqual(first.shape, (SIZE[1], SIZE[0]))
        self.assertLess(first.mean(), 70)                          # the first scene is cut at the left
        second = np.asarray(Image.open(made["stills"][1]).convert("L"), dtype=float)
        self.assertGreater(second.mean(), 170)


class LengthTests(ShortFixture):
    def test_several_lengths_come_out_to_the_frame(self):
        for length in (8, 12.5):
            film, plan = self.short(length, name=f"short-{length}")
            result = self.render(plan, name=f"short-{length}")
            self.assertTrue(result.ok, result.problems)
            self.assertEqual(result.frames, frames(length))
            self.assertEqual(len(media.read_yuv_frames(result.path, SIZE[0], SIZE[1])), frames(length))


class SubtitleTests(ShortFixture):
    def setUp(self):
        builtin_font(self)

    def test_burned_subtitles_stay_in_the_safe_area_of_the_vertical_frame(self):
        cues = [{"id": "c001", "anchor": f"s{i:03d}", "start_s": 0.4, "end_s": 3.0,
                 "text": "Listen to the sizzle when it starts, it is loud.", "origin": "manual"} for i in range(1, 13)]
        film, plan = self.short(20, cues=cues, name="short-subs")
        self.assertTrue(plan.data["subtitles"]["cues"])
        result = self.render(plan, name="short-subs")
        self.assertTrue(result.ok, result.problems)
        yuv = media.read_yuv_frames(result.path, SIZE[0], SIZE[1])
        g = textlayout.geometry(SIZE)
        at = 0
        checked = 0
        for cue in plan.data["subtitles"]["cues"]:
            seg = next(s for s in plan.data["segments"] if s["id"] == cue["anchor"])
            start = sum(s["out"] - s["in"] for s in plan.data["segments"][:plan.data["segments"].index(seg)])
            frame = start + int((cue["start_s"] + cue["end_s"]) / 2 * 29.97)
            if frame >= len(yuv) - 70:
                continue
            y = yuv[frame][0].astype(int)
            bg = int(np.median(y))
            pts = np.argwhere(np.abs(y - bg) > 50)
            self.assertTrue(len(pts), f"no text at frame {frame}")
            self.assertGreaterEqual(pts[:, 0].min(), g.safe[1] - 1)
            self.assertLessEqual(pts[:, 0].max(), g.safe[3] + g.pad)
            self.assertGreaterEqual(pts[:, 1].min(), g.safe[0] - 1)
            self.assertLessEqual(pts[:, 1].max(), g.safe[2] + 1)
            checked += 1
        self.assertGreater(checked, 1)


def run_short(argv, decoder=None):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    args = main.build_parser().parse_args(argv)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            main.cmd_short(args, decoder=decoder)
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue(), err.getvalue()


class CommandTests(ShortFixture):
    def setUp(self):
        self.name = self.id().split(".")[-1]
        self.film_path = self.root / f"film-{self.name}.json"
        self.work_dir = self.root / f"work-{self.name}"
        self.make_film().save(str(self.film_path))
        self.output = self.root / f"out-{self.name}" / "short-9.mp4"

    def short_cmd(self, *extra, decoder=None):
        return run_short(["short", "--file", str(self.film_path), "--work", str(self.work_dir), "--output",
                          str(self.output), "--size", "180x320", *extra], decoder=decoder or GradientDecoder(self.clips))

    def test_it_plans_renders_writes_the_plan_next_to_the_file_and_the_stills(self):
        code, out, err = self.short_cmd("--length", "9", "--stills")
        self.assertEqual(code, 0, out + err)
        plan_path = self.output.with_suffix(".json")
        self.assertTrue(self.output.is_file())
        self.assertTrue(plan_path.is_file())
        self.assertIn("Plan saved", out)
        self.assertIn("Short:", out)
        self.assertTrue(self.output.with_suffix(".contact.jpg").is_file())
        self.assertTrue((self.output.parent / "short-9.stills").is_dir())
        plan = timeline.Timeline.load(str(plan_path))
        self.assertEqual(plan.data["kind"], "short")
        self.assertEqual(plan.film_length_frames(), frames(9))
        self.assertEqual(len(media.read_yuv_frames(str(self.output), 180, 320)), frames(9))
        self.assertEqual([f for f in plan.validate(file_checks=False) if f.level == "error"], [])

    def test_a_dry_run_prints_the_plan_and_writes_nothing(self):
        code, out, err = self.short_cmd("--length", "9", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("Short from", out)
        self.assertIn("film in - out", out)
        self.assertIn("Dry run: nothing was written", out)
        self.assertFalse(self.output.parent.exists())

    def test_no_render_writes_only_the_plan(self):
        code, out, err = self.short_cmd("--length", "9", "--no-render")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.output.with_suffix(".json").is_file())
        self.assertFalse(self.output.exists())

    def test_without_a_length_the_short_is_60_seconds_or_the_whole_film(self):
        code, out, err = self.short_cmd("--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("asked for 60 s", out)
        self.assertIn("whole film", out.lower())

    def test_a_plan_is_rendered_as_it_is_and_an_edit_of_the_plan_shows_in_the_picture(self):
        self.short_cmd("--length", "9", "--no-render")
        plan_path = self.output.with_suffix(".json")
        data = json.loads(plan_path.read_text())
        data["segments"][0]["crop"] = {"x": 1.0}
        plan_path.write_text(json.dumps(data))
        code, out, err = run_short(["short", "--file", str(plan_path), "--work", str(self.work_dir)],
                                   decoder=GradientDecoder(self.clips))
        self.assertEqual(code, 0, out + err)
        yuv = media.read_yuv_frames(str(self.output), 180, 320)
        self.assertGreater(float(yuv[50][0].mean()), 170)             # the first scene was centred, now at the right

    def test_a_dry_run_of_a_plan_prints_it_and_renders_nothing(self):
        self.short_cmd("--length", "9", "--no-render")
        plan_path = self.output.with_suffix(".json")
        code, out, err = run_short(["short", "--file", str(plan_path), "--work", str(self.work_dir), "--dry-run"])
        self.assertEqual(code, 0, out + err)
        self.assertIn("film in - out", out)
        self.assertIn("Dry run", out)
        self.assertFalse(self.output.exists())

    def test_detach_plans_here_and_starts_the_render_of_the_plan_in_a_session_of_its_own(self):
        started = []
        real = export.spawn_detached
        export.spawn_detached = lambda argv, log_path, pid_path, cwd=None: started.append((argv, log_path, pid_path)) or 4242
        self.addCleanup(setattr, export, "spawn_detached", real)
        code, out, err = self.short_cmd("--length", "9", "--detach", "--stills")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(started), 1)
        argv, log_path, pid_path = started[0]
        plan_path = str(self.output.with_suffix(".json"))
        self.assertEqual(argv[2:4], ["short", "--file"])
        self.assertIn(plan_path, argv)
        self.assertIn("--stills", argv)
        self.assertNotIn("--detach", argv)
        self.assertTrue(log_path.endswith("short-short-9.log"))
        self.assertTrue(os.path.isfile(plan_path))
        self.assertFalse(self.output.exists())
        self.assertIn("4242", out)

    def test_a_plan_does_not_take_the_options_of_a_new_short(self):
        self.short_cmd("--length", "9", "--no-render")
        plan_path = self.output.with_suffix(".json")
        code, out, err = run_short(["short", "--file", str(plan_path), "--length", "30"])
        self.assertEqual(code, 2)
        self.assertIn("already planned", err)

    def test_planning_again_keeps_the_old_plan_as_a_snapshot(self):
        self.short_cmd("--length", "9", "--no-render")
        code, out, err = self.short_cmd("--length", "8", "--no-render")
        self.assertEqual(code, 0, err)
        self.assertIn("snapshot", out)

    def test_an_unknown_scene_is_an_error(self):
        code, out, err = self.short_cmd("--length", "9", "--prefer", "s099", "--dry-run")
        self.assertEqual(code, 1)
        self.assertIn("s099", err)

    def test_a_size_that_is_not_vertical_is_an_error(self):
        code, out, err = run_short(["short", "--file", str(self.film_path), "--size", "320x180", "--output",
                                    str(self.output), "--dry-run"])
        self.assertEqual(code, 1)
        self.assertIn("vertical", err)

    def test_the_options_of_the_choice_reach_the_plan(self):
        code, out, err = self.short_cmd("--length", "9", "--prefer", "s003", "--exclude", "s001", "--no-render")
        self.assertEqual(code, 0, err)
        data = json.loads(self.output.with_suffix(".json").read_text())
        kept = [s["x_film_segment"] for s in data["segments"]]
        self.assertIn("s003", kept)
        self.assertNotIn("s001", kept)
        self.assertEqual(data["derived_from"]["settings"]["prefer"], ["s003"])
        self.assertEqual(data["derived_from"]["settings"]["exclude"], ["s001"])

    def test_an_output_inside_the_folder_of_the_originals_is_refused(self):
        code, out, err = run_short(["short", "--file", str(self.film_path), "--size", "180x320", "--output",
                                    str(self.originals / "short.mp4"), "--length", "9", "--no-render"])
        self.assertEqual(code, 1)
        self.assertIn("original", err)
        self.assertFalse((self.originals / "short.json").exists())

    def test_the_short_does_not_change_the_film_file(self):
        before = self.film_path.read_bytes()
        self.short_cmd("--length", "9")
        self.assertEqual(self.film_path.read_bytes(), before)

    def test_the_originals_are_not_touched(self):
        before = {p.name: p.read_bytes() for p in self.originals.iterdir()}
        self.short_cmd("--length", "9")
        self.assertEqual({p.name: p.read_bytes() for p in self.originals.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
