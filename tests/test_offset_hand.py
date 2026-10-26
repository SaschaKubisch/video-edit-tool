"""The sync offset typed by hand, per clip (SPECS 3.2 `audio_offset_hand_s`, rule V23,
the operations `set_offset` and `reset_offset` of 4.4, the render of 5.4 and 5.5).

The owner decided on 2026-10-26: "Leave them, I set them" (PRD section 3). The offset
from matching stays in `audio_offset_s`; the offset the owner types goes next to it in
`audio_offset_hand_s`. The sound uses the hand offset when there is one. A change of the
offset marks the sound of that clip's segments for a new render, and no picture."""

import copy
import json
import math
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import numpy as np

import edits
import render
import timeline
from tests import media
from tests.test_preview import PreviewFixture, W, H
from tests.timeline_fixture import example

SRC = "A002_01011540_C002"        # the first source of the example: offset 0.276, frames 2915
HAND = "audio_offset_hand_s"
HAND_B = 0.4013     # a 660 Hz sine moved by this much (less the 0.1 s before) changes its phase


def with_hand(value, source=SRC):
    d = example()
    d["sources"][source][HAND] = value
    return d


def rules(findings, level=None):
    return [(f.rule, f.path) for f in findings if level is None or f.level == level]


class TheSoundUsesTheHandOffset(unittest.TestCase):
    def start(self, d, seg="s001"):
        tl = timeline.Timeline(d)
        return tl.source_audio_start(tl.segment(seg))

    def test_without_a_hand_offset_the_offset_from_matching_is_used(self):
        want = round((Fraction(38) * 1001 / 30000 + Fraction("0.276")) * 48000)
        self.assertEqual(self.start(example()), want)

    def test_a_hand_offset_replaces_it(self):
        want = round((Fraction(38) * 1001 / 30000 + Fraction("0.5")) * 48000)
        self.assertEqual(self.start(with_hand(0.5)), want)

    def test_the_hand_offset_is_used_for_every_segment_of_the_clip_and_for_no_other(self):
        d = with_hand(0.5)
        tl = timeline.Timeline(d)
        base = timeline.Timeline(example())
        for seg in ("s001", "s002"):                      # both from the clip
            self.assertNotEqual(tl.source_audio_start(tl.segment(seg)),
                                base.source_audio_start(base.segment(seg)))
        self.assertEqual(tl.source_audio_start(tl.segment("s003")),     # another clip
                         base.source_audio_start(base.segment("s003")))

    def test_effective_offset_is_the_hand_offset_or_the_original(self):
        src = example()["sources"][SRC]
        self.assertEqual(timeline.effective_offset(src), 0.276)
        self.assertEqual(timeline.effective_offset({**src, HAND: -1.25}), -1.25)
        self.assertEqual(timeline.effective_offset({**src, HAND: 0}), 0)        # zero is a value

    def test_a_negative_hand_offset_puts_silence_before_the_sound_and_does_not_crash(self):
        d = with_hand(-2.0)                              # s001 starts at 1.27 s: 0.73 s before the WAV
        tl = timeline.Timeline(d)
        self.assertLess(self.start(d), 0)
        plan = render.make_plan(tl, preview_width=320)
        mix = render.build_mix(tl, plan, lambda path: np.ones((48000 * 120, 2), np.float32) * 0.1)
        self.assertTrue(any("segment s001: the sound starts" in w for w in mix.warnings),
                        mix.warnings)

    def test_an_offset_that_leaves_no_sound_in_the_wav_is_a_warning_and_the_segment_is_silent(self):
        d = with_hand(25.0)
        tl = timeline.Timeline(d)
        plan = render.make_plan(tl, preview_width=320)
        mix = render.build_mix(tl, plan, lambda path: np.ones((48000 * 20, 2), np.float32) * 0.1)
        self.assertTrue(any("segment s001: the WAV holds no sound" in w for w in mix.warnings),
                        mix.warnings)
        self.assertEqual(float(np.abs(mix.samples[: plan.segments[0].sample_length]).max()), 0.0)


class TheFileFormat(unittest.TestCase):
    def test_the_key_stands_right_after_the_offset_from_matching(self):
        text = timeline.dumps(with_hand(0.5))
        a, b = text.index('"audio_offset_s"'), text.index(f'"{HAND}"')
        c = text.index('"audio_drift_ppm"')
        self.assertTrue(a < b < c)

    def test_a_file_with_the_key_is_not_rejected_as_unknown(self):
        self.assertEqual(rules(timeline.validate(with_hand(0.5), file_checks=False)), [])

    def test_the_key_survives_a_save_and_a_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            timeline.Timeline(with_hand(-0.25)).save(str(path))
            self.assertEqual(timeline.Timeline.load(str(path)).data["sources"][SRC][HAND], -0.25)


class RuleV23(unittest.TestCase):
    def check(self, value):
        return timeline.validate(with_hand(value), file_checks=False)

    PATH = f"sources.{SRC}.{HAND}"

    def test_values_in_range_pass_including_negative_ones_and_zero(self):
        for v in (0, 0.0, 0.02, -5.027, 1.282, 30, -30, 29.999):
            with self.subTest(value=v):
                self.assertEqual(rules(self.check(v)), [])

    def test_values_out_of_range_are_an_error_that_names_the_field(self):
        for v in (30.001, -30.001, 1000, -1e9):
            with self.subTest(value=v):
                f = self.check(v)
                self.assertEqual(rules(f, "error"), [("V23", self.PATH)])
                self.assertIn("30", f[0].message)

    def test_a_value_that_is_not_a_finite_number_is_an_error(self):
        for v in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=v):
                self.assertEqual(rules(self.check(v), "error"), [("V23", self.PATH)])

    def test_a_value_of_the_wrong_type_is_an_error_of_rule_v0(self):
        for v in ("0.5", True, None, [0.5], {"s": 0.5}):
            with self.subTest(value=v):
                self.assertEqual(rules(self.check(v), "error"), [("V0", self.PATH)])


class TheWavAfterTheChange(unittest.TestCase):
    """With a hand offset, the check can read the length of the WAV and warn when the
    clip's sound no longer covers a segment. A warning never stops the tool."""

    def check(self, d, seconds=20.0, **kw):
        return timeline.validate(d, file_checks=False, wav_seconds=lambda path: seconds, **kw)

    def warnings(self, findings):
        return [f for f in findings if f.rule == "V23" and f.level == "warning"]

    def test_a_wav_with_no_sound_for_a_segment_gives_a_warning_that_names_the_segment(self):
        # s001 is 1.27 s to 30.03 s of the clip; with +29 s the WAV (20 s) has nothing there
        w = self.warnings(self.check(with_hand(29.0)))
        self.assertEqual({f.path for f in w}, {f"sources.{SRC}.{HAND}"})
        self.assertTrue(any("s001" in f.message and "no sound" in f.message for f in w), w)
        self.assertTrue(any("s002" in f.message and "no sound" in f.message for f in w), w)
        self.assertEqual(rules(self.check(with_hand(29.0)), "error"), [])

    def test_a_wav_that_covers_only_part_of_a_segment_gives_a_warning_with_the_seconds(self):
        w = self.warnings(self.check(with_hand(0.0), seconds=20.0))     # s001 reaches 30 s
        self.assertTrue(any("s001" in f.message and "only" in f.message for f in w), w)

    def test_a_wav_that_covers_the_segments_gives_no_warning(self):
        self.assertEqual(self.warnings(self.check(with_hand(0.5), seconds=200.0)), [])

    def test_only_clips_with_a_hand_offset_are_checked(self):
        # in a 5 s WAV the other clip's s003 (1.0 s to 21.7 s) is not covered either, but
        # that is not a change the owner made
        w = self.warnings(self.check(with_hand(0.5), seconds=5.0))
        self.assertTrue(w)
        self.assertFalse(any("s003" in f.message for f in w), w)
        self.assertEqual(self.warnings(self.check(example(), seconds=1.0)), [])

    def test_a_negative_hand_offset_that_pushes_the_sound_past_the_start_is_a_warning(self):
        w = self.warnings(self.check(with_hand(-3.0), seconds=200.0))   # s001 starts at 1.27 s
        self.assertTrue(any("s001" in f.message and "only" in f.message for f in w), w)

    def test_a_wav_whose_length_cannot_be_read_is_a_warning_not_a_crash(self):
        def broken(path):
            raise RuntimeError("ffprobe failed")
        f = timeline.validate(with_hand(0.5), file_checks=False, wav_seconds=broken)
        w = self.warnings(f)
        self.assertEqual(len(w), 1)
        self.assertIn("cannot", w[0].message)
        self.assertEqual(rules(f, "error"), [])

    def test_without_a_way_to_read_the_wav_nothing_is_checked(self):
        self.assertEqual(self.warnings(timeline.validate(with_hand(29.0), file_checks=False)), [])

    def test_a_disabled_segment_is_not_checked(self):
        d = with_hand(29.0)
        for s in d["segments"]:
            if s["source"] == SRC:
                s["enabled"] = False
        self.assertEqual(self.warnings(self.check(d)), [])


class TheOperations(unittest.TestCase):
    def apply(self, ops, data=None):
        return edits.apply_operations(data or example(), ops)

    def test_set_offset_writes_the_hand_offset_and_keeps_the_original(self):
        out = self.apply([{"op": "set_offset", "source": SRC, "offset_s": 0.5}])
        self.assertEqual(out["sources"][SRC][HAND], 0.5)
        self.assertEqual(out["sources"][SRC]["audio_offset_s"], 0.276)

    def test_set_offset_takes_negative_numbers_zero_and_whole_numbers(self):
        for v in (-5.027, 0, 3):
            with self.subTest(value=v):
                out = self.apply([{"op": "set_offset", "source": SRC, "offset_s": v}])
                self.assertEqual(out["sources"][SRC][HAND], v)

    def test_reset_offset_removes_it_and_the_original_is_back_in_use(self):
        out = self.apply([{"op": "reset_offset", "source": SRC}], with_hand(0.5))
        self.assertNotIn(HAND, out["sources"][SRC])
        self.assertEqual(timeline.effective_offset(out["sources"][SRC]), 0.276)

    def test_reset_offset_without_a_hand_offset_changes_nothing(self):
        out = self.apply([{"op": "reset_offset", "source": SRC}])
        self.assertEqual(out, example())

    def test_the_input_is_not_changed(self):
        d = example()
        edits.apply_operations(d, [{"op": "set_offset", "source": SRC, "offset_s": 0.5}])
        self.assertEqual(d, example())

    def test_an_unknown_source_is_refused(self):
        for op in ({"op": "set_offset", "source": "nope", "offset_s": 0.5},
                   {"op": "reset_offset", "source": "nope"}):
            with self.subTest(op=op["op"]):
                with self.assertRaisesRegex(edits.OperationError, "no clip"):
                    self.apply([op])

    def test_a_value_that_is_not_a_number_is_refused(self):
        for v in ("0.5", True, None, [1]):
            with self.subTest(value=v):
                with self.assertRaisesRegex(edits.OperationError, "offset_s"):
                    self.apply([{"op": "set_offset", "source": SRC, "offset_s": v}])
        with self.assertRaisesRegex(edits.OperationError, "offset_s"):
            self.apply([{"op": "set_offset", "source": SRC}])

    def test_a_missing_source_field_is_refused(self):
        with self.assertRaisesRegex(edits.OperationError, "source"):
            self.apply([{"op": "set_offset", "offset_s": 0.5}])

    def test_an_out_of_range_value_is_left_to_the_rules(self):
        out = self.apply([{"op": "set_offset", "source": SRC, "offset_s": 99}])
        self.assertEqual(rules(timeline.validate(out, file_checks=False), "error"),
                         [("V23", f"sources.{SRC}.{HAND}")])


class TheSummary(unittest.TestCase):
    def test_the_summary_lists_every_clip_with_both_offsets(self):
        tl = timeline.Timeline(with_hand(0.5))
        clips = edits.summary(tl)["clips"]
        self.assertEqual([c["source"] for c in clips], list(example()["sources"]))
        first = clips[0]
        self.assertEqual(first["segments"], ["s001", "s002"])
        self.assertEqual((first["offset_original_s"], first["offset_hand_s"], first["offset_s"]),
                         (0.276, 0.5, 0.5))
        second = clips[1]
        self.assertEqual(second["segments"], ["s003"])
        self.assertEqual((second["offset_original_s"], second["offset_hand_s"], second["offset_s"]),
                         (0.248, None, 0.248))

    def test_the_timeline_summary_text_shows_a_hand_offset(self):
        tl = timeline.Timeline(with_hand(0.5))
        text = edits.format_summary(edits.summary(tl))
        self.assertIn("offsets set by hand", text)
        self.assertIn(f"{SRC} 0.500 s (from matching 0.276 s)", text)
        self.assertNotIn("offsets set by hand", edits.format_summary(edits.summary(
            timeline.Timeline(example()))))


class SoundKeysAndTheRender(PreviewFixture):
    """A changed offset marks the sound of the clip's segments, and no picture."""

    def tl_with(self, hand_b=None):
        tl = self.make_tl()
        if hand_b is not None:
            tl.data["sources"]["B"][HAND] = hand_b
        return tl

    def loader(self):
        return lambda path: render.load_wav(path, 48000)

    def test_the_sound_keys_change_for_the_clip_whose_offset_changed_only(self):
        a = render.sound_keys(self.tl_with(), render.make_plan(self.tl_with(), preview_width=W))
        b = render.sound_keys(self.tl_with(HAND_B), render.make_plan(self.tl_with(HAND_B), preview_width=W))
        self.assertEqual(set(a), {"s001", "s002", "s003"})
        self.assertEqual(a["s001"], b["s001"])
        self.assertEqual(a["s003"], b["s003"])
        self.assertNotEqual(a["s002"], b["s002"])           # the segment of clip B

    def test_trimming_one_segment_changes_the_sound_key_of_that_segment_only(self):
        a = self.make_tl()
        b = self.make_tl([("A", 10, 70), ("B", 5, 65), ("A", 40, 90)])      # s003 ends earlier
        ka = render.sound_keys(a, render.make_plan(a, preview_width=W))
        kb = render.sound_keys(b, render.make_plan(b, preview_width=W))
        self.assertEqual((ka["s001"], ka["s002"]), (kb["s001"], kb["s002"]))
        self.assertNotEqual(ka["s003"], kb["s003"])

    def test_after_an_offset_change_the_pictures_are_cached_and_only_that_sound_is_pending(self):
        name = "offset-pending"
        self.render(self.tl_with(), name=name)
        pending = render.pending_work(self.tl_with(HAND_B), str(self.work), self.settings, name=name)
        self.assertEqual(pending["picture"], [])
        self.assertEqual(pending["sound"], ["s002"])

    def test_with_no_change_nothing_is_pending(self):
        name = "offset-nothing"
        self.render(self.tl_with(), name=name)
        pending = render.pending_work(self.tl_with(), str(self.work), self.settings, name=name)
        self.assertEqual(pending, {"picture": [], "sound": []})

    def test_without_a_preview_everything_is_pending(self):
        pending = render.pending_work(self.tl_with(), str(self.work), self.settings,
                                      name="never-rendered")
        self.assertEqual(pending["sound"], ["s001", "s002", "s003"])

    def test_a_second_render_after_the_change_renders_no_picture_and_reports_the_sound(self):
        name = "offset-render"
        first = self.render(self.tl_with(), name=name)
        self.assertEqual(sorted(first.sound_changed), ["s001", "s002", "s003"])
        second = self.render(self.tl_with(HAND_B), name=name)
        self.assertEqual(second.rendered, [])
        self.assertEqual(sorted(second.cached), ["s001", "s002", "s003"])
        self.assertEqual(second.sound_changed, ["s002"])
        record = json.loads((self.work / "previews" / f"{name}.json").read_text())
        self.assertEqual(record["sound_changed"], ["s002"])
        self.assertEqual(set(record["sound_keys"]), {"s001", "s002", "s003"})

    def test_the_mix_changes_where_the_offset_changed_and_nowhere_else(self):
        a, b = self.tl_with(), self.tl_with(HAND_B)
        plan = render.make_plan(a, preview_width=W)
        mix_a = render.build_mix(a, plan, self.loader()).samples
        mix_b = render.build_mix(b, render.make_plan(b, preview_width=W), self.loader()).samples
        # B's 440 Hz and A's 660 Hz differ, so B's shifted start must change B's samples
        # (a sine moved by 0.3 s differs in phase), and the clip A samples far from B must not.
        seg1 = plan.segments[0]
        far_end = seg1.film_start_sample + seg1.sample_length - int(0.3 * 48000)
        self.assertTrue(np.array_equal(mix_a[:far_end], mix_b[:far_end]))
        seg2 = plan.segments[1]
        mid = slice(seg2.film_start_sample + 2000, seg2.film_start_sample + seg2.sample_length - 2000)
        self.assertFalse(np.allclose(mix_a[mid], mix_b[mid], atol=1e-4))


class TheClickLinesUpWithAHandOffset(unittest.TestCase):
    """Check 2.10 again with a hand offset: the stored offset is wrong (0.0), the owner
    types the right one, and the click lies at the film time of the white frame."""

    TRUE_OFFSET = 0.276

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.work = root / "work"
        (cls.work / "copies").mkdir(parents=True)
        cls.copy = cls.work / "copies" / "M.mp4"
        media.write_ramp_mp4(cls.copy, 200, W, H, white_frame=100, value=lambda i: 60)
        rate = 96000
        x = np.zeros((12 * rate, 2))
        click_at = round((100 / media.FPS + cls.TRUE_OFFSET) * rate)
        x[click_at:click_at + 96] = np.random.default_rng(5).uniform(-0.9, 0.9, (96, 2))
        cls.wav = root / "M.wav"
        media.write_wav(cls.wav, x, rate)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def tl(self, hand):
        src = {"original_video": "/o/M.braw", "preview_copy": str(self.copy), "audio": str(self.wav),
               "audio_offset_s": 0.0, "frames": 200, "fps": {"num": 30000, "den": 1001},
               "size": {"width": W * 2, "height": H * 2}, "copy_lut_sha256": "0" * 64}
        if hand is not None:
            src[HAND] = hand
        return timeline.Timeline({
            "version": 1, "kind": "film",
            "film": {"fps": {"num": 30000, "den": 1001}, "frame": {"width": W, "height": H},
                     "fit": "fit", "fade_in_s": 0, "fade_out_s": 0,
                     "audio_join": {"duration_s": 0, "curve": "equal_power"}},
            "sources": {"M": src},
            "segments": [{"id": "s001", "source": "M", "in": 50, "out": 150}],
            "look": {"base": {"mode": "none"}},
            "audio": {"sample_rate": 48000, "loudness": {"mode": "off"}}})

    def first_click(self, tl):
        plan = render.make_plan(tl, preview_width=W)
        mix = render.build_mix(tl, plan, lambda p: render.load_wav(p, 48000))
        mono = np.abs(mix.samples).max(axis=1)
        return int(np.argmax(mono > 0.3 * mono.max()))

    def test_with_the_wrong_stored_offset_the_click_is_late_in_the_film(self):
        want = round(50 * 1001 / 30000 * 48000)
        self.assertGreater(abs(self.first_click(self.tl(None)) - want), 48)

    def test_with_the_hand_offset_the_click_lies_within_1_ms_of_the_white_frame(self):
        want = round(50 * 1001 / 30000 * 48000)
        self.assertAlmostEqual(self.first_click(self.tl(self.TRUE_OFFSET)), want, delta=48)


if __name__ == "__main__":
    unittest.main()
