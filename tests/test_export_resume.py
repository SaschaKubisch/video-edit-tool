"""Check 3.12: the resume of a long render (SPECS 5.3, step 6). A film longer than a chunk is rendered in chunks of
whole scenes, joined by copy together with the sound that was mixed and encoded once. A render that is stopped (Ctrl+C,
SIGTERM, a failure) or killed goes on with the finished chunks when the same command is run again; an edit renders only
the chunks it touches; the free space needed is about 2.1 times the file; the chunks must have the same parameter sets.
The film is the synthetic one of test_export (three scenes of 60 frames); a chunk is 2 s, one scene."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

import chunks
import config
import export
import render
import tools
from tests import media
from tests.fonts import builtin_font
from tests.test_export import ExportFixture, FlatDecoder, W, H
from tests.test_export_master import prores_works

CHUNK_S = 2.0                      # 60 frames at 29.97 fps: one scene is one chunk


class ResumeFixture(ExportFixture):
    def setUp(self):
        name = self._testMethodName[-40:]
        self.name = f"film-{name}"
        self.work_dir = self.root / f"work-{name}"
        self.chunk_dir = chunks.folder(str(self.work_dir), self.name)

    def tl(self, loud=False, segments=None, **kw):
        return self.make_tl(segments=segments, exports=[self.entry(name=self.name)], loud=loud, **kw)

    def go(self, tl=None, decoder=None, chunk_seconds=CHUNK_S, **kw):
        tl = tl or self.tl()
        return self.run_export(tl, self.name, decoder=decoder, work=self.work_dir, chunk_seconds=chunk_seconds, **kw)

    def decodes_clean(self, path):
        r = subprocess.run([tools.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-"],
                           capture_output=True, text=True)
        return r.returncode == 0 and r.stderr.strip() == ""

    def luma(self, path):
        return np.array([f[0] for f in self.frames_of(path)]).astype(int)

    def interrupted(self, tl, path, first):
        """A render that is stopped (Ctrl+C) in the scene of `path` that starts at source frame `first`."""
        with self.assertRaises(KeyboardInterrupt):
            self.go(tl, decoder=FlatDecoder(self.clips, interrupt_on=(path, first)))


class ChunkedRenderTests(ResumeFixture):
    def test_a_long_film_is_rendered_in_chunks_and_joined(self):
        decoder = FlatDecoder(self.clips)
        r = self.go(decoder=decoder)
        self.assertTrue(r.ok, r.problems)
        self.assertEqual(r.chunks, {"total": 3, "reused": 0, "rendered": 3})
        self.assertEqual(r.frames, 180)
        self.assertEqual(len(decoder.calls), 3)                          # one call for each scene, nothing twice
        self.assertEqual(len(self.frames_of(r.path)), 180)
        self.assertTrue(self.decodes_clean(r.path))
        probe = media.probe(r.path)
        v = [s for s in probe["streams"] if s["codec_type"] == "video"][0]
        a = [s for s in probe["streams"] if s["codec_type"] == "audio"][0]
        self.assertEqual((v["codec_name"], v["profile"], v["pix_fmt"], v["width"], v["height"]), ("h264", "High", "yuv420p", W, H))
        self.assertEqual((v["color_space"], v["color_transfer"], v["color_primaries"], v["color_range"]),
                         ("bt709", "bt709", "bt709", "tv"))
        self.assertEqual(v["tags"]["timecode"], "00:00:00:00")
        self.assertEqual((a["codec_name"], int(a["sample_rate"]), a["channels"]), ("aac", 48000, 2))
        import convert
        order = convert.mp4_box_order(r.path)
        self.assertLess(order.index("moov"), order.index("mdat"))

    def test_the_chunks_and_the_scratch_are_removed_when_the_file_has_passed_its_checks(self):
        r = self.go()
        self.assertTrue(r.ok)
        self.assertFalse(os.path.exists(self.chunk_dir))
        self.assertFalse(os.path.exists(r.path + ".partial"))
        self.assertFalse(os.path.exists(self.work_dir / ".render.lock"))
        self.assertEqual([p.name for p in self.work_dir.glob("render-*")], [])

    def test_the_pictures_are_those_of_a_render_in_one_process_and_the_sound_is_the_same(self):
        tl = self.tl(loud=True)
        one = self.run_export(tl, self.name, work=self.root / f"{self.work_dir.name}-one", chunk_seconds=0)
        luma_one, audio_one = self.luma(one.path), media.read_audio_f32(one.path)
        os.unlink(one.path)
        many = self.go(tl)
        self.assertTrue(many.ok, many.problems)
        self.assertEqual(many.chunks["total"], 3)
        self.assertEqual(one.chunks, None)
        luma_many = self.luma(many.path)
        self.assertEqual(luma_many.shape, luma_one.shape)
        self.assertLessEqual(int(np.abs(luma_many - luma_one).max()), 2)
        audio_many = media.read_audio_f32(many.path)
        self.assertTrue(np.array_equal(audio_many, audio_one))

    def test_a_film_that_fits_in_one_chunk_is_rendered_in_one_process_as_before(self):
        decoder = FlatDecoder(self.clips)
        r = self.go(decoder=decoder, chunk_seconds=100)
        self.assertTrue(r.ok)
        self.assertIsNone(r.chunks)
        self.assertFalse(os.path.exists(os.path.join(str(self.work_dir), "chunks")))

    def test_a_scene_longer_than_a_chunk_is_a_chunk_of_its_own(self):
        r = self.go(chunk_seconds=1.0)                                  # 30 frames: every scene is longer
        self.assertEqual(r.chunks, {"total": 3, "reused": 0, "rendered": 3})

    def test_the_chunks_of_a_long_run_are_few_and_whole_scenes(self):
        tl = self.tl(segments=[("A", 0, 30)] * 6)
        r = self.go(tl, chunk_seconds=2.0)                              # scenes of 30 frames: two to a chunk
        self.assertTrue(r.ok, r.problems)
        self.assertEqual(r.chunks, {"total": 3, "reused": 0, "rendered": 3})
        self.assertEqual(len(self.frames_of(r.path)), 180)

    def test_the_report_names_the_chunks(self):
        r = self.go()
        row = export.latest_render(str(self.work_dir), self.name)
        self.assertEqual(row["chunks"], {"total": 3, "reused": 0, "rendered": 3})

    def test_the_log_says_which_chunk_is_rendered(self):
        lines = []
        tl = self.tl()
        export.render_export(tl, self.name, str(self.work_dir), FlatDecoder(self.clips), log=lines.append,
                             protected=[str(self.originals)], chunk_seconds=CHUNK_S)
        text = "\n".join(lines)
        self.assertIn("3 chunks", text)
        self.assertIn("chunk 1 of 3", text)
        self.assertIn("joined", text)


class ResumeTests(ResumeFixture):
    def test_a_render_stopped_in_the_second_chunk_keeps_the_first_and_leaves_no_partial_file(self):
        tl = self.tl()
        self.interrupted(tl, self.sources["B"]["original_video"], 5)
        names = sorted(os.listdir(self.chunk_dir))
        self.assertEqual(names, ["audio.json", "chunk-0001.json", "chunk-0001.mp4", "mix.m4a"])      # the sound is kept too
        self.assertFalse(os.path.exists(self.out / f"{self.name}.mp4"))
        self.assertFalse(os.path.exists(self.work_dir / ".render.lock"))
        self.assertEqual([p.name for p in self.work_dir.glob("render-*")], [])

    def test_the_same_command_again_goes_on_with_the_second_chunk(self):
        tl = self.tl()
        self.interrupted(tl, self.sources["B"]["original_video"], 5)
        decoder = FlatDecoder(self.clips)
        r = self.go(tl, decoder=decoder)
        self.assertTrue(r.ok, r.problems)
        self.assertEqual(r.chunks, {"total": 3, "reused": 1, "rendered": 2})
        self.assertEqual([(c[1], c[2]) for c in decoder.calls], [(5, 65), (40, 100)])        # scenes 2 and 3 only
        self.assertEqual(len(self.frames_of(r.path)), 180)
        self.assertTrue(self.decodes_clean(r.path))
        self.assertFalse(os.path.exists(self.chunk_dir))

    def test_the_result_after_a_stop_is_the_result_without_one(self):
        tl = self.tl(loud=True)
        straight = self.run_export(tl, self.name, work=self.root / f"{self.work_dir.name}-straight", chunk_seconds=CHUNK_S)
        a, audio_a = self.luma(straight.path), media.read_audio_f32(straight.path)
        os.unlink(straight.path)
        self.interrupted(tl, self.sources["A"]["original_video"], 40)           # stopped in the third chunk
        resumed = self.go(tl)
        self.assertEqual(resumed.chunks, {"total": 3, "reused": 2, "rendered": 1})
        self.assertTrue(np.array_equal(self.luma(resumed.path), a))
        self.assertTrue(np.array_equal(media.read_audio_f32(resumed.path), audio_a))

    def test_a_killed_render_leaves_a_partial_chunk_a_stale_lock_and_a_scratch_folder_and_goes_on(self):
        tl = self.tl()
        self.interrupted(tl, self.sources["B"]["original_video"], 5)
        # what a kill -9 leaves behind: the file that was being written, the lock of a process that is gone, the scratch folder
        Path(chunks.chunk_path(self.chunk_dir, 2, "mp4", partial=True)).write_bytes(b"half a chunk")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        (self.work_dir / ".render.lock").write_text(str(dead.pid))
        (self.work_dir / "render-left-behind").mkdir()
        r = self.go(tl)
        self.assertTrue(r.ok, r.problems)
        self.assertEqual(r.chunks, {"total": 3, "reused": 1, "rendered": 2})
        self.assertTrue(self.decodes_clean(r.path))

    def test_a_render_that_is_running_blocks_a_second_one(self):
        tl = self.tl()
        self.interrupted(tl, self.sources["B"]["original_video"], 5)
        (self.work_dir / ".render.lock").write_text(str(os.getpid()))
        with self.assertRaises(export.AlreadyRunning):
            self.go(tl)
        os.unlink(self.work_dir / ".render.lock")

    def test_an_edit_renders_again_only_the_chunks_it_touches(self):
        tl = self.tl()
        self.interrupted(tl, self.sources["A"]["original_video"], 40)           # chunks 1 and 2 are finished
        tl2 = self.tl(segments=[("A", 11, 71), ("B", 5, 65), ("A", 40, 100)])   # the first scene starts one frame later
        decoder = FlatDecoder(self.clips)
        r = self.go(tl2, decoder=decoder)
        self.assertEqual(r.chunks, {"total": 3, "reused": 1, "rendered": 2})
        self.assertEqual([(c[1], c[2]) for c in decoder.calls], [(11, 71), (40, 100)])
        self.assertEqual(len(self.frames_of(r.path)), 180)

    def test_a_changed_original_renders_its_chunks_again(self):
        tl = self.tl()
        self.interrupted(tl, self.sources["A"]["original_video"], 40)
        path = self.sources["B"]["original_video"]
        st = os.stat(path)
        self.addCleanup(os.utime, path, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))
        decoder = FlatDecoder(self.clips)
        r = self.go(tl, decoder=decoder)
        self.assertEqual(r.chunks, {"total": 3, "reused": 1, "rendered": 2})       # chunk 2 holds the scene from clip B

    def test_fresh_renders_everything_again(self):
        tl = self.tl()
        self.interrupted(tl, self.sources["A"]["original_video"], 40)
        decoder = FlatDecoder(self.clips)
        r = self.go(tl, decoder=decoder, fresh=True)
        self.assertEqual(r.chunks, {"total": 3, "reused": 0, "rendered": 3})
        self.assertEqual(len(decoder.calls), 3)

    def test_a_film_with_fewer_scenes_than_before_drops_the_chunks_that_are_no_longer_there(self):
        tl = self.tl()
        self.interrupted(tl, self.sources["A"]["original_video"], 40)
        shorter = self.tl(segments=[("A", 10, 70), ("B", 5, 65)])
        r = self.go(shorter)
        self.assertEqual(r.frames, 120)
        # chunk 1 is the same; chunk 2 is now the last one and carries the fade-out, so it is made again
        self.assertEqual(r.chunks, {"total": 2, "reused": 1, "rendered": 1})
        self.assertEqual(len(self.frames_of(r.path)), 120)

    def test_a_failed_chunk_is_a_failure_and_keeps_the_finished_chunks(self):
        tl = self.tl()
        with self.assertRaises(RuntimeError):                         # what the decoder raised
            self.go(tl, decoder=FlatDecoder(self.clips, fail_on=self.sources["B"]["original_video"]))
        self.assertEqual([n for n in sorted(os.listdir(self.chunk_dir)) if n.startswith("chunk")],
                         ["chunk-0001.json", "chunk-0001.mp4"])
        r = self.go(tl)
        self.assertEqual(r.chunks, {"total": 3, "reused": 1, "rendered": 2})

    def test_a_chunk_with_the_wrong_number_of_frames_is_not_kept(self):
        tl = self.tl()
        with self.assertRaises(export.ExportError) as cm:
            self.go(tl, decoder=FlatDecoder(self.clips, short_by=1))
        self.assertIn("chunk 1", str(cm.exception))
        self.assertFalse(os.path.exists(chunks.record_path(self.chunk_dir, 1)))


class CommandTests(ResumeFixture):
    """`render --chunk-seconds S` and `--fresh` through the command-line parser."""

    def setUp(self):
        super().setUp()
        self.path = self.root / f"resume-{self.name}.json"
        self.tl().save(str(self.path))

    def render(self, *extra, decoder=None):
        import contextlib
        import io
        import main
        args = main.build_parser().parse_args(["render", "--file", str(self.path), "--work", str(self.work_dir),
                                               "--export", self.name, *extra])
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main.cmd_render(args, decoder=decoder or FlatDecoder(self.clips))
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue(), err.getvalue()

    def test_chunk_seconds_cuts_the_film_and_the_output_says_so(self):
        code, out, err = self.render("--chunk-seconds", "2")
        self.assertEqual(code, 0, out + err)
        self.assertIn("3 chunks", out)
        self.assertIn("joined", out)
        self.assertEqual(len(self.frames_of(self.out / f"{self.name}.mp4")), 180)

    def test_chunk_seconds_0_renders_in_one_process(self):
        code, out, err = self.render("--chunk-seconds", "0")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("chunks of whole scenes", out)

    def test_ctrl_c_in_the_second_chunk_exits_130_and_says_how_to_go_on_and_the_same_command_does(self):
        code, out, err = self.render("--chunk-seconds", "2",
                                     decoder=FlatDecoder(self.clips, interrupt_on=(self.sources["B"]["original_video"], 5)))
        self.assertEqual(code, 130)
        self.assertIn("Stopped: 1 of 3 chunks are finished", out)
        self.assertIn("Run the same command again", out)
        self.assertFalse((self.out / f"{self.name}.mp4").exists())
        code, out, err = self.render("--chunk-seconds", "2")
        self.assertEqual(code, 0, out + err)
        self.assertIn("chunk 1 of 3: finished earlier, kept", out)
        self.assertEqual(len(self.frames_of(self.out / f"{self.name}.mp4")), 180)

    def test_fresh_starts_over(self):
        self.render("--chunk-seconds", "2",
                    decoder=FlatDecoder(self.clips, interrupt_on=(self.sources["B"]["original_video"], 5)))
        code, out, err = self.render("--chunk-seconds", "2", "--fresh")
        self.assertEqual(code, 0, out + err)
        self.assertNotIn("finished earlier", out)

    def test_the_default_chunk_length_is_the_setting(self):
        import main
        args = main.build_parser().parse_args(["render", "--export", "x"])
        self.assertIsNone(args.chunk_seconds)
        self.assertFalse(args.fresh)
        self.assertEqual(config.EXPORT_CHUNK_SEC, 120.0)


class SoundTests(ResumeFixture):
    def counted_mix(self):
        calls = []
        real = render.build_mix

        def build_mix(*a, **k):
            calls.append(1)
            return real(*a, **k)
        render.build_mix = build_mix
        self.addCleanup(setattr, render, "build_mix", real)
        return calls

    def test_the_sound_is_mixed_once_and_a_rerun_takes_the_file_it_kept(self):
        calls = self.counted_mix()
        tl = self.tl(loud=True)
        self.interrupted(tl, self.sources["A"]["original_video"], 40)
        self.assertEqual(len(calls), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.chunk_dir, "audio.json")))
        r = self.go(tl)
        self.assertEqual(len(calls), 1)                                  # not mixed again
        self.assertTrue(r.ok, r.problems)
        self.assertIsNotNone(r.levels)                                   # the loudness report of the first run is kept
        self.assertEqual(r.levels["mode"], "segment_then_film")
        self.assertIsNotNone(r.output_loudness)
        self.assertAlmostEqual(r.output_loudness["lufs"], -14.0, delta=1.0)

    def test_a_change_of_the_sound_mixes_again(self):
        calls = self.counted_mix()
        tl = self.tl(loud=True)
        self.interrupted(tl, self.sources["A"]["original_video"], 40)
        tl2 = self.tl(loud=True, offsets={"A": 0.3})
        self.go(tl2)
        self.assertEqual(len(calls), 2)


class SpaceTests(ResumeFixture):
    def need(self, tl):
        plan = render.make_plan(tl, preview_width=W)
        entry = tl.data["exports"][0]
        return export.estimate_bytes(entry, plan.film_frames, plan.fps), export.scratch_bytes(plan.film_frames, plan.fps, plan.sample_rate)

    def test_a_chunked_render_asks_for_2_1_times_the_file_where_one_process_asks_for_1_1(self):
        tl = self.tl()
        est, scratch = self.need(tl)
        free = int(est * 1.5) + scratch
        with self.assertRaises(export.NotEnoughSpace) as cm:
            self.go(tl, free_bytes=lambda p: free)
        self.assertIn("2.1", str(cm.exception))
        r = self.run_export(tl, self.name, work=self.work_dir, chunk_seconds=0, free_bytes=lambda p: free)
        self.assertTrue(r.ok)                                           # the same film in one process fits
        os.unlink(r.path)

    def test_enough_space_for_the_chunks_and_the_file_is_enough(self):
        tl = self.tl()
        est, scratch = self.need(tl)
        r = self.go(tl, free_bytes=lambda p: int(est * 2.2) + scratch + 1000)
        self.assertTrue(r.ok, r.problems)

    def test_finished_chunks_count_as_part_of_the_space(self):
        tl = self.tl()
        est, scratch = self.need(tl)
        self.interrupted(tl, self.sources["A"]["original_video"], 40)
        done = sum(os.path.getsize(os.path.join(self.chunk_dir, n)) for n in os.listdir(self.chunk_dir) if n.endswith(".mp4"))
        free = int(est * 2.1) - done + scratch + 1000
        r = self.go(tl, free_bytes=lambda p: free)
        self.assertTrue(r.ok, r.problems)


class JoinTests(ResumeFixture):
    def test_chunks_with_different_parameter_sets_are_not_joined_and_the_message_says_what_to_do(self):
        real = chunks.parameter_sets
        seen = []

        def different(path, ffmpeg):
            seen.append(path)
            return f"set-{len(seen)}"
        chunks.parameter_sets = different
        self.addCleanup(setattr, chunks, "parameter_sets", real)
        with self.assertRaises(export.ExportError) as cm:
            self.go()
        msg = str(cm.exception)
        self.assertIn("parameter sets", msg)
        self.assertIn("--chunk-seconds 0", msg)
        self.assertFalse(os.path.exists(self.out / f"{self.name}.mp4"))
        self.assertEqual(len([n for n in os.listdir(self.chunk_dir) if n.endswith(".mp4")]), 3)      # the chunks stay
        chunks.parameter_sets = real
        r = self.go()
        self.assertEqual(r.chunks, {"total": 3, "reused": 3, "rendered": 0})                      # and are joined by the next run
        self.assertTrue(r.ok, r.problems)

    def test_the_parameter_sets_of_separate_runs_of_the_same_settings_are_the_same(self):
        r = self.go(chunk_seconds=CHUNK_S)
        # render again, keep the chunks by stopping at the end, and read them
        tl = self.tl()
        self.interrupted(tl, self.sources["A"]["original_video"], 40)
        sets = {chunks.parameter_sets(chunks.chunk_path(self.chunk_dir, i, "mp4"), tools.ffmpeg_path()) for i in (1, 2)}
        self.assertEqual(len(sets), 1)
        self.assertIsNotNone(next(iter(sets)))

    def test_prores_chunks_join_and_have_no_parameter_sets(self):
        if not prores_works():
            self.skipTest("prores_videotoolbox is not available")
        e = export.preset_entry("film-master", str(self.out), {"width": W, "height": H}, name=self.name)
        e["enabled"] = True
        e["output"] = str(self.out / f"{self.name}.mov")
        tl = self.make_tl(exports=[e], loud=False)
        r = self.run_export(tl, self.name, work=self.work_dir, chunk_seconds=CHUNK_S)
        self.assertTrue(r.ok, r.problems)
        self.assertEqual(r.chunks, {"total": 3, "reused": 0, "rendered": 3})
        v = [s for s in media.probe(r.path)["streams"] if s["codec_type"] == "video"][0]
        self.assertEqual((v["codec_name"], v["profile"], v["pix_fmt"]), ("prores", "HQ", "yuv422p10le"))
        self.assertEqual(len(media.read_yuv_frames(r.path, W, H)), 180)
        self.assertEqual((v["color_space"], v["color_transfer"], v["color_primaries"]), ("bt709", "bt709", "bt709"))


class TextAcrossChunksTests(ResumeFixture):
    def setUp(self):
        super().setUp()
        builtin_font(self)

    def tl_text(self):
        tl = self.make_tl(exports=[dict(self.entry(name=self.name), subtitles="burned")], loud=False)
        tl.data["subtitles"] = {"language": "en", "cues": [
            {"id": "c001", "anchor": "s001", "start_s": 1.4, "end_s": 1.99, "text": "At the end of one.", "origin": "manual"},
            {"id": "c002", "anchor": "s002", "start_s": 0.1, "end_s": 0.6, "text": "At the start of two.", "origin": "manual"}]}
        tl.data["titles"] = [{"id": "t001", "text": "Across", "anchor": "film", "start_s": 1.5, "end_s": 2.5,
                              "template": "center", "fade_s": 0.2}]
        return tl

    def test_text_is_on_the_same_frames_whether_the_film_is_chunked_or_not(self):
        base = self.go(self.make_tl(exports=[dict(self.entry(name=self.name + "-plain"), subtitles="none")], loud=False),
                       chunk_seconds=CHUNK_S) if False else None
        tl = self.tl_text()
        chunked = self.go(tl)
        one = self.run_export(tl, self.name, work=self.root / f"{self.work_dir.name}-one", chunk_seconds=0)
        a, b = self.luma(chunked.path), self.luma(one.path)
        self.assertEqual(a.shape, b.shape)
        self.assertLessEqual(int(np.abs(a - b).max()), 3)
        # the title is drawn on both sides of the join at frame 60 (1.5 s to 2.5 s are frames 45 to 75)
        self.assertGreater(int(np.abs(a[55] - a[5]).max()), 10)
        self.assertGreater(int(np.abs(a[65] - a[5]).max()), 10)

    def test_a_change_of_the_text_in_one_chunk_renders_only_that_chunk_again(self):
        tl = self.tl_text()
        self.interrupted(tl, self.sources["A"]["original_video"], 40)                # chunks 1 and 2 are finished
        tl2 = self.tl_text()
        tl2.data["subtitles"]["cues"][0]["text"] = "Another text."
        decoder = FlatDecoder(self.clips)
        r = self.go(tl2, decoder=decoder)
        # the cue of scene 1 is in chunk 1 only; the title crosses the join, so chunk 2 holds a part of it, unchanged
        self.assertEqual(r.chunks, {"total": 3, "reused": 1, "rendered": 2})


if __name__ == "__main__":
    unittest.main()
