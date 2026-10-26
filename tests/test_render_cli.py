"""The commands `export list|add|enable|disable` and `render` (SPECS 8.5), run through the
command-line parser with a decoder that makes flat frames; and the background start with a
log, a pid file and a clean stop on SIGTERM."""

import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import export
import main
import store
import timeline
from tests.test_export import ExportFixture, FlatDecoder, W, H
from tests.test_timeline_commands import run


class ExportCommandTests(ExportFixture):
    def setUp(self):
        self.path = self.root / f"timeline-{self.id().split('.')[-1]}.json"
        tl = self.make_tl(segments=[("A", 10, 40)], exports=[])
        tl.save(str(self.path))

    def data(self):
        return json.loads(self.path.read_text())

    def test_add_writes_a_preset_into_the_file(self):
        code, out, err = run(["export", "add", "--file", str(self.path), "--preset", "film-4k",
                              "--output-dir", str(self.out)])
        self.assertEqual(code, 0, err)
        e = self.data()["exports"][0]
        self.assertEqual((e["name"], e["video"]["codec"]), ("film-4k", "h264_videotoolbox"))
        self.assertEqual((e["width"], e["height"]), (W, H))
        self.assertIn("film-4k", out)

    def test_a_custom_size_and_a_second_preset(self):
        run(["export", "add", "--file", str(self.path), "--preset", "film-4k", "--output-dir", str(self.out)])
        code, out, err = run(["export", "add", "--file", str(self.path), "--preset", "film-4k-x264",
                              "--width", "160", "--height", "90", "--output-dir", str(self.out)])
        self.assertEqual(code, 0, err)
        names = [e["name"] for e in self.data()["exports"]]
        self.assertEqual(names, ["film-4k", "film-x264-90p"][:1] + [names[1]])
        self.assertEqual(self.data()["exports"][1]["width"], 160)

    def test_a_shape_that_is_not_the_films_is_refused_and_nothing_changes(self):
        before = self.path.read_bytes()
        code, out, err = run(["export", "add", "--file", str(self.path), "--preset", "film-4k",
                              "--width", "160", "--height", "160", "--output-dir", str(self.out)])
        self.assertEqual(code, 1)
        self.assertIn("V5", out)
        self.assertEqual(self.path.read_bytes(), before)

    def test_adding_the_same_name_twice_needs_replace(self):
        args = ["export", "add", "--file", str(self.path), "--preset", "film-4k", "--output-dir", str(self.out)]
        run(args)
        code, out, err = run(args)
        self.assertEqual(code, 1)
        code, out, err = run(args + ["--replace"])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.data()["exports"]), 1)

    def test_the_master_is_added_off_and_enable_switches_it_on(self):
        run(["export", "add", "--file", str(self.path), "--preset", "film-master", "--output-dir", str(self.out)])
        self.assertIs(self.data()["exports"][0]["enabled"], False)
        code, out, err = run(["export", "enable", "film-master", "--file", str(self.path)])
        self.assertEqual(code, 0, err)
        self.assertIs(self.data()["exports"][0]["enabled"], True)
        run(["export", "disable", "film-master", "--file", str(self.path)])
        self.assertIs(self.data()["exports"][0]["enabled"], False)

    def test_enable_of_an_unknown_export_is_exit_code_1(self):
        code, out, err = run(["export", "enable", "nope", "--file", str(self.path)])
        self.assertEqual(code, 1)

    def test_list_shows_state_and_estimate(self):
        run(["export", "add", "--file", str(self.path), "--preset", "film-4k", "--output-dir", str(self.out)])
        run(["export", "add", "--file", str(self.path), "--preset", "film-master", "--output-dir", str(self.out)])
        code, out, err = run(["export", "list", "--file", str(self.path), "--work", str(self.work)])
        self.assertEqual(code, 0, err)
        self.assertIn("film-4k", out)
        self.assertIn("off", out)
        self.assertIn("prores_videotoolbox", out)
        self.assertIn("GB", out)


class RenderCommandTests(ExportFixture):
    def setUp(self):
        self.path = self.root / f"render-{self.id().split('.')[-1]}.json"
        self.work_dir = self.root / f"work-{self.id().split('.')[-1]}"

    def render(self, *extra, decoder=None):
        args = main.build_parser().parse_args(["render", "--file", str(self.path), "--work",
                                               str(self.work_dir), *extra])
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                main.cmd_render(args, decoder=decoder or FlatDecoder(self.clips))
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue(), err.getvalue()

    def test_render_export_makes_the_file_and_exits_0(self):
        self.make_tl(segments=[("A", 10, 40)]).save(str(self.path))
        code, out, err = self.render("--export", "film-x264")
        self.assertEqual(code, 0, out + err)
        self.assertTrue((self.out / "film-x264.mp4").is_file())
        self.assertIn("Done", out)

    def test_render_all_renders_what_is_switched_on_and_not_the_rest(self):
        off = self.entry("off-one")
        off["enabled"] = False
        tl = self.make_tl(segments=[("A", 10, 40)], exports=[self.entry("on-one"), off])
        tl.save(str(self.path))
        code, out, err = self.render("--all")
        self.assertEqual(code, 0, out + err)
        self.assertTrue((self.out / "on-one.mp4").is_file())
        self.assertFalse((self.out / "off-one.mp4").exists())

    def test_a_switched_off_export_is_refused_with_the_command_to_switch_it_on(self):
        off = self.entry("off-two")
        off["enabled"] = False
        self.make_tl(segments=[("A", 10, 40)], exports=[off]).save(str(self.path))
        code, out, err = self.render("--export", "off-two")
        self.assertEqual(code, 1)
        self.assertIn("export enable off-two", err)

    def test_force_renders_it_anyway(self):
        off = self.entry("off-three")
        off["enabled"] = False
        self.make_tl(segments=[("A", 10, 40)], exports=[off]).save(str(self.path))
        code, out, err = self.render("--export", "off-three", "--force")
        self.assertEqual(code, 0, out + err)

    def test_a_failed_check_is_exit_code_1(self):
        self.make_tl(segments=[("A", 10, 70)], loud=False).save(str(self.path))
        code, out, err = self.render("--export", "film-x264", decoder=FlatDecoder(self.clips, short_by=1))
        self.assertEqual(code, 1)
        for p in self.out.glob("film-x264.mp4.partial"):
            p.unlink()

    def test_neither_export_nor_all_is_a_usage_error(self):
        self.make_tl(segments=[("A", 10, 40)]).save(str(self.path))
        code, out, err = self.render()
        self.assertEqual(code, 2)

    def test_a_missing_file_is_exit_code_2(self):
        code, out, err = self.render("--export", "film-x264")
        self.assertEqual(code, 2)

    def test_the_credit_file_is_named_in_the_output(self):
        tl = self.make_tl(segments=[("A", 10, 40)], lut=self.luts / "identity.cube", loud=False)
        tl.data["look"]["lut"]["credit"] = "LUTs: [slay](https://example.org) (CC BY 4.0)"
        tl.data["exports"] = [self.entry("credited-cli")]
        tl.save(str(self.path))
        code, out, err = self.render("--export", "credited-cli")
        self.assertEqual(code, 0, out + err)
        self.assertIn("credited-cli.credit.txt", out)


class DetachTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    SCRIPT = ("import signal, sys, time\n"
              "def stop(*a):\n"
              "    print('stopping cleanly', flush=True); sys.exit(130)\n"
              "signal.signal(signal.SIGTERM, stop)\n"
              "print('started', flush=True)\n"
              "time.sleep(60)\n")

    def wait_for(self, predicate, seconds=10):
        end = time.time() + seconds
        while time.time() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_the_process_has_a_session_of_its_own_a_log_and_a_pid_file(self):
        log, pidf = self.dir / "reports" / "r.log", self.dir / "reports" / "r.pid"
        pid = export.spawn_detached([sys.executable, "-c", self.SCRIPT], str(log), str(pidf))
        try:
            self.assertEqual(int(pidf.read_text()), pid)
            self.assertTrue(self.wait_for(lambda: log.exists() and "started" in log.read_text()))
            self.assertNotEqual(os.getsid(pid), os.getsid(0))
            self.assertEqual(os.getsid(pid), pid)              # it leads its own session
        finally:
            os.kill(pid, signal.SIGKILL)

    def test_sigterm_stops_it_cleanly(self):
        log, pidf = self.dir / "r.log", self.dir / "r.pid"
        pid = export.spawn_detached([sys.executable, "-c", self.SCRIPT], str(log), str(pidf))
        self.assertTrue(self.wait_for(lambda: log.exists() and "started" in log.read_text()))
        os.kill(pid, signal.SIGTERM)
        self.assertTrue(self.wait_for(lambda: "stopping cleanly" in log.read_text()))

    def test_a_second_start_is_refused_while_the_first_lives(self):
        log, pidf = self.dir / "r.log", self.dir / "r.pid"
        pid = export.spawn_detached([sys.executable, "-c", self.SCRIPT], str(log), str(pidf))
        try:
            with self.assertRaises(export.AlreadyRunning):
                export.spawn_detached([sys.executable, "-c", self.SCRIPT], str(log), str(pidf))
        finally:
            os.kill(pid, signal.SIGKILL)

    def test_a_pid_file_of_a_dead_process_does_not_block(self):
        log, pidf = self.dir / "r.log", self.dir / "r.pid"
        pidf.write_text("999999\n")
        pid = export.spawn_detached([sys.executable, "-c", "print('hi')"], str(log), str(pidf))
        self.assertEqual(int(pidf.read_text()), pid)
        self.assertTrue(self.wait_for(lambda: "hi" in log.read_text()))


class StopTest(unittest.TestCase):
    def test_sigterm_during_a_real_render_command_removes_its_files(self):
        """Runs `main.py render` in a process of its own with a decoder that waits, and stops it."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        script = root / "run.py"
        script.write_text(
            "import sys, time\n"
            f"sys.path.insert(0, {str(Path(main.__file__).parent)!r})\n"
            "import main\n"
            "from tests.test_export import ExportFixture, FlatDecoder\n"
            "class Slow(FlatDecoder):\n"
            "    def write_frame_range(self, path, scale, sink, first, last):\n"
            "        n = super().write_frame_range(path, scale, sink, first, first + 5)\n"
            "        print('waiting', flush=True); time.sleep(60)\n"
            "ExportFixture.setUpClass()\n"
            "tl = ExportFixture.make_tl(segments=[('A', 10, 70)])\n"
            f"tl.save({str(root / 'tl.json')!r})\n"
            "main.install_graceful_stop()\n"
            "args = main.build_parser().parse_args(['render', '--file', "
            f"{str(root / 'tl.json')!r}, '--work', {str(root / 'work')!r}, '--export', 'film-x264'])\n"
            "main.cmd_render(args, decoder=Slow(ExportFixture.clips))\n")
        with open(root / "log.txt", "wb") as log:
            p = subprocess.Popen([sys.executable, str(script)], stdout=log, stderr=log,
                                 cwd=str(Path(main.__file__).parent))
        end = time.time() + 30
        while time.time() < end and b"waiting" not in (root / "log.txt").read_bytes():
            time.sleep(0.1)
        self.assertIn(b"waiting", (root / "log.txt").read_bytes())
        p.send_signal(signal.SIGTERM)
        self.assertEqual(p.wait(timeout=30), 130)
        work = root / "work"
        self.assertEqual([x.name for x in work.iterdir() if x.name != "reports"], [])
        self.assertIn(b"Interrupted", (root / "log.txt").read_bytes())


if __name__ == "__main__":
    unittest.main()
