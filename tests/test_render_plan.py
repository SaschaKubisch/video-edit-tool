"""Check 2.9: the render plan (SPECS 5.1, 5.2, 3.4). Pure functions: no media is read."""

import unittest

import render
import timeline
from tests.timeline_fixture import example


class PlacementTests(unittest.TestCase):
    """The table of SPECS 5.2 (the clips seen: 4096 x 2160 and 4096 x 1720)."""

    def test_a_dci_4k_picture_fills_the_dci_4k_frame_with_no_bars(self):
        p = render.placement((4096, 2160), (4096, 2160), "fit")
        self.assertEqual((p["w"], p["h"], p["x"], p["y"]), (4096, 2160, 0, 0))
        self.assertEqual(p["scale"], 1.0)

    def test_a_wide_picture_gets_bars_of_220_pixels_above_and_below(self):
        p = render.placement((4096, 1720), (4096, 2160), "fit")
        self.assertEqual((p["w"], p["h"], p["x"], p["y"]), (4096, 1720, 0, 220))

    def test_a_picture_is_never_enlarged_in_fit(self):
        p = render.placement((1000, 500), (4096, 2160), "fit")
        self.assertEqual((p["w"], p["h"]), (1000, 500))
        self.assertEqual((p["x"], p["y"]), (1548, 830))

    def test_fit_at_preview_size(self):
        # 4096 x 1720 into 960 x 506: scaled by 0.234375 to 960 x 403 (rounded to even)
        p = render.placement((4096, 1720), (960, 506), "fit", allow_enlarge=True)
        self.assertEqual(p["w"], 960)
        self.assertEqual(p["h"] % 2, 0)
        self.assertAlmostEqual(p["h"], 403, delta=1)
        self.assertEqual(p["x"], 0)
        self.assertEqual(p["y"] % 2, 0)
        self.assertAlmostEqual(p["y"], (506 - 403) / 2, delta=1.5)

    def test_fill_cuts_the_window_of_the_output_shape(self):
        p = render.placement((4096, 2160), (1080, 1920), "fill")
        self.assertEqual((p["ww"], p["wh"]), (1216, 2160))
        self.assertEqual(p["wy"], 0)
        self.assertEqual(p["wx"], render.round_even((4096 - 1216) * 0.5))
        p = render.placement((4096, 1720), (1080, 1920), "fill")
        self.assertEqual((p["ww"], p["wh"]), (968, 1720))

    def test_fill_window_follows_crop_x(self):
        left = render.placement((4096, 2160), (1080, 1920), "fill", crop={"x": 0})
        right = render.placement((4096, 2160), (1080, 1920), "fill", crop={"x": 1})
        self.assertEqual(left["wx"], 0)
        self.assertEqual(right["wx"], 4096 - 1216)

    def test_round_even_rounds_to_the_nearest_even_number_and_a_tie_goes_up(self):
        self.assertEqual(render.round_even(5), 6)       # a tie between 4 and 6
        self.assertEqual(render.round_even(4.9), 4)
        self.assertEqual(render.round_even(5.1), 6)
        self.assertEqual(render.round_even(220), 220)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.tl = timeline.Timeline(example())
        self.plan = render.make_plan(self.tl)

    def test_film_starts_and_lengths_follow_the_derived_values(self):
        self.assertEqual([s.id for s in self.plan.segments], ["s001", "s002", "s003"])
        self.assertEqual([s.film_start_frame for s in self.plan.segments], [0, 862, 1922])
        self.assertEqual([s.length_frames for s in self.plan.segments], [862, 1060, 620])
        self.assertEqual(self.plan.film_frames, 2542)
        self.assertEqual(self.plan.film_samples, self.tl.film_sample(2542))

    def test_sample_positions_follow_section_3_4(self):
        s = self.plan.segments[1]
        self.assertEqual(s.film_start_sample, self.tl.film_sample(862))
        self.assertEqual(s.sample_length, self.tl.film_sample(1922) - self.tl.film_sample(862))
        self.assertEqual(s.source_audio_start, self.tl.source_audio_start(self.tl.segment("s002")))

    def test_joins_fades_and_the_segment_own_join(self):
        first, second, third = self.plan.segments
        self.assertEqual(first.join_in_samples, 0)           # the film fade-in takes its place
        self.assertEqual(second.join_in_samples, round(0.5 * 48000))
        self.assertEqual(third.join_in_samples, round(0.25 * 48000))
        self.assertEqual(third.curve_in, "linear")           # s003 overrides the film's join
        self.assertEqual(second.curve_in, "equal_power")
        self.assertEqual(first.fade_in_samples, 48000)
        self.assertEqual(third.fade_out_samples, 2 * 48000)
        self.assertEqual(second.fade_in_samples, 0)
        self.assertEqual(second.fade_out_samples, 0)

    def test_picture_fades_count_frames(self):
        self.assertEqual(self.plan.segments[0].picture_fade_in_frames, round(1.0 * 30000 / 1001))
        self.assertEqual(self.plan.segments[-1].picture_fade_out_frames, round(2.0 * 30000 / 1001))

    def test_gain_adjust_is_carried(self):
        self.assertEqual(self.plan.segments[2].gain_db, -2.0)
        self.assertEqual(self.plan.segments[0].gain_db, 0.0)

    def test_disabled_segments_are_not_in_the_plan(self):
        d = example()
        d["segments"][1]["enabled"] = False
        plan = render.make_plan(timeline.Timeline(d))
        self.assertEqual([s.id for s in plan.segments], ["s001", "s003"])
        self.assertEqual(plan.segments[1].film_start_frame, 862)

    def test_film_samples_do_not_drift_over_150_segments_at_24000_1001(self):
        d = example()
        d["film"]["fps"] = {"num": 24000, "den": 1001}
        for s in d["sources"].values():
            s["fps"] = {"num": 24000, "den": 1001}
        d["sources"]["A002_01060615_C083"]["frames"] = 600
        d["segments"] = [{"id": f"s{i + 1:03d}", "source": "A002_01060615_C083",
                          "in": i * 3, "out": i * 3 + 7} for i in range(150)]
        d["film"]["fade_in_s"] = d["film"]["fade_out_s"] = 0
        d["film"]["audio_join"]["duration_s"] = 0
        tl = timeline.Timeline(d)
        plan = render.make_plan(tl)
        self.assertEqual(sum(s.sample_length for s in plan.segments), plan.film_samples)
        self.assertEqual(plan.film_samples, round(150 * 7 * 48000 * 1001 / 24000))
        ends = [s.film_start_sample + s.sample_length for s in plan.segments]
        starts = [s.film_start_sample for s in plan.segments[1:]]
        self.assertEqual(ends[:-1], starts)

    def test_the_preview_frame_follows_the_film_shape(self):
        self.assertEqual(render.preview_frame(self.tl.data["film"]["frame"], 960), (960, 506))
        self.assertEqual(render.preview_frame({"width": 1920, "height": 1080}, 960), (960, 540))

    def test_the_placement_of_each_segment_is_in_the_plan(self):
        plan = render.make_plan(self.tl, preview_width=960)
        wide = plan.segments[0]            # A002_01011540_C002 is 4096 x 1720
        tall = plan.segments[2]            # A002_01060615_C083 is 4096 x 2160
        self.assertEqual(wide.placement["w"], 960)
        self.assertGreater(wide.placement["y"], 0)
        self.assertEqual((tall.placement["w"], tall.placement["h"]), (960, 506))
        self.assertEqual(tall.placement["y"], 0)


if __name__ == "__main__":
    unittest.main()
