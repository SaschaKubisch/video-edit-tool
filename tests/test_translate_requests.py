"""Check 4.16 (SPECS 9.10): the limits of a request to the model, and the waits between tries (SPECS 9.9).

First real run of the follow-up at a032a47 (2026-10-27): two requests for 9 short cues were billed 1030 tokens in
and 65538 tokens out, about US$ 0.026: the model ran on until its output limit, and the parser found the object
inside the runaway text. The first try of the next run got HTTP 429 on all follow-ups after 3 tries; a run minutes
later worked.

- Every request carries `max_tokens`, sized from the cues in it (`translate.max_tokens_for`).
- An answer that ended at the limit (`finish_reason` "length", `native_finish_reason` "MAX_TOKENS", or as many tokens out as
  `max_tokens`) is a failed try with the reason "the answer was cut off at max_tokens", even when the JSON in it can be
  read. It is tried once more; a second cut-off fails the request. Every try is listed in the report with its tokens.
- HTTP 429 and 503 wait for Retry-After (seconds or an HTTP date, at most 60 s) or, when there is none, 5, 15 and 30 s,
  and make up to 4 tries. Other errors keep the waits of 1 s and 3 s and 3 tries.
Fake service on a local port, a fake clock for the waits; no real OpenRouter call."""

import email.utils
import json
import unittest

import config
import translate
from tests.test_translate import KEY, TranslateCase
from tests.test_translate_shorten import JA, JA_MISFIT, JA_SHORT, FollowUpCase


class MaxTokensTests(unittest.TestCase):
    def test_the_size_grows_with_the_cues_and_has_a_floor(self):
        self.assertEqual(translate.max_tokens_for("translate", 1), config.TRANSLATE_MAX_TOKENS_FLOOR)
        self.assertEqual(translate.max_tokens_for("translate", 11), 128 + 160 * 11)
        self.assertEqual(translate.max_tokens_for("translate", 30), 128 + 160 * 30)
        self.assertEqual(translate.max_tokens_for("shorten", 8), 128 + 100 * 8)
        self.assertEqual(translate.max_tokens_for("shorten", 1), config.TRANSLATE_MAX_TOKENS_FLOOR)

    def test_it_is_far_below_a_runaway_answer(self):
        self.assertLess(translate.max_tokens_for("translate", config.TRANSLATE_BATCH), 8192)


class RequestSizeTests(FollowUpCase):
    def test_every_request_carries_max_tokens_sized_from_its_cues(self):
        self.go(self.tl, "ja")
        first, second = [r["body"] for r in self.server.requests]
        self.assertEqual(first["max_tokens"], 128 + 160 * 11)                      # 11 cues
        self.assertEqual(second["max_tokens"], 128 + 100 * len(JA_MISFIT))         # 8 cues asked again

    def test_the_request_log_lists_every_try_with_its_tokens(self):
        report = self.go(self.tl, "ja")
        log = report["requests"]
        self.assertEqual([(r["lang"], r["kind"], r["cues"], r["try"], r["result"]) for r in log],
                         [("ja", "translate", 11, 1, "ok"), ("ja", "shorten", 8, 1, "ok")])
        self.assertEqual([r["max_tokens"] for r in log], [128 + 160 * 11, 128 + 100 * 8])
        self.assertEqual([(r["tokens_in"], r["tokens_out"]) for r in log], self.server.usage)
        self.assertEqual(sum(r["tokens_out"] for r in log), report["tokens_out"])
        self.assertEqual({r["finish_reason"] for r in log}, {"stop"})
        saved = json.loads((self.work / "reports" / "translate-report.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["requests"], log)

    def test_a_try_that_got_no_answer_is_in_the_log_without_tokens(self):
        self.server.script = ["http500"]
        translate.time.sleep = lambda s: None
        report = self.go(self.tl, "zh-Hans")
        self.assertEqual([(r["try"], r["result"], r["tokens_out"]) for r in report["requests"]],
                         [(1, "HTTP 500 upstream error", 0), (2, "ok", report["requests"][1]["tokens_out"])])


class CutOffTests(FollowUpCase):
    REASON = "the answer was cut off at max_tokens"

    def setUp(self):
        super().setUp()
        translate.time.sleep = lambda s: None

    def test_a_runaway_answer_is_a_failed_try_even_when_the_json_in_it_can_be_read(self):
        self.server.script = ["ok", "length", "ok"]
        report = self.go(self.tl, "ja")
        self.assertEqual(self.server.count, 3)                                    # tried once more
        tries = [r for r in report["requests"] if r["kind"] == "shorten"]
        self.assertEqual([(r["try"], r["result"]) for r in tries], [(1, self.REASON), (2, "ok")])
        self.assertEqual(tries[0]["tokens_out"], 65536)
        self.assertEqual(tries[0]["finish_reason"], "length")
        self.assertEqual(report["warnings"], [])
        self.assertIn(JA_SHORT["c001"], {c["text"].replace("\n", "") for c in self.srt("ja")})

    def test_the_tokens_of_the_runaway_try_are_in_the_totals_and_the_cost(self):
        self.server.script = ["ok", "length", "ok"]
        report = self.go(self.tl, "ja")
        self.assertGreaterEqual(report["tokens_out"], 65536)
        price = config.TRANSLATE_PRICES["google/gemini-2.5-flash-lite"]
        self.assertGreater(report["cost_usd"], 65536 * price[1] / 1e6 - 1e-9)

    def test_a_second_cut_off_ends_the_request_at_once(self):
        self.server.script = ["ok", "length", "length", "ok"]
        report = self.go(self.tl, "ja")
        self.assertEqual(self.server.count, 3)                                    # not a third try
        self.assertEqual(report["follow_up_requests"], 2)
        message = [w for w in report["warnings"] if w.startswith("ja c001: no shorter")][0]
        self.assertIn("after 2 tries", message)
        self.assertIn(self.REASON, message)
        self.assertIn(JA["c001"], {c["text"].replace("\n", "") for c in self.srt("ja")})      # the first text stays

    def test_the_translation_too(self):
        self.server.script = ["length", "length", "ok"]
        with self.assertRaises(translate.TranslateError) as ctx:
            self.go(self.tl, "ja")
        self.assertEqual(self.server.count, 2)
        self.assertIn(self.REASON, str(ctx.exception))
        self.assertIn("2 tries", str(ctx.exception))

    def test_the_other_signs_of_a_cut_off(self):
        for behaviour in ("native_length", "full"):
            with self.subTest(behaviour):
                self.server.requests.clear()
                self.server.script = ["ok", behaviour, behaviour]
                cache = self.work / "translations" / "cache.json"
                if cache.exists():
                    cache.unlink()
                report = self.go(self.tl, "ja")
                self.assertEqual(self.server.count, 3, behaviour)
                self.assertTrue(any(self.REASON in w for w in report["warnings"]), behaviour)

    def test_a_cut_off_answer_is_saved_with_save_raw(self):
        raw = self.root / "raw"
        self.server.script = ["ok", "length", "ok"]
        report = self.go(self.tl, "ja", save_raw=str(raw))
        files = sorted(raw.iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_text(encoding="utf-8"), self.server.contents[1])
        self.assertEqual(report["raw_answers"][0]["reason"], self.REASON)

    def test_a_normal_answer_that_ended_at_stop_is_not_a_cut_off(self):
        report = self.go(self.tl, "ja")
        self.assertEqual(self.server.count, 2)
        self.assertEqual(report["warnings"], [])


class CommandOutputTests(FollowUpCase):
    def test_the_command_lists_the_tries_that_failed_with_their_tokens(self):
        from tests.test_translate import run
        translate.time.sleep = lambda s: None
        path = self.root / "timeline.json"
        self.tl.save(str(path))
        self.server.script = ["ok", "length", "ok"]
        code, out, err = run(["subtitles", "translate", "--file", str(path), "--work", str(self.work), "--lang", "ja"])
        self.assertEqual(code, 0, err + out)
        self.assertIn("1 of 3 tries failed", out)
        self.assertIn("ja shorten try 1: the answer was cut off at max_tokens (65536 tokens out)", out)
        self.assertNotIn(KEY, out + err)

    def test_a_run_without_a_failed_try_says_nothing_about_tries(self):
        from tests.test_translate import run
        path = self.root / "timeline.json"
        self.tl.save(str(path))
        code, out, err = run(["subtitles", "translate", "--file", str(path), "--work", str(self.work), "--lang", "ja"])
        self.assertEqual(code, 0, err + out)
        self.assertNotIn("tries failed", out)


class WaitCase(FollowUpCase):
    """A fake clock: the waits are recorded, not slept."""

    def setUp(self):
        super().setUp()
        self.waits = []
        translate.time.sleep = self.waits.append
        self.now = 1_800_000_000.0                      # 2027-01-15 08:00:00 UTC
        self._old_now = translate._now
        translate._now = lambda: self.now
        self.addCleanup(setattr, translate, "_now", self._old_now)


class RateWaitTests(WaitCase):
    def test_without_retry_after_the_waits_are_5_15_and_30_and_there_are_4_tries(self):
        self.server.script = ["http429", "http429", "http429"]
        self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, [5.0, 15.0, 30.0])
        self.assertEqual(self.server.count, 4)

    def test_four_answers_of_429_end_the_request_without_a_wait_after_the_last(self):
        self.server.script = ["http429"] * 4
        with self.assertRaises(translate.TranslateError) as ctx:
            self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, [5.0, 15.0, 30.0])
        self.assertEqual(self.server.count, 4)
        self.assertIn("4 tries", str(ctx.exception))
        self.assertIn("HTTP 429", str(ctx.exception))

    def test_503_waits_the_same_way(self):
        self.server.script = ["http503", "http503", "http503"]
        self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, [5.0, 15.0, 30.0])
        self.assertEqual(self.server.count, 4)

    def test_retry_after_in_seconds_is_the_wait(self):
        self.server.retry_after = "7"
        self.server.script = ["http429", "http429"]
        self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, [7.0, 7.0])

    def test_retry_after_is_cut_at_60_seconds(self):
        self.server.retry_after = "300"
        self.server.script = ["http503"]
        self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, [60.0])

    def test_retry_after_as_an_http_date(self):
        self.server.retry_after = email.utils.formatdate(self.now + 12, usegmt=True)
        self.server.script = ["http429"]
        self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, [12.0])

    def test_an_http_date_far_ahead_is_cut_and_one_in_the_past_waits_nothing(self):
        self.server.retry_after = email.utils.formatdate(self.now + 3600, usegmt=True)
        self.server.script = ["http429"]
        self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, [60.0])
        self.waits.clear()
        self.server.retry_after = email.utils.formatdate(self.now - 30, usegmt=True)
        self.server.script = ["http429"]
        cache = self.work / "translations" / "cache.json"
        cache.unlink()
        self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, [0.0])

    def test_a_retry_after_that_is_not_a_time_is_ignored(self):
        for value in ("soon", "", "-5", "1e999"):
            with self.subTest(value):
                self.waits.clear()
                self.server.requests.clear()
                self.server.retry_after = value
                self.server.script = ["http429"]
                cache = self.work / "translations" / "cache.json"
                if cache.exists():
                    cache.unlink()
                self.go(self.tl, "zh-Hans")
                self.assertEqual(self.waits, [5.0], value)

    def test_other_errors_keep_their_waits_and_ignore_retry_after(self):
        self.server.retry_after = "30"
        self.server.script = ["http500", "http500"]
        self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, [1.0, 3.0])
        self.assertEqual(self.server.count, 3)

    def test_three_other_errors_still_end_the_request(self):
        self.server.script = ["http500"] * 3
        with self.assertRaises(translate.TranslateError) as ctx:
            self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, [1.0, 3.0])
        self.assertEqual(self.server.count, 3)
        self.assertIn("3 tries", str(ctx.exception))

    def test_an_error_of_another_kind_before_a_429_still_allows_four_tries(self):
        self.server.script = ["http500", "http429", "http429"]
        self.go(self.tl, "zh-Hans")
        self.assertEqual(self.waits, [1.0, 5.0, 15.0])                          # 500 waits 1 s, the first 429 5 s, the next 15 s
        self.assertEqual(self.server.count, 4)

    def test_the_follow_up_waits_the_same_way(self):
        self.server.script = ["ok", "http429", "http429", "http429"]
        report = self.go(self.tl, "ja")
        self.assertEqual(self.waits, [5.0, 15.0, 30.0])
        self.assertEqual(self.server.count, 5)
        self.assertEqual(report["follow_up_requests"], 4)
        self.assertEqual(report["warnings"], [])
        self.assertIn(JA_SHORT["c001"], {c["text"].replace("\n", "") for c in self.srt("ja")})

    def test_a_follow_up_that_stays_limited_fails_after_four_tries_and_says_so(self):
        self.server.script = ["ok"] + ["http429"] * 4
        report = self.go(self.tl, "ja")
        self.assertEqual(self.server.count, 5)
        message = [w for w in report["warnings"] if w.startswith("ja c001: no shorter")][0]
        self.assertIn("after 4 tries", message)
        self.assertIn("HTTP 429", message)
        self.assertEqual(self.waits, [5.0, 15.0, 30.0])

    def test_every_try_of_a_rate_limited_request_is_in_the_report(self):
        self.server.script = ["http429", "http429"]
        report = self.go(self.tl, "zh-Hans")
        self.assertEqual([(r["try"], r["result"]) for r in report["requests"]],
                         [(1, "HTTP 429 rate limited"), (2, "HTTP 429 rate limited"), (3, "ok")])


if __name__ == "__main__":
    unittest.main()
