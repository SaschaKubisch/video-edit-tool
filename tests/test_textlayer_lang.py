"""Check 4.18: the lanes of a film whose subtitles are in Japanese, Simplified Chinese or Thai (SPECS 9.6, step 6):
the language is `subtitles.language` of the data the plan is built from, the lines are cut at phrase boundaries in
the real font, a cue that needs more than two lines becomes several cues that share its time, the lane is as high as
the script needs, and the cache signature moves with the language. Needs the Noto files (VET_NOTO_FONT_DIR)."""

import tempfile
import unittest
from unittest import mock

import numpy as np

import config
import textlayer
import textlayout
from tests.fonts import requires_noto
from tests.test_textlayer import CUE, FRAME, FULL, H, W, data_with, ink, run_overlay
from tests.test_textlang import JA, TH, ZH


def lang_data(lang, text, **cue):
    d = data_with([dict(CUE, text=text, **cue)])
    d["subtitles"]["language"] = lang
    return d


@requires_noto
class LangPlanTests(unittest.TestCase):
    def test_the_plan_knows_its_language_and_a_lane_has_the_line_height_of_the_script(self):
        for lang, text, em in (("ja", JA, 1.3), ("zh-Hans", ZH, 1.3), ("th", TH, 1.45)):
            plan = textlayer.build(lang_data(lang, text), FRAME, 0, 400, FULL)
            g = textlayout.geometry(FRAME, lang=lang)
            self.assertEqual(plan.lang, lang)
            self.assertEqual(plan.lanes[0].height, 2 * round(g.size * em) + 2 * g.pad, lang)
            self.assertEqual(plan.cue_count, 1)

    def test_an_english_film_is_as_before(self):
        plan = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL)
        self.assertEqual(plan.lang, "en")
        g = textlayout.geometry(FRAME)
        self.assertEqual(plan.lanes[0].height, 2 * g.line_height + 2 * g.pad)

    def test_a_cue_of_two_lines_is_one_item_on_the_frames_of_the_cue(self):
        plan = textlayer.build(lang_data("zh-Hans", ZH), (4096, 2160), 0, 400,
                               {k: (0, 0, 4096, 2160) for k in FULL})
        items = plan.lanes[0].items
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0].first, items[0].end), (96, 180))
        self.assertEqual(items[0].lines, ("她把罐子里淡黄色的酱汁", "倒进一个小小的白色茶壶里。"))

    def test_a_cue_that_needs_three_lines_becomes_two_cues_that_share_its_time(self):
        plan = textlayer.build(lang_data("ja", JA), (4096, 2160), 0, 400, {k: (0, 0, 4096, 2160) for k in FULL})
        items = plan.lanes[0].items
        self.assertEqual([i.lines for i in items],
                         [("彼女は背の高い容器に", "ハンドブレンダーを"), ("持っている。",)])
        self.assertEqual(items[0].first, 96)
        self.assertLessEqual(items[-1].end, 180)
        self.assertLess(items[0].end, items[1].first)                      # a gap, no overlap
        self.assertGreater(items[0].end - items[0].first, items[1].end - items[1].first)   # in proportion to the text
        self.assertEqual(plan.cue_count, 1)
        self.assertFalse(any("cut inside" in w for w in plan.warnings))

    def test_no_item_has_more_than_two_lines_and_no_line_is_wider_than_the_safe_width(self):
        frame = (1080, 1920)
        plan = textlayer.build(lang_data("th", TH * 2, start_s=3.0, end_s=7.0), frame, 0, 400, {k: (0, 0, 1080, 1920) for k in FULL})
        g = textlayout.geometry(frame, lang="th")
        font = textlayout.get_font("subtitle", g.size, "th")
        for lane in plan.lanes:
            for item in lane.items:
                self.assertLessEqual(len(item.lines), 2)
                for line in item.lines:
                    self.assertLessEqual(font.getlength(line), g.max_width + 1e-6)

    def test_the_text_of_a_letterboxed_scene_stays_inside_its_picture(self):
        wide = {"s001": (0, 55, W, 485), "s002": (0, 0, W, H), "s003": (0, 0, W, H)}
        plan = textlayer.build(lang_data("ja", JA), FRAME, 0, 400, wide)
        g = textlayout.geometry(FRAME, wide["s001"], lang="ja")
        lane = plan.lanes[0]
        self.assertEqual(lane.y + lane.height - g.pad, g.bottom)
        self.assertLessEqual(g.bottom, 485)

    def test_the_signature_moves_with_the_language_and_the_font(self):
        a = textlayer.build(lang_data("ja", JA), FRAME, 0, 400, FULL).signature
        b = textlayer.build(lang_data("ja", JA), FRAME, 0, 400, FULL).signature
        zh = textlayer.build(lang_data("zh-Hans", JA), FRAME, 0, 400, FULL).signature
        en = textlayer.build(lang_data("en", JA), FRAME, 0, 400, FULL).signature
        self.assertEqual(a, b)
        self.assertEqual(len({a, zh, en}), 3)

    def test_the_signature_of_an_english_film_is_the_one_it_had_before(self):
        # no key for the language is added when it is English, so a preview that was made earlier stays valid
        plan = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL)
        self.assertNotIn("lang", textlayer._signature_material(plan))

    def test_thai_without_raqm_stops_the_plan_with_a_message(self):
        with mock.patch.object(textlayout, "_raqm_available", lambda: False):
            with self.assertRaises(textlayout.LayoutEngineMissing):
                textlayer.build(lang_data("th", TH), FRAME, 0, 400, FULL)

    def test_a_missing_noto_folder_stops_the_plan_and_names_the_variable(self):
        with mock.patch.dict("os.environ", {config.TEXT_NOTO_DIR_ENV: ""}):
            with self.assertRaises(textlayout.FontMissing) as cm:
                textlayer.build(lang_data("ja", JA), FRAME, 0, 400, FULL)
        self.assertIn("VET_NOTO_FONT_DIR", str(cm.exception))

    def test_titles_stay_in_inter(self):
        from tests.test_textlayer import TITLE
        d = lang_data("ja", JA)
        d["titles"] = [dict(TITLE)]
        plan = textlayer.build(d, FRAME, 0, 400, FULL)
        self.assertEqual(plan.title_count, 1)


class FontCreditTests(unittest.TestCase):
    """The credit file of a film that burns text names the fonts and their licence (SPECS 9.6)."""

    def test_english_cues_name_inter(self):
        from tests.fonts import builtin_font
        builtin_font(self)
        plan = textlayer.build(data_with([CUE]), FRAME, 0, 400, FULL)
        line = textlayer.font_credit(plan)
        self.assertIn("Inter", line)
        self.assertIn("SIL Open Font License 1.1", line)
        self.assertNotIn("Noto", line)

    def test_a_title_names_inter_too(self):
        from tests.fonts import builtin_font
        from tests.test_textlayer import TITLE
        builtin_font(self)
        plan = textlayer.build(data_with(titles=[TITLE]), FRAME, 0, 400, FULL)
        self.assertIn("Inter", textlayer.font_credit(plan))

    def test_nothing_drawn_needs_no_credit(self):
        plan = textlayer.build(data_with(), FRAME, 0, 400, FULL)
        self.assertIsNone(textlayer.font_credit(plan))

    @requires_noto
    def test_each_language_names_its_font_with_the_licence_and_the_source(self):
        for lang, text, family, url in (("ja", JA, "Noto Sans JP", "https://github.com/notofonts/noto-cjk"),
                                        ("zh-Hans", ZH, "Noto Sans SC", "https://github.com/notofonts/noto-cjk"),
                                        ("th", TH, "Noto Sans Thai", "https://github.com/notofonts/thai")):
            plan = textlayer.build(lang_data(lang, text), FRAME, 0, 400, FULL)
            line = textlayer.font_credit(plan)
            self.assertIn(family, line, lang)
            self.assertIn(url, line, lang)
            self.assertIn("SIL Open Font License 1.1", line, lang)
            self.assertNotIn("Inter", line, lang)             # no title: Inter is not used

    @requires_noto
    def test_a_title_in_a_translated_film_adds_inter(self):
        from tests.test_textlayer import TITLE
        d = lang_data("ja", JA)
        d["titles"] = [dict(TITLE)]
        line = textlayer.font_credit(textlayer.build(d, FRAME, 0, 400, FULL))
        self.assertIn("Inter", line)
        self.assertIn("Noto Sans JP", line)


@requires_noto
class LangOverlayTests(unittest.TestCase):
    """Drawn, laid over a grey picture by ffmpeg, and looked at as pixels."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_each_script_shows_on_its_frames_inside_the_safe_area(self):
        for lang, text in (("ja", JA), ("zh-Hans", ZH), ("th", TH)):
            plan = textlayer.build(lang_data(lang, text), FRAME, 0, 400, FULL)
            video = run_overlay(plan, tempfile.mkdtemp(dir=self.tmp.name))
            shown = [n for n in range(len(video)) if len(ink(video[n]))]
            self.assertEqual((shown[0], shown[-1]), (96, 179), lang)
            pts = ink(video[100])
            self.assertGreater(len(pts), 400, lang)
            safe = textlayout.geometry(FRAME, lang=lang).safe
            self.assertGreaterEqual(pts[:, 1].min(), safe[0] - 1, lang)
            self.assertLessEqual(pts[:, 1].max(), safe[2] + 1, lang)
            self.assertGreaterEqual(pts[:, 0].min(), safe[1] - 1, lang)
            self.assertLessEqual(pts[:, 0].max(), safe[3] + 1, lang)


if __name__ == "__main__":
    unittest.main()
