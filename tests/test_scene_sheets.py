"""Check 5.1, the drawing part: one still sheet per scene, the overview pages and the finer look inside
one scene (SPECS 10.2). The clips are synthetic: every frame of a preview copy has its own grey value
(tests/media.py), so a test can tell which frame a still shows."""

import os
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import cv2

import config
import edits
import sceneindex
import timeline
from tests import fonts, media

W, H = 320, 180
FPS = (30000, 1001)
F = 30000 / 1001


def frame_at_tile(image, rect):
    """The grey value in the middle of a still's rectangle on a sheet."""
    x, y, w, h = rect
    return int(image[y + h // 2, x + w // 2].mean())


class SheetFixture(unittest.TestCase):
    """Clip A: 1000 frames, 320 x 180. Clip B: 200 frames, 320 x 136 (letterboxed in the frame).
    s001 A 0-60 (2 s), s002 A 100-400 (10 s), s003 A 50-1000 (31.7 s), s004 B 0-100 (3.3 s)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.copies = root / "copies"
        cls.copies.mkdir()
        media.write_ramp_mp4(cls.copies / "A.mp4", 1000, W, H)
        media.write_ramp_mp4(cls.copies / "B.mp4", 200, W, 136)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.work = self.dir / "work"
        self.work.mkdir()
        self.path = self.dir / "timeline-sheets.json"
        self.segments = [("A", 0, 60), ("A", 100, 400), ("A", 50, 1000), ("B", 0, 100)]
        self.removed = set()
        self.extract_calls = []
        sceneindex._fonts.cache_clear()
        self.addCleanup(sceneindex._fonts.cache_clear)
        real = sceneindex.extract_frames

        def counting(*a, **kw):
            self.extract_calls.append(a[1:3])
            return real(*a, **kw)

        sceneindex.extract_frames = counting
        self.addCleanup(setattr, sceneindex, "extract_frames", real)

    def make(self):
        src = lambda name, h: {
            "original_video": "/o/x.braw", "preview_copy": str(self.copies / f"{name}.mp4"),
            "audio": "/o/x.wav", "audio_offset_s": 0.1, "frames": 1000 if name == "A" else 200,
            "fps": {"num": FPS[0], "den": FPS[1]}, "size": {"width": W * 2, "height": h * 2},
            "copy_lut_sha256": "0" * 64}
        segs = []
        for i, (s, a, b) in enumerate(self.segments):
            seg = {"id": f"s{i + 1:03d}", "source": s, "in": a, "out": b, "label": f"scene {i + 1}"}
            if seg["id"] in self.removed:
                seg["enabled"] = False
            segs.append(seg)
        tl = timeline.Timeline({
            "version": 1, "kind": "film",
            "film": {"fps": {"num": FPS[0], "den": FPS[1]}, "frame": {"width": W, "height": H}, "fit": "fit"},
            "sources": {"A": src("A", H), "B": src("B", 136)}, "segments": segs,
            "look": {"base": {"mode": "none"}}, "audio": {"sample_rate": 48000, "loudness": {"mode": "off"}}})
        tl.save(str(self.path))
        return tl

    def draw(self, **kw):
        tl = self.make()
        return tl, sceneindex.draw_sheets(tl, str(self.path), str(self.work), **kw)

    def folder(self):
        return self.work / "index" / "timeline-sheets"

    def image(self, name):
        img = cv2.imread(str(self.folder() / name))
        self.assertIsNotNone(img, name)
        return img


class Rules(unittest.TestCase):
    def test_the_number_of_stills_follows_the_length_of_the_scene(self):
        self.assertEqual([sceneindex.sheet_frame_count(s) for s in (0.5, 2, 6.0, 6.1, 30.0, 30.1, 120)],
                         [3, 3, 3, 6, 6, 9, 9])

    def test_the_stills_are_at_the_centres_of_equal_parts(self):
        self.assertEqual(sceneindex.sample_offsets(60, 3), [10, 30, 50])
        self.assertEqual(sceneindex.sample_offsets(300, 6), [25, 75, 125, 175, 225, 275])
        self.assertEqual(sceneindex.sample_offsets(2, 3), [0, 1])          # a scene of two frames has two
        self.assertEqual(sceneindex.sample_offsets(1, 9), [0])

    def test_the_tile_rectangles_fill_the_width(self):
        rects = sceneindex.tile_rects(6, columns=3, width=1600, gap=8, tile_h=293, label_h=30, top=70)
        self.assertEqual(len(rects), 6)
        self.assertEqual(rects[0][:2], (8, 70 + 8))
        self.assertEqual(rects[3][1], 70 + 8 + 293 + 30 + 8)               # the second row
        right = max(x + w for x, y, w, h in rects)
        self.assertLessEqual(right, 1600 - 8)
        self.assertGreaterEqual(right, 1600 - 8 - 3)
        self.assertEqual(len({(x, y) for x, y, w, h in rects}), 6)


class SheetTests(SheetFixture):
    def test_there_is_one_jpeg_per_scene_and_it_is_as_wide_as_the_setting(self):
        tl, report = self.draw()
        for sid in ("s001", "s002", "s003", "s004"):
            path = self.folder() / "sheets" / f"{sid}.jpg"
            self.assertTrue(path.is_file(), path)
            img = cv2.imread(str(path))
            self.assertEqual(img.shape[1], config.SHEET_WIDTH)
            self.assertLess(img.shape[0], 2000)
        self.assertEqual(sorted(report["drawn"]), ["s001", "s002", "s003", "s004"])
        self.assertEqual(report["reused"], [])

    def test_the_height_follows_the_number_of_stills(self):
        self.draw()
        h = {sid: self.image(f"sheets/{sid}.jpg").shape[0] for sid in ("s001", "s002", "s003", "s004")}
        self.assertEqual(h["s001"], h["s004"])                  # 3 stills, one row
        self.assertGreater(h["s002"], h["s001"])                # 6 stills, two rows
        self.assertGreater(h["s003"], h["s002"])                # 9 stills, three rows

    def test_every_still_shows_the_frame_it_is_for(self):
        tl, _ = self.draw()
        rows = sceneindex.scene_rows(tl, str(self.path), str(self.work))
        s2 = next(r for r in rows if r["id"] == "s002")
        tiles = sceneindex.sheet_tiles(s2, tl.fps)
        self.assertEqual([t["frame"] for t in tiles], [125, 175, 225, 275, 325, 375])
        img = self.image("sheets/s002.jpg")
        rects = sceneindex.tile_rects(len(tiles), config.SHEET_COLUMNS, config.SHEET_WIDTH, config.SHEET_GAP,
                                      sceneindex.tile_height(), config.SHEET_LABEL_HEIGHT,
                                      sceneindex.header_height(s2))
        for tile, rect in zip(tiles, rects):
            self.assertAlmostEqual(frame_at_tile(img, rect), media.frame_value(tile["frame"]), delta=4,
                                   msg=f"frame {tile['frame']}")

    def test_the_stills_of_a_letterboxed_clip_keep_their_shape_and_are_centred(self):
        tl, _ = self.draw()
        rows = sceneindex.scene_rows(tl, str(self.path), str(self.work))
        s4 = next(r for r in rows if r["id"] == "s004")
        tiles = sceneindex.sheet_tiles(s4, tl.fps)
        img = self.image("sheets/s004.jpg")
        x, y, w, h = sceneindex.tile_rects(3, config.SHEET_COLUMNS, config.SHEET_WIDTH, config.SHEET_GAP,
                                           sceneindex.tile_height(), config.SHEET_LABEL_HEIGHT,
                                           sceneindex.header_height(s4))[0]
        self.assertAlmostEqual(frame_at_tile(img, (x, y, w, h)), media.frame_value(tiles[0]["frame"]), delta=4)
        # 320 x 136 into a 522 x 293 tile: the picture is 522 x 222, the strip above it is the tile's background
        self.assertLess(int(img[y + 2, x + w // 2].mean()), 60)

    def test_the_header_names_the_scene_its_film_time_the_clip_and_the_frames(self):
        tl, _ = self.draw()
        rows = sceneindex.scene_rows(tl, str(self.path), str(self.work))
        s2 = next(r for r in rows if r["id"] == "s002")
        text = " | ".join(t for style, t in sceneindex.sheet_header(s2))
        self.assertIn("s002", text)
        self.assertIn(edits.clock(60 / F), text)                              # film start
        self.assertIn(edits.clock(360 / F), text)                             # film end
        self.assertIn("clip A", text)
        self.assertIn("frames 100-400", text)
        self.assertIn("scene 2", text)                                        # the label

    def test_a_still_is_labelled_with_the_scene_and_its_film_time(self):
        tl, _ = self.draw()
        rows = sceneindex.scene_rows(tl, str(self.path), str(self.work))
        s2 = next(r for r in rows if r["id"] == "s002")
        tiles = sceneindex.sheet_tiles(s2, tl.fps)
        label = sceneindex.tile_label(s2, tiles[1])
        self.assertTrue(label.startswith("s002"))
        self.assertIn(edits.clock((60 + 75) / F), label)                      # 75 frames into the scene
        self.assertIn("+2.5 s", label)

    def test_a_removed_scene_gets_a_sheet_that_says_so_and_has_source_times_only(self):
        self.removed = {"s002"}
        tl, report = self.draw()
        rows = sceneindex.scene_rows(tl, str(self.path), str(self.work))
        s2 = next(r for r in rows if r["id"] == "s002")
        self.assertIn("s002", report["drawn"])
        header = " | ".join(t for style, t in sceneindex.sheet_header(s2))
        self.assertIn("removed", header)
        self.assertIn("scenes restore s002", header)
        label = sceneindex.tile_label(s2, sceneindex.sheet_tiles(s2, tl.fps)[0])
        self.assertIn("removed", label)
        self.assertNotIn(edits.clock(0), label)
        self.assertTrue((self.folder() / "sheets" / "s002.jpg").is_file())

    def test_the_overview_has_a_page_for_twelve_scenes_with_the_middle_still_of_each(self):
        self.segments = [("A", 60 * i, 60 * i + 60) for i in range(13)]
        tl, report = self.draw()
        self.assertEqual(report["overview"]["drawn"], ["overview-01.jpg", "overview-02.jpg"])
        rows = sceneindex.scene_rows(tl, str(self.path), str(self.work))
        page1 = self.image("overview-01.jpg")
        self.assertEqual(page1.shape[1], config.SHEET_WIDTH)
        rects = sceneindex.tile_rects(12, config.OVERVIEW_COLUMNS, config.SHEET_WIDTH, config.SHEET_GAP,
                                      sceneindex.overview_tile_height(), config.SHEET_LABEL_HEIGHT,
                                      sceneindex.overview_header_height())
        for k in (0, 5, 11):
            mid = rows[k]["source_in"] + rows[k]["length_frames"] // 2
            self.assertAlmostEqual(frame_at_tile(page1, rects[k]), media.frame_value(mid), delta=4, msg=f"scene {k}")
        page2 = self.image("overview-02.jpg")
        mid = rows[12]["source_in"] + rows[12]["length_frames"] // 2
        self.assertAlmostEqual(frame_at_tile(page2, rects[0]), media.frame_value(mid), delta=4)
        self.assertLess(page2.shape[0], page1.shape[0])                       # one scene, one row

    def test_the_overview_pages_are_listed_for_each_scene(self):
        tl, report = self.draw()
        self.assertEqual(report["overview_of"]["s001"], ("overview-01.jpg", 1))
        self.assertEqual(report["overview_of"]["s004"], ("overview-01.jpg", 4))


class IncrementalTests(SheetFixture):
    def test_a_second_run_draws_nothing(self):
        self.draw()
        before = {p.name: p.stat().st_mtime_ns for p in (self.folder() / "sheets").iterdir()}
        self.extract_calls.clear()
        tl, report = self.draw()
        self.assertEqual(report["drawn"], [])
        self.assertEqual(sorted(report["reused"]), ["s001", "s002", "s003", "s004"])
        self.assertEqual(report["overview"]["drawn"], [])
        self.assertEqual(self.extract_calls, [])
        self.assertEqual({p.name: p.stat().st_mtime_ns for p in (self.folder() / "sheets").iterdir()}, before)

    def test_a_trimmed_scene_is_drawn_again_and_the_others_are_not(self):
        self.draw()
        self.segments[1] = ("A", 100, 360)
        tl, report = self.draw()
        # s003 and s004 start earlier in the film now, so their labels change
        self.assertEqual(report["reused"], ["s001"])
        self.assertEqual(sorted(report["drawn"]), ["s002", "s003", "s004"])

    def test_a_change_at_the_end_of_the_film_draws_one_scene(self):
        self.draw()
        self.segments[3] = ("B", 0, 90)
        tl, report = self.draw()
        self.assertEqual(report["drawn"], ["s004"])
        self.assertEqual(sorted(report["reused"]), ["s001", "s002", "s003"])

    def test_removing_a_scene_changes_the_film_times_after_it_but_extracts_no_frame_again(self):
        self.draw()
        self.extract_calls.clear()
        self.removed = {"s001"}
        tl, report = self.draw()
        self.assertEqual(sorted(report["drawn"]), ["s001", "s002", "s003", "s004"])
        self.assertEqual(self.extract_calls, [], "the stills were kept; only the labels are new")

    def test_a_deleted_sheet_is_drawn_again(self):
        self.draw()
        (self.folder() / "sheets" / "s003.jpg").unlink()
        tl, report = self.draw()
        self.assertEqual(report["drawn"], ["s003"])

    def test_a_new_preview_copy_draws_the_scenes_of_the_clip_again(self):
        self.draw()
        os.utime(self.copies / "B.mp4", (1, 1))
        try:
            tl, report = self.draw()
            self.assertEqual(report["drawn"], ["s004"])
        finally:
            os.utime(self.copies / "B.mp4", None)

    def test_a_scene_that_left_the_file_loses_its_sheet(self):
        self.draw()
        self.segments = self.segments[:3]
        tl, report = self.draw()
        self.assertFalse((self.folder() / "sheets" / "s004.jpg").exists())
        self.assertEqual(report["deleted"], ["sheets/s004.jpg"])

    def test_a_missing_preview_copy_stops_before_anything_is_drawn(self):
        tl = self.make()
        tl.data["sources"]["B"]["preview_copy"] = str(self.copies / "nope.mp4")
        with self.assertRaises(sceneindex.SheetError) as ctx:
            sceneindex.draw_sheets(tl, str(self.path), str(self.work))
        self.assertIn("nope.mp4", str(ctx.exception))
        self.assertIn("convert", str(ctx.exception))
        self.assertFalse((self.folder() / "sheets").exists())

    def test_the_timeline_file_and_the_copies_are_not_touched(self):
        tl = self.make()
        before = (self.path.read_bytes(), (self.copies / "A.mp4").stat().st_mtime_ns)
        sceneindex.draw_sheets(tl, str(self.path), str(self.work))
        self.assertEqual((self.path.read_bytes(), (self.copies / "A.mp4").stat().st_mtime_ns), before)


class FontTests(SheetFixture):
    def test_without_the_font_folder_the_sheets_are_drawn_with_a_plain_font_and_a_warning_says_so(self):
        old = os.environ.pop("VET_FONT_DIR", None)
        self.addCleanup(lambda: old and os.environ.__setitem__("VET_FONT_DIR", old))
        sceneindex._fonts.cache_clear()
        tl, report = self.draw()
        self.assertEqual(len(report["drawn"]), 4)
        self.assertTrue(any("VET_FONT_DIR" in w for w in report["warnings"]), report["warnings"])

    @fonts.requires_inter
    def test_with_inter_there_is_no_warning(self):
        sceneindex._fonts.cache_clear()
        tl, report = self.draw()
        self.assertEqual(report["warnings"], [])

    @fonts.requires_inter
    def test_a_changed_font_draws_the_sheets_again(self):
        folder = fonts.real_font_dir()
        os.environ.pop("VET_FONT_DIR")
        self.addCleanup(os.environ.__setitem__, "VET_FONT_DIR", folder)
        sceneindex._fonts.cache_clear()
        self.draw()
        os.environ["VET_FONT_DIR"] = folder
        sceneindex._fonts.cache_clear()
        tl, report = self.draw()
        self.assertEqual(len(report["drawn"]), 4)


class FramesTests(SheetFixture):
    """A finer look inside one scene: s002 is A 100-400, 10.01 s, and starts the film at 60 / 29.97 s."""

    def look(self, scene="s002", every=1.0, **kw):
        tl = self.make()
        return tl, sceneindex.draw_frames(tl, str(self.path), str(self.work), scene, every, **kw)

    def test_a_still_every_second_over_the_whole_scene(self):
        tl, result = self.look()
        self.assertEqual(result["count"], 10)                  # t = 0 ... 9 s; 10 s is frame 400, the first one not in the scene
        (path,) = result["paths"]
        self.assertTrue(path.startswith(str(self.folder() / "frames")), path)
        self.assertIn("s002", os.path.basename(path))
        img = cv2.imread(path)
        self.assertEqual(img.shape[1], config.SHEET_WIDTH)

    def test_each_still_is_the_frame_at_its_time(self):
        tl, result = self.look(every=2.0)
        tiles = result["tiles"]
        self.assertEqual([t["frame"] for t in tiles], [100 + round(2 * k * F) for k in range(5)])
        img = cv2.imread(result["paths"][0])
        rows = sceneindex.scene_rows(tl, str(self.path), str(self.work))
        s2 = next(r for r in rows if r["id"] == "s002")
        rects = sceneindex.tile_rects(5, config.SHEET_COLUMNS, config.SHEET_WIDTH, config.SHEET_GAP,
                                      sceneindex.tile_height(), config.SHEET_LABEL_HEIGHT,
                                      sceneindex.header_height(s2))
        for tile, rect in zip(tiles, rects):
            self.assertAlmostEqual(frame_at_tile(img, rect), media.frame_value(tile["frame"]), delta=4)

    def test_from_and_to_are_film_seconds(self):
        start = 60 / F                                          # s002 starts the film here
        tl, result = self.look(every=1.0, from_s=start + 4.0, to_s=start + 7.0)
        self.assertEqual([t["frame"] for t in result["tiles"]], [100 + round(k * F) for k in (4, 5, 6)])
        self.assertAlmostEqual(result["tiles"][0]["film_s"], start + 4.0, places=1)

    def test_more_than_twelve_stills_make_more_than_one_page(self):
        tl, result = self.look(every=0.5)                       # 20 stills
        self.assertEqual(result["count"], 20)
        self.assertEqual(len(result["paths"]), 2)
        self.assertEqual(len(set(result["paths"])), 2)
        self.assertGreater(cv2.imread(result["paths"][0]).shape[0], cv2.imread(result["paths"][1]).shape[0])

    def test_too_many_stills_are_refused_with_the_number(self):
        with self.assertRaises(sceneindex.SheetError) as ctx:
            self.look(scene="s003", every=0.1)                  # 31.7 s every 0.1 s
        self.assertIn(str(config.FRAMES_MAX), str(ctx.exception))
        self.assertFalse((self.folder() / "frames").exists())

    def test_a_second_look_with_the_same_arguments_draws_nothing(self):
        tl, first = self.look()
        self.extract_calls.clear()
        tl, second = self.look()
        self.assertEqual(second["paths"], first["paths"])
        self.assertEqual(second["drawn"], [])
        self.assertEqual(self.extract_calls, [])

    def test_a_range_outside_the_scene_an_unknown_scene_and_a_step_below_a_frame_are_refused(self):
        with self.assertRaises(sceneindex.SheetError) as ctx:
            self.look(from_s=0.0, to_s=1.0)                     # s002 starts at 2.0 s
        self.assertIn("s002", str(ctx.exception))
        with self.assertRaises(sceneindex.SheetError):
            self.look(from_s=5.0, to_s=4.0)
        with self.assertRaises(sceneindex.SheetError) as ctx:
            self.look(scene="s999")
        self.assertIn("s999", str(ctx.exception))
        with self.assertRaises(sceneindex.SheetError):
            self.look(every=0.0)

    def test_a_removed_scene_can_be_looked_at_without_a_range(self):
        self.removed = {"s002"}
        tl, result = self.look(every=3.0)
        self.assertEqual(result["count"], 4)
        self.assertIsNone(result["tiles"][0]["film_s"])
        with self.assertRaises(sceneindex.SheetError) as ctx:
            self.look(every=3.0, from_s=1.0, to_s=5.0)
        self.assertIn("removed", str(ctx.exception))

    def test_every_is_read_from_text_like_1s_or_0_5s_or_a_plain_number(self):
        self.assertEqual(sceneindex.parse_step("1s"), 1.0)
        self.assertEqual(sceneindex.parse_step("0.5s"), 0.5)
        self.assertEqual(sceneindex.parse_step("2"), 2.0)
        self.assertEqual(sceneindex.parse_step("500ms"), 0.5)
        for bad in ("", "fast", "-1s", "0s"):
            with self.assertRaises(ValueError):
                sceneindex.parse_step(bad)


if __name__ == "__main__":
    unittest.main()
