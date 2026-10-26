"""Check 1.1 (second part): the converter's flow with a fake decoder. The
pictures are tiny synthetic frames; ffmpeg is real and encodes with libx264,
so the whole chain (pipe, filter, encode, check, rename) runs."""

import json
import os
import threading
import unittest
from pathlib import Path

import convert
from decoder import ClipInfo
from tests.fakes import Workspace, small_clip


def make_converter(ws, log=None, free=None, **kw):
    settings = kw.pop("settings", convert.PreviewSettings(encoder="libx264"))
    return convert.Converter(
        work_dir=str(ws.work), decoder=ws.decoder, settings=settings,
        lut_path=str(ws.lut), originals_folders=[str(ws.src)],
        log=log if log is not None else (lambda line: None),
        free_bytes=free or (lambda path: 10 ** 12), **kw)


def refs(ws):
    return [convert.ClipRef(stem=n, path=p) for n, p in ws.refs]


def load_json(ws):
    return json.loads((ws.work / "conversion.json").read_text())


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace(self)

    def test_fresh_run_makes_a_checked_copy_of_every_clip(self):
        summary = make_converter(self.ws).run(refs(self.ws))
        self.assertEqual(summary.exit_code, 0)
        self.assertEqual((summary.converted, summary.skipped, summary.failed), (2, 0, 0))
        data = load_json(self.ws)
        for name, path in self.ws.refs:
            copy = self.ws.work / "copies" / f"{name}.mp4"
            self.assertTrue(copy.is_file())
            e = data["clips"][name]
            self.assertEqual(e["status"], "done")
            self.assertEqual(e["original"], path)
            self.assertEqual(e["preview_copy"], str(copy))
            self.assertEqual(e["original_bytes"], os.path.getsize(path))
            self.assertEqual(e["original_mtime_ns"], os.stat(path).st_mtime_ns)
            self.assertEqual(e["frames"], 12)
            self.assertEqual(e["fps"], {"num": 30000, "den": 1001})
            self.assertEqual(e["size"], {"width": 128, "height": 72})
            self.assertEqual(e["copy_size"], {"width": 64, "height": 36})
            self.assertEqual(e["timecode_start"], "01:02:03:04")
            self.assertEqual(e["colour_science_gen"], "5")
            self.assertEqual(e["bytes"], os.path.getsize(copy))
            self.assertEqual(e["lut_sha256"], convert.sha256_file(str(self.ws.lut)))
            self.assertEqual(e["check"]["frames"], 12)
            self.assertEqual(e["check"]["audio"], "aac")
            self.assertEqual(e["check"]["timecode"], "01:02:03:04")
            self.assertEqual(e["audio"], {"sample_rate": 48000, "channels": 2,
                                          "samples": 19219})
            self.assertGreater(e["convert_fps"], 0)

    def test_header_of_conversion_json(self):
        make_converter(self.ws).run(refs(self.ws))
        data = load_json(self.ws)
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["decoder"], "fake")
        self.assertEqual(data["settings"]["encoder"], "libx264")
        self.assertEqual(data["base_lut"]["path"], str(self.ws.lut))
        self.assertEqual(data["base_lut"]["sha256"],
                         convert.sha256_file(str(self.ws.lut)))

    def test_no_partial_file_and_no_scratch_audio_is_left(self):
        make_converter(self.ws).run(refs(self.ws))
        left = [p.name for p in self.ws.work.rglob("*")
                if p.suffix == ".partial" or p.suffix == ".s32le"]
        self.assertEqual(left, [])
        self.assertFalse(list(self.ws.work.glob(".conversion-*")))

    def test_second_run_converts_nothing(self):
        make_converter(self.ws).run(refs(self.ws))
        self.ws.decoder.calls.clear()
        summary = make_converter(self.ws).run(refs(self.ws))
        self.assertEqual((summary.converted, summary.skipped), (0, 2))
        self.assertNotIn("frames", [c[0] for c in self.ws.decoder.calls])

    def test_partial_entry_and_stale_partial_file_are_converted_again(self):
        make_converter(self.ws).run(refs(self.ws))
        data = load_json(self.ws)
        name = self.ws.refs[0][0]
        data["clips"][name]["status"] = "partial"
        (self.ws.work / "conversion.json").write_text(json.dumps(data))
        stale = self.ws.work / "copies" / f"{name}.mp4.partial"
        stale.write_bytes(b"junk")
        summary = make_converter(self.ws).run(refs(self.ws))
        self.assertEqual((summary.converted, summary.skipped), (1, 1))
        self.assertFalse(stale.exists())
        self.assertEqual(load_json(self.ws)["clips"][name]["status"], "done")

    def test_changed_lut_converts_all_clips_again(self):
        make_converter(self.ws).run(refs(self.ws))
        self.ws.lut.write_text(self.ws.lut.read_text() + "# changed\n")
        summary = make_converter(self.ws).run(refs(self.ws))
        self.assertEqual(summary.converted, 2)

    def test_changed_original_is_converted_again(self):
        make_converter(self.ws).run(refs(self.ws))
        path = Path(self.ws.refs[0][1])
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        summary = make_converter(self.ws).run(refs(self.ws))
        self.assertEqual((summary.converted, summary.skipped), (1, 1))

    def test_missing_or_resized_copy_is_converted_again(self):
        make_converter(self.ws).run(refs(self.ws))
        (self.ws.work / "copies" / f"{self.ws.refs[0][0]}.mp4").unlink()
        copy2 = self.ws.work / "copies" / f"{self.ws.refs[1][0]}.mp4"
        copy2.write_bytes(copy2.read_bytes() + b"x")
        summary = make_converter(self.ws).run(refs(self.ws))
        self.assertEqual(summary.converted, 2)

    def test_changed_settings_convert_again(self):
        make_converter(self.ws).run(refs(self.ws))
        other = convert.PreviewSettings(encoder="libx264", gop=15)
        summary = make_converter(self.ws, settings=other).run(refs(self.ws))
        self.assertEqual(summary.converted, 2)

    def test_a_decoder_crash_fails_that_clip_only(self):
        self.ws.decoder.fail_on = {self.ws.refs[0][1]}
        summary = make_converter(self.ws).run(refs(self.ws))
        self.assertEqual((summary.converted, summary.failed), (1, 1))
        self.assertEqual(summary.exit_code, 1)
        e = load_json(self.ws)["clips"][self.ws.refs[0][0]]
        self.assertEqual(e["status"], "failed")
        self.assertIn("decoder crashed", e["error"])
        self.assertFalse((self.ws.work / "copies" / f"{self.ws.refs[0][0]}.mp4").exists())
        self.assertEqual(list(self.ws.work.rglob("*.partial")), [])

    def test_a_failed_clip_is_tried_again_on_the_next_run(self):
        self.ws.decoder.fail_on = {self.ws.refs[0][1]}
        make_converter(self.ws).run(refs(self.ws))
        self.ws.decoder.fail_on = set()
        summary = make_converter(self.ws).run(refs(self.ws))
        self.assertEqual((summary.converted, summary.skipped, summary.failed), (1, 1, 0))

    def test_a_copy_with_the_wrong_frame_count_fails_the_check(self):
        self.ws.decoder.short_by = 2
        summary = make_converter(self.ws).run(refs(self.ws))
        self.assertEqual(summary.failed, 2)
        e = load_json(self.ws)["clips"][self.ws.refs[0][0]]
        self.assertEqual(e["status"], "failed")
        self.assertIn("frames", e["error"])
        self.assertFalse(list((self.ws.work / "copies").glob("*.mp4")))

    def test_a_clip_that_cannot_be_probed_fails(self):
        extra = self.ws.src / "Broken.braw"
        extra.write_bytes(b"x")
        r = refs(self.ws) + [convert.ClipRef(stem="Broken", path=str(extra))]
        summary = make_converter(self.ws).run(r)
        self.assertEqual((summary.converted, summary.failed), (2, 1))
        self.assertIn("cannot open", load_json(self.ws)["clips"]["Broken"]["error"])

    def test_clip_without_audio_converts_without_an_audio_stream(self):
        self.ws.decoder.audio = False
        summary = make_converter(self.ws).run(refs(self.ws))
        self.assertEqual(summary.failed, 0)
        e = load_json(self.ws)["clips"][self.ws.refs[0][0]]
        self.assertIsNone(e["audio"])
        self.assertEqual(e["check"]["audio"], "")

    def test_other_colour_science_generation_is_converted_but_warned(self):
        ws = Workspace(self, names=("B001_C001",), gen="4")
        summary = make_converter(ws).run(refs(ws))
        self.assertEqual(summary.failed, 0)
        e = load_json(ws)["clips"]["B001_C001"]
        self.assertEqual(e["status"], "done")
        self.assertTrue(any("generation 4" in w for w in e["warnings"]), e["warnings"])

    def test_not_a_braw_file_is_skipped_not_failed(self):
        mov = self.ws.src / "Other.mov"
        mov.write_bytes(b"x")
        r = refs(self.ws) + [convert.ClipRef(stem="Other", path=str(mov))]
        summary = make_converter(self.ws).run(r)
        self.assertEqual(summary.exit_code, 0)
        self.assertEqual(load_json(self.ws)["clips"]["Other"]["status"], "skipped")

    def test_two_clips_with_one_name_are_refused(self):
        r = refs(self.ws) + [refs(self.ws)[0]]
        with self.assertRaises(ValueError):
            make_converter(self.ws).run(r)

    def test_dry_run_writes_nothing(self):
        summary = make_converter(self.ws).run(refs(self.ws), dry_run=True)
        self.assertEqual(summary.would_convert, 2)
        self.assertFalse(self.ws.work.exists())
        self.assertNotIn("frames", [c[0] for c in self.ws.decoder.calls])

    def test_progress_lines_name_the_clip_the_speed_and_the_size(self):
        lines = []
        make_converter(self.ws, log=lines.append).run(refs(self.ws))
        text = "\n".join(lines)
        self.assertIn("[1/2]", text)
        self.assertIn("[2/2]", text)
        self.assertIn(self.ws.refs[0][0], text)
        self.assertRegex(text, r"\d+(\.\d+)? fps")
        self.assertRegex(text, r"MB")
        self.assertIn("Converted 2", text)

    def test_the_summary_reports_speed_size_and_time(self):
        summary = make_converter(self.ws).run(refs(self.ws))
        self.assertEqual(summary.total_frames, 24)
        self.assertGreater(summary.total_seconds, 0)
        self.assertGreater(summary.total_bytes, 0)
        self.assertGreater(summary.fps, 0)


class LockTests(unittest.TestCase):
    def test_a_second_run_on_the_same_work_directory_is_refused(self):
        ws = Workspace(self)
        first = make_converter(ws)
        started = threading.Event()
        release = threading.Event()
        real = ws.decoder.write_frames

        def slow(path, scale, sink, max_frames=None):
            started.set()
            release.wait(10)
            return real(path, scale, sink, max_frames)

        ws.decoder.write_frames = slow
        t = threading.Thread(target=lambda: first.run(refs(ws)))
        t.start()
        try:
            self.assertTrue(started.wait(10))
            with self.assertRaises(convert.AlreadyRunning):
                make_converter(ws).run(refs(ws))
        finally:
            release.set()
            t.join(30)

    def test_a_stale_lock_of_a_dead_process_is_taken_over(self):
        ws = Workspace(self)
        ws.work.mkdir()
        (ws.work / ".convert.lock").write_text("999999")   # no such process
        summary = make_converter(ws).run(refs(ws))
        self.assertEqual(summary.converted, 2)
        self.assertFalse((ws.work / ".convert.lock").exists())


if __name__ == "__main__":
    unittest.main()
