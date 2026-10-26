"""Check 2.2: for each validation rule V1 to V22 (SPECS 3.5) a broken copy of
the example. The validator must report that rule at the expected field path,
and the valid example must report nothing."""

import json
import os
import tempfile
import unittest
from pathlib import Path

import timeline
from tests.timeline_fixture import example, short_example

SRC = "A002_01011540_C002"        # first source of the example (4096 x 1720)
SRC2 = "A002_01060615_C083"       # second source (4096 x 2160)


class RuleCase(unittest.TestCase):
    def findings(self, data, **kw):
        kw.setdefault("file_checks", False)
        return timeline.validate(data, **kw)

    def expect(self, data, rule, path, level="error", only=True, **kw):
        """`rule` is reported at `path` with `level`. With only=True nothing else is."""
        found = self.findings(data, **kw)
        got = [(f.rule, f.path, f.level) for f in found]
        self.assertIn((rule, path, level), got, f"expected {(rule, path, level)} in {got}")
        if only:
            self.assertEqual({r for r, _, _ in got}, {rule}, f"other findings: {got}")
        return [f for f in found if f.rule == rule and f.path == path][0]

    def expect_clean(self, data, **kw):
        found = self.findings(data, **kw)
        self.assertEqual([(f.rule, f.path, f.message) for f in found], [])


class ValidExampleTests(RuleCase):
    def test_the_example_reports_nothing(self):
        self.expect_clean(example())

    def test_a_valid_short_reports_nothing_except_the_file_check_off(self):
        self.expect_clean(short_example())

    def test_findings_serialise_as_in_the_specification(self):
        d = example()
        d["segments"][1]["out"] = 3000
        f = self.expect(d, "V9", "segments[1].out")
        self.assertEqual(set(f.as_dict()), {"level", "rule", "path", "message", "hint"})
        self.assertEqual(f.message, f"out 3000 is greater than the 2915 frames of source {SRC}")


class V0StructureTests(RuleCase):
    def test_a_missing_required_key(self):
        d = example()
        del d["film"]
        self.expect(d, "V0", "film", only=False)

    def test_a_missing_required_key_in_a_segment(self):
        d = example()
        del d["segments"][0]["out"]
        self.expect(d, "V0", "segments[0].out", only=False)

    def test_wrong_types(self):
        d = example()
        d["segments"] = {}
        self.expect(d, "V0", "segments", only=False)
        d = example()
        d["segments"][0]["in"] = "38"
        self.expect(d, "V0", "segments[0].in", only=False)
        d = example()
        d["segments"][0]["in"] = True            # a bool is not an integer
        self.expect(d, "V0", "segments[0].in", only=False)
        d = example()
        d["segments"][0]["enabled"] = "yes"
        self.expect(d, "V0", "segments[0].enabled", only=False)

    def test_film_fit_must_be_fit_or_fill(self):
        d = example()
        d["film"]["fit"] = "stretch"
        self.expect(d, "V0", "film.fit", only=False)

    def test_look_base_mode(self):
        d = example()
        d["look"]["base"]["mode"] = "auto"
        self.expect(d, "V0", "look.base.mode", only=False)

    def test_lut_mode_needs_path_and_hash(self):
        d = example()
        del d["look"]["base"]["path"]
        self.expect(d, "V0", "look.base.path", only=False)

    def test_not_an_object_at_the_top(self):
        found = timeline.validate([], file_checks=False)
        self.assertEqual([(f.rule, f.path) for f in found], [("V0", "")])


class V1VersionTests(RuleCase):
    def test_unknown_version(self):
        d = example()
        d["version"] = 2
        f = self.expect(d, "V1", "version", only=False)
        self.assertIn("update", f.hint.lower())

    def test_version_must_be_an_integer(self):
        d = example()
        d["version"] = "1"
        self.expect(d, "V1", "version", only=False)

    def test_missing_version(self):
        d = example()
        del d["version"]
        self.expect(d, "V1", "version", only=False)


class V2UnknownKeyTests(RuleCase):
    def test_unknown_key_names_the_closest_known_key(self):
        d = example()
        d["film"]["fade_in"] = 1.0
        f = self.expect(d, "V2", "film.fade_in")
        self.assertIn("fade_in_s", f.hint)

    def test_unknown_key_in_other_objects(self):
        cases = [
            (lambda d: d["segments"][0].__setitem__("labl", "x"), "segments[0].labl"),
            (lambda d: d["sources"][SRC].__setitem__("frame", 1), f"sources.{SRC}.frame"),
            (lambda d: d["look"]["base"].__setitem__("hash", "x"), "look.base.hash"),
            (lambda d: d["audio"]["loudness"].__setitem__("target", -14), "audio.loudness.target"),
            (lambda d: d["exports"][0]["video"].__setitem__("bitrate", 1), "exports[0].video.bitrate"),
            (lambda d: d["titles"][0].__setitem__("txt", "x"), "titles[0].txt"),
            (lambda d: d["subtitles"]["cues"][0].__setitem__("anchr", "s001"), "subtitles.cues[0].anchr"),
            (lambda d: d["film"]["fps"].__setitem__("numerator", 1), "film.fps.numerator"),
            (lambda d: d.__setitem__("segmentz", []), "segmentz"),
        ]
        for mutate, path in cases:
            d = example()
            mutate(d)
            with self.subTest(path=path):
                self.expect(d, "V2", path)

    def test_keys_that_start_with_x_are_free_everywhere(self):
        d = example()
        d["x_a"] = 1
        d["film"]["x_a"] = 1
        d["film"]["fps"]["x_a"] = 1
        d["sources"][SRC]["x_a"] = 1
        d["segments"][0]["x_a"] = 1
        d["segments"][2]["audio_join"]["x_a"] = 1
        d["look"]["x_a"] = 1
        d["look"]["base"]["x_a"] = 1
        d["audio"]["x_a"] = 1
        d["audio"]["loudness"]["x_a"] = 1
        d["titles"][0]["x_a"] = 1
        d["subtitles"]["x_a"] = 1
        d["subtitles"]["style"]["x_a"] = 1
        d["subtitles"]["cues"][0]["x_a"] = 1
        d["exports"][0]["x_a"] = 1
        d["exports"][0]["video"]["x_a"] = 1
        d["exports"][0]["video"]["rate_control"]["x_a"] = 1
        d["exports"][0]["audio"]["x_a"] = 1
        self.expect_clean(d)


class V3KindTests(RuleCase):
    def test_unknown_kind(self):
        d = example()
        d["kind"] = "movie"
        self.expect(d, "V3", "kind", only=False)

    def test_short_needs_derived_from(self):
        d = short_example()
        del d["derived_from"]
        self.expect(d, "V3", "derived_from", only=False)

    def test_film_must_not_have_derived_from(self):
        d = example()
        d["derived_from"] = {"film": "/x", "target_length_s": 10, "settings": {}}
        self.expect(d, "V3", "derived_from", only=False)


class V4FpsTests(RuleCase):
    def test_film_fps_must_be_positive_integers(self):
        for field, value in (("num", 0), ("den", -1), ("num", 29.97), ("den", "1001")):
            d = example()
            d["film"]["fps"][field] = value
            with self.subTest(field=field, value=value):
                self.expect(d, "V4", f"film.fps.{field}", only=False)

    def test_source_fps_must_equal_film_fps(self):
        d = example()
        d["sources"][SRC]["fps"] = {"num": 25, "den": 1}
        self.expect(d, "V4", f"sources.{SRC}.fps")

    def test_equal_ratio_written_differently_is_equal(self):
        d = example()
        d["sources"][SRC]["fps"] = {"num": 60000, "den": 2002}
        self.expect_clean(d)


class V5FrameSizeTests(RuleCase):
    def test_film_frame_must_be_even_positive_integers(self):
        for field, value in (("width", 4095), ("height", 0), ("width", -2), ("height", 21.5)):
            d = example()
            d["film"]["frame"][field] = value
            with self.subTest(field=field, value=value):
                self.expect(d, "V5", f"film.frame.{field}", only=False)

    def test_source_size_must_be_even_positive_integers(self):
        d = example()
        d["sources"][SRC]["size"]["height"] = 1721
        self.expect(d, "V5", f"sources.{SRC}.size.height")

    def test_export_must_have_the_shape_of_the_film(self):
        d = example()
        d["exports"][0]["width"] = 1920
        d["exports"][0]["height"] = 1200
        self.expect(d, "V5", "exports[0].height")

    def test_export_of_another_size_with_the_same_shape_is_allowed(self):
        d = example()
        d["exports"][0]["width"] = 1920
        d["exports"][0]["height"] = 1012          # 1011.97 rounded to an even number
        self.expect_clean(d)

    def test_export_sizes_must_be_even(self):
        d = example()
        d["exports"][0]["width"] = 1921
        d["exports"][0]["height"] = 1013
        self.expect(d, "V5", "exports[0].width", only=False)


class V6IdTests(RuleCase):
    def test_source_id_characters(self):
        d = example()
        d["sources"]["bad id"] = d["sources"].pop(SRC)
        for s in d["segments"]:
            if s["source"] == SRC:
                s["source"] = "bad id"
        self.expect(d, "V6", "sources.bad id")

    def test_segment_title_and_cue_id_forms(self):
        for mutate, path in (
                (lambda d: d["segments"][0].__setitem__("id", "x1"), "segments[0].id"),
                (lambda d: d["segments"][0].__setitem__("id", "s"), "segments[0].id"),
                (lambda d: d["titles"][0].__setitem__("id", "s001"), "titles[0].id"),
                (lambda d: d["subtitles"]["cues"][0].__setitem__("id", "cue1"), "subtitles.cues[0].id")):
            d = example()
            mutate(d)
            with self.subTest(path=path):
                self.expect(d, "V6", path, only=False)

    def test_duplicate_ids_in_a_list(self):
        d = example()
        d["segments"][1]["id"] = "s001"
        self.expect(d, "V6", "segments[1].id")
        d = example()
        d["titles"].append(dict(d["titles"][0]))
        self.expect(d, "V6", "titles[1].id")
        d = example()
        d["subtitles"]["cues"].append(dict(d["subtitles"]["cues"][0]))
        self.expect(d, "V6", "subtitles.cues[1].id")

    def test_duplicate_source_id_in_the_json_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            text = json.dumps(example())
            text = text.replace(f'"sources": {{"{SRC}"', f'"sources": {{"{SRC}": {{}}, "{SRC}"')
            path.write_text(text)
            tl = timeline.Timeline.load(str(path))
            found = tl.validate(file_checks=False)
            self.assertTrue(any(f.rule == "V6" and "duplicate" in f.message for f in found),
                            [(f.rule, f.message) for f in found])


class V7FileTests(RuleCase):
    def make(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        d = example()
        for sid, src in d["sources"].items():
            for key in ("original_video", "preview_copy", "audio"):
                p = root / f"{sid}.{key}"
                p.write_bytes(b"x")
                src[key] = str(p)
        # LUT files for rule V16
        base = root / "base.cube"
        base.write_bytes(b"base")
        lut = root / "lut.cube"
        lut.write_bytes(b"lut")
        import hashlib
        d["look"]["base"]["path"] = str(base)
        d["look"]["base"]["sha256"] = hashlib.sha256(b"base").hexdigest()
        d["look"]["lut"]["path"] = str(lut)
        d["look"]["lut"]["sha256"] = hashlib.sha256(b"lut").hexdigest()
        for src in d["sources"].values():
            src["copy_lut_sha256"] = d["look"]["base"]["sha256"]
        self.root = root
        return d

    def counter(self, d):
        frames = {src["preview_copy"]: src["frames"] for src in d["sources"].values()}
        return lambda path: frames[path]

    def test_all_files_present_and_frames_equal(self):
        d = self.make()
        self.expect_clean(d, file_checks=True, frame_counter=self.counter(d))

    def test_missing_files_are_named_by_field(self):
        for key in ("preview_copy", "audio", "original_video"):
            d = self.make()
            os.unlink(d["sources"][SRC][key])
            with self.subTest(key=key):
                self.expect(d, "V7", f"sources.{SRC}.{key}", file_checks=True,
                            frame_counter=lambda p: 805 if p.endswith("C083.preview_copy") else 2915,
                            only=False)

    def test_frames_must_equal_the_frame_count_of_the_preview_copy(self):
        d = self.make()
        counts = self.counter(d)
        self.expect(d, "V7", f"sources.{SRC}.frames", file_checks=True,
                    frame_counter=lambda p: counts(p) - 1 if SRC in p else counts(p))

    def test_with_file_checks_off_nothing_is_read(self):
        d = example()          # the paths of the example do not exist
        self.expect_clean(d, file_checks=False)

    def test_the_default_frame_counter_reads_a_real_file(self):
        import shutil
        import subprocess
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg is missing")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clip = Path(tmp.name) / "c.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=64x36:rate=30000/1001", "-t", "1", "-c:v", "libx264",
                        str(clip)], check=True)
        self.assertEqual(timeline.count_video_frames(str(clip)), 30)


class V8SourceTests(RuleCase):
    def test_segment_names_a_source_that_does_not_exist(self):
        d = example()
        d["segments"][0]["source"] = "NOPE"
        self.expect(d, "V8", "segments[0].source")


class V9RangeTests(RuleCase):
    def test_out_beyond_the_frames_of_the_source(self):
        d = example()
        d["segments"][1]["out"] = 3000
        f = self.expect(d, "V9", "segments[1].out")
        self.assertIn("3000", f.message)
        self.assertIn("2915", f.message)
        self.assertEqual(f.hint, "use a value of 2915 or less")

    def test_in_must_not_be_negative(self):
        d = example()
        d["segments"][0]["in"] = -1
        self.expect(d, "V9", "segments[0].in", only=False)

    def test_out_must_be_greater_than_in(self):
        d = example()
        d["segments"][0]["out"] = d["segments"][0]["in"]
        self.expect(d, "V9", "segments[0].out", only=False)

    def test_out_equal_to_the_frames_is_allowed(self):
        d = example()
        d["segments"][1]["out"] = 2915
        self.expect_clean(d)


class V10EnabledTests(RuleCase):
    def test_all_segments_disabled(self):
        d = example()
        for s in d["segments"]:
            s["enabled"] = False
        self.expect(d, "V10", "segments", only=False)

    def test_no_segments(self):
        d = example()
        d["segments"] = []
        d["titles"] = []
        d["subtitles"]["cues"] = []
        self.expect(d, "V10", "segments", only=False)


class V11LengthTests(RuleCase):
    def test_a_middle_segment_shorter_than_its_two_half_joins(self):
        d = example()
        d["film"]["audio_join"]["duration_s"] = 2.0        # the join into s002
        d["segments"][2]["audio_join"]["duration_s"] = 2.0  # the join into s003
        s2 = d["segments"][1]
        s2["out"] = s2["in"] + 50                          # 1.67 s < 1 s + 1 s
        self.expect(d, "V11", "segments[1].out")

    def test_exactly_long_enough_passes(self):
        d = example()
        d["film"]["audio_join"]["duration_s"] = 2.0
        d["segments"][2]["audio_join"]["duration_s"] = 2.0
        s2 = d["segments"][1]
        # needs exactly 2 s: 59.94 frames, so 60 frames pass
        s2["out"] = s2["in"] + 60
        self.expect_clean(d)

    def test_first_segment_must_hold_the_fade_in_and_half_the_next_join(self):
        d = example()
        s1 = d["segments"][0]
        s1["out"] = s1["in"] + 36                          # 1.2 s < 1.0 + 0.25
        self.expect(d, "V11", "segments[0].out", only=False)    # the cue now reaches outside

    def test_last_segment_must_hold_half_the_join_before_and_the_fade_out(self):
        d = example()
        s3 = d["segments"][2]
        s3["out"] = s3["in"] + 60                          # 2.0 s < 0.125 + 2.0
        self.expect(d, "V11", "segments[2].out")

    def test_a_single_segment_must_hold_both_fades(self):
        d = example()
        d["segments"] = [{"id": "s001", "source": SRC2, "in": 0, "out": 80}]   # 2.67 s < 3.0
        d["titles"] = []
        d["subtitles"]["cues"] = []
        self.expect(d, "V11", "segments[0].out")

    def test_disabled_segments_take_no_part(self):
        d = example()
        d["segments"][1]["enabled"] = False
        d["segments"][1]["out"] = d["segments"][1]["in"] + 1
        self.expect_clean(d)


class V12JoinTests(RuleCase):
    def test_duration_out_of_range(self):
        for value in (6.0, -0.5):
            d = example()
            d["film"]["audio_join"]["duration_s"] = value
            with self.subTest(value=value):
                self.expect(d, "V12", "film.audio_join.duration_s", only=False)

    def test_curve_must_be_known(self):
        d = example()
        d["segments"][2]["audio_join"]["curve"] = "cosine"
        self.expect(d, "V12", "segments[2].audio_join.curve")

    def test_join_on_the_first_enabled_segment_is_ignored_with_a_warning(self):
        d = example()
        d["segments"][0]["audio_join"] = {"duration_s": 0.5, "curve": "linear"}
        self.expect(d, "V12", "segments[0].audio_join", level="warning")

    def test_the_first_enabled_segment_is_the_first_that_is_enabled(self):
        d = example()
        d["segments"][0]["enabled"] = False
        d["segments"][1]["audio_join"] = {"duration_s": 0.5, "curve": "linear"}
        self.expect(d, "V12", "segments[1].audio_join", level="warning")


class V13CropTests(RuleCase):
    def fill(self):
        d = example()
        d["film"]["fit"] = "fill"
        return d

    def test_x_alone_is_valid_in_fill(self):
        d = self.fill()
        d["segments"][0]["crop"] = {"x": 0.25}
        self.expect_clean(d)

    def test_x_from_with_x_to_is_valid(self):
        d = self.fill()
        d["segments"][0]["crop"] = {"x_from": 0.0, "x_to": 1.0}
        self.expect_clean(d)

    def test_x_cannot_be_mixed_with_x_from(self):
        d = self.fill()
        d["segments"][0]["crop"] = {"x": 0.5, "x_from": 0.0, "x_to": 1.0}
        self.expect(d, "V13", "segments[0].crop")

    def test_x_from_needs_x_to(self):
        d = self.fill()
        d["segments"][0]["crop"] = {"x_from": 0.0}
        self.expect(d, "V13", "segments[0].crop")

    def test_values_between_zero_and_one(self):
        d = self.fill()
        d["segments"][0]["crop"] = {"x": 1.5}
        self.expect(d, "V13", "segments[0].crop.x")

    def test_crop_in_a_fit_film_has_no_effect_and_warns(self):
        d = example()
        d["segments"][0]["crop"] = {"x": 0.5}
        self.expect(d, "V13", "segments[0].crop", level="warning")

    def test_film_without_fit_key_counts_as_fit(self):
        d = example()
        del d["film"]["fit"]
        d["segments"][0]["crop"] = {"x": 0.5}
        self.expect(d, "V13", "segments[0].crop", level="warning")


class V14GainTests(RuleCase):
    def test_gain_range(self):
        for value in (40, -31):
            d = example()
            d["segments"][0]["gain_db_adjust"] = value
            with self.subTest(value=value):
                self.expect(d, "V14", "segments[0].gain_db_adjust")

    def test_gain_at_the_limit_is_allowed(self):
        d = example()
        d["segments"][0]["gain_db_adjust"] = -30
        self.expect_clean(d)


class V15AnchorTests(RuleCase):
    def test_title_anchor_must_exist(self):
        d = example()
        d["titles"][0]["anchor"] = "s009"
        self.expect(d, "V15", "titles[0].anchor")

    def test_cue_anchor_must_be_a_segment(self):
        d = example()
        d["subtitles"]["cues"][0]["anchor"] = "film"
        self.expect(d, "V15", "subtitles.cues[0].anchor")

    def test_start_must_be_before_end(self):
        d = example()
        d["titles"][0]["start_s"] = 5.0
        d["titles"][0]["end_s"] = 5.0
        self.expect(d, "V15", "titles[0].end_s")
        d = example()
        d["subtitles"]["cues"][0]["start_s"] = 7.0
        self.expect(d, "V15", "subtitles.cues[0].end_s")

    def test_interval_beyond_its_segment_is_a_warning(self):
        d = example()
        d["subtitles"]["cues"][0]["end_s"] = 40.0            # s001 is 28.8 s long
        self.expect(d, "V15", "subtitles.cues[0].end_s", level="warning")

    def test_title_beyond_the_film_is_a_warning(self):
        d = example()
        d["titles"][0]["end_s"] = 100.0                      # the film is 84.8 s long
        self.expect(d, "V15", "titles[0].end_s", level="warning")

    def test_title_on_a_segment_is_checked_against_the_segment(self):
        d = example()
        d["titles"][0]["anchor"] = "s003"
        d["titles"][0]["end_s"] = 30.0                       # s003 is 20.7 s long
        self.expect(d, "V15", "titles[0].end_s", level="warning")


class V16LutTests(V7FileTests):
    def test_interp_must_be_tetrahedral_without_reading_files(self):
        d = example()
        d["look"]["lut"]["interp"] = "trilinear"
        self.expect(d, "V16", "look.lut.interp")

    def test_hashes_equal_the_files(self):
        d = self.make()
        self.expect_clean(d, file_checks=True, frame_counter=self.counter(d))

    def test_changed_creative_lut_fails(self):
        d = self.make()
        Path(d["look"]["lut"]["path"]).write_bytes(b"changed")
        self.expect(d, "V16", "look.lut.sha256", file_checks=True,
                    frame_counter=self.counter(d))

    def test_changed_base_lut_fails(self):
        d = self.make()
        Path(d["look"]["base"]["path"]).write_bytes(b"changed")
        found = self.findings(d, file_checks=True, frame_counter=self.counter(d))
        self.assertIn(("V16", "look.base.sha256", "error"),
                      [(f.rule, f.path, f.level) for f in found])

    def test_missing_lut_file_fails(self):
        d = self.make()
        os.unlink(d["look"]["lut"]["path"])
        self.expect(d, "V16", "look.lut.path", file_checks=True,
                    frame_counter=self.counter(d))

    def test_base_mode_none_needs_no_file(self):
        d = example()
        d["look"]["base"] = {"mode": "none"}
        for s in d["sources"].values():
            s["copy_lut_sha256"] = "1" * 64
        self.expect_clean(d)

    def test_preview_copy_with_another_base_lut_is_a_warning(self):
        d = example()
        d["sources"][SRC]["copy_lut_sha256"] = "1" * 64
        self.expect(d, "V16", f"sources.{SRC}.copy_lut_sha256", level="warning")


class V17LoudnessTests(RuleCase):
    def test_mode_must_be_known(self):
        d = example()
        d["audio"]["loudness"]["mode"] = "loud"
        self.expect(d, "V17", "audio.loudness.mode", only=False)

    def test_target_is_needed_unless_mode_is_off(self):
        d = example()
        del d["audio"]["loudness"]["target_lufs"]
        self.expect(d, "V17", "audio.loudness.target_lufs")
        d = example()
        d["audio"]["loudness"] = {"mode": "off"}
        self.expect_clean(d)

    def test_target_range(self):
        for value in (-41, -4):
            d = example()
            d["audio"]["loudness"]["target_lufs"] = value
            with self.subTest(value=value):
                self.expect(d, "V17", "audio.loudness.target_lufs")

    def test_true_peak_at_most_zero(self):
        d = example()
        d["audio"]["loudness"]["true_peak_dbtp"] = 0.5
        self.expect(d, "V17", "audio.loudness.true_peak_dbtp")

    def test_max_gain_range(self):
        for value in (-1, 31):
            d = example()
            d["audio"]["loudness"]["max_gain_db"] = value
            with self.subTest(value=value):
                self.expect(d, "V17", "audio.loudness.max_gain_db")


class V18ExportTests(RuleCase):
    def test_export_names_are_unique(self):
        d = example()
        d["exports"][1]["name"] = "film-4k"
        self.expect(d, "V18", "exports[1].name")

    def test_output_inside_the_folder_of_an_original_is_refused(self):
        d = example()
        d["exports"][0]["output"] = "/Volumes/CARD/Recordings/out/film.mp4"
        self.expect(d, "V18", "exports[0].output")

    def test_output_in_the_folder_of_the_wav_is_refused_too(self):
        d = example()
        d["sources"][SRC]["audio"] = "/Volumes/ZOOM/WAV/ZOOM0001.WAV"
        d["exports"][0]["output"] = "/Volumes/ZOOM/WAV/film.mp4"
        self.expect(d, "V18", "exports[0].output")

    def test_a_sibling_folder_with_the_same_prefix_is_allowed(self):
        d = example()
        d["exports"][0]["output"] = "/Volumes/CARD/Recordings-renders/film.mp4"
        self.expect_clean(d)


class V19SubtitleTests(RuleCase):
    def test_mode_must_be_known(self):
        d = example()
        d["subtitles"]["mode"] = "hardcode"
        self.expect(d, "V19", "subtitles.mode")

    def test_language_must_be_english(self):
        d = example()
        d["subtitles"]["language"] = "de"
        self.expect(d, "V19", "subtitles.language")

    def test_cue_text_must_not_be_empty(self):
        d = example()
        d["subtitles"]["cues"][0]["text"] = "  "
        self.expect(d, "V19", "subtitles.cues[0].text")

    def test_more_than_two_lines_is_a_warning(self):
        d = example()
        d["subtitles"]["cues"][0]["text"] = "one\ntwo\nthree"
        self.expect(d, "V19", "subtitles.cues[0].text", level="warning")

    def test_line_longer_than_max_chars_is_a_warning(self):
        d = example()
        d["subtitles"]["cues"][0]["text"] = "x" * 43
        self.expect(d, "V19", "subtitles.cues[0].text", level="warning")

    def test_line_of_exactly_max_chars_is_allowed(self):
        d = example()
        d["subtitles"]["cues"][0]["text"] = "x" * 42 + "\n" + "y" * 42
        self.expect_clean(d)


class V20DerivedTests(RuleCase):
    def test_derived_from_film_must_exist(self):
        d = short_example()
        self.expect(d, "V20", "derived_from.film", file_checks=True,
                    frame_counter=lambda p: 0, only=False)

    def test_existing_film_file_passes_the_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            film = Path(tmp) / "timeline.json"
            film.write_text("{}")
            d = short_example()
            d["derived_from"]["film"] = str(film)
            found = self.findings(d, file_checks=True, frame_counter=lambda p: 0)
            self.assertNotIn("V20", {f.rule for f in found})


class V21ContainerTests(RuleCase):
    def test_mp4_needs_h264_and_aac(self):
        d = example()
        d["exports"][0]["video"]["codec"] = "prores_videotoolbox"
        self.expect(d, "V21", "exports[0].video.codec")
        d = example()
        d["exports"][0]["audio"]["codec"] = "pcm_s24le"
        self.expect(d, "V21", "exports[0].audio.codec")

    def test_mov_needs_prores_hq_and_pcm(self):
        d = example()
        d["exports"][1]["video"]["codec"] = "libx264"
        self.expect(d, "V21", "exports[1].video.codec")
        d = example()
        d["exports"][1]["video"]["profile"] = "lt"
        self.expect(d, "V21", "exports[1].video.profile")
        d = example()
        d["exports"][1]["audio"]["codec"] = "aac"
        self.expect(d, "V21", "exports[1].audio.codec")

    def test_other_extensions_are_refused(self):
        d = example()
        d["exports"][0]["output"] = "/Volumes/SSD/film/renders/film.avi"
        self.expect(d, "V21", "exports[0].output")

    def test_libx264_with_crf_is_valid(self):
        d = example()
        d["exports"][0]["video"] = {"codec": "libx264", "preset": "slow",
                                   "rate_control": {"mode": "crf", "crf": 18}}
        self.expect_clean(d)

    def test_crf_is_only_for_libx264(self):
        d = example()
        d["exports"][0]["video"]["rate_control"] = {"mode": "crf", "crf": 18}
        self.expect(d, "V21", "exports[0].video.rate_control.mode")

    def test_rate_control_values(self):
        d = example()
        d["exports"][0]["video"]["rate_control"] = {"mode": "bitrate", "mbps": 0}
        self.expect(d, "V21", "exports[0].video.rate_control.mbps")
        d = example()
        d["exports"][0]["video"]["rate_control"] = {"mode": "vbr"}
        self.expect(d, "V21", "exports[0].video.rate_control.mode")


class V22ShortLengthTests(RuleCase):
    def test_a_short_longer_than_180_seconds_warns(self):
        d = short_example()
        d["sources"][SRC]["frames"] = 20000
        d["segments"] = [{"id": "s001", "source": SRC, "in": 0, "out": 6000}]    # 200 s
        d["titles"] = []
        d["subtitles"]["cues"] = []
        self.expect(d, "V22", "segments", level="warning")

    def test_a_film_may_be_longer(self):
        d = example()
        d["sources"][SRC]["frames"] = 20000
        d["segments"] = [{"id": "s001", "source": SRC, "in": 0, "out": 6000}]
        d["titles"] = []
        d["subtitles"]["cues"] = []
        self.expect_clean(d)


class OutputFormatTests(RuleCase):
    def test_exit_code_and_json_list_of_validate_command(self):
        import contextlib
        import io
        import main
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "timeline.json"
            d = example()
            d["segments"][1]["out"] = 3000
            path.write_text(json.dumps(d))
            args = main.build_parser().parse_args(["timeline", "validate", "--file", str(path),
                                                    "--no-file-checks"])
            out = io.StringIO()
            code = 0
            with contextlib.redirect_stdout(out):
                try:
                    args.func(args)
                except SystemExit as e:
                    code = e.code
            self.assertEqual(code, 1)
            data = json.loads(out.getvalue())
            self.assertEqual(data[0]["rule"], "V9")
            self.assertEqual(data[0]["path"], "segments[1].out")
            self.assertEqual(set(data[0]), {"level", "rule", "path", "message", "hint"})
            d["segments"][1]["out"] = 2380
            path.write_text(json.dumps(d))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                try:
                    args.func(args)
                    code = 0
                except SystemExit as e:
                    code = e.code
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue()), [])


if __name__ == "__main__":
    unittest.main()
