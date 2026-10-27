"""Check 4.15 (SPECS 9.10): reading the answer of the model, saving the answers that cannot be read, and trying a
rate-limited request again. In the first real run of the follow-up request (2026-10-27, `google/gemini-2.5-flash-lite`)
the one request for the 8 Japanese cues that did not fit failed on all 3 tries with "the answer is not JSON", and the
request for one Portuguese cue with "HTTP 429 Provider returned error"; the text of the Japanese answers was not kept.

- `parse_answer` takes the one JSON object the prompt asks for, also when it is not the whole text (a fence among prose,
  a sentence before or after), when a text holds a line break (strict JSON forbids it), and in the shapes the tool
  itself sends (an object that wraps the object, a list of {"id", "translation"}). It does not guess: a cut-off
  answer, a trailing comma, single quotes, a missing id, an id that was not asked for, an empty text are errors.
- `subtitles translate --save-raw DIR` writes the text of every answer that cannot be read to a file in DIR (never
  a header, never the key) and the warning or the error names the files.
- The follow-up request is tried again after an HTTP 429 with the same waits as the first request, 3 tries.
Fake service on a local port; no real OpenRouter call."""

import json
import os
import unittest
from pathlib import Path

import config
import project
import translate
from tests.fake_openrouter import FakeOpenRouter
from tests.test_translate import KEY, TranslateCase, run
from tests.test_translate_shorten import (ENGLISH, JA, JA_MISFIT, JA_SHORT, FollowUpCase, real_film)

IDS = ["c001", "c004"]
GOOD = {"c001": "短い", "c004": "もっと短い"}
GOOD_TEXT = json.dumps(GOOD, ensure_ascii=False)


class ParseTests(unittest.TestCase):
    def read(self, content, ids=IDS):
        return translate.parse_answer(content, ids)

    def refused(self, content, ids=IDS):
        with self.assertRaises(translate._Retry) as ctx:
            translate.parse_answer(content, ids)
        return str(ctx.exception)

    def test_the_object_alone_and_in_a_fence_still_work(self):
        self.assertEqual(self.read(GOOD_TEXT), GOOD)
        self.assertEqual(self.read("```json\n" + GOOD_TEXT + "\n```"), GOOD)
        self.assertEqual(self.read("```\n" + GOOD_TEXT + "\n```"), GOOD)

    def test_a_fence_that_is_not_the_whole_text(self):
        self.assertEqual(self.read("Here you go:\n```json\n" + GOOD_TEXT + "\n```\nTell me if you want more."), GOOD)
        self.assertEqual(self.read("```json\n" + GOOD_TEXT + "\n```\nNote: both are shorter."), GOOD)

    def test_prose_before_and_after_one_object(self):
        self.assertEqual(self.read("Here are the shorter translations:\n" + GOOD_TEXT + "\nI hope this helps."), GOOD)
        self.assertEqual(self.read("Sure! " + GOOD_TEXT), GOOD)
        self.assertEqual(self.read(GOOD_TEXT + "\n\nThe first one is 2 characters."), GOOD)

    def test_a_line_break_inside_a_text_is_taken_and_flattened(self):
        content = '{"c001": "短\nい", "c004": "もっと\tも短い"}'
        self.assertEqual(self.read(content), {"c001": "短 い", "c004": "もっと も短い"})

    def test_a_decoy_object_before_the_answer_is_passed_over(self):
        self.assertEqual(self.read('The format is {"id": "text"}. Answer:\n' + GOOD_TEXT), GOOD)
        self.assertEqual(self.read("{}\n" + GOOD_TEXT), GOOD)

    def test_the_first_object_that_fits_is_taken(self):
        other = json.dumps({"c001": "別", "c004": "別2"}, ensure_ascii=False)
        self.assertEqual(self.read(GOOD_TEXT + "\nor\n" + other), GOOD)

    def test_the_shapes_the_tool_sends_itself(self):
        self.assertEqual(self.read(json.dumps({"shorten": GOOD}, ensure_ascii=False)), GOOD)         # an object around it
        self.assertEqual(self.read(json.dumps({"translations": GOOD}, ensure_ascii=False)), GOOD)
        as_list = [{"id": k, "translation": v} for k, v in GOOD.items()]
        self.assertEqual(self.read(json.dumps(as_list, ensure_ascii=False)), GOOD)                   # the item as sent
        self.assertEqual(self.read(json.dumps({"shorten": as_list}, ensure_ascii=False)), GOOD)
        as_text = [{"id": k, "text": v} for k, v in GOOD.items()]
        self.assertEqual(self.read(json.dumps(as_text, ensure_ascii=False)), GOOD)                   # the first request's item

    def test_a_list_is_refused_when_it_is_not_clear(self):
        both = [{"id": "c001", "text": "a", "translation": "b"}, {"id": "c004", "translation": "c"}]
        self.assertIn("not clear", self.refused(json.dumps(both)))
        twice = [{"id": "c001", "translation": "a"}, {"id": "c001", "translation": "b"}]
        self.assertIn("more than once", self.refused(json.dumps(twice)))
        self.assertIn("misses", self.refused(json.dumps([{"id": "c001", "translation": "a"}])))
        self.assertIn("empty or not a text", self.refused(json.dumps(
            [{"id": "c001", "translation": "a"}, {"id": "c004", "translation": " "}])))

    def test_what_cannot_be_read_without_guessing(self):
        self.assertEqual(self.refused(GOOD_TEXT[: len(GOOD_TEXT) // 2]), "the answer is not JSON")      # cut off
        self.assertEqual(self.refused('{"c001": "a", "c004": "b",}'), "the answer is not JSON")           # trailing comma
        self.assertEqual(self.refused("{'c001': 'a', 'c004': 'b'}"), "the answer is not JSON")            # single quotes
        self.assertEqual(self.refused("I cannot help with that"), "the answer is not JSON")
        self.assertEqual(self.refused(""), "the answer is not JSON")

    def test_wrong_content_in_an_object_that_can_be_read(self):
        self.assertIn("misses ['c004']", self.refused(json.dumps({"c001": "a"})))
        self.assertIn("c999", self.refused(json.dumps({"c001": "a", "c004": "b", "c999": "c"})))
        self.assertIn("empty or not a text", self.refused(json.dumps({"c001": "a", "c004": 5})))
        self.assertIn("empty or not a text", self.refused(json.dumps({"c001": "a", "c004": "  "})))
        self.assertIn("not a JSON object", self.refused(json.dumps(["a", "b"])))
        self.assertIn("not a JSON object", self.refused("5"))

    def test_an_answer_without_text(self):
        self.assertEqual(self.refused(None), "the answer has no text")
        self.assertEqual(self.refused(["a"]), "the answer has no text")

    def test_the_reason_is_that_of_the_first_object_when_none_fits(self):
        reason = self.refused('{"c001": "a"} and then {"c004": "b"}')
        self.assertIn("misses ['c004']", reason)


class PromptTests(unittest.TestCase):
    """The prompt asks for exactly what the parser reads."""

    def items(self):
        return [{"id": "c001", "english": "She holds a hand blender.", "translation": "彼女は持っている。", "max_chars": 8},
                {"id": "c004", "english": "She pours it.", "translation": "彼女は注ぐ。", "max_chars": 6}]

    def test_the_follow_up_asks_for_an_object_of_ids_and_texts_and_shows_it(self):
        system = translate.build_shorten_messages("ja", self.items())[0]["content"]
        self.assertIn("one JSON object", system)
        self.assertIn('{"c001": "<shorter translation>", "c004": "<shorter translation>"}', system)    # the ids of this request
        example = system[system.index('{"c001"'):system.index("}", system.index('{"c001"')) + 1]
        self.assertEqual(sorted(translate.parse_answer(example, ["c001", "c004"])), ["c001", "c004"])

    def test_the_two_requests_ask_for_the_same_shape_of_answer(self):
        first = translate.build_messages("ja", [{"id": "c001", "text": "x"}], [], [])[0]["content"]
        second = translate.build_shorten_messages("ja", self.items())[0]["content"]
        for system in (first, second):
            self.assertIn("one JSON object", system)
            self.assertIn("maps every id", system)
            self.assertIn("as a string", system)


class FollowUpAnswerTests(FollowUpCase):
    """A follow-up whose answer is wrapped or spoiled in a way the parser takes is one request, and it works."""

    def test_the_answers_that_can_be_read(self):
        for behaviour in ("prose", "fenced_prose", "controlchar", "echo_list", "fenced"):
            with self.subTest(behaviour):
                self.server.requests.clear()
                self.server.script = ["ok", behaviour]
                cache = self.work / "translations" / "cache.json"
                if cache.exists():
                    cache.unlink()
                report = self.go(self.tl, "ja")
                self.assertEqual(self.server.count, 2, behaviour)                                 # no second try
                self.assertEqual(report["follow_up_requests"], 1, behaviour)
                self.assertIn(JA_SHORT["c011"], {c["text"].replace("\n", "") for c in self.srt("ja")}, behaviour)
                self.assertEqual(report["warnings"], [], behaviour)

    def test_a_cut_off_answer_fails_after_the_tries_and_keeps_the_first_text(self):
        self.server.script = ["ok", "truncated", "truncated", "truncated"]
        report = self.go(self.tl, "ja")
        self.assertEqual(self.server.count, 1 + config.TRANSLATE_TRIES)
        self.assertEqual(report["follow_up_requests"], config.TRANSLATE_TRIES)
        message = [w for w in report["warnings"] if w.startswith("ja c001: no shorter")][0]
        self.assertIn(f"after {config.TRANSLATE_TRIES} tries", message)
        self.assertIn("the answer is not JSON", message)
        self.assertIn(JA["c001"], {c["text"].replace("\n", "") for c in self.srt("ja")})


class SaveRawTests(FollowUpCase):
    def setUp(self):
        super().setUp()
        self.raw = self.root / "raw"

    def files(self):
        return sorted(self.raw.iterdir()) if self.raw.exists() else []

    def test_nothing_is_saved_without_the_option(self):
        self.server.script = ["ok", "truncated", "truncated", "truncated"]
        self.go(self.tl, "ja")
        self.assertFalse(self.raw.exists())

    def test_every_answer_that_cannot_be_read_is_saved_and_named(self):
        self.server.script = ["ok", "truncated", "truncated", "truncated"]
        report = self.go(self.tl, "ja", save_raw=str(self.raw))
        saved = self.files()
        self.assertEqual(len(saved), config.TRANSLATE_TRIES)
        # the content of each file is the text of the answer as the service sent it (the first answer was the translation)
        self.assertEqual(sorted(p.read_text(encoding="utf-8") for p in saved), sorted(self.server.contents[1:]))
        for p in saved:
            self.assertIn("ja-shorten-c001-c011", p.name)
        line = [w for w in report["warnings"] if w.startswith("ja: the follow-up request for 8 cue(s) failed")]
        self.assertEqual(len(line), 1, report["warnings"])                         # one line for the request, not one per cue
        for p in saved:
            self.assertIn(str(p), line[0])
        self.assertNotIn(str(saved[0]), [w for w in report["warnings"] if w.startswith("ja c001:")][0])
        self.assertEqual(len(report["raw_answers"]), config.TRANSLATE_TRIES)
        entry = report["raw_answers"][0]
        self.assertEqual((entry["lang"], entry["kind"], entry["reason"]), ("ja", "shorten", "the answer is not JSON"))
        self.assertEqual(entry["ids"], JA_MISFIT)

    def test_an_answer_that_was_unusable_once_and_then_worked_is_saved_too(self):
        self.server.script = ["ok", "garbage", "ok"]
        report = self.go(self.tl, "ja", save_raw=str(self.raw))
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(self.files()[0].read_text(encoding="utf-8"), "I cannot help with that {")
        self.assertEqual(report["warnings"], [])
        self.assertEqual(len(report["raw_answers"]), 1)

    def test_an_answer_with_the_wrong_ids_is_saved(self):
        self.server.script = ["ok", "missing", "missing", "missing"]
        self.go(self.tl, "ja", save_raw=str(self.raw))
        self.assertEqual(len(self.files()), config.TRANSLATE_TRIES)
        left = json.loads(self.files()[0].read_text(encoding="utf-8"))
        self.assertEqual(len(left), len(JA_MISFIT) - 1)                            # the answer lacks one id

    def test_the_first_request_too_and_the_error_names_the_files(self):
        self.server.script = ["garbage"] * config.TRANSLATE_TRIES
        with self.assertRaises(translate.TranslateError) as ctx:
            self.go(self.tl, "ja", save_raw=str(self.raw))
        saved = self.files()
        self.assertEqual(len(saved), config.TRANSLATE_TRIES)
        for p in saved:
            self.assertIn("ja-translate-c001-c011", p.name)
            self.assertIn(str(p), str(ctx.exception))

    def test_the_key_is_never_in_a_saved_file(self):
        self.server.script = ["ok", "garbage_key", "garbage_key", "garbage_key"]
        report = self.go(self.tl, "ja", save_raw=str(self.raw))
        saved = self.files()
        self.assertEqual(len(saved), config.TRANSLATE_TRIES)
        for p in saved:
            self.assertNotIn(KEY, p.read_text(encoding="utf-8"))
            self.assertIn("***", p.read_text(encoding="utf-8"))
        self.assertNotIn(KEY, json.dumps(report))
        self.assertNotIn(KEY, (self.work / "reports" / "translate-report.json").read_text(encoding="utf-8"))

    def test_only_the_text_of_the_answer_is_saved(self):
        self.server.script = ["ok", "truncated", "truncated", "truncated"]
        self.go(self.tl, "ja", save_raw=str(self.raw))
        for p in self.files():
            text = p.read_text(encoding="utf-8")
            for forbidden in ("Authorization", "Bearer", "usage", "prompt_tokens", "choices", "\"model\""):
                self.assertNotIn(forbidden, text)


class SaveRawCommandTests(FollowUpCase):
    def setUp(self):
        super().setUp()
        self.path = self.root / "timeline.json"
        self.tl.save(str(self.path))
        self.raw = self.root / "raw"

    def command(self, *extra):
        return run(["subtitles", "translate", "--file", str(self.path), "--work", str(self.work), "--lang", "ja", *extra])

    def test_the_option_saves_and_the_command_says_where(self):
        self.server.script = ["ok", "truncated", "truncated", "truncated"]
        code, out, err = self.command("--save-raw", str(self.raw))
        self.assertEqual(code, 0, err + out)
        self.assertEqual(len(list(self.raw.iterdir())), config.TRANSLATE_TRIES)
        self.assertIn("3 answer(s) of the model that could not be read are saved", out)
        self.assertIn(str(self.raw), out)
        self.assertNotIn(KEY, out + err)

    def test_without_the_option_nothing_is_saved_and_nothing_is_said(self):
        self.server.script = ["ok", "truncated", "truncated", "truncated"]
        code, out, err = self.command()
        self.assertEqual(code, 0, err + out)
        self.assertNotIn("could not be read are saved", out)
        self.assertFalse(self.raw.exists())

    def test_a_folder_that_holds_originals_is_refused(self):
        originals = self.root / "originals"
        originals.mkdir()
        proj = project.Project(video_dir=str(originals), audio_dir=str(originals))
        project_file = self.root / "project.json"
        proj.save(str(project_file))
        code, out, err = run(["--project", str(project_file), "subtitles", "translate", "--file", str(self.path),
                              "--work", str(self.work), "--lang", "ja", "--save-raw", str(originals / "raw")])
        self.assertEqual(code, 1)
        self.assertIn("original", err)
        self.assertFalse((originals / "raw").exists())
        self.assertEqual(self.server.count, 0)


class RateLimitTests(FollowUpCase):
    """HTTP 429 is tried again for the follow-up as for the first request: the same waits, 3 tries."""

    def setUp(self):
        super().setUp()
        self.waits = []
        translate.time.sleep = self.waits.append               # TranslateCase restores the real one afterwards

    def test_the_first_request_waits_and_tries_again(self):
        self.server.script = ["http429", "http429"]
        self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, list(config.TRANSLATE_RETRY_WAIT_SEC))
        self.assertEqual(self.server.count, 3)

    def test_the_follow_up_waits_and_tries_again_the_same_way(self):
        self.server.script = ["ok", "http429", "http429", "ok"]
        report = self.go(self.tl, "ja")
        self.assertEqual(self.waits, list(config.TRANSLATE_RETRY_WAIT_SEC))
        self.assertEqual(self.server.count, 4)
        self.assertEqual(report["follow_up_requests"], 3)
        self.assertIn(JA_SHORT["c001"], {c["text"].replace("\n", "") for c in self.srt("ja")})
        self.assertEqual(report["warnings"], [])

    def test_a_follow_up_that_stays_rate_limited_fails_after_three_tries_and_says_so(self):
        self.server.script = ["ok", "http429", "http429", "http429"]
        report = self.go(self.tl, "ja")
        self.assertEqual(self.server.count, 1 + config.TRANSLATE_TRIES)
        self.assertEqual(self.waits, list(config.TRANSLATE_RETRY_WAIT_SEC))          # no wait after the last try
        message = [w for w in report["warnings"] if w.startswith("ja c001: no shorter")][0]
        self.assertIn(f"after {config.TRANSLATE_TRIES} tries", message)
        self.assertIn("HTTP 429", message)
        self.assertIn(JA["c001"], {c["text"].replace("\n", "") for c in self.srt("ja")})

    def test_a_rate_limited_follow_up_does_not_stop_the_other_languages(self):
        self.server.script = ["ok", "http429", "http429", "http429"]
        report = self.go(self.tl, "ja,zh-Hans")
        self.assertTrue((self.out.parent / "film.zh-Hans.srt").is_file())
        self.assertEqual(sorted(report["languages"]), ["ja", "zh-Hans"])


if __name__ == "__main__":
    unittest.main()
