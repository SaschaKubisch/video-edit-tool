"""Check 4.11: the text side of the subtitles (SPECS 9.3, 9.4): the local cleanup of a scene's
text, wrapping lines, splitting text into cues, and spreading cues across a scene. These tests
use fake widths (10 pixels per character), so they need no font."""

import re
import unittest
from fractions import Fraction

import config
import textlayout
import words

FPS = Fraction(30000, 1001)


def rules(max_chars=42, max_width=None, measure=None):
    r = textlayout.char_rules(max_chars)
    if max_width is not None:
        r.max_width = max_width
    if measure is not None:
        r.measure = measure
    return r


def plain(cue_text):
    """The cue text as one string, as the viewer reads it."""
    return re.sub(r"\s+", " ", cue_text.replace("\n", " ")).strip()


class CleanupTests(unittest.TestCase):
    def test_filler_words_go_with_one_comma(self):
        text, report = words.clean_text("Um, so this is, uh, the pan. Erm it is hot")
        self.assertEqual(text, "So this is, the pan. It is hot.")
        self.assertEqual(report["fillers_removed"], 3)

    def test_filler_must_be_a_whole_word(self):
        text, _ = words.clean_text("Eric heard the umbrella and the herb.")
        self.assertEqual(text, "Eric heard the umbrella and the herb.")

    def test_spaces_and_punctuation(self):
        text, _ = words.clean_text("Hello ,world  !How are you ?  Fine")
        self.assertEqual(text, "Hello, world! How are you? Fine.")

    def test_numbers_times_and_abbreviations_stay(self):
        text, _ = words.clean_text("It costs 3.5 euros at 10:30, e.g. today.")
        self.assertEqual(text, "It costs 3.5 euros at 10:30, e.g. today.")

    def test_capital_after_a_sentence_end_and_at_the_start(self):
        text, _ = words.clean_text("the pan is hot. now we cook! really? yes")
        self.assertEqual(text, "The pan is hot. Now we cook! Really? Yes.")

    def test_the_word_i_is_capital(self):
        text, _ = words.clean_text("i think i'm done and i'll go")
        self.assertEqual(text, "I think I'm done and I'll go.")

    def test_end_punctuation_is_added_once(self):
        self.assertEqual(words.clean_text("we are done")[0], "We are done.")
        self.assertEqual(words.clean_text("are we done?")[0], "Are we done?")
        self.assertEqual(words.clean_text("we are done...")[0], "We are done...")
        self.assertEqual(words.clean_text("we are done…")[0], "We are done…")
        self.assertEqual(words.clean_text('he said "go"')[0], 'He said "go".')

    def test_there_is_no_rewording(self):
        text, _ = words.clean_text("gonna put the thing on, okay, yeah")
        self.assertEqual(text, "Gonna put the thing on, okay, yeah.")

    def test_lines_join_and_paragraphs_stay(self):
        text, _ = words.clean_text("line one\n  line two\n\nnew paragraph")
        self.assertEqual(text, "Line one line two.\n\nNew paragraph.")

    def test_empty_text(self):
        self.assertEqual(words.clean_text("  \n ")[0], "")
        self.assertEqual(words.clean_text("um, uh")[0], "")

    def test_clean_text_is_stable(self):
        once, _ = words.clean_text("Um, the pan is hot,uh it is . now")
        twice, _ = words.clean_text(once)
        self.assertEqual(once, twice)


class WrapTests(unittest.TestCase):
    def test_a_short_text_is_one_line(self):
        self.assertEqual(words.wrap("Hello there.", rules()), ["Hello there."])

    def test_a_long_text_is_two_lines_and_bottom_heavy(self):
        text = "The pan is hot enough now so we can add the sausage"      # 51 characters
        lines = words.wrap(text, rules())
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(s) <= 42 for s in lines))
        self.assertGreaterEqual(len(lines[1]), len(lines[0]) - 3)
        self.assertEqual(plain(" ".join(lines)), text)

    def test_the_line_breaks_after_a_comma(self):
        lines = words.wrap("When the pan is hot enough, we add the sausage and wait", rules())
        self.assertEqual(lines[0], "When the pan is hot enough,")

    def test_the_line_does_not_end_on_an_article_or_a_preposition(self):
        text = "He put the pan on the stove and waited for the water to boil in a pot"
        for width in (32, 36, 42):
            lines = words.wrap(text, rules(max_chars=width, max_width=width * 10), max_lines=3)
            self.assertIsNotNone(lines, width)
            for line in lines[:-1]:
                self.assertNotIn(line.split()[-1].lower(), {"the", "a", "an", "on", "for", "to", "in"},
                                 f"{width}: {lines}")

    def test_a_dash_stays_with_the_word_before_it(self):
        lines = words.wrap("We waited a long time for the water — it was cold", rules(max_chars=34))
        for line in lines:
            self.assertFalse(line.startswith("—"), lines)

    def test_the_width_in_pixels_limits_a_line(self):
        # 100 characters would fit, but 10 characters wide is all the picture allows
        lines = words.wrap("alpha beta gamma delta", rules(max_chars=100, max_width=100), max_lines=4)
        self.assertTrue(all(len(s) * 10 <= 100 for s in lines), lines)
        self.assertEqual(" ".join(lines), "alpha beta gamma delta")

    def test_text_that_needs_more_lines_gives_none(self):
        self.assertIsNone(words.wrap("x " * 60, rules(max_chars=20)))

    def test_a_word_wider_than_a_line_is_cut_with_hyphens(self):
        text = "It is Supercalifragilisticexpialidocious really"
        parts = words.tokens(text, rules(max_chars=16))
        self.assertTrue(all(len(words.join_tokens([p])) <= 16 for p in parts))
        self.assertEqual(words.join_tokens(parts).replace("-", ""), text.replace("-", ""))
        cues, _ = words.split_cues(text, rules(max_chars=16))
        self.assertTrue(len(cues) >= 1)
        flat = re.sub(r"[\s-]", "", "".join(cues))
        self.assertEqual(flat, re.sub(r"\s", "", text))
        for c in cues:
            self.assertTrue(all(len(line) <= 16 for line in c.split("\n")), c)


class SplitTests(unittest.TestCase):
    def test_a_short_sentence_is_one_cue(self):
        cues, notes = words.split_cues("Now the pan is hot enough.", rules())
        self.assertEqual(cues, ["Now the pan is hot enough."])

    def test_two_short_sentences_share_a_cue_when_they_fit_one_line(self):
        cues, _ = words.split_cues("Yes. I do.", rules())
        self.assertEqual(cues, ["Yes. I do."])

    def test_two_longer_sentences_are_two_cues(self):
        cues, _ = words.split_cues("The pan is hot enough now. We can add the sausage and wait "
                                   "for it to cook.", rules())
        self.assertEqual(len(cues), 2)
        self.assertTrue(cues[0].startswith("The pan"))

    def test_a_long_sentence_splits_at_a_comma(self):
        text = "It was a quiet morning in the kitchen, and the light came in low over the old wooden table."
        cues, _ = words.split_cues(text, rules())
        self.assertEqual(cues[0], "It was a quiet morning in the kitchen,")
        self.assertEqual(len(cues), 2)

    def test_a_cue_does_not_end_on_an_article_when_there_is_a_better_cut(self):
        text = ("The kitchen was quiet that morning and the light came in low over the table, "
                "so we made coffee first.")
        cues, _ = words.split_cues(text, rules())
        for c in cues:
            self.assertNotIn(plain(c).split()[-1].lower().strip(".,"), {"the", "a", "an", "to", "of"})

    def test_every_cue_fits_and_no_word_is_lost(self):
        text = ("The kitchen was quiet that morning and the light came in low over the table, "
                "so we made coffee first. Then we cut the bread, which was still warm, and "
                "put the butter on the table next to the jam. Nobody talked for a while. "
                "It was one of those days when the only sound is the kettle.")
        for width in (24, 32, 42):
            r = rules(max_chars=width)
            cues, _ = words.split_cues(text, r)
            for c in cues:
                lines = c.split("\n")
                self.assertLessEqual(len(lines), 2, c)
                self.assertTrue(all(len(s) <= width for s in lines), (width, c))
            self.assertEqual(plain(" ".join(cues)), plain(text), width)

    def test_an_abbreviation_does_not_end_a_sentence(self):
        cues, _ = words.split_cues("Mr. Smith is here. He brought the bread.", rules())
        self.assertEqual(plain(cues[0]).split()[:2], ["Mr.", "Smith"])
        self.assertEqual(len(cues), 2)

    def test_a_blank_line_ends_a_cue(self):
        cues, _ = words.split_cues("Yes.\n\nI do.", rules())
        self.assertEqual(cues, ["Yes.", "I do."])

    def test_no_text_gives_no_cue(self):
        self.assertEqual(words.split_cues("", rules())[0], [])

    def test_the_width_in_pixels_is_kept(self):
        r = rules(max_chars=100, max_width=200)             # 20 characters of 10 pixels
        cues, _ = words.split_cues("The pan is hot enough now and we are ready to cook it.", r)
        for c in cues:
            for line in c.split("\n"):
                self.assertLessEqual(len(line) * 10, 200, c)


class SpreadTests(unittest.TestCase):
    def spread(self, texts, scene_s, **kw):
        return words.spread(texts, scene_s, FPS, **kw)

    def test_equal_cues_are_spread_evenly(self):
        spans, report = self.spread(["x" * 30] * 3, 30.0)
        starts = [round(a, 1) for a, _ in spans]
        self.assertEqual(starts[0], 0.0)
        self.assertAlmostEqual(starts[1], 10.0, delta=0.1)
        self.assertAlmostEqual(starts[2], 20.0, delta=0.15)
        self.assertFalse(report["overflow"])
        self.assertTrue(all(b - a <= config.SUBTITLE_MAX_SEC + 1e-6 for a, b in spans))

    def test_longer_text_gets_a_longer_share(self):
        spans, _ = self.spread(["x" * 20, "x" * 40, "x" * 20], 40.0)
        starts = [a for a, _ in spans]
        self.assertAlmostEqual(starts[1] - starts[0], 10.0, delta=0.2)
        self.assertAlmostEqual(starts[2] - starts[1], 20.0, delta=0.2)

    def test_a_cue_lasts_at_least_the_minimum_where_the_scene_allows(self):
        spans, report = self.spread(["Hi."] * 5, 30.0)
        self.assertFalse(report["overflow"])
        for a, b in spans:
            self.assertGreaterEqual(b - a, config.SUBTITLE_MIN_SEC - 1e-6)

    def test_reading_speed_sets_the_minimum_of_a_long_cue(self):
        spans, _ = self.spread(["x" * 80, "Hi."], 12.0)
        self.assertGreaterEqual(spans[0][1] - spans[0][0], 80 / config.SUBTITLE_MAX_CPS - 0.05)

    def test_a_cue_lasts_at_most_the_maximum(self):
        spans, _ = self.spread(["Just one short line."], 60.0)
        self.assertEqual(spans[0][0], 0.0)
        self.assertLessEqual(spans[0][1] - spans[0][0], config.SUBTITLE_MAX_SEC + 1e-6)
        self.assertAlmostEqual(spans[0][1] - spans[0][0], config.SUBTITLE_MAX_SEC, delta=0.05)

    def test_the_gap_between_cues_is_kept(self):
        spans, _ = self.spread(["x" * 40] * 6, 30.0)
        for (a0, b0), (a1, b1) in zip(spans, spans[1:]):
            self.assertGreaterEqual(a1 - b0, 2 * float(1 / FPS) - 1e-6)

    def test_text_that_needs_more_time_than_the_scene_has_is_reported_not_dropped(self):
        spans, report = self.spread(["x" * 80] * 20, 30.0)       # needs about 94 s
        self.assertTrue(report["overflow"])
        self.assertGreater(report["needed_s"], 90)
        self.assertEqual(report["available_s"], 30.0)
        self.assertEqual(len(spans), 20)
        self.assertLessEqual(spans[-1][1], 30.0 + 1e-6)
        for (a0, b0), (a1, b1) in zip(spans, spans[1:]):
            self.assertLessEqual(b0, a1 + 1e-9)
            self.assertLess(a0, b0)

    def test_times_are_on_frames(self):
        spans, _ = self.spread(["x" * 33] * 4, 25.0)
        frame = float(1 / FPS)
        for a, b in spans:
            for t in (a, b):
                self.assertAlmostEqual(t / frame, round(t / frame), delta=0.01)

    def test_nothing_to_spread(self):
        spans, report = self.spread([], 10.0)
        self.assertEqual(spans, [])
        self.assertFalse(report["overflow"])


if __name__ == "__main__":
    unittest.main()
