"""Check 4.20: the final film with its subtitles burned in another language (SPECS 9.6, 9.9, step 6). A render takes a
language (`render_export(subtitle_lang=...)`, `render --lang`): the cues are the translations that `subtitles translate`
left in the cache, drawn with the font of the language, into a file of its own (`<film>.ja.mp4`), so the English film
stays. The form must draw something (burned or both). Everything that can go wrong stops the render before it writes
anything: no translation, no font folder, a Pillow without raqm for Thai, a language that is not one of the six. The film
is the synthetic one of test_export; the Noto fonts draw the text."""

import os
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import config
import export
import textlayout
import translate
import words
from tests.fonts import requires_noto
from tests.test_export import ExportFixture, W, H

CUES = [
    {"id": "c001", "anchor": "s001", "start_s": 0.5, "end_s": 1.5, "text": "First scene.", "origin": "manual"},
    {"id": "c002", "anchor": "s002", "start_s": 0.5, "end_s": 1.5, "text": "Second scene with bars.", "origin": "manual"},
    {"id": "c003", "anchor": "s003", "start_s": 0.5, "end_s": 1.5, "text": "Third scene.", "origin": "manual"},
]
JA = {"First scene.": "最初のシーン。", "Second scene with bars.": "黒い帯のある二つ目のシーン。", "Third scene.": "三つ目のシーン。"}
TH = {"First scene.": "ฉากแรก", "Second scene with bars.": "ฉากที่สองมีแถบดำ", "Third scene.": "ฉากที่สาม"}


def put_translations(work, lang, table, model=None):
    model = translate.model_name(model)
    cache = translate.load_cache(str(work))
    for english, text in table.items():
        cache[translate.cache_key(model, lang, english)] = {"lang": lang, "model": model, "text": text}
    translate.save_cache(str(work), cache)


class LangFixture(ExportFixture):
    def setUp(self):
        import shutil
        self.work_dir = self.root / f"work-{self._testMethodName}"
        shutil.rmtree(self.work_dir, ignore_errors=True)
        self.work_dir.mkdir()
        put_translations(self.work_dir, "ja", JA)
        put_translations(self.work_dir, "th", TH)

    def tl(self, form="burned", name=None):
        name = name or f"film-lang-{self._testMethodName[-30:]}"
        entry = self.entry(name=name)
        entry["subtitles"] = form
        tl = self.make_tl(exports=[entry], loud=False)
        tl.data["subtitles"] = {"language": "en", "cues": [dict(c) for c in CUES]}
        return tl, name

    def base(self, form="none"):
        """The same film without a translation, under another name."""
        return self.run_export(*self.tl(form, name="base-" + self._testMethodName[-25:]), work=self.work_dir)

    def go(self, form="burned", lang="ja", **kw):
        tl, name = self.tl(form)
        return self.run_export(tl, name, work=self.work_dir, subtitle_lang=lang, **kw), tl, name

    def luma(self, path):
        return np.array([f[0] for f in self.frames_of(path)]).astype(int)

    def shown(self, d, frame, threshold=40, minimum=20):
        return int((d[frame] > threshold).sum()) >= minimum


class NamesTests(unittest.TestCase):
    def test_the_file_of_a_language_has_the_code_before_the_extension(self):
        self.assertEqual(export.lang_output("/x/film.mp4", "ja"), "/x/film.ja.mp4")
        self.assertEqual(export.lang_output("/x/film.mov", "zh-Hans"), "/x/film.zh-Hans.mov")
        self.assertEqual(export.lang_output("/x/film.mp4", "en"), "/x/film.mp4")
        self.assertEqual(export.lang_output("/x/film.mp4", None), "/x/film.mp4")


class RefusalTests(LangFixture):
    """Nothing is written when the render cannot be done."""

    def nothing_written(self, name):
        out = self.out
        left = sorted(p.name for p in out.iterdir()) if out.exists() else []
        self.assertFalse([n for n in left if name in n], left)

    def test_a_form_that_draws_nothing_is_refused(self):
        for form in ("srt", "none"):
            tl, name = self.tl(form)
            with self.assertRaises(export.ExportError) as cm:
                self.run_export(tl, name, work=self.work_dir, subtitle_lang="ja")
            self.assertIn("burned", str(cm.exception))
            self.assertIn("--lang", str(cm.exception))
            self.nothing_written(name)

    def test_a_language_that_is_not_one_of_the_six_is_refused(self):
        tl, name = self.tl()
        with self.assertRaises(export.ExportError) as cm:
            self.run_export(tl, name, work=self.work_dir, subtitle_lang="fr")
        self.assertIn("fr", str(cm.exception))
        self.nothing_written(name)

    def test_a_cue_without_a_translation_is_refused_and_names_the_command(self):
        tl, name = self.tl()
        tl.data["subtitles"]["cues"].append({"id": "c004", "anchor": "s003", "start_s": 1.6, "end_s": 2.2,
                                             "text": "Not translated.", "origin": "manual"})
        with self.assertRaises(export.ExportError) as cm:
            self.run_export(tl, name, work=self.work_dir, subtitle_lang="ja")
        self.assertIn("c004", str(cm.exception))
        self.assertIn("subtitles translate", str(cm.exception))
        self.nothing_written(name)

    def test_without_the_noto_folder_the_message_names_the_variable(self):
        tl, name = self.tl()
        with mock.patch.dict(os.environ, {config.TEXT_NOTO_DIR_ENV: ""}):
            with self.assertRaises(export.ExportError) as cm:
                self.run_export(tl, name, work=self.work_dir, subtitle_lang="ja")
        self.assertIn("VET_NOTO_FONT_DIR", str(cm.exception))
        self.nothing_written(name)

    @requires_noto
    def test_thai_without_raqm_is_refused_before_anything_is_written(self):
        tl, name = self.tl()
        with mock.patch.object(textlayout, "_raqm_available", lambda: False):
            with self.assertRaises(export.ExportError) as cm:
                self.run_export(tl, name, work=self.work_dir, subtitle_lang="th")
        self.assertIn("raqm", str(cm.exception))
        self.nothing_written(name)


@requires_noto
class BurnedLanguageTests(LangFixture):
    def test_the_translation_is_drawn_on_its_frames_into_a_file_of_its_own(self):
        base = self.base()
        result, tl, name = self.go("burned", "ja")
        self.assertTrue(result.ok, result.problems)
        self.assertEqual(result.path, str(self.out / f"{name}.ja.mp4"))
        self.assertTrue(os.path.isfile(result.path))
        self.assertFalse(os.path.exists(str(self.out / f"{name}.mp4")))
        self.assertEqual(result.frames, 180)
        d = np.abs(self.luma(result.path) - self.luma(base.path))
        self.assertFalse(self.shown(d, 14))
        self.assertTrue(self.shown(d, 15))
        self.assertTrue(self.shown(d, 44))
        self.assertFalse(self.shown(d, 45))
        self.assertTrue(self.shown(d, 60 + 20))
        self.assertTrue(self.shown(d, 120 + 20))
        self.assertIsNone(result.srt_file)

    def test_the_japanese_picture_is_not_the_english_one(self):
        english = self.base("burned")
        ja, tl, name = self.go("burned", "ja")
        self.assertNotEqual(self.luma(english.path)[20].tolist(), self.luma(ja.path)[20].tolist())

    def test_both_writes_the_srt_of_the_language_with_the_translated_text(self):
        result, tl, name = self.go("both", "ja")
        self.assertTrue(result.ok)
        self.assertEqual(result.srt_file, str(self.out / f"{name}.ja.srt"))
        parsed = words.parse_srt(Path(result.srt_file).read_text(encoding="utf-8"))
        self.assertEqual([c["text"].replace("\n", "") for c in parsed], list(JA.values()))
        self.assertAlmostEqual(parsed[0]["start_s"], 0.5, delta=0.04)

    def test_thai_is_drawn_with_the_thai_font(self):
        result, tl, name = self.go("burned", "th")
        self.assertTrue(result.ok, result.problems)
        self.assertEqual(result.path, str(self.out / f"{name}.th.mp4"))
        base = self.base()
        d = np.abs(self.luma(result.path) - self.luma(base.path))
        self.assertTrue(self.shown(d, 20, minimum=10))

    def test_the_english_cues_and_the_film_are_not_changed(self):
        result, tl, name = self.go("burned", "ja")
        self.assertEqual(tl.data["subtitles"]["language"], "en")
        self.assertEqual([c["text"] for c in tl.data["subtitles"]["cues"]], [c["text"] for c in CUES])

    def test_the_report_names_the_language_and_the_file(self):
        result, tl, name = self.go("burned", "ja")
        row = export.latest_render(str(self.work_dir), name)
        self.assertEqual(row["language"], "ja")
        self.assertEqual(row["output"], result.path)

    def test_the_credit_file_names_the_font_and_its_licence(self):
        result, tl, name = self.go("burned", "ja")
        self.assertEqual(result.credit_file, str(self.out / f"{name}.ja.credit.txt"))
        text = Path(result.credit_file).read_text(encoding="utf-8")
        self.assertIn("Noto Sans JP", text)
        self.assertIn("https://github.com/notofonts/noto-cjk", text)
        self.assertIn("SIL Open Font License 1.1", text)
        self.assertNotIn("Inter", text)

    def test_the_credit_of_the_lut_comes_first_and_the_fonts_after_it(self):
        tl, name = self.tl("burned")
        tl.data["look"]["lut"] = {"path": str(self.luts / "identity.cube"), "interp": "tetrahedral",
                                  "sha256": __import__("hashlib").sha256((self.luts / "identity.cube").read_bytes()).hexdigest(),
                                  "credit": "LUTs: [slay](https://example.org/x) (CC BY 4.0)"}
        result = self.run_export(tl, name, work=self.work_dir, subtitle_lang="th")
        lines = Path(result.credit_file).read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "LUTs: slay (https://example.org/x) (CC BY 4.0)")
        self.assertIn("Noto Sans Thai", lines[1])
        self.assertEqual(len(lines), 2)

    def test_english_text_in_the_film_names_inter(self):
        from tests.fonts import builtin_font
        builtin_font(self)
        tl, name = self.tl("burned", name="english-credit")
        result = self.run_export(tl, name, work=self.work_dir)
        self.assertTrue(result.ok)
        text = Path(result.credit_file).read_text(encoding="utf-8")
        self.assertIn("Inter", text)
        self.assertNotIn("Noto", text)

    def test_english_as_the_language_is_the_ordinary_render(self):
        result, tl, name = self.go("burned", "en")
        self.assertEqual(result.path, str(self.out / f"{name}.mp4"))


if __name__ == "__main__":
    unittest.main()
