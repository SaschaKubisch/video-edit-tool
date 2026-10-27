"""Check 4.19: the follow-up request for a shorter translation (SPECS 9.9). A translated cue that does not fit in two
lines at phrase boundaries, or is read faster than its language allows, is sent once more with a stated maximum
number of characters: the smaller of (2 lines x the line limit) and (reading speed x the cue's duration). The shorter
text is kept when it fits better; otherwise the first translation stays and the warning stays. The follow-up is counted
in the tokens and the cost and is cached under a key of its own. A fake OpenRouter on a local port answers; the
strings are those of the first real translation of 11 cues (2026-10-27), see tests/test_linebreak.py."""

import json
import os
import unittest

import config
import main
import translate
import words
from tests.fake_openrouter import FakeOpenRouter
from tests.test_linebreak import REAL
from tests.test_translate import KEY, TranslateCase, film, run
from tests.timeline_fixture import example
import timeline

ENGLISH = {row[0]: " ".join(row[1].split()) for row in REAL}
DURATION = {row[0]: row[2] for row in REAL}
JA = {row[0]: row[3] for row in REAL}
ZH = {row[0]: row[4] for row in REAL}
TH = {row[0]: row[5] for row in REAL}
PLACED = {"s001": ["c001", "c002", "c003"], "s002": ["c004", "c005", "c006", "c007", "c008"],
          "s003": ["c009", "c010", "c011"]}

# What a model that obeys the limit might answer for the Japanese cues that do not fit.
JA_SHORT = {
    "c001": "彼女はハンドブレンダーを持っている。",
    "c004": "彼女はドレッシングをティーポットに注ぐ。",
    "c006": "テーブルに皿とスプーンとフォーク。",
    "c007": "手がサラダボウルを置く。",
    "c008": "丸い木のトレイがある。",
    "c009": "赤いブレンダーが回る。",
    "c010": "蓋に手を添える。",
    "c011": "水のグラスがある。",
}
JA_MISFIT = ["c001", "c004", "c006", "c007", "c008", "c009", "c010", "c011"]       # in film order


def real_film(output):
    """The 11 cues of the real run, with their real durations, on the scenes s001 to s003 of the example."""
    d = example()
    d["subtitles"]["cues"] = []
    for anchor, ids in PLACED.items():
        at = 0.0
        for cid in ids:
            d["subtitles"]["cues"].append({"id": cid, "anchor": anchor, "start_s": round(at, 4),
                                           "end_s": round(at + DURATION[cid], 4), "text": ENGLISH[cid],
                                           "origin": "manual"})
            at += DURATION[cid] + 0.1
    d["exports"][0]["output"] = output
    return timeline.Timeline(d)


class MaxCharsTests(unittest.TestCase):
    def test_the_smaller_of_the_lines_and_the_reading_speed(self):
        self.assertEqual(translate.max_chars_for("ja", 6.9736), 26)          # 2 x 13 = 26; 4 x 6.97 = 27.9
        self.assertEqual(translate.max_chars_for("ja", 2.7694), 11)          # 4 x 2.77 = 11.08
        self.assertEqual(translate.max_chars_for("ja", 60.0), 26)
        self.assertEqual(translate.max_chars_for("zh-Hans", 3.5702), 32)     # 2 x 16 = 32; 9 x 3.57 = 32.13
        self.assertEqual(translate.max_chars_for("zh-Hans", 2.0), 18)
        self.assertEqual(translate.max_chars_for("th", 2.0688), 35)          # 17 x 2.07 = 35.17
        self.assertEqual(translate.max_chars_for("de", 0.3), 5)              # 17 x 0.3 = 5.1
        self.assertEqual(translate.max_chars_for("pt-BR", 10.0), 84)         # 2 x 42


class AssessTests(unittest.TestCase):
    def test_a_translation_that_fits_has_no_problem(self):
        self.assertEqual(translate.assess(ZH["c001"], "zh-Hans", DURATION["c001"])["problems"], [])
        self.assertEqual(translate.assess(JA["c003"], "ja", DURATION["c003"])["problems"], [])
        self.assertEqual(translate.assess(TH["c004"], "th", DURATION["c004"])["problems"], [])

    def test_three_lines_at_phrase_boundaries_are_a_problem(self):
        found = translate.assess(JA["c001"], "ja", DURATION["c001"])          # 25 wide: no cut makes two lines of 13
        self.assertEqual(found["problems"], ["lines"])
        self.assertEqual(len(found["lines"]), 3)

    def test_reading_faster_than_the_language_allows_is_a_problem(self):
        found = translate.assess(JA["c011"], "ja", DURATION["c011"])          # 23 characters in 2.77 s
        self.assertEqual(found["problems"], ["speed"])
        self.assertAlmostEqual(found["speed"], 23 / 2.7694, places=3)

    def test_the_speed_does_not_count_the_space_between_lines(self):
        found = translate.assess(JA["c004"], "ja", DURATION["c004"])
        self.assertAlmostEqual(found["speed"], 34 / 6.9736, places=3)          # the old report said 5.0

    def test_both_problems_at_once(self):
        found = translate.assess(JA["c009"], "ja", DURATION["c009"])
        self.assertEqual(found["problems"], ["lines", "speed"])

    def test_a_phrase_cut_inside_is_a_problem(self):
        found = translate.assess("x" * 60, "ja", 30.0)          # 30 wide, no place to cut: three lines, cut by the count
        self.assertEqual(found["problems"], ["lines", "split"])

    def test_better_means_fewer_problems_then_less_excess(self):
        first = translate.assess(JA["c010"], "ja", DURATION["c010"])          # 14 wide in 2.07 s: 6.8 characters a second
        less = translate.assess("彼女は蓋に手を添えている。", "ja", DURATION["c010"])    # 13: 6.3 characters a second
        none = translate.assess(JA_SHORT["c010"], "ja", DURATION["c010"])
        self.assertTrue(translate.better(less, first))
        self.assertTrue(translate.better(none, less))
        self.assertFalse(translate.better(first, first))                       # equal: the first stays


class FollowUpCase(TranslateCase):
    def setUp(self):
        super().setUp()
        self.server.translations = {("ja", ENGLISH[c]): JA[c] for c in ENGLISH}
        self.server.translations.update({("zh-Hans", ENGLISH[c]): ZH[c] for c in ENGLISH})
        self.server.translations.update({("th", ENGLISH[c]): TH[c] for c in ENGLISH})
        self.server.shorten_mode = "given"
        self.server.shortenings = {("ja", ENGLISH[c]): t for c, t in JA_SHORT.items()}
        self.tl = real_film(str(self.out))

    def follow_ups(self):
        out = []
        for r in self.server.requests:
            user = json.loads(r["body"]["messages"][-1]["content"])
            if "shorten" in user:
                out.append((r, user))
        return out

    def srt_lines(self, lang):
        return {c["text"].replace("\n", "") for c in self.srt(lang)}


class FollowUpTests(FollowUpCase):
    def test_cues_that_fit_get_no_follow_up(self):
        self.go(self.tl, "zh-Hans,th")
        self.assertEqual(self.follow_ups(), [])
        self.assertEqual(self.server.count, 2)                                  # one batch of 11 cues for each language

    def test_one_follow_up_request_asks_for_the_cues_that_do_not_fit(self):
        report = self.go(self.tl, "ja")
        self.assertEqual(self.server.count, 2)                                  # the translation and one follow-up
        (request, user), = self.follow_ups()
        self.assertEqual([item["id"] for item in user["shorten"]], JA_MISFIT)
        self.assertEqual(report["follow_up_requests"], 1)
        self.assertEqual(report["follow_up_cues"], len(JA_MISFIT))

    def test_what_is_asked(self):
        self.go(self.tl, "ja")
        (request, user), = self.follow_ups()
        body = request["body"]
        self.assertTrue(request["auth_ok"])
        self.assertEqual(body["model"], "google/gemini-2.5-flash-lite")
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        system = body["messages"][0]["content"]
        self.assertIn("Japanese", system)
        self.assertIn("max_chars", system)
        self.assertIn("JSON", system)
        self.assertEqual(user["target_language"], "ja")
        by_id = {item["id"]: item for item in user["shorten"]}
        self.assertEqual(by_id["c001"], {"id": "c001", "english": ENGLISH["c001"], "translation": JA["c001"],
                                         "max_chars": 26})
        self.assertEqual(by_id["c006"]["max_chars"], 20)                       # 4 x 5.07 = 20.3
        self.assertEqual(by_id["c010"]["max_chars"], 8)                        # 4 x 2.07 = 8.3
        self.assertEqual(by_id["c011"]["max_chars"], 11)

    def test_the_shorter_text_is_written_when_it_fits_better(self):
        report = self.go(self.tl, "ja")
        written = self.srt_lines("ja")
        for cid in JA_MISFIT:
            self.assertIn(JA_SHORT[cid], written, cid)
        for cid in ("c002", "c003", "c005"):
            self.assertIn(JA[cid], written, cid)                               # these fitted: not asked, not changed
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["languages"]["ja"]["warnings"], 0)
        self.assertEqual(sorted(s["id"] for s in report["shortened"]), JA_MISFIT)
        first = [s for s in report["shortened"] if s["id"] == "c001"][0]
        self.assertEqual((first["lang"], first["before"], first["after"]), ("ja", JA["c001"], JA_SHORT["c001"]))

    def test_the_times_do_not_change(self):
        self.go(self.tl, "ja")
        english = self.srt("en")
        for a, b in zip(english, self.srt("ja")):
            self.assertAlmostEqual(a["start_s"], b["start_s"], places=3)
            self.assertAlmostEqual(a["end_s"], b["end_s"], places=3)

    def test_a_model_that_cannot_shorten_leaves_the_first_text_and_the_warning(self):
        self.server.shorten_mode = "same"
        report = self.go(self.tl, "ja")
        self.assertIn(JA["c001"], self.srt_lines("ja"))
        self.assertIn("ja c001: 3 lines", report["warnings"])
        self.assertTrue(any(w.startswith("ja c011:") and "per second" in w for w in report["warnings"]))
        self.assertEqual(report["shortened"], [])

    def test_a_longer_answer_is_not_kept(self):
        self.server.shorten_mode = "longer"
        report = self.go(self.tl, "ja")
        self.assertNotIn(JA["c011"] + "ですよ", self.srt_lines("ja"))
        self.assertIn(JA["c011"], self.srt_lines("ja"))
        self.assertEqual(report["shortened"], [])
        self.assertIn("ja c001: 3 lines", report["warnings"])

    def test_a_text_that_is_better_but_not_good_is_kept_and_still_warned(self):
        self.server.shortenings = {("ja", ENGLISH["c010"]): "彼女は蓋に手を添えている。"}      # 13 wide: 6.3 a second
        report = self.go(self.tl, "ja")
        self.assertIn("彼女は蓋に手を添えている。", self.srt_lines("ja"))
        self.assertTrue(any(w.startswith("ja c010:") and "per second" in w for w in report["warnings"]))

    def test_the_other_languages_are_asked_too(self):
        self.server.translations[("de", ENGLISH["c010"])] = "Sie hält eine Hand auf dem Deckel, ganz ruhig und still"
        report = self.go(self.tl, "de")
        (request, user), = self.follow_ups()
        self.assertEqual([item["id"] for item in user["shorten"]], ["c010"])       # 55 characters in 2.07 s: 26.6 a second
        self.assertEqual(user["shorten"][0]["max_chars"], 35)                      # 17 x 2.07 = 35.2
        self.assertIn("Sie hält eine Hand auf dem Deckel,", self.srt_lines("de"))   # the fake cut it at 35 characters
        self.assertEqual(report["warnings"], [])

    def test_no_shorten_sends_no_follow_up(self):
        report = self.go(self.tl, "ja", shorten=False)
        self.assertEqual(self.follow_ups(), [])
        self.assertEqual(self.server.count, 1)
        self.assertIn("ja c001: 3 lines", report["warnings"])
        self.assertEqual(report["follow_up_requests"], 0)

    def test_the_command_has_no_shorten(self):
        path = self.root / "timeline.json"
        self.tl.save(str(path))
        code, out, err = run(["subtitles", "translate", "--file", str(path), "--work", str(self.work),
                              "--lang", "ja", "--no-shorten"])
        self.assertEqual(code, 0, err + out)
        self.assertEqual(self.follow_ups(), [])
        code, out, err = run(["subtitles", "translate", "--file", str(path), "--work", str(self.work),
                              "--lang", "ja"])
        self.assertEqual(code, 0, err + out)
        self.assertEqual(len(self.follow_ups()), 1)
        self.assertIn("shorter", out)

    def test_a_dry_run_sends_nothing(self):
        self.go(self.tl, "ja", dry_run=True)
        self.assertEqual(self.server.count, 0)


class ReportTests(FollowUpCase):
    def test_the_follow_up_is_in_the_tokens_and_the_cost(self):
        report = self.go(self.tl, "ja")
        self.assertEqual(len(self.server.usage), 2)
        self.assertEqual(report["tokens_in"], sum(u[0] for u in self.server.usage))
        self.assertEqual(report["tokens_out"], sum(u[1] for u in self.server.usage))
        price = config.TRANSLATE_PRICES["google/gemini-2.5-flash-lite"]
        self.assertAlmostEqual(report["cost_usd"],
                               round((report["tokens_in"] * price[0] + report["tokens_out"] * price[1]) / 1e6, 5), places=6)
        saved = json.loads((self.work / "reports" / "translate-report.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["follow_up_requests"], 1)
        self.assertEqual(saved["shortened"][0]["id"], "c001")

    def test_the_command_prints_what_was_shortened(self):
        path = self.root / "timeline.json"
        self.tl.save(str(path))
        code, out, err = run(["subtitles", "translate", "--file", str(path), "--work", str(self.work),
                              "--lang", "ja"])
        self.assertEqual(code, 0, err + out)
        self.assertIn("8 cue(s) asked for a shorter text", out)
        self.assertIn("ja c001", out)


class CacheTests(FollowUpCase):
    def cache(self):
        return json.loads((self.work / "translations" / "cache.json").read_text(encoding="utf-8"))["entries"]

    def test_the_long_text_stays_under_its_own_key_and_the_short_one_has_another(self):
        self.go(self.tl, "ja")
        entries = self.cache()
        model = "google/gemini-2.5-flash-lite"
        long = entries[translate.cache_key(model, "ja", ENGLISH["c001"])]
        short = entries[translate.short_cache_key(model, "ja", ENGLISH["c001"], 26)]
        self.assertEqual(long["text"], JA["c001"])
        self.assertEqual(short["text"], JA_SHORT["c001"])
        self.assertEqual(short["max_chars"], 26)
        self.assertNotEqual(translate.cache_key(model, "ja", ENGLISH["c001"]),
                            translate.short_cache_key(model, "ja", ENGLISH["c001"], 26))
        self.assertNotEqual(translate.short_cache_key(model, "ja", ENGLISH["c001"], 26),
                            translate.short_cache_key(model, "ja", ENGLISH["c001"], 20))

    def test_a_second_run_sends_nothing_and_writes_the_same_files(self):
        self.go(self.tl, "ja")
        first = (self.out.parent / "film.ja.srt").read_bytes()
        count = self.server.count
        report = self.go(self.tl, "ja")
        self.assertEqual(self.server.count, count)
        self.assertEqual((self.out.parent / "film.ja.srt").read_bytes(), first)
        self.assertEqual(sorted(s["id"] for s in report["shortened"]), JA_MISFIT)      # still said, from the cache
        self.assertEqual(report["follow_up_requests"], 0)

    def test_a_rerun_does_not_take_the_long_text_for_a_cue_that_was_shortened(self):
        self.go(self.tl, "ja")
        self.go(self.tl, "ja")
        self.assertIn(JA_SHORT["c001"], self.srt_lines("ja"))
        self.assertNotIn(JA["c001"], self.srt_lines("ja"))

    def test_another_duration_asks_again_for_that_cue_only(self):
        self.go(self.tl, "ja")
        before = self.server.count
        cue = [c for c in self.tl.data["subtitles"]["cues"] if c["id"] == "c001"][0]
        cue["end_s"] = round(cue["start_s"] + 5.0, 4)                           # 4 x 5.0 = 20 characters now
        self.go(self.tl, "ja")
        self.assertEqual(self.server.count, before + 1)
        (request, user) = self.follow_ups()[-1]
        self.assertEqual([(i["id"], i["max_chars"]) for i in user["shorten"]], [("c001", 20)])

    def test_a_follow_up_that_failed_is_not_cached_and_is_tried_again_next_time(self):
        self.server.script = ["ok"] + ["http500"] * config.TRANSLATE_TRIES
        report = self.go(self.tl, "ja")
        self.assertEqual(self.server.count, 1 + config.TRANSLATE_TRIES)
        self.assertIn(JA["c001"], self.srt_lines("ja"))                            # the first translation is written
        self.assertTrue(any("shorter" in w and "c001" in w for w in report["warnings"]), report["warnings"])
        self.assertIn("ja c001: 3 lines", report["warnings"])
        count = self.server.count
        self.go(self.tl, "ja")
        self.assertEqual(self.server.count, count + 1)                              # asked again, now answered
        self.assertIn(JA_SHORT["c001"], self.srt_lines("ja"))


class RetryTests(FollowUpCase):
    def test_an_unusable_answer_to_the_follow_up_is_tried_again(self):
        self.server.script = ["ok", "garbage", "ok"]
        report = self.go(self.tl, "ja")
        self.assertEqual(self.server.count, 3)
        self.assertIn(JA_SHORT["c001"], self.srt_lines("ja"))
        self.assertEqual(report["follow_up_requests"], 2)                           # both requests are counted
        self.assertEqual(report["tokens_in"], sum(u[0] for u in self.server.usage))     # also the unusable answer

    def test_a_rejected_key_stops_the_run(self):
        self.server.script = ["ok", "http401"]
        with self.assertRaises(translate.TranslateError) as ctx:
            self.go(self.tl, "ja")
        self.assertIn("rejected", str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
