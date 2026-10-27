"""Check 6.1 to 6.3: the plan of a vertical short (SPECS 11). A synthetic film with many scenes of different lengths
goes in; what comes out is a timeline file of kind `short`. Nothing here renders: the numbers are frames, the
lengths are exact, and the rules of the choice (one scene per equal part of the film, importance, proportional
time with a floor and a cap, the window that does not cut a cue) are read off the plan."""

import copy
import json
import random
import unittest
from fractions import Fraction

import config
import shorts
import textlayout
import timeline

FPS = Fraction(30000, 1001)
FPS_DICT = {"num": 30000, "den": 1001}
RULES = textlayout.char_rules(config.TEXT_LAYOUTS["9:16"]["max_chars"])      # 24 characters, no font file needed


def frames(seconds):
    return int(round(seconds * FPS))


def make_film(lengths_s, sizes=None, cues=(), titles=(), fade_in=1.0, fade_out=2.0, join=0.5, text=None,
              priority=None, crop=None, enabled=None):
    """A film of scenes with the given lengths in seconds. Every scene is cut from a clip of its own
    (so scenes never share frames), 4096 x 2160 unless `sizes` says otherwise."""
    sources, segments = {}, []
    for i, sec in enumerate(lengths_s):
        sid = f"C{i + 1:03d}"
        n = frames(sec)
        w, h = (sizes or {}).get(i, (4096, 2160))
        sources[sid] = {"original_video": f"/o/{sid}.braw", "preview_copy": f"/p/{sid}.mp4", "audio": f"/a/{sid}.wav",
                        "audio_offset_s": 0.1, "frames": n + 200, "fps": FPS_DICT, "size": {"width": w, "height": h},
                        "copy_lut_sha256": "0" * 64}
        seg = {"id": f"s{i + 1:03d}", "source": sid, "in": 100, "out": 100 + n, "label": f"clip {i + 1}",
               "origin": "assembly"}
        if text and i in text:
            seg["text"] = text[i]
        if priority and i in priority:
            seg["priority"] = priority[i]
        if crop and i in crop:
            seg["crop"] = crop[i]
        if enabled and i in enabled:
            seg["enabled"] = enabled[i]
        segments.append(seg)
    data = {"version": 1, "kind": "film",
            "film": {"title": "Test film", "fps": FPS_DICT, "frame": {"width": 4096, "height": 2160}, "fit": "fit",
                     "fade_in_s": fade_in, "fade_out_s": fade_out, "audio_join": {"duration_s": join, "curve": "equal_power"}},
            "sources": sources, "segments": segments,
            "look": {"base": {"mode": "none"}},
            "audio": {"sample_rate": 48000, "loudness": {"mode": "segment_then_film", "target_lufs": -14.0,
                                                          "true_peak_dbtp": -1.0, "max_gain_db": 12.0}}}
    if cues:
        data["subtitles"] = {"language": "en", "cues": [dict(c) for c in cues]}
    if titles:
        data["titles"] = [dict(t) for t in titles]
    return data


def first_shoot(n=171, seed=7):
    """Lengths like the first shoot: the median about 16 s, the longest about 124 s, none below 2.04 s."""
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        out.append(max(2.04, min(124.0, rng.lognormvariate(2.75, 0.9))))
    return out


def build(film, **kw):
    kw.setdefault("rules", RULES)
    kw.setdefault("film_path", "/work/timeline.json")
    return shorts.plan_short(film, **kw)


def short_segments(plan):
    return plan.data["segments"]


def seg_frames(seg):
    return seg["out"] - seg["in"]


class AllocateTests(unittest.TestCase):
    def test_the_frames_add_up_to_the_total_exactly(self):
        for total in (400, 900, 1349):
            lengths = [3000, 900, 450, 200, 2500, 60]
            a = shorts.allocate(lengths, [60] * 6, [240] * 6, total)
            self.assertEqual(sum(a), total, total)

    def test_floors_and_caps_hold(self):
        lengths = [3000, 900, 450, 200, 2500, 62]
        a = shorts.allocate(lengths, [60, 60, 60, 60, 60, 60], [240, 240, 240, 200, 240, 62], 900)
        for x, f, c in zip(a, [60] * 6, [240, 240, 240, 200, 240, 62]):
            self.assertGreaterEqual(x, f)
            self.assertLessEqual(x, c)

    def test_time_follows_the_length_of_the_scene(self):
        a = shorts.allocate([400, 800, 1600], [30, 30, 30], [10 ** 6] * 3, 700)
        self.assertEqual(sum(a), 700)
        self.assertLess(a[0], a[1])
        self.assertLess(a[1], a[2])
        self.assertAlmostEqual(a[2] / a[1], 2.0, delta=0.05)

    def test_a_short_scene_stays_at_its_floor(self):
        a = shorts.allocate([70, 5000, 5000], [60, 60, 60], [70, 240, 240], 400)
        self.assertEqual(a[0], 60)
        self.assertEqual(sum(a), 400)

    def test_floors_that_do_not_fit_are_an_error_that_names_both_lengths(self):
        with self.assertRaises(shorts.ShortError) as ctx:
            shorts.allocate([300] * 5, [60] * 5, [240] * 5, 200)
        self.assertIn("300", str(ctx.exception))
        self.assertIn("200", str(ctx.exception))

    def test_caps_that_are_too_low_are_an_error(self):
        with self.assertRaises(shorts.ShortError):
            shorts.allocate([300] * 2, [60] * 2, [100] * 2, 500)

    def test_the_result_does_not_depend_on_anything_but_the_numbers(self):
        args = ([3000, 900, 450, 200, 2500], [60] * 5, [240] * 5, 777)
        self.assertEqual(shorts.allocate(*args), shorts.allocate(*args))


class FloorTests(unittest.TestCase):
    def test_the_floors_of_the_positions_follow_the_fades_and_the_joins(self):
        f = shorts.position_floors(1.0, 2.0, 0.5, FPS)
        self.assertEqual(f["inner"], 60)                  # 2.0 s is 59.94 frames
        self.assertEqual(f["first"], 60)                  # 1.0 + 0.25 s = 1.25 s is less than 2.0 s
        self.assertEqual(f["last"], 68)                   # 0.25 + 2.0 = 2.25 s is 67.4 frames
        self.assertEqual(f["only"], 90)                   # 1.0 + 2.0 = 3.0 s is 89.9 frames

    def test_a_floor_always_satisfies_rule_v11(self):
        for fade_in, fade_out, join in ((1.0, 2.0, 0.5), (0.5, 1.0, 0.25), (0.0, 0.0, 0.0), (2.0, 4.0, 1.0)):
            f = shorts.position_floors(fade_in, fade_out, join, FPS)
            self.assertGreaterEqual(float(f["last"] / FPS) + 1e-9, join / 2 + fade_out)
            self.assertGreaterEqual(float(f["first"] / FPS) + 1e-9, fade_in + join / 2)
            self.assertGreaterEqual(float(f["only"] / FPS) + 1e-9, fade_in + fade_out)


class WindowTests(unittest.TestCase):
    def test_without_cues_the_window_is_in_the_middle_of_the_scene(self):
        self.assertEqual(shorts.best_window(600, 100, [], FPS), 250)
        self.assertEqual(shorts.best_window(601, 100, [], FPS), 250)

    def test_a_scene_that_fits_whole_starts_at_zero(self):
        self.assertEqual(shorts.best_window(100, 100, [(1.0, 2.0)], FPS), 0)

    def test_the_window_takes_in_a_whole_cue_that_it_can(self):
        # a cue from 3.0 to 5.0 s in a scene of 20 s; a window of 4 s that holds it starts between 1.0 and 3.0 s
        s = shorts.best_window(frames(20), frames(4), [(3.0, 5.0)], FPS)
        self.assertGreaterEqual(s / FPS, 1.0 - 0.04)
        self.assertLessEqual(s / FPS, 3.0 + 0.04)

    def test_the_window_does_not_cut_through_speech_when_it_can_stay_clear(self):
        # a cue from 9.0 to 12.0 s in a scene of 20 s, a window of 3 s: the cue fits exactly, any other window
        # either cuts it or leaves it; the window that holds it whole wins
        s = shorts.best_window(frames(20), frames(3), [(9.0, 12.0)], FPS)
        self.assertAlmostEqual(s / FPS, 9.0, delta=0.05)

    def test_a_window_that_cannot_hold_a_cue_stays_away_from_it(self):
        # a cue of 6 s, a window of 3 s: keeping 0.8 of it (4.8 s) is impossible, so the window avoids it and its
        # edges stay 0.25 s clear
        s = shorts.best_window(frames(30), frames(3), [(10.0, 16.0)], FPS)
        a, b = s / FPS, s / FPS + 3
        clear = b <= 10.0 - 0.25 + 0.04 or a >= 16.0 + 0.25 - 0.04
        self.assertTrue(clear, (a, b))

    def test_the_choice_is_the_same_every_time(self):
        a = shorts.best_window(frames(30), frames(4), [(5.0, 7.0), (14.0, 15.5)], FPS)
        b = shorts.best_window(frames(30), frames(4), [(5.0, 7.0), (14.0, 15.5)], FPS)
        self.assertEqual(a, b)


class PlanLengthTests(unittest.TestCase):
    def test_the_default_length_is_60_seconds(self):
        plan = build(make_film(first_shoot()))
        self.assertEqual(plan.target_frames, frames(60))
        self.assertEqual(plan.frames, plan.target_frames)
        self.assertEqual(plan.data["derived_from"]["target_length_s"], 60.0)

    def test_the_length_is_exact_to_the_frame_for_many_lengths(self):
        film = make_film(first_shoot())
        for length in (15, 30, 37.5, 45, 60, 90, 120, 180):
            with self.subTest(length=length):
                plan = build(film, length_s=length)
                self.assertEqual(plan.frames, frames(length))
                self.assertEqual(sum(seg_frames(s) for s in short_segments(plan)), frames(length))

    def test_the_film_shorter_than_the_length_is_taken_whole(self):
        film = make_film([10, 12, 9])
        plan = build(film, length_s=60)
        self.assertEqual([s["id"] for s in short_segments(plan)], ["s001", "s002", "s003"])
        self.assertEqual(plan.frames, frames(10) + frames(12) + frames(9))
        self.assertTrue(any("shorter" in n for n in plan.notes))

    def test_the_short_is_valid(self):
        film = make_film(first_shoot())
        for length in (15, 45, 60, 180):
            plan = build(film, length_s=length)
            findings = timeline.validate(plan.data, file_checks=False)
            self.assertEqual([f for f in findings if f.level == "error"], [], length)

    def test_the_source_film_is_not_changed(self):
        film = make_film(first_shoot(), cues=[{"id": "c001", "anchor": "s010", "start_s": 1.0, "end_s": 3.0,
                                               "text": "Hello there.", "origin": "text"}])
        before = json.dumps(film, sort_keys=True)
        build(film, length_s=45)
        self.assertEqual(json.dumps(film, sort_keys=True), before)

    def test_two_runs_give_the_same_file(self):
        film = make_film(first_shoot())
        a = timeline.dumps(build(film, length_s=45).data)
        b = timeline.dumps(build(film, length_s=45).data)
        self.assertEqual(a, b)

    def test_a_length_below_the_shortest_possible_short_is_an_error(self):
        with self.assertRaises(shorts.ShortError) as ctx:
            build(make_film(first_shoot()), length_s=2.0)
        self.assertIn("3.0", str(ctx.exception))


class PlanChoiceTests(unittest.TestCase):
    def test_about_one_scene_per_four_seconds(self):
        film = make_film(first_shoot())
        for length, want in ((15, 4), (30, 8), (45, 11), (60, 15), (90, 22)):
            plan = build(film, length_s=length)
            self.assertAlmostEqual(len(short_segments(plan)), want, delta=1, msg=length)

    def test_every_scene_lies_between_the_floor_and_the_cap(self):
        film = make_film(first_shoot())
        plan = build(film, length_s=60)
        segs = short_segments(plan)
        for i, s in enumerate(segs):
            n = seg_frames(s)
            self.assertGreaterEqual(n, 60, s["id"])
            self.assertLessEqual(n, frames(8), s["id"])
        self.assertGreaterEqual(seg_frames(segs[-1]), 68)

    def test_one_scene_comes_from_each_equal_part_of_the_film(self):
        film = make_film(first_shoot())
        plan = build(film, length_s=60)
        pool = [(s["id"], seg_frames(s)) for s in film["segments"]]
        total = sum(n for _, n in pool)
        position, parts = 0, {}
        for sid, n in pool:
            parts[sid] = int((position + n / 2) * 15 / total)
            position += n
        kept = [s["x_film_segment"] for s in short_segments(plan)]
        self.assertEqual(sorted(parts[k] for k in kept), list(range(15)))

    def test_the_order_of_the_film_is_kept(self):
        film = make_film(first_shoot())
        plan = build(film, length_s=60)
        kept = [s["x_film_segment"] for s in short_segments(plan)]
        self.assertEqual(kept, sorted(kept))

    def test_the_new_scenes_have_their_own_ids_and_the_origin_short(self):
        plan = build(make_film(first_shoot()), length_s=30)
        segs = short_segments(plan)
        self.assertEqual([s["id"] for s in segs], [f"s{i + 1:03d}" for i in range(len(segs))])
        self.assertTrue(all(s["origin"] == "short" for s in segs))

    def test_the_window_lies_inside_the_film_scene(self):
        film = make_film(first_shoot())
        plan = build(film, length_s=60)
        by_id = {s["id"]: s for s in film["segments"]}
        for s in short_segments(plan):
            f = by_id[s["x_film_segment"]]
            self.assertGreaterEqual(s["in"], f["in"])
            self.assertLessEqual(s["out"], f["out"])
            self.assertEqual(s["source"], f["source"])

    def test_the_film_times_of_a_window_are_written_down(self):
        film = make_film([30, 30, 30, 30, 30, 30])
        plan = build(film, length_s=20)
        for s in short_segments(plan):
            self.assertAlmostEqual(s["x_film_out_s"] - s["x_film_in_s"], seg_frames(s) / FPS, delta=0.001)

    def test_scenes_that_are_removed_or_dropped_are_never_used(self):
        film = make_film([20] * 12, enabled={3: False}, priority={5: "drop"})
        for length in (10, 30, 60):
            plan = build(film, length_s=length)
            kept = {s["x_film_segment"] for s in short_segments(plan)}
            self.assertNotIn("s004", kept)
            self.assertNotIn("s006", kept)

    def test_the_longest_scene_of_a_part_is_taken_when_nothing_else_matters(self):
        # two parts of three scenes each; the long one of each part wins
        film = make_film([5, 40, 6, 7, 6, 38])
        plan = build(film, length_s=8)
        self.assertEqual([s["x_film_segment"] for s in short_segments(plan)], ["s002", "s006"])

    def test_a_scene_with_text_wins_over_a_longer_scene_without(self):
        film = make_film([5, 40, 6, 7, 6, 38], text={0: "Now the pan is hot."})
        plan = build(film, length_s=8)
        self.assertEqual([s["x_film_segment"] for s in short_segments(plan)], ["s001", "s006"])

    def test_a_scene_with_a_cue_counts_like_a_scene_with_text(self):
        cue = {"id": "c001", "anchor": "s003", "start_s": 0.5, "end_s": 2.5, "text": "Listen.", "origin": "manual"}
        film = make_film([5, 40, 6, 7, 6, 38], cues=[cue])
        plan = build(film, length_s=8)
        self.assertEqual([s["x_film_segment"] for s in short_segments(plan)], ["s003", "s006"])

    def test_a_pinned_scene_wins_over_a_scene_with_text(self):
        film = make_film([5, 40, 6, 7, 6, 38], text={0: "Words."}, priority={2: "must"})
        plan = build(film, length_s=8)
        self.assertEqual([s["x_film_segment"] for s in short_segments(plan)], ["s003", "s006"])

    def test_a_scene_named_in_the_prompt_wins_like_a_pinned_scene(self):
        film = make_film([5, 40, 6, 7, 6, 38], text={3: "Words."})
        plan = build(film, length_s=8, prefer=["s005"])
        self.assertEqual([s["x_film_segment"] for s in short_segments(plan)], ["s002", "s005"])

    def test_every_pinned_scene_is_kept_even_two_in_one_part(self):
        film = make_film([20] * 20, priority={0: "must", 1: "must", 15: "must"})
        plan = build(film, length_s=60)
        kept = {s["x_film_segment"] for s in short_segments(plan)}
        self.assertTrue({"s001", "s002", "s016"} <= kept)
        self.assertEqual(plan.frames, frames(60))

    def test_only_limits_the_scenes_the_short_is_made_from(self):
        film = make_film([20] * 30)
        plan = build(film, length_s=20, only=["s010", "s011", "s012", "s013", "s014", "s015", "s016", "s017"])
        kept = {s["x_film_segment"] for s in short_segments(plan)}
        self.assertTrue(kept <= {f"s{i:03d}" for i in range(10, 18)})
        self.assertEqual(plan.frames, frames(20))

    def test_exclude_leaves_scenes_out(self):
        film = make_film([20] * 30)
        everything = build(film, length_s=40)
        names = [s["x_film_segment"] for s in short_segments(everything)]
        plan = build(film, length_s=40, exclude=names[:3])
        kept = [s["x_film_segment"] for s in short_segments(plan)]
        for n in names[:3]:
            self.assertNotIn(n, kept)

    def test_an_unknown_scene_is_an_error_with_a_hint(self):
        film = make_film([20] * 5)
        for key in ("prefer", "only", "exclude"):
            with self.assertRaises(shorts.ShortError) as ctx:
                build(film, length_s=20, **{key: ["s099"]})
            self.assertIn("s099", str(ctx.exception))

    def test_a_removed_scene_cannot_be_named(self):
        film = make_film([20] * 5, enabled={1: False})
        with self.assertRaises(shorts.ShortError) as ctx:
            build(film, length_s=20, prefer=["s002"])
        self.assertIn("removed", str(ctx.exception))

    def test_pinned_scenes_that_do_not_fit_are_an_error_that_names_both_lengths(self):
        film = make_film([20] * 20, priority={i: "must" for i in range(10)})
        with self.assertRaises(shorts.ShortError) as ctx:
            build(film, length_s=10)
        text = str(ctx.exception)
        self.assertIn("10", text)
        self.assertIn("pinned", text.lower())

    def test_the_last_scene_is_long_enough_for_the_fade_out(self):
        # the only scene of the last part is just above 2 s: it cannot close a short with a fade-out of 2 s
        lengths = [30] * 9 + [2.1]
        plan = build(make_film(lengths), length_s=20)
        last = short_segments(plan)[-1]
        self.assertGreaterEqual(seg_frames(last) / FPS, 2.25)
        self.assertNotEqual(last["x_film_segment"], "s010")

    def test_the_cap_is_raised_when_the_scenes_cannot_reach_the_length(self):
        film = make_film([40, 40, 40])
        plan = build(film, length_s=100)
        self.assertEqual(plan.frames, frames(100))
        self.assertTrue(any("cap" in n for n in plan.notes))

    def test_one_scene_in_the_whole_film_fills_the_short(self):
        plan = build(make_film([200]), length_s=30)
        self.assertEqual(len(short_segments(plan)), 1)
        self.assertEqual(plan.frames, frames(30))


class PlanFileTests(unittest.TestCase):
    def setUp(self):
        self.film = make_film(first_shoot(), sizes={i: (4096, 1720) for i in range(0, 171, 2)})
        self.plan = build(self.film, length_s=60)
        self.data = self.plan.data

    def test_the_file_is_a_short_with_a_vertical_fill_frame(self):
        d = self.data
        self.assertEqual(d["kind"], "short")
        self.assertEqual(d["film"]["frame"], {"width": 1080, "height": 1920})
        self.assertEqual(d["film"]["fit"], "fill")
        self.assertEqual(d["film"]["fps"], FPS_DICT)

    def test_the_fades_the_look_and_the_loudness_are_the_films(self):
        d = self.data
        self.assertEqual(d["film"]["fade_in_s"], 1.0)
        self.assertEqual(d["film"]["fade_out_s"], 2.0)
        self.assertEqual(d["film"]["audio_join"], self.film["film"]["audio_join"])
        self.assertEqual(d["look"], self.film["look"])
        self.assertEqual(d["audio"], self.film["audio"])

    def test_only_the_clips_in_use_are_listed(self):
        used = {s["source"] for s in self.data["segments"]}
        self.assertEqual(set(self.data["sources"]), used)

    def test_derived_from_names_the_film_the_length_and_the_settings(self):
        df = self.data["derived_from"]
        self.assertEqual(df["film"], "/work/timeline.json")
        self.assertEqual(df["target_length_s"], 60.0)
        s = df["settings"]
        self.assertEqual((s["aim_scene_s"], s["min_scene_s"], s["max_scene_s"]), (4.0, 2.0, 8.0))
        self.assertEqual(s["size"], [1080, 1920])
        self.assertEqual(len(s["film_sha256"]), 64)

    def test_the_export_is_mp4_h264_at_8_mbit_with_aac(self):
        e = self.data["exports"][0]
        self.assertEqual((e["width"], e["height"]), (1080, 1920))
        self.assertEqual(e["video"]["codec"], "h264_videotoolbox")
        self.assertEqual(e["video"]["rate_control"], {"mode": "bitrate", "mbps": 8})
        self.assertEqual(e["audio"], {"codec": "aac", "bitrate_kbps": 192, "sample_rate": 48000})
        self.assertEqual(e["subtitles"], "burned")
        self.assertTrue(e["output"].endswith(".mp4"))

    def test_the_encoder_of_the_films_export_is_taken(self):
        film = copy.deepcopy(self.film)
        film["exports"] = [{"name": "film", "output": "/x/film.mp4", "width": 4096, "height": 2160,
                            "video": {"codec": "libx264", "preset": "slow", "rate_control": {"mode": "bitrate", "mbps": 45}},
                            "audio": {"codec": "aac", "bitrate_kbps": 384, "sample_rate": 48000}}]
        plan = build(film, length_s=30)
        v = plan.data["exports"][0]["video"]
        self.assertEqual(v["codec"], "libx264")
        self.assertEqual(v["rate_control"], {"mode": "bitrate", "mbps": 8})

    def test_a_custom_size_is_the_frame_and_the_export(self):
        plan = build(self.film, length_s=30, size=(720, 1280))
        d = plan.data
        self.assertEqual(d["film"]["frame"], {"width": 720, "height": 1280})
        self.assertEqual((d["exports"][0]["width"], d["exports"][0]["height"]), (720, 1280))
        self.assertEqual([f for f in timeline.validate(d, file_checks=False) if f.level == "error"], [])

    def test_a_size_that_is_not_vertical_or_even_is_refused(self):
        for size in ((1920, 1080), (1081, 1920), (1080, 1921)):
            with self.assertRaises(shorts.ShortError, msg=size):
                build(self.film, length_s=30, size=size)

    def test_the_window_comes_from_the_full_size_clip(self):
        d = self.data
        import render
        plan = render.make_plan(timeline.Timeline(d), preview_width=1080)
        for s in plan.segments:
            src = d["sources"][s.source]["size"]
            self.assertEqual(s.placement["mode"], "fill")
            if src["height"] == 1720:
                self.assertEqual((s.placement["ww"], s.placement["wh"]), (968, 1720))
            else:
                self.assertEqual((s.placement["ww"], s.placement["wh"]), (1216, 2160))
            self.assertEqual(s.placement["wx"], render.round_even((src["width"] - s.placement["ww"]) * 0.5))

    def test_the_output_names_the_export_and_the_plan_file_sits_next_to_it(self):
        plan = build(self.film, length_s=30, output="/Volumes/X/exports/short-30.mp4")
        self.assertEqual(plan.data["exports"][0]["output"], "/Volumes/X/exports/short-30.mp4")
        self.assertEqual(plan.data["exports"][0]["name"], "short-30")
        self.assertEqual(plan.plan_path, "/Volumes/X/exports/short-30.json")


class CropTests(unittest.TestCase):
    def test_a_scene_without_crop_is_centred(self):
        plan = build(make_film([30] * 6), length_s=12)
        for s in short_segments(plan):
            self.assertNotIn("crop", s)

    def test_the_offset_of_a_scene_is_copied(self):
        film = make_film([30] * 6, crop={i: {"x": 0.3} for i in range(6)})
        plan = build(film, length_s=12)
        for s in short_segments(plan):
            self.assertEqual(s["crop"], {"x": 0.3})

    def test_a_pan_is_cut_to_the_window_and_keeps_its_speed(self):
        film = make_film([30], crop={0: {"x_from": 0.2, "x_to": 0.8}})
        plan = build(film, length_s=10)
        s = short_segments(plan)[0]
        L, a, n0 = frames(30), seg_frames(s), s["in"] - 100
        self.assertLess(a, L)
        film_x = lambda n: 0.2 + 0.6 * n / (L - 1)               # the render moves the window by n / (length - 1)
        short_x = lambda k: s["crop"]["x_from"] + (s["crop"]["x_to"] - s["crop"]["x_from"]) * k / (a - 1)
        for k in (0, 1, a // 2, a - 1):
            self.assertAlmostEqual(short_x(k), film_x(n0 + k), delta=2e-6)


class CueTests(unittest.TestCase):
    def cue(self, cid, anchor, a, b, text, origin="text"):
        return {"id": cid, "anchor": anchor, "start_s": a, "end_s": b, "text": text, "origin": origin}

    def test_a_cue_inside_the_window_moves_with_the_picture(self):
        film = make_film([40], cues=[self.cue("c001", "s001", 18.0, 20.5, "Listen to this.")])
        plan = build(film, length_s=6)
        seg = short_segments(plan)[0]
        cues = plan.data["subtitles"]["cues"]
        self.assertEqual(len(cues), 1)
        shift = (seg["in"] - 100) / FPS
        self.assertAlmostEqual(cues[0]["start_s"], 18.0 - shift, delta=0.04)
        self.assertAlmostEqual(cues[0]["end_s"], 20.5 - shift, delta=0.04)
        self.assertEqual(cues[0]["anchor"], "s001")
        self.assertEqual(cues[0]["text"], "Listen to this.")

    def test_a_cue_outside_the_window_is_dropped_and_listed(self):
        # two cues, 25 s apart in a scene of 40 s; a window of 6 s holds one of them (the one nearer the middle wins)
        cues = [self.cue("c001", "s001", 5.0, 7.0, "Early."), self.cue("c002", "s001", 30.0, 32.0, "Late.")]
        plan = build(make_film([40], cues=cues), length_s=6)
        self.assertEqual([c["text"] for c in plan.data["subtitles"]["cues"]], ["Late."])
        self.assertEqual([(c["id"], c["scene"]) for c in plan.cues_dropped], [("c001", "s001")])
        self.assertEqual(plan.cues_kept, ["c002"])

    def test_a_cut_cue_stays_only_with_most_of_it(self):
        # the cue is 3 s long; 0.8 of it is 2.4 s
        for kept_part, stays in ((2.6, True), (2.2, False)):
            with self.subTest(kept_part=kept_part):
                plan = shorts.keep_cue(10.0, 13.0, 10.0 + 3.0 - kept_part, 40.0)       # window starts inside the cue
                self.assertEqual(plan is not None, stays)

    def test_a_short_cue_needs_one_second_or_all_of_it(self):
        self.assertIsNone(shorts.keep_cue(10.0, 10.8, 10.3, 40.0))        # 0.5 s of 0.8 s: dropped
        self.assertEqual(shorts.keep_cue(10.0, 10.8, 5.0, 40.0), (10.0, 10.8))
        # 1.2 s cue: needs max(1.0, 0.96) = 1.0 s
        self.assertEqual(shorts.keep_cue(10.0, 11.2, 10.2, 40.0), (10.2, 11.2))
        self.assertIsNone(shorts.keep_cue(10.0, 11.2, 10.3, 40.0))

    def test_a_long_cue_is_split_into_cues_that_fit_two_lines_of_24_characters(self):
        text = "Now the pan is hot enough, so we add the sausage and wait for it to cook through."
        film = make_film([30], cues=[self.cue("c001", "s001", 10.0, 14.0, text)])
        plan = build(film, length_s=30)
        cues = plan.data["subtitles"]["cues"]
        self.assertGreater(len(cues), 1)
        for c in cues:
            lines = c["text"].split("\n")
            self.assertLessEqual(len(lines), 2)
            self.assertTrue(all(len(l) <= 24 for l in lines), c["text"])
        joined = " ".join(" ".join(c["text"].split()) for c in cues)
        self.assertEqual(joined, text)
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a["end_s"], b["start_s"] + 1e-9)

    def test_a_cue_of_a_16_9_film_with_two_lines_is_laid_out_again(self):
        text = "It takes a minute\nto get hot, so wait."
        film = make_film([30], cues=[self.cue("c001", "s001", 10.0, 14.0, text)])
        plan = build(film, length_s=30)
        for c in plan.data["subtitles"]["cues"]:
            for line in c["text"].split("\n"):
                self.assertLessEqual(len(line), 24)

    def test_the_cues_are_numbered_again_in_film_order_and_sit_on_the_new_scenes(self):
        cues = [self.cue("c010", "s002", 2.0, 4.0, "Second scene."), self.cue("c011", "s001", 2.0, 4.0, "First scene.")]
        film = make_film([8, 8], cues=cues)
        plan = build(film, length_s=16)
        got = plan.data["subtitles"]["cues"]
        self.assertEqual([c["id"] for c in got], ["c001", "c002"])
        self.assertEqual([c["anchor"] for c in got], ["s001", "s002"])
        self.assertEqual([c["text"] for c in got], ["First scene.", "Second scene."])

    def test_the_cues_make_a_valid_file(self):
        cues = [self.cue(f"c{i + 1:03d}", f"s{i + 1:03d}", 1.0, 3.5, f"Words of scene {i + 1}.") for i in range(10)]
        film = make_film([20] * 10, cues=cues, text={i: "Words" for i in range(10)})
        plan = build(film, length_s=40)
        errors = [f for f in timeline.validate(plan.data, file_checks=False) if f.level == "error"]
        self.assertEqual(errors, [])

    def test_the_text_box_of_a_scene_is_not_copied(self):
        film = make_film([30], text={0: "Words in the box."},
                         cues=[self.cue("c001", "s001", 10.0, 12.0, "Words in the box.")])
        plan = build(film, length_s=30)
        self.assertNotIn("text", short_segments(plan)[0])


class TitleTests(unittest.TestCase):
    def test_a_title_of_a_scene_stays_with_the_scene(self):
        t = {"id": "t001", "text": "Sausage", "anchor": "s001", "start_s": 14.0, "end_s": 19.0, "template": "lower_third"}
        film = make_film([40], titles=[t])
        plan = build(film, length_s=20)
        titles = plan.data["titles"]
        self.assertEqual(len(titles), 1)
        self.assertEqual(titles[0]["anchor"], "s001")
        self.assertEqual(titles[0]["template"], "lower_third")

    def test_a_title_of_the_film_goes_to_the_scene_that_held_its_start(self):
        t = {"id": "t001", "text": "Opening", "anchor": "film", "start_s": 1.0, "end_s": 5.0, "template": "center"}
        film = make_film([40, 40], titles=[t])
        plan = build(film, length_s=40)
        # the first scene of the short is cut from the middle of the film scene: the title at 1 to 5 s is not in it
        self.assertEqual(plan.titles_dropped, ["t001"])

    def test_a_title_of_the_film_that_lies_in_a_kept_window_is_kept(self):
        t = {"id": "t001", "text": "Mid", "anchor": "film", "start_s": 52.0, "end_s": 56.0, "template": "center"}
        film = make_film([40, 40], titles=[t])
        plan = build(film, length_s=70)
        titles = plan.data.get("titles") or []
        self.assertEqual([x["text"] for x in titles], ["Mid"])
        self.assertEqual(titles[0]["anchor"], "s002")
        self.assertNotEqual(titles[0]["anchor"], "film")


if __name__ == "__main__":
    unittest.main()
