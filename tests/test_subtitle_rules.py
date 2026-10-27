"""Check 4.10: the validator rules for cues, titles and the subtitle form of an export
(SPECS 3.5: V19, V24, V25, V26). Each test breaks a copy of the example once."""

import unittest

from tests.test_timeline_rules import RuleCase
from tests.timeline_fixture import example


def cue(cid, anchor, start, end, text="Now the pan is hot enough.", **extra):
    return {"id": cid, "anchor": anchor, "start_s": start, "end_s": end, "text": text, **extra}


class V24CueTimingTests(RuleCase):
    def test_two_cues_that_overlap_are_an_error(self):
        d = example()
        d["subtitles"]["cues"].append(cue("c002", "s001", 5.0, 8.0))      # c001 is 3.2 to 6.0
        self.expect(d, "V24", "subtitles.cues[1].start_s")

    def test_overlap_is_found_by_film_time_across_segments(self):
        d = example()
        # s001 is 28.76 s long; a cue on s002 cannot overlap one on s001 inside the segments
        d["subtitles"]["cues"].append(cue("c002", "s002", 1.0, 4.0))
        self.expect_clean(d)

    def test_cues_that_touch_are_a_warning(self):
        d = example()
        d["subtitles"]["cues"].append(cue("c002", "s001", 6.0, 8.5))
        self.expect(d, "V24", "subtitles.cues[1].start_s", level="warning")

    def test_a_gap_of_two_frames_is_allowed(self):
        d = example()
        d["subtitles"]["cues"].append(cue("c002", "s001", 6.0 + 2 * 1001 / 30000 + 0.001, 8.5))
        self.expect_clean(d)

    def test_cues_out_of_film_order_are_a_warning(self):
        d = example()
        d["subtitles"]["cues"].insert(0, cue("c002", "s002", 1.0, 4.0))
        self.expect(d, "V24", "subtitles.cues[1].start_s", level="warning")

    def test_a_cue_on_a_removed_segment_is_not_compared(self):
        d = example()
        d["segments"][0]["enabled"] = False
        d["subtitles"]["cues"].append(cue("c002", "s001", 5.0, 8.0))        # overlaps c001 on s001
        self.expect_clean(d)

    def test_overlap_of_a_cue_that_reaches_into_the_next_segment(self):
        d = example()
        d["subtitles"]["cues"][0].update(start_s=27.0, end_s=29.5)            # s001 is 28.76 s
        d["subtitles"]["cues"].append(cue("c002", "s002", 0.0, 2.5))
        found = self.findings(d)
        self.assertIn(("V15", "warning"), {(f.rule, f.level) for f in found})


class V19ReadabilityTests(RuleCase):
    def test_reading_faster_than_17_characters_per_second_is_a_warning(self):
        d = example()
        d["subtitles"]["cues"][0]["text"] = "x" * 40                        # 40 characters in 2.8 s = 14 cps
        self.expect_clean(d)
        d["subtitles"]["cues"][0]["end_s"] = 4.7                           # 1.5 s: 26 cps
        self.expect(d, "V19", "subtitles.cues[0].text", level="warning")

    def test_a_cue_longer_than_7_seconds_is_a_warning(self):
        d = example()
        d["subtitles"]["cues"][0].update(start_s=1.0, end_s=8.5)
        self.expect(d, "V19", "subtitles.cues[0].end_s", level="warning")

    def test_a_cue_of_exactly_7_seconds_is_allowed(self):
        d = example()
        d["subtitles"]["cues"][0].update(start_s=1.0, end_s=8.0)
        self.expect_clean(d)

    def test_a_cue_shorter_than_1_second_is_a_warning(self):
        d = example()
        d["subtitles"]["cues"][0].update(start_s=1.0, end_s=1.6, text="Hi.")
        self.expect(d, "V19", "subtitles.cues[0].end_s", level="warning")


class V25SubtitleFormTests(RuleCase):
    def test_every_form_is_allowed(self):
        for form in ("srt", "burned", "both", "none"):
            d = example()
            d["exports"][0]["subtitles"] = form
            self.expect_clean(d)

    def test_an_unknown_form_is_an_error(self):
        d = example()
        d["exports"][0]["subtitles"] = "burn"
        self.expect(d, "V25", "exports[0].subtitles")

    def test_the_form_must_be_a_string(self):
        d = example()
        d["exports"][0]["subtitles"] = True
        self.expect(d, "V0", "exports[0].subtitles")

    def test_the_old_mode_and_style_keys_are_refused_with_a_hint(self):
        for key, value in (("mode", "both"), ("style", {"font": "Arial"})):
            d = example()
            d["subtitles"][key] = value
            f = self.expect(d, "V2", f"subtitles.{key}")
            self.assertIn("exports", f.hint if key == "mode" else f.hint + "exports")


class V26TitleAndTextTests(RuleCase):
    def test_title_text_must_not_be_empty(self):
        d = example()
        d["titles"][0]["text"] = "  "
        self.expect(d, "V26", "titles[0].text")

    def test_title_template_must_be_known(self):
        d = example()
        d["titles"][0]["template"] = "corner"
        self.expect(d, "V26", "titles[0].template")

    def test_title_size_must_be_in_range(self):
        for bad in (0, 0.5, 31, -4):
            d = example()
            d["titles"][0]["size_pct"] = bad
            self.expect(d, "V26", "titles[0].size_pct")
        d = example()
        d["titles"][0]["size_pct"] = 12
        self.expect_clean(d)

    def test_title_fade_must_not_be_negative(self):
        d = example()
        d["titles"][0]["fade_s"] = -0.1
        self.expect(d, "V26", "titles[0].fade_s")

    def test_scene_text_must_be_a_string(self):
        d = example()
        d["segments"][0]["text"] = 4
        self.expect(d, "V0", "segments[0].text")
        d["segments"][0]["text"] = "Now the pan is hot enough."
        self.expect_clean(d)

    def test_cue_origin_and_locked(self):
        d = example()
        d["subtitles"]["cues"][0]["origin"] = "auto"
        d["subtitles"]["cues"][0]["locked"] = True
        self.expect_clean(d)
        d["subtitles"]["cues"][0]["origin"] = "dictated"
        self.expect(d, "V26", "subtitles.cues[0].origin")
        d["subtitles"]["cues"][0]["origin"] = "text"
        d["subtitles"]["cues"][0]["locked"] = "yes"
        self.expect(d, "V0", "subtitles.cues[0].locked")


if __name__ == "__main__":
    unittest.main()
