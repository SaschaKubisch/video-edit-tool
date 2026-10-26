"""Check 2.7, over files: `assemble` reads project.json, conversion.json and the
analysis files of a work folder, writes timeline.json and the assembly report, and
only reads the originals, the preview copies and conversion.json."""

import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import assemble
import config
import main
import safety
import store
import timeline
import tools
from convert import ConversionStore
from project import Project, SceneMatch
from tests.fakes import IDENTITY_CUBE

FRAMES = 179        # 6 s at 30000/1001
FPS = {"num": 30000, "den": 1001}


def make_copy(path):
    subprocess.run([tools.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30000/1001",
                    "-frames:v", str(FRAMES), "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-g", "30", str(path)], check=True)


class ProjectFiles(unittest.TestCase):
    NAMES = ["A_C2", "A_C10", "A_C1"]       # not in natural order on purpose

    @classmethod
    def setUpClass(cls):
        cls._shared = tempfile.TemporaryDirectory()
        cls.sample_copy = Path(cls._shared.name) / "sample.mp4"
        make_copy(cls.sample_copy)

    @classmethod
    def tearDownClass(cls):
        cls._shared.cleanup()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        self.src = root / "Recordings"
        self.src.mkdir()
        self.work = root / "work"
        (self.work / "copies").mkdir(parents=True)
        self.lut = root / "id.cube"
        self.lut.write_text(IDENTITY_CUBE)
        self.lut_sha = hashlib.sha256(self.lut.read_bytes()).hexdigest()
        store_ = ConversionStore(str(self.work / config.CONVERSION_FILE))
        store_.data["base_lut"] = {"path": str(self.lut), "sha256": self.lut_sha}
        matches = []
        for i, name in enumerate(self.NAMES):
            braw = self.src / f"{name}.braw"
            braw.write_bytes(b"fake braw %d " % i * 50)
            wav = self.src / f"ZOOM{i:04d}.WAV"
            wav.write_bytes(b"fake wav")
            copy = self.work / "copies" / f"{name}.mp4"
            copy.write_bytes(self.sample_copy.read_bytes())
            store_.clips[name] = {
                "original": str(braw), "preview_copy": str(copy), "status": "done",
                "frames": FRAMES, "fps": dict(FPS), "size": {"width": 4096, "height": 2160},
                "copy_size": {"width": 160, "height": 90}, "lut_sha256": self.lut_sha}
            adir = self.work / "analysis" / name
            adir.mkdir(parents=True)
            (adir / "clap.json").write_text(json.dumps(
                {"result": "agree", "video_time_s": 0.3, "review": False, "reasons": []}))
            (adir / "scenes.json").write_text(json.dumps(
                {"runs": [{"in": 5, "out": 100, "score": 0.8, "low_confidence": False},
                          {"in": 110, "out": 175, "score": 0.7, "low_confidence": False}],
                 "dropped": [], "moving": []}))
            matches.append(SceneMatch(index=i, video_path=str(braw), audio_path=str(wav),
                                      video_duration=6.0, audio_duration=6.0, offset=0.2 + i,
                                      confidence=1.0))
        store_.save()
        self.conv_path = self.work / config.CONVERSION_FILE
        self.conv = store_
        # the project lists the clips in an order that is not the recording order
        self.project = Project(video_dir=str(self.src), audio_dir=str(self.src),
                               matches=list(matches), work_dir=str(self.work))
        self.project_file = root / "project.json"
        self.project.save(str(self.project_file))
        self.out = root / "film" / "timeline.json"
        self.out.parent.mkdir()

    def listing(self, folder):
        return sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                      for p in Path(folder).rglob("*") if p.is_file())

    def run_cli(self, argv):
        args = main.build_parser().parse_args(argv)
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out):
            try:
                args.func(args)
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue()


class AssembleProjectTests(ProjectFiles):
    def test_writes_a_valid_timeline_and_the_reports(self):
        result = assemble.assemble_project(self.project, str(self.work), str(self.out),
                                           log=lambda s: None)
        tl = timeline.Timeline.load(str(self.out))
        self.assertEqual(timeline.has_errors(tl.validate()), False)       # with file checks
        self.assertEqual(tl.data, json.loads(json.dumps(result.data)))
        self.assertEqual(len(tl.data["segments"]), 6)
        report = json.loads((self.work / "reports" / "assembly-report.json").read_text())
        self.assertEqual(report["film"]["segments"], 6)
        text = (self.work / "reports" / "assembly-report.txt").read_text()
        self.assertIn("A_C10", text)

    def test_clips_are_in_recording_order_not_in_the_order_of_the_project(self):
        result = assemble.assemble_project(self.project, str(self.work), str(self.out),
                                           log=lambda s: None)
        self.assertEqual(list(result.data["sources"]), ["A_C1", "A_C2", "A_C10"])
        self.assertEqual([s["source"] for s in result.data["segments"]][::2],
                         ["A_C1", "A_C2", "A_C10"])

    def test_the_selection_of_the_project_sets_the_order_when_there_is_one(self):
        self.project.selection = [0, 2]            # A_C2 then A_C1
        result = assemble.assemble_project(self.project, str(self.work), str(self.out),
                                           log=lambda s: None)
        self.assertEqual(list(result.data["sources"]), ["A_C2", "A_C1"])

    def test_offsets_files_and_hashes_come_from_the_project_and_conversion_file(self):
        result = assemble.assemble_project(self.project, str(self.work), str(self.out),
                                           log=lambda s: None)
        s = result.data["sources"]["A_C10"]
        self.assertEqual(s["audio_offset_s"], 1.2)
        self.assertEqual(s["audio"], str(self.src / "ZOOM0001.WAV"))
        self.assertEqual(s["original_video"], str(self.src / "A_C10.braw"))
        self.assertEqual(s["preview_copy"], str(self.work / "copies" / "A_C10.mp4"))
        self.assertEqual(s["copy_lut_sha256"], self.lut_sha)
        self.assertEqual(result.data["look"]["base"],
                         {"mode": "lut", "path": str(self.lut), "sha256": self.lut_sha})

    def test_a_clip_whose_copy_is_not_done_is_left_out_and_listed(self):
        self.conv.clips["A_C10"]["status"] = "partial"
        self.conv.save()
        result = assemble.assemble_project(self.project, str(self.work), str(self.out),
                                           log=lambda s: None)
        self.assertEqual(list(result.data["sources"]), ["A_C1", "A_C2"])
        self.assertEqual(result.report["film"]["left_out"], ["A_C10"])

    def test_a_clip_without_scene_analysis_is_still_assembled_as_unanalysed(self):
        (self.work / "analysis" / "A_C1" / "scenes.json").unlink()
        result = assemble.assemble_project(self.project, str(self.work), str(self.out),
                                           log=lambda s: None)
        notes = [s["note"] for s in result.data["segments"] if s["source"] == "A_C1"]
        self.assertEqual(len(notes), 1)
        self.assertIn("unanalysed", notes[0])

    def test_limit_and_only(self):
        result = assemble.assemble_project(self.project, str(self.work), str(self.out),
                                           limit=1, log=lambda s: None)
        self.assertEqual(list(result.data["sources"]), ["A_C1"])
        self.out.unlink()
        result = assemble.assemble_project(self.project, str(self.work), str(self.out),
                                           only=["A_C10"], log=lambda s: None)
        self.assertEqual(list(result.data["sources"]), ["A_C10"])

    def test_an_existing_timeline_is_not_overwritten_without_replace(self):
        assemble.assemble_project(self.project, str(self.work), str(self.out), log=lambda s: None)
        before = self.out.read_bytes()
        with self.assertRaises(assemble.TimelineExists):
            assemble.assemble_project(self.project, str(self.work), str(self.out),
                                      log=lambda s: None)
        self.assertEqual(self.out.read_bytes(), before)
        self.assertEqual(store.list_snapshots(str(self.out)), [])

    def test_replace_snapshots_the_old_file_first(self):
        assemble.assemble_project(self.project, str(self.work), str(self.out), log=lambda s: None)
        before = self.out.read_bytes()
        assemble.assemble_project(self.project, str(self.work), str(self.out), replace=True,
                                  only=["A_C1"], log=lambda s: None)
        snaps = store.list_snapshots(str(self.out))
        self.assertEqual(len(snaps), 1)
        self.assertEqual(Path(snaps[0]).read_bytes(), before)
        self.assertEqual(len(timeline.Timeline.load(str(self.out)).data["segments"]), 2)

    def test_only_reads_the_originals_the_copies_and_conversion_json(self):
        src_before = self.listing(self.src)
        copies_before = self.listing(self.work / "copies")
        conv_before = (self.conv_path.read_bytes(), self.conv_path.stat().st_mtime_ns)
        assemble.assemble_project(self.project, str(self.work), str(self.out), log=lambda s: None)
        self.assertEqual(self.listing(self.src), src_before)
        self.assertEqual(self.listing(self.work / "copies"), copies_before)
        self.assertEqual((self.conv_path.read_bytes(), self.conv_path.stat().st_mtime_ns),
                         conv_before)

    def test_a_timeline_inside_the_originals_folder_is_refused(self):
        with self.assertRaises(safety.OriginalsProtectedError):
            assemble.assemble_project(self.project, str(self.work),
                                      str(self.src / "timeline.json"), log=lambda s: None)
        self.assertFalse((self.src / "timeline.json").exists())

    def test_a_work_directory_inside_the_originals_folder_is_refused(self):
        with self.assertRaises(safety.OriginalsProtectedError):
            assemble.assemble_project(self.project, str(self.src / "work"), str(self.out),
                                      log=lambda s: None)

    def test_a_missing_base_lut_in_conversion_json_is_an_error(self):
        del self.conv.data["base_lut"]
        self.conv.save()
        with self.assertRaises(assemble.AssemblyError) as cm:
            assemble.assemble_project(self.project, str(self.work), str(self.out),
                                      log=lambda s: None)
        self.assertIn("base_lut", str(cm.exception))

    def test_a_timeline_that_breaks_a_rule_is_not_written(self):
        # the preview copy has 179 frames; the conversion file says 200: rule V7
        self.conv.clips["A_C1"]["frames"] = 200
        self.conv.save()
        with self.assertRaises(assemble.AssemblyError) as cm:
            assemble.assemble_project(self.project, str(self.work), str(self.out),
                                      log=lambda s: None)
        self.assertIn("V7", str(cm.exception))
        self.assertFalse(self.out.exists())


class AssembleCommandTests(ProjectFiles):
    def test_command_line(self):
        code, text = self.run_cli(["--project", str(self.project_file), "assemble",
                                   "--work", str(self.work), "--output", str(self.out),
                                   "--limit", "2"])
        self.assertEqual(code, 0, text)
        self.assertTrue(self.out.exists())
        self.assertIn("A_C1", text)
        self.assertIn("4 segments", text)

    def test_command_line_refuses_to_overwrite_and_replace_allows_it(self):
        argv = ["--project", str(self.project_file), "assemble", "--work", str(self.work),
                "--output", str(self.out)]
        self.assertEqual(self.run_cli(argv)[0], 0)
        code, text = self.run_cli(argv)
        self.assertEqual(code, 2)
        self.assertIn("--replace", text)
        self.assertEqual(self.run_cli(argv + ["--replace"])[0], 0)

    def test_command_line_settings(self):
        code, text = self.run_cli(["--project", str(self.project_file), "assemble",
                                   "--work", str(self.work), "--output", str(self.out),
                                   "--fade-in", "0.5", "--fade-out", "1.5", "--join", "0.25",
                                   "--frame", "1920x1080", "--fit", "fit"])
        self.assertEqual(code, 0, text)
        film = json.loads(self.out.read_text())["film"]
        self.assertEqual((film["fade_in_s"], film["fade_out_s"]), (0.5, 1.5))
        self.assertEqual(film["audio_join"]["duration_s"], 0.25)
        self.assertEqual(film["frame"], {"width": 1920, "height": 1080})

    def test_the_default_timeline_file_is_next_to_the_project_file(self):
        code, text = self.run_cli(["--project", str(self.project_file), "assemble",
                                   "--work", str(self.work)])
        self.assertEqual(code, 0, text)
        self.assertTrue((self.project_file.parent / "timeline.json").exists())

    def test_command_line_without_a_work_directory_is_an_error(self):
        self.project.work_dir = ""
        self.project.save(str(self.project_file))
        code, text = self.run_cli(["--project", str(self.project_file), "assemble"])
        self.assertEqual(code, 2)
        self.assertIn("--work", text)


class HandOffsetsAreKept(ProjectFiles):
    """An offset the owner typed (`audio_offset_hand_s`, SPECS 3.2) is the owner's work. When
    assemble builds the file again with --replace, it carries the offset over."""

    def assemble(self, **kw):
        return assemble.assemble_project(self.project, str(self.work), str(self.out),
                                         log=lambda s: None, **kw)

    def set_hand(self, source, value):
        tl = timeline.Timeline.load(str(self.out))
        tl.data["sources"][source]["audio_offset_hand_s"] = value
        store.save(tl, str(self.out))

    def test_assembling_again_keeps_the_offsets_typed_by_hand(self):
        self.assemble()
        self.set_hand("A_C2", -0.75)
        result = self.assemble(replace=True)
        sources = result.data["sources"]
        self.assertEqual(sources["A_C2"]["audio_offset_hand_s"], -0.75)
        self.assertNotIn("audio_offset_hand_s", sources["A_C1"])
        self.assertNotIn("audio_offset_hand_s", sources["A_C10"])
        on_disk = timeline.Timeline.load(str(self.out)).data["sources"]["A_C2"]
        self.assertEqual(on_disk["audio_offset_hand_s"], -0.75)
        self.assertEqual(on_disk["audio_offset_s"], 0.2)         # from matching, as before

    def test_the_report_names_the_offsets_that_were_kept(self):
        self.assemble()
        self.set_hand("A_C2", -0.75)
        result = self.assemble(replace=True)
        self.assertEqual(result.report["hand_offsets_kept"], {"A_C2": -0.75})
        text = (self.work / "reports" / "assembly-report.txt").read_text()
        self.assertIn("A_C2", text)
        self.assertIn("-0.750", text)

    def test_a_clip_that_leaves_the_film_loses_its_offset_and_the_report_says_so(self):
        self.assemble()
        self.set_hand("A_C2", -0.75)
        result = self.assemble(replace=True, only=["A_C1"])
        self.assertNotIn("A_C2", result.data["sources"])
        self.assertEqual(result.report["hand_offsets_dropped"], {"A_C2": -0.75})
        # the earlier file stays in the history, with the offset in it
        newest = json.loads(Path(store.list_snapshots(str(self.out))[-1]).read_text())
        self.assertEqual(newest["sources"]["A_C2"]["audio_offset_hand_s"], -0.75)

    def test_without_an_old_file_or_without_hand_offsets_nothing_is_reported(self):
        first = self.assemble()
        self.assertEqual(first.report["hand_offsets_kept"], {})
        again = self.assemble(replace=True)
        self.assertEqual(again.report["hand_offsets_kept"], {})
        self.assertEqual(again.report["hand_offsets_dropped"], {})
        self.assertNotIn("set by hand", (self.work / "reports" / "assembly-report.txt").read_text())

    def test_an_old_file_that_cannot_be_read_does_not_stop_the_assembly(self):
        self.out.write_text("this is not json")
        result = self.assemble(replace=True)
        self.assertEqual(result.report["hand_offsets_kept"], {})
        self.assertTrue(any("could not be read" in w for w in result.report["film"]["warnings"]),
                        result.report["film"]["warnings"])
        self.assertEqual(len(timeline.Timeline.load(str(self.out)).data["segments"]), 6)


if __name__ == "__main__":
    unittest.main()
