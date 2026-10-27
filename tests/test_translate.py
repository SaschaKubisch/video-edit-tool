"""Check 4.17: translating the subtitles through OpenRouter (SPECS 9.9). A fake OpenRouter on a local port
answers; the key is a made-up one. The tests check what is asked, what is written (one SRT file per language with
the times of the English cues), the cache, the retries, the refusal to guess, and that the key never
reaches a file or a message. One test with the real service runs only when OPENROUTER_API_KEY is set or
VET_REAL_OPENROUTER=1 (then the key is read from the keychain, tests/test_translate_keychain.py)."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import config
import main
import textlayout
import timeline
import translate
import words
from tests.fake_openrouter import FakeOpenRouter
from tests.timeline_fixture import example

KEY = "sk-or-test-key-abc123"


def film(n_cues=3, output=None):
    d = example()
    d["subtitles"]["cues"] = []
    for i in range(n_cues):
        d["subtitles"]["cues"].append({
            "id": f"c{i + 1:03d}", "anchor": "s002" if i % 2 else "s001", "start_s": 1.0 + 4 * (i // 2),
            "end_s": 3.5 + 4 * (i // 2), "text": f"Line number {i + 1} of the film.", "origin": "manual"})
    if output:
        d["exports"][0]["output"] = output
    return timeline.Timeline(d)


class TranslateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.work = self.root / "work"
        self.work.mkdir()
        self.out = self.root / "renders" / "film.mp4"
        self.server = FakeOpenRouter(KEY)
        self.addCleanup(self.server.stop)
        self._old = {k: os.environ.get(k) for k in ("OPENROUTER_API_KEY", "VET_OPENROUTER_URL", "VET_TRANSLATE_MODEL")}
        os.environ["OPENROUTER_API_KEY"] = KEY
        os.environ["VET_OPENROUTER_URL"] = self.server.url
        os.environ.pop("VET_TRANSLATE_MODEL", None)
        self.addCleanup(self._restore)
        self._factory = words.RULES_FACTORY
        words.RULES_FACTORY = lambda data: textlayout.char_rules(42)
        self.addCleanup(setattr, words, "RULES_FACTORY", self._factory)
        self._sleep = translate.time.sleep
        translate.time.sleep = lambda s: None               # no waiting between tries
        self.addCleanup(setattr, translate.time, "sleep", self._sleep)

    def _restore(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def go(self, tl=None, langs=None, **kw):
        tl = tl or film(output=str(self.out))
        langs = translate.parse_languages(langs)
        entry = translate.pick_export(tl.data, None)
        return translate.translate_film(tl, str(self.work), entry, langs, **kw)

    def srt(self, lang):
        return words.parse_srt((self.out.parent / f"film.{lang}.srt").read_text(encoding="utf-8"))

    def everything_written(self):
        text = ""
        for p in self.root.rglob("*"):
            if p.is_file() and p.stat().st_size < 5_000_000:
                text += p.read_text(errors="replace") if p.suffix in (".json", ".srt", ".txt", ".log") else ""
        return text


class SettingsTests(TranslateCase):
    def test_the_default_is_the_six_languages_of_the_owner(self):
        self.assertEqual(translate.parse_languages(None), ["pt-BR", "zh-Hans", "th", "de", "ja", "ru"])
        self.assertEqual(translate.parse_languages(""), ["pt-BR", "zh-Hans", "th", "de", "ja", "ru"])

    def test_a_list_of_codes(self):
        self.assertEqual(translate.parse_languages("de, ja"), ["de", "ja"])
        self.assertEqual(translate.parse_languages(["pt-br", "ZH-HANS"]), ["pt-BR", "zh-Hans"])
        with self.assertRaises(translate.TranslateError) as ctx:
            translate.parse_languages("de,fr")
        self.assertIn("fr", str(ctx.exception))

    def test_the_model_is_a_setting(self):
        self.assertEqual(translate.model_name(), "google/gemini-2.5-flash-lite")
        os.environ["VET_TRANSLATE_MODEL"] = "qwen/qwen3.7-flash"
        self.assertEqual(translate.model_name(), "qwen/qwen3.7-flash")
        self.assertEqual(translate.model_name("anthropic/claude-haiku-5.5"), "anthropic/claude-haiku-5.5")

    def test_without_a_key_nothing_is_sent_and_the_error_says_where_to_set_it(self):
        os.environ.pop("OPENROUTER_API_KEY")
        with self.assertRaises(translate.TranslateError) as ctx:
            self.go()
        self.assertIn("OPENROUTER_API_KEY", str(ctx.exception))
        self.assertEqual(self.server.count, 0)
        with self.assertRaises(translate.TranslateError):
            translate.check_ready()

    def test_pick_the_export(self):
        d = film(output=str(self.out)).data
        self.assertEqual(translate.pick_export(d, None)["name"], "film-4k")
        self.assertEqual(translate.pick_export(d, "film-master")["name"], "film-master")
        with self.assertRaises(translate.TranslateError):
            translate.pick_export(d, "nope")
        d["exports"] = []
        with self.assertRaises(translate.TranslateError):
            translate.pick_export(d, None)


class FilesTests(TranslateCase):
    def test_one_srt_file_per_language_and_the_english_one(self):
        report = self.go()
        names = sorted(p.name for p in self.out.parent.iterdir())
        self.assertEqual(names, ["film.de.srt", "film.en.srt", "film.ja.srt", "film.pt-BR.srt", "film.ru.srt",
                                 "film.th.srt", "film.zh-Hans.srt"])
        self.assertEqual(sorted(report["written"]), sorted(str(self.out.parent / n) for n in names))

    def test_the_times_are_those_of_the_english_cues(self):
        self.go()
        english = self.srt("en")
        for lang in config.TRANSLATE_LANGUAGES:
            got = self.srt(lang)
            self.assertEqual(len(got), len(english), lang)
            for a, b in zip(english, got):
                self.assertAlmostEqual(a["start_s"], b["start_s"], places=3)
                self.assertAlmostEqual(a["end_s"], b["end_s"], places=3)
        self.assertIn("[pt-BR] Line number 1 of the film.", self.srt("pt-BR")[0]["text"].replace("\n", " "))
        self.assertEqual(english[0]["text"], "Line number 1 of the film.")

    def test_one_translated_cue_for_each_english_cue_in_film_order(self):
        tl = film(5, output=str(self.out))
        self.go(tl, "de")
        texts = [c["text"].replace("\n", " ") for c in self.srt("de")]
        self.assertEqual(texts, [f"[de] Line number {n} of the film." for n in (1, 3, 5, 2, 4)])

    def test_a_cue_on_a_scene_that_is_switched_off_is_not_translated(self):
        tl = film(3, output=str(self.out))
        tl.data["segments"][1]["enabled"] = False
        report = self.go(tl, "de")
        self.assertEqual(len(self.srt("de")), 2)
        sent = [c["id"] for r in self.server.requests for c in json.loads(r["body"]["messages"][-1]["content"])["translate"]]
        self.assertNotIn("c002", sent)

    def test_a_film_without_subtitles_is_an_error(self):
        tl = film(0, output=str(self.out))
        with self.assertRaises(translate.TranslateError):
            self.go(tl)
        self.assertEqual(self.server.count, 0)


class RequestTests(TranslateCase):
    def test_what_is_asked(self):
        self.go(film(3, output=str(self.out)), "ja")
        r = self.server.requests[0]
        self.assertTrue(r["auth_ok"])
        body = r["body"]
        self.assertEqual(body["model"], "google/gemini-2.5-flash-lite")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        system = body["messages"][0]["content"]
        self.assertIn("Japanese", system)
        self.assertIn("JSON", system)
        user = json.loads(body["messages"][-1]["content"])
        self.assertEqual(user["target_language"], "ja")
        self.assertEqual([c["id"] for c in user["translate"]], ["c001", "c003", "c002"])      # in film order
        self.assertEqual(user["translate"][0]["text"], "Line number 1 of the film.")

    def test_cues_go_in_batches_with_context(self):
        tl = film(70, output=str(self.out))
        tl.data["subtitles"]["cues"] = [
            {"id": f"c{i + 1:03d}", "anchor": "s001", "start_s": i * 0.4, "end_s": i * 0.4 + 0.3,
             "text": f"Cue {i + 1}.", "origin": "manual"} for i in range(70)]
        self.go(tl, "de")
        self.assertEqual(self.server.count, 3)                                  # 30 + 30 + 10
        sizes = [len(json.loads(r["body"]["messages"][-1]["content"])["translate"]) for r in self.server.requests]
        self.assertEqual(sizes, [30, 30, 10])
        second = json.loads(self.server.requests[1]["body"]["messages"][-1]["content"])
        self.assertEqual([c["id"] for c in second["context_before"]], ["c028", "c029", "c030"])
        self.assertEqual([c["id"] for c in second["context_after"]], ["c061", "c062", "c063"])
        first = json.loads(self.server.requests[0]["body"]["messages"][-1]["content"])
        self.assertEqual(first["context_before"], [])

    def test_the_model_of_the_command_line_is_used(self):
        self.go(film(2, output=str(self.out)), "de", model="qwen/qwen3.7-flash")
        self.assertEqual(self.server.requests[0]["body"]["model"], "qwen/qwen3.7-flash")

    def test_the_key_is_in_no_file_and_no_message(self):
        report = self.go()
        everything = self.everything_written() + json.dumps(report)
        self.assertNotIn(KEY, everything)

    def test_a_rejected_key_stops_at_once_and_does_not_show_the_key(self):
        os.environ["OPENROUTER_API_KEY"] = "sk-or-wrong-key"
        with self.assertRaises(translate.TranslateError) as ctx:
            self.go()
        self.assertEqual(self.server.count, 1)                                  # no retries for a rejected key
        self.assertIn("rejected", str(ctx.exception))
        self.assertNotIn("sk-or-wrong-key", str(ctx.exception))
        self.assertEqual(list(self.out.parent.glob("*.srt")) if self.out.parent.exists() else [], [])


class CacheTests(TranslateCase):
    def test_a_second_run_sends_nothing(self):
        self.go()
        first = self.server.count
        report = self.go()
        self.assertEqual(self.server.count, first)
        self.assertEqual(report["sent_cues"], 0)
        self.assertEqual(report["cached_cues"], 3 * 6)
        self.assertTrue((self.out.parent / "film.ja.srt").is_file())

    def test_after_an_edit_only_the_changed_cue_is_sent(self):
        tl = film(3, output=str(self.out))
        self.go(tl, "de")
        before = self.server.count
        tl.data["subtitles"]["cues"][1]["text"] = "A changed line."
        report = self.go(tl, "de")
        self.assertEqual(self.server.count, before + 1)
        user = json.loads(self.server.requests[-1]["body"]["messages"][-1]["content"])
        self.assertEqual([c["id"] for c in user["translate"]], ["c002"])
        self.assertEqual(report["sent_cues"], 1)
        self.assertIn("[de] A changed line.", self.srt("de")[1]["text"] + self.srt("de")[2]["text"] + self.srt("de")[0]["text"])

    def test_another_model_translates_again(self):
        self.go(film(2, output=str(self.out)), "de")
        before = self.server.count
        self.go(film(2, output=str(self.out)), "de", model="qwen/qwen3.7-flash")
        self.assertEqual(self.server.count, before + 1)

    def test_another_language_translates_only_that_language(self):
        self.go(film(2, output=str(self.out)), "de")
        before = self.server.count
        self.go(film(2, output=str(self.out)), "de,ru")
        self.assertEqual(self.server.count, before + 1)

    def test_the_cache_holds_no_key(self):
        self.go()
        cache = (self.work / "translations" / "cache.json").read_text()
        self.assertNotIn(KEY, cache)
        self.assertIn("google/gemini-2.5-flash-lite", cache)


class RetryTests(TranslateCase):
    def test_a_server_error_is_tried_again(self):
        self.server.script = ["http500", "http429"]
        self.go(film(2, output=str(self.out)), "de")
        self.assertEqual(self.server.count, 3)
        self.assertEqual(len(self.srt("de")), 2)

    def test_an_answer_that_misses_an_id_is_tried_again(self):
        for behaviour in ("missing", "extra", "empty", "number", "garbage"):
            with self.subTest(behaviour):
                self.server.requests.clear()
                self.server.script = [behaviour]
                os.remove(self.work / "translations" / "cache.json") if (self.work / "translations" / "cache.json").exists() else None
                self.go(film(2, output=str(self.out)), "de")
                self.assertEqual(self.server.count, 2, behaviour)
                self.assertEqual(len(self.srt("de")), 2)

    def test_a_json_answer_in_a_markdown_fence_is_accepted(self):
        self.server.script = ["fenced"]
        self.go(film(2, output=str(self.out)), "de")
        self.assertEqual(self.server.count, 1)

    def test_three_bad_answers_stop_the_run_and_name_the_cues(self):
        self.server.script = ["missing", "missing", "missing"]
        with self.assertRaises(translate.TranslateError) as ctx:
            self.go(film(2, output=str(self.out)), "de")
        self.assertEqual(self.server.count, config.TRANSLATE_TRIES)
        self.assertIn("c001", str(ctx.exception))
        self.assertIn("de", str(ctx.exception))
        self.assertFalse((self.out.parent / "film.de.srt").exists())             # no English in a German file

    def test_work_done_before_a_failure_is_kept(self):
        tl = film(70, output=str(self.out))
        tl.data["subtitles"]["cues"] = [
            {"id": f"c{i + 1:03d}", "anchor": "s001", "start_s": i * 0.4, "end_s": i * 0.4 + 0.3,
             "text": f"Cue {i + 1}.", "origin": "manual"} for i in range(70)]
        self.server.script = ["ok", "http500", "http500", "http500"]
        with self.assertRaises(translate.TranslateError):
            self.go(tl, "de")
        before = self.server.count
        self.go(tl, "de")
        self.assertEqual(self.server.count - before, 2)                           # the first 30 cues were kept


class WrapTests(unittest.TestCase):
    def test_visual_length(self):
        self.assertEqual(translate.visual_len("Guten Tag", "de"), 9)
        self.assertEqual(translate.visual_len("你好，世界", "zh-Hans"), 5)
        self.assertEqual(translate.visual_len("日本語ab", "ja"), 4.0)               # full-width 1, half-width 0.5
        self.assertEqual(translate.visual_len("สวัสดี", "th"), 4)                   # the tone mark and the vowel mark are not counted

    def test_european_languages_wrap_at_42(self):
        text = "Quando a frigideira estiver bem quente, colocamos a linguiça e esperamos"
        lines = translate.wrap_translation(text, "pt-BR")
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(l) <= 42 for l in lines))
        self.assertEqual(" ".join(lines), text)
        self.assertEqual(translate.wrap_translation("Guten Morgen.", "de"), ["Guten Morgen."])

    def test_chinese_wraps_at_16_characters_without_spaces(self):
        text = "锅已经够热了所以我们现在可以放香肠然后等它煎熟"          # 24 characters
        lines = translate.wrap_translation(text, "zh-Hans")
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(translate.visual_len(l, "zh-Hans") <= 16 for l in lines))
        self.assertEqual("".join(lines), text)
        self.assertGreaterEqual(len(lines[1]), len(lines[0]) - 1)                  # bottom-heavy

    def test_chinese_breaks_after_punctuation_and_never_before_a_closing_mark(self):
        text = "锅已经够热了，我们现在放香肠，然后等它煎熟。"
        lines = translate.wrap_translation(text, "zh-Hans")
        self.assertTrue(lines[0].endswith("，"))
        for l in lines[1:]:
            self.assertNotIn(l[0], "，。、！？；：）」』")

    def test_japanese_counts_half_width_as_half(self):
        lines = translate.wrap_translation("フライパンが十分に熱くなったのでソーセージを入れます", "ja")
        self.assertTrue(all(translate.visual_len(l, "ja") <= 13 for l in lines))
        self.assertEqual("".join(lines), "フライパンが十分に熱くなったのでソーセージを入れます")
        one = translate.wrap_translation("Pan 12 ok", "ja")
        self.assertEqual(one, ["Pan 12 ok"])

    def test_thai_breaks_at_spaces_and_not_inside_a_cluster(self):
        text = "กระทะร้อนพอแล้ว เราใส่ไส้กรอกลงไปแล้วรอให้สุก"
        lines = translate.wrap_translation(text, "th")
        self.assertTrue(all(translate.visual_len(l, "th") <= 35 for l in lines), lines)
        self.assertEqual("".join(lines).replace(" ", ""), text.replace(" ", ""))
        long = "ก" * 30 + "เ" + "ก" * 30
        for l in translate.wrap_translation(long, "th"):
            self.assertFalse(l.endswith("เ"))                                      # a leading vowel stays with its consonant
            self.assertNotEqual(l[:1], "ั")

    def test_a_text_that_needs_three_lines_is_kept_and_reported(self):
        lines = translate.wrap_translation("wort " * 40, "de")
        self.assertGreater(len(lines), 2)
        self.assertTrue(all(len(l) <= 42 for l in lines))


class ReportTests(TranslateCase):
    def test_the_report_has_tokens_cost_and_warnings(self):
        report = self.go()
        self.assertEqual(report["model"], "google/gemini-2.5-flash-lite")
        self.assertGreater(report["tokens_in"], 0)
        self.assertGreater(report["tokens_out"], 0)
        self.assertGreater(report["cost_usd"], 0)
        self.assertEqual(set(report["languages"]), set(config.TRANSLATE_LANGUAGES))
        self.assertEqual(report["languages"]["de"]["cues"], 3)
        self.assertTrue((self.work / "reports" / "translate-report.json").is_file())

    def test_reading_speed_over_the_limit_of_a_language_is_a_warning(self):
        tl = film(1, output=str(self.out))
        tl.data["subtitles"]["cues"][0].update(start_s=1.0, end_s=2.0, text="x" * 30)           # 30 characters in 1 s
        report = self.go(tl, "ja,de")
        self.assertTrue(any("ja" in w and "per second" in w for w in report["warnings"]), report["warnings"])

    def test_dry_run_sends_nothing(self):
        report = self.go(dry_run=True)
        self.assertEqual(self.server.count, 0)
        self.assertEqual(report["to_send_cues"], 3 * 6)
        self.assertGreater(report["estimate_usd"], 0)
        self.assertEqual(report["written"], [])

    def test_the_estimate_for_a_whole_film(self):
        small = translate.estimate_cost(100, list(config.TRANSLATE_LANGUAGES), "google/gemini-2.5-flash-lite")
        big = translate.estimate_cost(600, list(config.TRANSLATE_LANGUAGES), "google/gemini-2.5-flash-lite")
        self.assertGreater(big, small * 5)
        self.assertLess(big, 1.0)
        self.assertIsNone(translate.estimate_cost(100, ["de"], "some/unknown-model"))


class PageStateTests(TranslateCase):
    def test_state_without_a_key(self):
        os.environ.pop("OPENROUTER_API_KEY")
        tl = film(3, output=str(self.out))
        state = translate.page_state(tl, str(self.work), tl.data["exports"])
        self.assertFalse(state["key"])
        self.assertEqual(state["model"], "google/gemini-2.5-flash-lite")
        self.assertEqual([l["code"] for l in state["languages"]], list(config.TRANSLATE_LANGUAGES))
        self.assertNotIn(KEY, json.dumps(state))

    def test_state_after_a_run(self):
        tl = film(3, output=str(self.out))
        self.go(tl, "de,ja")
        state = translate.page_state(tl, str(self.work), tl.data["exports"])
        self.assertTrue(state["key"])
        langs = {l["code"]: l for l in state["languages"]}
        self.assertEqual((langs["de"]["translated"], langs["de"]["cues"], langs["de"]["file"]), (3, 3, True))
        self.assertEqual(langs["ru"]["translated"], 0)
        self.assertFalse(langs["ru"]["file"])
        self.assertNotIn(KEY, json.dumps(state))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args = main.build_parser().parse_args(argv)
            args.func(args)
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue(), err.getvalue()


class CommandTests(TranslateCase):
    def setUp(self):
        super().setUp()
        self.path = self.root / "timeline.json"
        film(3, output=str(self.out)).save(str(self.path))

    def test_the_command_writes_the_files(self):
        code, out, err = run(["subtitles", "translate", "--file", str(self.path), "--work", str(self.work),
                              "--lang", "de,ja"])
        self.assertEqual(code, 0, err + out)
        self.assertTrue((self.out.parent / "film.de.srt").is_file())
        self.assertTrue((self.out.parent / "film.en.srt").is_file())
        self.assertIn("film.ja.srt", out)
        self.assertNotIn(KEY, out + err)

    def test_the_command_without_a_key_says_what_to_set(self):
        os.environ.pop("OPENROUTER_API_KEY")
        code, out, err = run(["subtitles", "translate", "--file", str(self.path), "--work", str(self.work)])
        self.assertEqual(code, 1)
        self.assertIn("OPENROUTER_API_KEY", err)
        self.assertEqual(self.server.count, 0)

    def test_dry_run_prints_the_estimate_and_needs_no_key(self):
        os.environ.pop("OPENROUTER_API_KEY")
        code, out, err = run(["subtitles", "translate", "--file", str(self.path), "--work", str(self.work), "--dry-run"])
        self.assertEqual(code, 0, err)
        self.assertIn("cues to send", out)
        self.assertEqual(self.server.count, 0)

    def test_a_failure_exits_1_and_names_the_cues(self):
        self.server.script = ["http500"] * config.TRANSLATE_TRIES
        code, out, err = run(["subtitles", "translate", "--file", str(self.path), "--work", str(self.work),
                              "--lang", "de"])
        self.assertEqual(code, 1)
        self.assertIn("c001", err)


@unittest.skipUnless((os.environ.get("OPENROUTER_API_KEY") or os.environ.get("VET_REAL_OPENROUTER") == "1")
                     and not os.environ.get("VET_OPENROUTER_URL"),
                     "OPENROUTER_API_KEY is not set and VET_REAL_OPENROUTER is not 1 (the key is then read "
                     "from the keychain)")
class RealOpenRouterTests(unittest.TestCase):
    def setUp(self):
        # the test package switches the keychain lookup off; this test is the one that may use it
        old = os.environ.pop(translate.KEYCHAIN_ENV, None)
        self.addCleanup(lambda: os.environ.__setitem__(translate.KEYCHAIN_ENV, old) if old is not None else None)
        translate.forget_key()
        self.addCleanup(translate.forget_key)
        self.assertTrue(translate.has_key(), "no key: set OPENROUTER_API_KEY or add the keychain item")

    def test_three_cues_into_all_six_languages(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "renders" / "film.mp4"
            tl = film(3, output=str(out))
            tl.data["subtitles"]["cues"][0]["text"] = "The pan is hot enough now."
            tl.data["subtitles"]["cues"][1]["text"] = "We add the sausage and wait."
            tl.data["subtitles"]["cues"][2]["text"] = "Breakfast is ready."
            work = Path(tmp) / "work"
            work.mkdir()
            entry = translate.pick_export(tl.data, None)
            report = translate.translate_film(tl, str(work), entry, list(config.TRANSLATE_LANGUAGES))
            print("\n  cost:", report["cost_usd"], "USD,", report["tokens_in"], "tokens in,", report["tokens_out"], "out")
            for lang in config.TRANSLATE_LANGUAGES:
                got = words.parse_srt((out.parent / f"film.{lang}.srt").read_text(encoding="utf-8"))
                self.assertEqual(len(got), 3, lang)
                print("  ", lang, "|", " / ".join(c["text"].replace("\n", " ") for c in got))
                for c in got:
                    self.assertTrue(c["text"].strip())
            self.assertTrue(any("一" <= ch <= "鿿" for c in words.parse_srt(
                (out.parent / "film.zh-Hans.srt").read_text(encoding="utf-8")) for ch in c["text"]))
            self.assertTrue(any("฀" <= ch <= "๿" for c in words.parse_srt(
                (out.parent / "film.th.srt").read_text(encoding="utf-8")) for ch in c["text"]))
            self.assertTrue(any("Ѐ" <= ch <= "ӿ" for c in words.parse_srt(
                (out.parent / "film.ru.srt").read_text(encoding="utf-8")) for ch in c["text"]))


if __name__ == "__main__":
    unittest.main()
