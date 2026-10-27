"""Check 4.17: burned-in subtitles in Japanese, Simplified Chinese and Thai (SPECS 9.6, as built in step 6): the
font of each language, the raqm layout for Thai, the line height of each script, Latin letters and digits inside a
cue, and the lines of a translated cue measured in the real font. The tests that open a Noto font run when
VET_NOTO_FONT_DIR names the folder with the three files; the others need none."""

import os
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import ImageFont

import config
import textlayout
from tests.fonts import requires_noto

FRAME = (4096, 2160)
VERTICAL = (1080, 1920)

JA = "彼女は背の高い容器にハンドブレンダーを持っている。"
ZH = "她把罐子里淡黄色的酱汁倒进一个小小的白色茶壶里。"
TH = "เธอเทน้ำสลัดสีเหลืองอ่อนจากโถลงในกาน้ำชาสีขาวใบเล็ก"
LATIN = "Hello World 2026 A002 C083 (ok) 100%"
SCRIPTS = {"ja": JA, "zh-Hans": ZH, "th": TH}


def notdef(font, ch) -> bool:
    """Whether the font draws `ch` as its missing-glyph box (the same picture as a character no font has)."""
    a, b = font.getmask(ch), font.getmask("￿")
    return a.size == b.size and bytes(a) == bytes(b)


def white_rows(image):
    """The rows of an RGBA picture that hold fill (white) pixels, not outline or shadow."""
    a = np.asarray(image)
    return np.where(((a[:, :, :3] > 200).all(axis=2) & (a[:, :, 3] > 200)).any(axis=1))[0]


class StyleTableTests(unittest.TestCase):
    def test_the_table_names_the_three_languages_with_their_files_and_line_heights(self):
        t = config.TEXT_LANG_STYLE
        self.assertEqual(set(t), {"ja", "zh-Hans", "th"})
        self.assertEqual(t["ja"]["font"], "NotoSansJP-Bold.otf")
        self.assertEqual(t["zh-Hans"]["font"], "NotoSansSC-Bold.otf")
        self.assertEqual(t["th"]["font"], "NotoSansThai-SemiBold.ttf")
        self.assertEqual(t["th"]["line_height_em"], 1.45)
        self.assertEqual(t["ja"]["line_height_em"], 1.3)
        self.assertEqual(t["zh-Hans"]["line_height_em"], 1.3)
        self.assertTrue(t["th"]["needs_raqm"])
        self.assertFalse(t["ja"].get("needs_raqm", False))
        self.assertEqual(config.TEXT_NOTO_DIR_ENV, "VET_NOTO_FONT_DIR")

    def test_every_translation_language_has_a_way_to_be_drawn(self):
        for lang in config.TRANSLATE_LANGUAGES:
            self.assertEqual(textlayout.own_font(lang), lang in ("ja", "zh-Hans", "th"), lang)
        self.assertFalse(textlayout.own_font("en"))
        self.assertFalse(textlayout.own_font(None))

    def test_line_height_by_language(self):
        for lang, em in (("th", 1.45), ("ja", 1.3), ("zh-Hans", 1.3), ("en", 1.2), ("de", 1.2), (None, 1.2)):
            g = textlayout.geometry(VERTICAL, lang=lang)
            self.assertEqual(g.line_height, round(g.size * em), lang)
        self.assertEqual(textlayout.geometry(VERTICAL, lang="th").size, textlayout.geometry(VERTICAL).size)
        self.assertEqual(textlayout.geometry(VERTICAL, lang="th").safe, textlayout.geometry(VERTICAL).safe)

    def test_a_lane_for_thai_is_higher_than_one_for_english(self):
        a, b = textlayout.geometry(FRAME), textlayout.geometry(FRAME, lang="th")
        self.assertGreater(b.band[3], a.band[3])
        self.assertEqual(b.band[3], 2 * b.line_height + 2 * b.pad)


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for v in config.TEXT_LANG_STYLE.values():
            open(os.path.join(self.tmp.name, v["font"]), "wb").close()
        patch = mock.patch.dict(os.environ, {config.TEXT_NOTO_DIR_ENV: self.tmp.name})
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_font_is_found_in_the_noto_folder_and_not_in_the_inter_folder(self):
        with mock.patch.dict(os.environ, {config.TEXT_FONT_DIR_ENV: "/nonexistent"}):
            self.assertEqual(textlayout.lang_font_file("ja"), os.path.join(self.tmp.name, "NotoSansJP-Bold.otf"))
            self.assertEqual(textlayout.lang_font_file("th"), os.path.join(self.tmp.name, "NotoSansThai-SemiBold.ttf"))

    def test_without_the_folder_the_message_says_what_to_set(self):
        with mock.patch.dict(os.environ, {config.TEXT_NOTO_DIR_ENV: ""}):
            with self.assertRaises(textlayout.FontMissing) as cm:
                textlayout.lang_font_file("zh-Hans")
        self.assertIn("VET_NOTO_FONT_DIR", str(cm.exception))
        self.assertIn("Noto Sans SC", str(cm.exception))

    def test_a_missing_file_is_named(self):
        os.remove(os.path.join(self.tmp.name, "NotoSansJP-Bold.otf"))
        with self.assertRaises(textlayout.FontMissing) as cm:
            textlayout.lang_font_file("ja")
        self.assertIn("NotoSansJP-Bold.otf", str(cm.exception))
        self.assertFalse(textlayout.lang_fonts_available("ja"))
        self.assertTrue(textlayout.lang_fonts_available("th"))

    def test_thai_with_a_pillow_without_raqm_fails_before_it_draws(self):
        with mock.patch.object(textlayout, "_raqm_available", lambda: False):
            with self.assertRaises(textlayout.LayoutEngineMissing) as cm:
                textlayout.get_font("subtitle", 64, "th")
        msg = str(cm.exception)
        self.assertIn("raqm", msg)
        self.assertIn("Thai", msg)
        self.assertIsInstance(cm.exception, textlayout.FontMissing)

    def test_a_language_without_a_font_of_its_own_asks_for_none(self):
        # Inter draws pt-BR, de and ru; no Noto folder is looked at
        with mock.patch.dict(os.environ, {config.TEXT_NOTO_DIR_ENV: ""}):
            self.assertIsNone(textlayout.lang_font_name("de"))
            self.assertIsNone(textlayout.lang_font_name("en"))
            self.assertEqual(textlayout.lang_font_name("ja"), "NotoSansJP-Bold.otf")


@requires_noto
class RealFontTests(unittest.TestCase):
    """Drawn with the real Noto files."""

    def test_each_font_draws_the_script_of_its_language(self):
        for lang, text in SCRIPTS.items():
            font = textlayout.get_font("subtitle", 64, lang)
            for ch in text:
                self.assertFalse(notdef(font, ch), f"{lang}: {ch!r} is drawn as the missing-glyph box")
                self.assertGreater(font.getlength(ch), 0, f"{lang}: {ch!r}")

    def test_inter_cannot_draw_them_and_one_cjk_font_cannot_draw_the_other_language(self):
        # why the font is chosen by language (SPECS 9.6)
        from tests.fonts import real_font_dir
        if real_font_dir():
            inter = ImageFont.truetype(os.path.join(real_font_dir(), "Inter-SemiBold.ttf"), 64)
            self.assertTrue(notdef(inter, "你"))
            self.assertTrue(notdef(inter, "ก"))
        ja = textlayout.get_font("subtitle", 64, "ja")
        sc = textlayout.get_font("subtitle", 64, "zh-Hans")
        th = textlayout.get_font("subtitle", 64, "th")
        self.assertTrue(notdef(ja, "这"))            # a simplified-only form
        self.assertFalse(notdef(sc, "这"))
        self.assertTrue(notdef(th, "你"))
        self.assertTrue(notdef(sc, "ก"))

    def test_latin_letters_and_digits_inside_a_cue_are_drawn(self):
        for lang, text in SCRIPTS.items():
            font = textlayout.get_font("subtitle", 64, lang)
            for ch in LATIN:
                if ch == " ":
                    continue
                self.assertFalse(notdef(font, ch), f"{lang}: {ch!r}")
                self.assertGreater(len(set(np.asarray(font.getmask(ch)).ravel().tolist())), 1, f"{lang}: {ch!r}")
            # different letters are different pictures, so they are not one stand-in glyph
            self.assertNotEqual(bytes(font.getmask("H")), bytes(font.getmask("W")))
            self.assertNotEqual(bytes(font.getmask("1")), bytes(font.getmask("7")))
            # a line that mixes the script with Latin and digits has ink for both parts
            g = textlayout.geometry(FRAME, lang=lang)
            mixed, _ = textlayout.render_lines([f"{LATIN} {text[:6]}"], g, lang=lang)
            latin, _ = textlayout.render_lines([LATIN], g, lang=lang)
            self.assertGreater(mixed.size[0], latin.size[0])
            self.assertTrue(textlayout.ink_box(latin))
            self.assertGreater(textlayout.ink_box(latin)[2] - textlayout.ink_box(latin)[0], 10 * g.size * 0.3)

    def test_thai_is_opened_with_the_raqm_engine(self):
        font = textlayout.get_font("subtitle", 64, "th")
        self.assertEqual(font.layout_engine, ImageFont.Layout.RAQM)

    def test_the_title_font_stays_inter_whatever_the_language_is(self):
        from tests.fonts import real_font_dir
        if not real_font_dir():
            self.skipTest("VET_FONT_DIR names no Inter folder")
        with mock.patch.dict(os.environ, {config.TEXT_FONT_DIR_ENV: real_font_dir()}):
            self.assertIn("Inter-Bold", textlayout.get_font("title", 64, "ja").path)

    def test_the_text_is_inside_its_picture_with_room_for_the_marks_of_thai(self):
        g = textlayout.geometry(VERTICAL, lang="th")
        image, _ = textlayout.render_lines(["ผู้ที่ให้ ก็ ปู่ ฝั้น นี้ น้ำ", "ฎฏฐญฐ ฎ ฏ"], g, lang="th",
                                           canvas=(VERTICAL[0], g.band[3]))
        x0, y0, x1, y1 = textlayout.ink_box(image)
        self.assertGreater(y0, 0)
        self.assertLess(y1, g.band[3])

    def fill_gap(self, lang, upper, lower, geo):
        """The rows without fill between two lines, drawn one at a time in the place they have in a cue of two lines."""
        a, _ = textlayout.render_lines([upper, ""], geo, lang=lang)
        b, _ = textlayout.render_lines(["", lower], geo, lang=lang)
        return int(white_rows(b).min() - white_rows(a).max() - 1)

    def test_the_line_height_of_thai_keeps_the_outlines_of_two_lines_apart_and_the_english_one_does_not(self):
        deep, tall = "ฎฏฐญฐ ฎ ฏ ก ฎ", "ผู้ที่ให้ ก็ ปู่ ฝั้น นี้ น้ำ"        # tails below the line, marks stacked above it
        geo = textlayout.geometry(VERTICAL, lang="th")
        self.assertEqual(geo.size, 64)
        outline = geo.outline
        self.assertEqual(self.fill_gap("th", deep, tall, geo), 15)           # [measured] 15 rows, the outlines need 2 x 5
        self.assertGreaterEqual(self.fill_gap("th", deep, tall, geo), 2 * outline)
        english = textlayout.geometry(VERTICAL)                               # the 1.2 em of Inter
        self.assertLess(self.fill_gap("th", deep, tall, english), 2 * outline)
        self.assertLessEqual(self.fill_gap("th", deep, tall, english), 0)     # the fills touch

    def test_the_line_height_of_japanese_and_chinese_keeps_the_outlines_apart(self):
        up, low = "彼女は背の高い容器にハンドブレンダーgjpqy", "「持っている。」彼女は背の高い"
        for lang in ("ja", "zh-Hans"):
            geo = textlayout.geometry(VERTICAL, lang=lang)
            self.assertEqual(self.fill_gap(lang, up, low, geo), 12, lang)    # [measured]
            self.assertGreaterEqual(self.fill_gap(lang, up, low, geo), 2 * geo.outline)
            self.assertLess(self.fill_gap(lang, up, low, textlayout.geometry(VERTICAL)), 2 * geo.outline)


@requires_noto
class LinesTests(unittest.TestCase):
    def lines(self, text, lang, frame=FRAME):
        g = textlayout.geometry(frame, lang=lang)
        found, split = textlayout.lang_lines(text, lang, g)
        return g, found, split

    def test_a_cue_that_fits_is_one_line(self):
        g, lines, split = self.lines("彼女は容器を持っている。", "ja")
        self.assertEqual(lines, ["彼女は容器を持っている。"])
        self.assertFalse(split)

    def test_japanese_is_cut_at_a_phrase_boundary_within_13_full_width_characters(self):
        g, lines, split = self.lines(JA, "ja")
        self.assertEqual("".join(lines), JA)
        self.assertEqual(lines, ["彼女は背の高い容器に", "ハンドブレンダーを", "持っている。"])
        for line in lines:
            self.assertLessEqual(sum(1.0 if ord(c) > 0x2000 else 0.5 for c in line), 13)
        self.assertFalse(split)

    def test_chinese_limit_is_16_characters(self):
        g, lines, _ = self.lines(ZH, "zh-Hans")
        self.assertEqual("".join(lines), ZH)
        self.assertEqual(lines, ["她把罐子里淡黄色的酱汁", "倒进一个小小的白色茶壶里。"])

    def test_thai_is_cut_at_a_phrase_boundary_and_no_line_starts_with_a_mark(self):
        g, lines, _ = self.lines(TH, "th")
        self.assertEqual("".join(lines), TH)
        self.assertEqual(lines, ["เธอเทน้ำสลัดสีเหลืองอ่อน", "จากโถลงในกาน้ำชาสีขาวใบเล็ก"])

    def test_every_line_fits_the_safe_width_of_the_9_16_frame(self):
        for lang, text in SCRIPTS.items():
            g, lines, _ = self.lines(text * 2, lang, VERTICAL)
            font = textlayout.get_font("subtitle", g.size, lang)
            self.assertGreaterEqual(len(lines), 2)
            for line in lines:
                self.assertLessEqual(font.getlength(line), g.max_width + 1e-6, f"{lang}: {line}")

    def test_latin_words_and_numbers_are_not_cut(self):
        g, lines, _ = self.lines("彼女はハンドブレンダー A002_C083 を2026年に持っている。", "ja", VERTICAL)
        self.assertTrue(any("A002_C083" in line for line in lines), lines)
        self.assertTrue(any("2026" in line for line in lines), lines)


if __name__ == "__main__":
    unittest.main()
