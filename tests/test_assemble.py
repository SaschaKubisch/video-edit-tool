"""Check 2.7: assembly (SPECS 7.5; decisions D6 clap only at the start, D7 every
steady scene and "Leave it out, list it", D8 hard cut with a sound crossfade and
film fades). `assemble.build` is a pure function: clips with their clap and scene
results go in, a timeline (version 1) and a report come out."""

import copy
import unittest

import assemble
import clap
import config
import timeline

FPS = {"num": 30000, "den": 1001}
LUT = {"path": "/L/Gen 5 Film to Video.cube", "sha256": "ab" * 32}


def clip(stem, frames=900, runs=((30, 400), (500, 880)), clap_time=0.4, clap_result="agree",
         copy_ok=True, fps=None, scenes=True, offset=0.25, review=False):
    scenes_data = None
    if scenes:
        scenes_data = {"runs": [{"in": a, "out": b, "score": 0.7, "low_confidence": False}
                                for a, b in runs], "dropped": [], "moving": []}
    clap_data = None
    if clap_result is not None:
        clap_data = {"result": clap_result,
                     "video_time_s": None if clap_result == "none" else clap_time,
                     "review": review or clap_result != "agree",
                     "reasons": [] if clap_result == "agree" and not review else ["see the clap report"]}
    return assemble.ClipInput(
        stem=stem, original_video=f"/O/{stem}.braw", audio=f"/O/{stem}.WAV", offset_s=offset,
        preview_copy=f"/W/copies/{stem}.mp4" if copy_ok else None, frames=frames,
        fps=fps or dict(FPS), size={"width": 4096, "height": 2160}, lut_sha256=LUT["sha256"],
        copy_ok=copy_ok, clap=clap_data, scenes=scenes_data)


def build(clips, **kw):
    return assemble.build(clips, LUT, **kw)


def seg_pairs(a):
    return [(s["source"], s["in"], s["out"]) for s in a.data["segments"]]


class OrderAndScenesTests(unittest.TestCase):
    def test_clips_in_the_order_given_and_ids_run_on(self):
        a = build([clip("C001"), clip("C002"), clip("C003")])
        self.assertEqual([s["id"] for s in a.data["segments"]],
                         [f"s{i:03d}" for i in range(1, 7)])
        self.assertEqual([s["source"] for s in a.data["segments"]],
                         ["C001", "C001", "C002", "C002", "C003", "C003"])
        self.assertEqual(list(a.data["sources"]), ["C001", "C002", "C003"])

    def test_a_clip_with_two_scenes_gives_two_segments_in_time_order(self):
        a = build([clip("C001", runs=((30, 400), (500, 880)), clap_time=0.0)])
        self.assertEqual(seg_pairs(a), [("C001", 30, 400), ("C001", 500, 880)])
        self.assertEqual([s["origin"] for s in a.data["segments"]], ["assembly", "assembly"])

    def test_every_steady_scene_is_kept_and_only_the_movement_is_dropped(self):
        a = build([clip("C001", runs=((10, 100), (200, 300), (400, 500)), clap_time=-1)])
        self.assertEqual(len(a.data["segments"]), 3)

    def test_the_clap_cut_raises_the_first_segment_in_point(self):
        # clap at 2.0 s + 0.5 s guard = 75 s * 29.97 / ... : first frame ceil(2.5 * 30000/1001) = 75
        a = build([clip("C001", runs=((10, 400),), clap_time=2.0, frames=900)])
        want = clap.first_frame_after(2.0, 30000, 1001)
        self.assertEqual(want, 75)
        self.assertEqual(seg_pairs(a), [("C001", want, 400)])
        self.assertIn("clap", a.data["segments"][0]["note"])

    def test_a_scene_before_the_clap_cut_is_dropped_and_listed(self):
        a = build([clip("C001", runs=((10, 120), (300, 500)), clap_time=5.0, frames=900)])
        self.assertEqual(seg_pairs(a), [("C001", 300, 500)])
        entry = a.report["clips"][0]
        self.assertEqual(len(entry["scenes_dropped"]), 1)
        self.assertIn("clap", entry["scenes_dropped"][0]["reason"])

    def test_a_scene_that_is_too_short_after_the_clap_cut_is_dropped(self):
        # clap at 3 s -> earliest frame ceil(3.5 * 29.97) = 105; run (40, 150) leaves 45 frames = 1.5 s
        a = build([clip("C001", runs=((40, 150), (300, 500)), clap_time=3.0)])
        self.assertEqual(seg_pairs(a), [("C001", 300, 500)])

    def test_a_clip_without_a_clap_starts_at_its_first_steady_frame_and_is_listed(self):
        a = build([clip("C001", runs=((45, 400),), clap_result="none")])
        self.assertEqual(seg_pairs(a), [("C001", 45, 400)])
        entry = a.report["clips"][0]
        self.assertTrue(entry["review"])
        self.assertEqual(entry["clap"]["result"], "none")
        self.assertTrue(any("clap" in r for r in entry["review_reasons"]))

    def test_a_clip_whose_clap_was_not_analysed_is_flagged(self):
        a = build([clip("C001", clap_result=None)])
        entry = a.report["clips"][0]
        self.assertTrue(entry["review"])
        self.assertTrue(any("clap" in r for r in entry["review_reasons"]))

    def test_a_clip_with_no_steady_scene_is_left_out_and_listed(self):
        a = build([clip("C001"), clip("C002", runs=()), clip("C003")])
        self.assertEqual({s["source"] for s in a.data["segments"]}, {"C001", "C003"})
        self.assertNotIn("C002", a.data["sources"])
        left = [c for c in a.report["clips"] if c["status"] == "left_out"]
        self.assertEqual([c["clip"] for c in left], ["C002"])
        self.assertIn("steady scene", left[0]["reason"])
        self.assertEqual(a.report["film"]["left_out"], ["C002"])

    def test_a_clip_without_a_finished_preview_copy_is_left_out(self):
        a = build([clip("C001"), clip("C002", copy_ok=False)])
        self.assertEqual(list(a.data["sources"]), ["C001"])
        self.assertIn("preview copy", a.report["clips"][1]["reason"])

    def test_a_clip_with_another_frame_rate_is_left_out(self):
        a = build([clip("C001"), clip("C002", fps={"num": 25, "den": 1})])
        self.assertEqual(list(a.data["sources"]), ["C001"])
        self.assertIn("frame rate", a.report["clips"][1]["reason"])

    def test_a_clip_without_scene_analysis_becomes_one_unanalysed_segment(self):
        a = build([clip("C001", scenes=False, clap_time=1.0)])
        want = clap.first_frame_after(1.0, 30000, 1001)
        self.assertEqual(seg_pairs(a), [("C001", want, 900)])
        self.assertIn("unanalysed", a.data["segments"][0]["note"])
        self.assertTrue(a.report["clips"][0]["review"])

    def test_nothing_to_assemble_is_an_error(self):
        with self.assertRaises(assemble.AssemblyError):
            build([clip("C001", runs=())])


class FilmFieldsTests(unittest.TestCase):
    def setUp(self):
        self.a = build([clip("C001"), clip("C002")])
        self.d = self.a.data

    def test_the_file_is_a_valid_timeline_of_version_1(self):
        self.assertEqual(timeline.validate(copy.deepcopy(self.d), file_checks=False), [])
        self.assertEqual((self.d["version"], self.d["kind"]), (1, "film"))

    def test_film_fields_follow_the_decisions(self):
        f = self.d["film"]
        self.assertEqual(f["fps"], FPS)
        self.assertEqual(f["frame"], {"width": 4096, "height": 2160})
        self.assertEqual(f["fit"], "fit")
        self.assertEqual((f["fade_in_s"], f["fade_out_s"]), (1.0, 2.0))
        self.assertEqual(f["audio_join"], {"duration_s": 0.5, "curve": "equal_power"})

    def test_sources_carry_what_the_render_needs(self):
        s = self.d["sources"]["C001"]
        self.assertEqual(s["original_video"], "/O/C001.braw")
        self.assertEqual(s["preview_copy"], "/W/copies/C001.mp4")
        self.assertEqual(s["audio"], "/O/C001.WAV")
        self.assertEqual(s["audio_offset_s"], 0.25)
        self.assertEqual(s["frames"], 900)
        self.assertEqual(s["fps"], FPS)
        self.assertEqual(s["size"], {"width": 4096, "height": 2160})
        self.assertEqual(s["copy_lut_sha256"], LUT["sha256"])

    def test_look_audio_and_loudness(self):
        self.assertEqual(self.d["look"]["base"], {"mode": "lut", **LUT})
        self.assertEqual(self.d["audio"]["sample_rate"], 48000)
        self.assertEqual(self.d["audio"]["loudness"], {"mode": "off"})

    def test_segment_labels_and_notes(self):
        s = self.d["segments"][1]
        self.assertIn("C001", s["label"])
        self.assertIn("2", s["label"])         # the second scene of the clip
        self.assertTrue(s["note"])

    def test_the_lengths_are_settings(self):
        st = assemble.AssembleSettings(fade_in_s=0.5, fade_out_s=1.5, join_s=0.25,
                                       min_run_sec=1.0, frame=(1920, 1080))
        a = build([clip("C001", runs=((10, 60), (100, 400)), clap_time=-1)], settings=st)
        f = a.data["film"]
        self.assertEqual((f["fade_in_s"], f["fade_out_s"]), (0.5, 1.5))
        self.assertEqual(f["audio_join"]["duration_s"], 0.25)
        self.assertEqual(f["frame"], {"width": 1920, "height": 1080})
        self.assertEqual(len(a.data["segments"]), 2)       # 50 frames = 1.67 s kept with a 1 s minimum


class FadesThatDoNotFitTests(unittest.TestCase):
    def test_a_last_scene_too_short_for_the_fade_out_shortens_the_fade_and_says_so(self):
        # 2.0 s last scene: the fade-out of 2.0 s plus half a join does not fit (rule V11)
        a = build([clip("C001", runs=((30, 400), (500, 560)), clap_time=-1)],
                  settings=assemble.AssembleSettings(min_run_sec=1.0))
        self.assertEqual(timeline.validate(copy.deepcopy(a.data), file_checks=False), [])
        self.assertLess(a.data["film"]["fade_out_s"], 2.0)
        self.assertTrue(a.report["film"]["fades_shortened"])
        self.assertTrue(any("fade-out" in w for w in a.report["film"]["warnings"]))

    def test_a_film_of_one_short_scene_shortens_both_fades(self):
        a = build([clip("C001", runs=((30, 100),), clap_time=-1)],
                  settings=assemble.AssembleSettings(min_run_sec=1.0))
        self.assertEqual(timeline.validate(copy.deepcopy(a.data), file_checks=False), [])
        f = a.data["film"]
        length = 70 * 1001 / 30000
        self.assertLessEqual(f["fade_in_s"] + f["fade_out_s"], length + 1e-6)

    def test_fades_that_fit_are_left_as_they_are(self):
        a = build([clip("C001")])
        self.assertFalse(a.report["film"]["fades_shortened"])
        self.assertEqual(a.data["film"]["fade_out_s"], 2.0)


class ReportTests(unittest.TestCase):
    def test_the_report_names_what_was_decided_for_every_clip(self):
        a = build([clip("C001", runs=((40, 150), (300, 500)), clap_time=3.0),
                   clip("C002", runs=()), clip("C003", clap_result="none")])
        r = a.report
        self.assertEqual([c["clip"] for c in r["clips"]], ["C001", "C002", "C003"])
        c1 = r["clips"][0]
        for key in ("status", "seconds", "kept_seconds", "segments", "clap", "earliest_frame",
                    "scenes_found", "scenes_dropped", "review", "review_reasons"):
            self.assertIn(key, c1)
        self.assertEqual(c1["segments"], ["s001"])
        self.assertEqual(c1["scenes_found"], 2)
        self.assertEqual(c1["earliest_frame"], clap.first_frame_after(3.0, 30000, 1001))
        self.assertEqual(r["film"]["segments"], len(a.data["segments"]))
        self.assertEqual(r["film"]["clips_used"], 2)
        self.assertEqual(r["film"]["left_out"], ["C002"])

    def test_the_text_report_lists_the_clips_left_out(self):
        a = build([clip("C001"), clip("C002", runs=()), clip("C003", clap_result="none")])
        text = assemble.format_report(a.report)
        self.assertIn("C002", text)
        self.assertIn("left out", text.lower())
        self.assertIn("C003", text)
        self.assertIn("no clap", text.lower())


if __name__ == "__main__":
    unittest.main()
