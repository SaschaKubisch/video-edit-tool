"""Check 4.13: the one style and its safe areas (SPECS 9.5): the numbers for 16:9, for a letterboxed
picture and for 9:16, and drawn text that stays inside the safe area. Most tests use Pillow's
built-in font; the ones marked with Inter run when VET_FONT_DIR names the Inter folder."""

import os
import unittest

import numpy as np

import config
import textlayout
import words
from tests.fonts import builtin_font, requires_inter

FRAME = (4096, 2160)
WIDE = (0, 220, 4096, 1940)           # a 4096 x 1720 picture in the DCI 4K frame
VERTICAL = (1080, 1920)


class GeometryTests(unittest.TestCase):
    def test_16_9_frame(self):
        g = textlayout.geometry(FRAME)
        self.assertEqual(g.key, "16:9")
        self.assertEqual(g.safe, (205, 108, 3891, 2052))             # 5 percent on every side (EBU R 95)
        self.assertEqual(g.size, 98)                                 # 4.5 percent of 2160, made even
        self.assertEqual(g.bottom, 2052 - 54)                        # lifted 2.5 percent of the height
        self.assertEqual(g.max_chars, 42)
        self.assertEqual(g.max_width, (3891 - 205) - 2 * g.outline)

    def test_sizes_are_fractions_of_the_height(self):
        a, b = textlayout.geometry((4096, 2160)), textlayout.geometry((960, 540))
        self.assertAlmostEqual(a.size / 2160, b.size / 540, delta=0.002)
        self.assertAlmostEqual(a.bottom / 2160, b.bottom / 540, delta=0.002)

    def test_a_letterboxed_picture_gets_the_safe_area_of_the_picture(self):
        g = textlayout.geometry(FRAME, WIDE)
        self.assertEqual(g.safe, (205, 306, 3891, 1854))             # 5 percent inside the picture
        self.assertEqual(g.bottom, 1854 - 43)
        full = textlayout.geometry(FRAME)
        self.assertLess(g.bottom, full.bottom)
        self.assertLess(g.bottom, WIDE[3])                           # above the bar
        self.assertEqual(g.size, full.size)                          # the same size in every scene

    def test_9_16_frame_uses_the_short_form_margins(self):
        g = textlayout.geometry(VERTICAL)
        self.assertEqual(g.key, "9:16")
        self.assertEqual(g.safe, (43, 288, 886, 1248))               # SPECS 11.6: top 15, bottom 35, left 4, right 18 percent
        self.assertEqual(g.bottom, 1248)
        self.assertEqual(g.max_chars, 24)
        self.assertEqual(g.size, 64)

    def test_the_band_holds_two_lines(self):
        g = textlayout.geometry(FRAME)
        x, y, w, h = g.band
        self.assertEqual((x, w), (0, 4096))
        self.assertGreaterEqual(h, 2 * g.line_height)
        self.assertEqual(y + h - g.pad, g.bottom)

    def test_no_font_gives_an_estimate_and_says_so(self):
        old = os.environ.pop("VET_FONT_DIR", None)
        self.addCleanup(lambda: os.environ.__setitem__("VET_FONT_DIR", old) if old else None)
        r = textlayout.rules(FRAME)
        self.assertFalse(r.exact)
        self.assertEqual(r.max_chars, 42)
        with self.assertRaises(textlayout.FontMissing):
            textlayout.font_file("Inter-SemiBold.ttf")
        self.assertFalse(textlayout.fonts_available())


class DrawingTests(unittest.TestCase):
    def setUp(self):
        builtin_font(self)

    def band_for(self, lines, frame=FRAME, picture=None):
        g = textlayout.geometry(frame, picture)
        image, _ = textlayout.render_lines(lines, g, canvas=(g.band[2], g.band[3]))
        return g, image

    def ink_in_frame(self, g, image):
        x0, y0, x1, y1 = textlayout.ink_box(image)
        return x0 + g.band[0], y0 + g.band[1], x1 + g.band[0], y1 + g.band[1]

    def test_two_lines_stay_inside_the_safe_area(self):
        g, image = self.band_for(["The pan is hot enough now so we", "can add the sausage and wait."])
        x0, y0, x1, y1 = self.ink_in_frame(g, image)
        self.assertGreaterEqual(x0, g.safe[0])
        self.assertLessEqual(x1, g.safe[2])
        self.assertGreaterEqual(y0, g.safe[1])
        self.assertLessEqual(y1, g.bottom + g.pad)
        self.assertEqual(image.size, (g.band[2], g.band[3]))

    def test_the_last_line_is_at_the_same_place_for_one_and_two_lines(self):
        g, one = self.band_for(["Hello there."])
        _, two = self.band_for(["Top line.", "Hello there."])
        self.assertEqual(textlayout.ink_box(one)[3], textlayout.ink_box(two)[3])
        self.assertGreater(textlayout.ink_box(one)[1], textlayout.ink_box(two)[1])

    def test_text_is_centred(self):
        g, image = self.band_for(["Centred text"])
        x0, _, x1, _ = self.ink_in_frame(g, image)
        self.assertAlmostEqual((x0 + x1) / 2, 2048, delta=4)

    def test_on_a_wide_picture_the_text_stays_above_the_bar(self):
        g, image = self.band_for(["Two lines of text", "above the black bar."], FRAME, WIDE)
        _, y0, _, y1 = self.ink_in_frame(g, image)
        self.assertLess(y1, WIDE[3])
        self.assertGreaterEqual(y0, WIDE[1])

    def test_9_16_text_stays_inside_its_safe_area(self):
        g, image = self.band_for(["Twenty-four characters", "on each of two lines"], VERTICAL)
        x0, y0, x1, y1 = self.ink_in_frame(g, image)
        self.assertGreaterEqual(x0, g.safe[0])
        self.assertLessEqual(x1, g.safe[2])
        self.assertLessEqual(y1, g.safe[3] + g.pad)

    def test_the_outline_and_shadow_are_drawn(self):
        g, image = self.band_for(["Hi"])
        a = np.asarray(image)
        solid = a[a[..., 3] == 255][:, :3]
        self.assertTrue(((solid == 255).all(axis=1)).any())          # white letters
        self.assertTrue(((solid == 0).all(axis=1)).any())            # black outline

    def test_opacity(self):
        g, image = self.band_for(["Hi"])
        faded = textlayout.with_opacity(image, 0.5)
        self.assertLessEqual(faded.getchannel("A").getextrema()[1], 128)
        self.assertEqual(image.getchannel("A").getextrema()[1], 255)


@requires_inter
class InterTests(unittest.TestCase):
    TEXT = ("The kitchen was quiet that morning and the light came in low over the table, so we made coffee first. "
            "Then we cut the bread, which was still warm, and put the butter on the table next to the jam. "
            "Nobody talked for a while. It was one of those days when the only sound is the kettle.")

    def test_the_font_is_inter(self):
        self.assertTrue(textlayout.fonts_available())
        self.assertTrue(textlayout.rules(FRAME).exact)

    def test_the_estimate_is_close_to_the_real_width(self):
        m = textlayout.Measurer("subtitle", 98)
        real = m.width(self.TEXT) / len(self.TEXT) / 98
        self.assertAlmostEqual(real, textlayout.EstimateMeasurer.EM, delta=0.03)

    def test_every_line_fits_the_safe_width_for_each_layout(self):
        for frame in (FRAME, (3840, 2160), (1920, 1080), (960, 540), VERTICAL):
            r = textlayout.rules(frame)
            cues, _ = words.split_cues(self.TEXT, r)
            for c in cues:
                lines = c.split("\n")
                self.assertLessEqual(len(lines), 2)
                for line in lines:
                    self.assertLessEqual(len(line), r.max_chars, (frame, line))
                    self.assertLessEqual(r.measure(line), r.max_width, (frame, line))

    def test_a_row_of_wide_letters_is_cut_by_the_width_not_the_character_count(self):
        r = textlayout.rules(FRAME)
        text = "WWWW MMMM WWWW MMMM WWWW MMMM WWWW MMMM WWWW MMMM WWWW"
        for line in (words.wrap(text, r) or []):
            self.assertLessEqual(r.measure(line), r.max_width)

    def test_a_word_wider_than_the_safe_area_is_cut_with_a_hyphen(self):
        r = textlayout.rules(VERTICAL)
        word = "Supercalifragilisticexpialidocious" * 2
        cues, notes = words.split_cues(f"It is {word} really.", r)
        self.assertTrue(notes)
        for c in cues:
            for line in c.split("\n"):
                self.assertLessEqual(r.measure(line), r.max_width, line)

    def test_twenty_cues_of_different_lengths_stay_inside_the_9_16_safe_area(self):
        g = textlayout.geometry(VERTICAL)
        r = textlayout.rules(VERTICAL)
        cues, _ = words.split_cues(self.TEXT * 4, r)
        self.assertGreaterEqual(len(cues), 20)
        for c in cues[:40]:
            image, _ = textlayout.render_lines(c.split("\n"), g, canvas=(g.band[2], g.band[3]))
            x0, y0, x1, y1 = textlayout.ink_box(image)
            x0, x1, y0, y1 = x0 + g.band[0], x1 + g.band[0], y0 + g.band[1], y1 + g.band[1]
            self.assertGreaterEqual(x0, g.safe[0] - 1, c)
            self.assertLessEqual(x1, g.safe[2] + 1, c)
            self.assertGreaterEqual(y0, g.safe[1], c)
            self.assertLessEqual(y1, g.safe[3] + g.pad, c)

    def test_a_wide_picture_keeps_the_text_inside_the_picture(self):
        g = textlayout.geometry(FRAME, WIDE)
        r = textlayout.rules(FRAME, WIDE)
        cues, _ = words.split_cues(self.TEXT, r)
        for c in cues:
            image, _ = textlayout.render_lines(c.split("\n"), g, canvas=(g.band[2], g.band[3]))
            x0, y0, x1, y1 = textlayout.ink_box(image)
            self.assertLess(y1 + g.band[1], WIDE[3], c)
            self.assertGreaterEqual(y0 + g.band[1], WIDE[1], c)
            self.assertGreaterEqual(x0, g.safe[0] - 1)
            self.assertLessEqual(x1, g.safe[2] + 1)


if __name__ == "__main__":
    unittest.main()
