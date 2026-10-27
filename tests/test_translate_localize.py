"""Check 4.19: the translated cues of a film or a short, for burning them in (SPECS 9.9, step 6). The text of a cue in
another language is what `subtitles translate` found, taken from the translation cache without a request: the first
translation, or the shorter one when the follow-up found one that fits the cue better. A film is localized cue by cue; a
short names the cue of the film each of its cues came from (`x_film_cue`) and takes the text from there, and the
pieces that the 9:16 layout made of one cue become one cue again. A cue without a translation is an error: no English
is written into a film of another language."""

import copy
import unittest

import config
import shorts
import translate
import words
from tests.test_shorts import build, make_film
from tests.test_translate_shorten import (DURATION, ENGLISH, JA, JA_SHORT, TH, ZH, FollowUpCase, real_film)
from tests.timeline_fixture import short_example


def flat_srt(path_cues):
    return {c["id"]: c["text"].replace("\n", "") for c in path_cues}


class FinalTextsTests(FollowUpCase):
    def setUp(self):
        super().setUp()
        self.report = self.go(self.tl, "ja,zh-Hans,th")
        self.spans = words.cue_spans(self.tl.data)

    def test_the_final_text_is_what_the_srt_file_has(self):
        for lang in ("ja", "zh-Hans", "th"):
            final = translate.final_texts(self.spans, str(self.work), lang)
            written = [c["text"].replace("\n", "") for c in self.srt(lang)]
            self.assertEqual([final[s["id"]] for s in self.spans], written, lang)

    def test_a_shorter_text_that_fits_better_is_the_final_text_and_the_others_are_the_first_translation(self):
        final = translate.final_texts(self.spans, str(self.work), "ja")
        self.assertEqual(final["c001"], JA_SHORT["c001"])
        self.assertEqual(final["c002"], JA["c002"])
        self.assertEqual(final["c005"], JA["c005"])

    def test_nothing_is_sent_to_the_service(self):
        before = self.server.count
        translate.final_texts(self.spans, str(self.work), "ja")
        translate.final_texts(self.spans, str(self.work), "th")
        self.assertEqual(self.server.count, before)

    def test_a_cue_without_a_translation_is_an_error_that_names_the_cues_and_the_command(self):
        spans = list(self.spans) + [{"id": "c099", "text": "A sentence nobody translated.", "start_s": 1.0,
                                     "end_s": 3.0, "segment": "s001", "cue": {}}]
        with self.assertRaises(translate.TranslateError) as cm:
            translate.final_texts(spans, str(self.work), "ja")
        msg = str(cm.exception)
        self.assertIn("c099", msg)
        self.assertIn("subtitles translate", msg)
        self.assertIn("ja", msg)
        self.assertNotIn("c001", msg)

    def test_another_model_has_no_translations(self):
        with self.assertRaises(translate.TranslateError) as cm:
            translate.final_texts(self.spans, str(self.work), "ja", model="qwen/qwen3.7-flash")
        self.assertIn("qwen/qwen3.7-flash", str(cm.exception))

    def test_the_languages_that_inter_draws_work_too(self):
        self.go(self.tl, "de")
        final = translate.final_texts(self.spans, str(self.work), "de")
        self.assertEqual(len(final), len(self.spans))

    def test_the_language_must_be_one_of_the_six(self):
        with self.assertRaises(translate.TranslateError):
            translate.final_texts(self.spans, str(self.work), "fr")


class LocalizeFilmTests(FollowUpCase):
    def setUp(self):
        super().setUp()
        self.go(self.tl, "ja,th")

    def test_a_copy_with_the_language_and_the_translated_texts(self):
        before = copy.deepcopy(self.tl.data)
        data, notes = translate.localize(self.tl.data, str(self.work), "ja")
        self.assertEqual(self.tl.data, before)                             # the film is not changed
        self.assertEqual(data["subtitles"]["language"], "ja")
        cues = data["subtitles"]["cues"]
        self.assertEqual([c["id"] for c in cues], [c["id"] for c in before["subtitles"]["cues"]])
        final = translate.final_texts(words.cue_spans(self.tl.data), str(self.work), "ja")
        self.assertEqual({c["id"]: c["text"] for c in cues}, final)
        for new, old in zip(cues, before["subtitles"]["cues"]):
            self.assertEqual((new["anchor"], new["start_s"], new["end_s"]), (old["anchor"], old["start_s"], old["end_s"]))
        self.assertEqual(notes, [])

    def test_a_cue_on_a_scene_that_is_switched_off_is_not_in_the_copy(self):
        self.tl.data["segments"][0]["enabled"] = False
        data, _ = translate.localize(self.tl.data, str(self.work), "ja")
        self.assertTrue(all(c["anchor"] != "s001" for c in data["subtitles"]["cues"]))
        self.assertTrue(data["subtitles"]["cues"])

    def test_english_is_the_film_itself(self):
        data, _ = translate.localize(self.tl.data, str(self.work), "en")
        self.assertEqual(data, self.tl.data)

    def test_a_language_that_was_not_translated_is_an_error(self):
        with self.assertRaises(translate.TranslateError) as cm:
            translate.localize(self.tl.data, str(self.work), "zh-Hans")
        self.assertIn("subtitles translate", str(cm.exception))


class LocalizeShortTests(FollowUpCase):
    """A short of the film: its cues name the cue of the film they came from."""

    def setUp(self):
        super().setUp()
        self.go(self.tl, "ja")
        self.film = self.tl.data
        self.short = short_example()
        self.short["subtitles"]["cues"] = [
            {"id": "c001", "anchor": "s001", "start_s": 0.5, "end_s": 1.7, "text": "She holds a hand blender", "origin": "text",
             "x_film_cue": "c001"},
            {"id": "c002", "anchor": "s001", "start_s": 1.8, "end_s": 3.4, "text": "in a tall jar.", "origin": "text",
             "x_film_cue": "c001"},
            {"id": "c003", "anchor": "s002", "start_s": 0.2, "end_s": 2.0, "text": ENGLISH["c002"], "origin": "text",
             "x_film_cue": "c002"},
        ]

    def test_the_pieces_of_one_film_cue_become_one_cue_with_the_translation_of_the_film_cue(self):
        data, notes = translate.localize(self.short, str(self.work), "ja", film=self.film)
        cues = data["subtitles"]["cues"]
        self.assertEqual(len(cues), 2)
        final = translate.final_texts(words.cue_spans(self.film), str(self.work), "ja")
        self.assertEqual(cues[0]["text"], final["c001"])
        self.assertEqual((cues[0]["start_s"], cues[0]["end_s"]), (0.5, 3.4))
        self.assertEqual(cues[0]["id"], "c001")
        self.assertEqual(cues[1]["text"], final["c002"])
        self.assertEqual(data["subtitles"]["language"], "ja")
        self.assertEqual(self.short["subtitles"]["cues"][0]["text"], "She holds a hand blender")   # the plan is not changed

    def test_the_film_is_read_from_the_file_the_short_was_made_from_when_none_is_given(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "film.json")
            self.tl.save(path)
            self.short["derived_from"]["film"] = path
            data, _ = translate.localize(self.short, str(self.work), "ja")
        self.assertEqual(len(data["subtitles"]["cues"]), 2)

    def test_a_missing_film_file_is_an_error_that_names_it(self):
        self.short["derived_from"]["film"] = "/nonexistent/film.json"
        with self.assertRaises(translate.TranslateError) as cm:
            translate.localize(self.short, str(self.work), "ja")
        self.assertIn("/nonexistent/film.json", str(cm.exception))

    def test_a_cue_that_names_no_film_cue_cannot_be_translated(self):
        del self.short["subtitles"]["cues"][2]["x_film_cue"]
        with self.assertRaises(translate.TranslateError) as cm:
            translate.localize(self.short, str(self.work), "ja", film=self.film)
        self.assertIn("c003", str(cm.exception))
        self.assertIn("x_film_cue", str(cm.exception))

    def test_a_film_cue_that_is_not_in_the_film_is_an_error(self):
        self.short["subtitles"]["cues"][2]["x_film_cue"] = "c777"
        with self.assertRaises(translate.TranslateError) as cm:
            translate.localize(self.short, str(self.work), "ja", film=self.film)
        self.assertIn("c777", str(cm.exception))


class ShortPlanNamesTheFilmCueTests(unittest.TestCase):
    def test_every_cue_of_a_short_names_the_cue_of_the_film_it_came_from(self):
        text = "Now the pan is hot enough, so we add the sausage and wait for it to cook through."
        cue = {"id": "c007", "anchor": "s001", "start_s": 10.0, "end_s": 14.0, "text": text, "origin": "text"}
        plan = build(make_film([30], cues=[cue]), length_s=30)
        cues = plan.data["subtitles"]["cues"]
        self.assertGreater(len(cues), 1)                                   # split into pieces for the narrow frame
        self.assertEqual({c["x_film_cue"] for c in cues}, {"c007"})
        # the key is free (x_), so the file is still valid
        import timeline
        errors = [f for f in timeline.validate(plan.data, file_checks=False) if f.level == "error"]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
