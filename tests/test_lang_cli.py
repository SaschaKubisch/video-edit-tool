"""Check 4.21: `render --lang LANG` and `short --lang LANG` (SPECS 9.6, step 6): the same film or short with its
subtitles burned in a translated language, from the translations that `subtitles translate` left in the cache, into a file
named `<film>.<lang>.<ext>`. Run through the command-line parser with synthetic clips and the Noto fonts."""

import contextlib
import io
import json
import os
import unittest
from pathlib import Path

import numpy as np

import config
import export
import main
import textlayout
import timeline
import translate
from tests import media
from tests.fonts import requires_inter, requires_noto
from tests.test_export import ExportFixture, FlatDecoder
from tests.test_export_lang import CUES, JA, put_translations
from tests.test_short_render import SIZE, GradientDecoder, ShortFixture, run_short


class RenderLangCommandTests(ExportFixture):
    def setUp(self):
        name = self.id().split(".")[-1]
        self.path = self.root / f"lang-{name}.json"
        self.work_dir = self.root / f"work-{name}"
        self.work_dir.mkdir(exist_ok=True)
        put_translations(self.work_dir, "ja", JA)
        self.export_name = f"film-{name[-24:]}"
        entry = self.entry(name=self.export_name)                   # the form is not set: a film gets srt
        tl = self.make_tl(exports=[entry], loud=False)
        tl.data["subtitles"] = {"language": "en", "cues": [dict(c) for c in CUES]}
        tl.save(str(self.path))

    def render(self, *extra):
        args = main.build_parser().parse_args(["render", "--file", str(self.path), "--work", str(self.work_dir),
                                               "--export", self.export_name, *extra])
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main.cmd_render(args, decoder=FlatDecoder(self.clips))
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue(), err.getvalue()

    def test_the_language_has_to_be_one_of_the_six_or_english(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                main.build_parser().parse_args(["render", "--export", "x", "--lang", "fr"])
        self.assertEqual(cm.exception.code, 2)
        for lang in config.TRANSLATE_LANGUAGES + ("en",):
            main.build_parser().parse_args(["render", "--export", "x", "--lang", lang])

    @requires_noto
    def test_a_language_without_a_form_burns_the_subtitles(self):
        code, out, err = self.render("--lang", "ja")
        self.assertEqual(code, 0, out + err)
        film = self.out / f"{self.export_name}.ja.mp4"
        self.assertTrue(film.is_file())
        self.assertFalse((self.out / f"{self.export_name}.mp4").exists())
        self.assertIn("Noto Sans JP", (self.out / f"{self.export_name}.ja.credit.txt").read_text(encoding="utf-8"))
        self.assertIn(str(self.out / f"{self.export_name}.ja.credit.txt"), out)

    @requires_noto
    def test_both_writes_the_srt_of_the_language_and_names_it(self):
        code, out, err = self.render("--lang", "ja", "--subtitles", "both")
        self.assertEqual(code, 0, out + err)
        srt = self.out / f"{self.export_name}.ja.srt"
        self.assertTrue(srt.is_file())
        self.assertIn(str(srt), out)

    def test_a_form_that_draws_nothing_is_a_failure_with_the_reason(self):
        code, out, err = self.render("--lang", "ja", "--subtitles", "srt")
        self.assertEqual(code, 1)
        self.assertIn("burned", err)

    def test_a_missing_translation_is_a_failure_that_names_the_command(self):
        code, out, err = self.render("--lang", "de")
        self.assertEqual(code, 1)
        self.assertIn("subtitles translate --lang de", err)

    def test_detach_keeps_the_language_in_the_command_it_starts(self):
        started = []
        real = export.spawn_detached
        export.spawn_detached = lambda argv, log_path, pid_path, cwd=None: started.append(argv) or 4242
        self.addCleanup(setattr, export, "spawn_detached", real)
        import sys
        from unittest import mock
        with mock.patch.object(sys, "argv", ["main.py", "render", "--file", str(self.path), "--work", str(self.work_dir),
                                             "--export", self.export_name, "--lang", "ja", "--detach"]):
            code, out, err = self.render("--lang", "ja", "--detach")
        self.assertEqual(code, 0, out + err)
        argv = started[0]
        self.assertEqual(argv[argv.index("--lang") + 1], "ja")
        self.assertNotIn("--detach", argv)


@requires_noto
class ShortLangTests(ShortFixture):
    """A short whose cues name the cues of the film they came from, rendered in Japanese."""

    def setUp(self):
        self.name = self.id().split(".")[-1]
        self.film_path = self.root / f"film-{self.name}.json"
        self.work_dir = self.root / f"work-{self.name}"
        self.work_dir.mkdir(exist_ok=True)
        cues = [{"id": f"c{i:03d}", "anchor": f"s{i:03d}", "start_s": 0.4, "end_s": 3.0, "text": text, "origin": "manual"}
                for i, text in ((1, "First scene."), (3, "Second scene with bars."), (5, "Third scene."))]
        self.make_film(cues=cues).save(str(self.film_path))
        put_translations(self.work_dir, "ja", JA)
        self.output = self.root / f"out-{self.name}" / "short-9.mp4"

    def plan(self):
        code, out, err = run_short(["short", "--file", str(self.film_path), "--work", str(self.work_dir), "--output",
                                    str(self.output), "--size", "180x320", "--length", "9", "--no-render"])
        self.assertEqual(code, 0, out + err)
        return self.output.with_suffix(".json")

    def test_the_plan_names_the_film_cue_of_every_cue(self):
        plan = json.loads(self.plan().read_text())
        cues = plan["subtitles"]["cues"]
        self.assertTrue(cues)
        self.assertTrue(all(c.get("x_film_cue") in ("c001", "c003", "c005") for c in cues), cues)

    def test_a_plan_is_rendered_in_a_language_and_the_english_file_is_not_made(self):
        plan_path = self.plan()
        code, out, err = run_short(["short", "--file", str(plan_path), "--work", str(self.work_dir), "--lang", "ja",
                                    "--stills"], decoder=GradientDecoder(self.clips))
        self.assertEqual(code, 0, out + err)
        ja = self.output.with_name("short-9.ja.mp4")
        self.assertTrue(ja.is_file())
        self.assertFalse(self.output.exists())
        self.assertIn("short-9.ja.mp4", out)
        self.assertTrue(ja.with_suffix(".contact.jpg").is_file())
        self.assertIn("Noto Sans JP", ja.with_suffix(".credit.txt").read_text(encoding="utf-8"))

    @requires_inter
    def test_the_japanese_text_is_drawn_inside_the_safe_area_of_the_vertical_frame(self):
        plan_path = self.plan()
        before = plan_path.read_bytes()
        english = run_short(["short", "--file", str(plan_path), "--work", str(self.work_dir)],
                            decoder=GradientDecoder(self.clips))
        self.assertEqual(english[0], 0, english[1] + english[2])
        run_short(["short", "--file", str(plan_path), "--work", str(self.work_dir), "--lang", "ja"],
                  decoder=GradientDecoder(self.clips))
        self.assertEqual(plan_path.read_bytes(), before)                         # the plan stays English
        ja = np.array([f[0] for f in media.read_yuv_frames(str(self.output.with_name("short-9.ja.mp4")), SIZE[0], SIZE[1])])
        en = np.array([f[0] for f in media.read_yuv_frames(str(self.output), SIZE[0], SIZE[1])])
        diff = np.abs(ja.astype(int) - en.astype(int))
        g = textlayout.geometry(SIZE, lang="ja")
        rows = np.argwhere(diff.max(axis=0) > 40)
        self.assertGreater(len(rows), 50)
        self.assertGreaterEqual(rows[:, 0].min(), g.safe[1] - 1)
        self.assertLessEqual(rows[:, 0].max(), g.safe[3] + g.pad)

    def test_a_short_that_names_no_film_cue_is_refused_with_a_reason(self):
        plan_path = self.plan()
        data = json.loads(plan_path.read_text())
        for c in data["subtitles"]["cues"]:
            c.pop("x_film_cue", None)
        plan_path.write_text(json.dumps(data))
        code, out, err = run_short(["short", "--file", str(plan_path), "--work", str(self.work_dir), "--lang", "ja"],
                                   decoder=GradientDecoder(self.clips))
        self.assertEqual(code, 1)
        self.assertIn("x_film_cue", err)

    def test_a_language_is_not_an_option_of_a_new_short_only(self):
        # --lang goes with --file FILM too: the plan is English and the render is Japanese
        code, out, err = run_short(["short", "--file", str(self.film_path), "--work", str(self.work_dir), "--output",
                                    str(self.output), "--size", "180x320", "--length", "9", "--lang", "ja"],
                                   decoder=GradientDecoder(self.clips))
        self.assertEqual(code, 0, out + err)
        self.assertTrue(self.output.with_name("short-9.ja.mp4").is_file())
        self.assertTrue(self.output.with_suffix(".json").is_file())


if __name__ == "__main__":
    unittest.main()
