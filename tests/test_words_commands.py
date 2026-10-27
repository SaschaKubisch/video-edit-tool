"""Check 4.4 and 4.9: the commands `subtitles` and `title` and the form of an export, run through the
command-line parser as the owner or Claude Code runs them (SPECS 9.4, 9.7, 9.8). The film is the
example of SPECS 3.3 saved as a timeline file; the files it names do not exist, so the commands do not
read them."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import main
import store
import textlayout
import timeline
import words
from tests.test_transcribe import FakeWhisperCase, phrase
from tests.timeline_fixture import example


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args = main.build_parser().parse_args(argv)
            args.func(args)
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue(), err.getvalue()


class CommandCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "timeline.json"
        data = example()
        data["exports"][0]["output"] = str(self.dir / "film-4k.mp4")
        store.save(timeline.Timeline(data), str(self.path))
        self._factory = words.RULES_FACTORY
        words.RULES_FACTORY = lambda data: textlayout.char_rules(42)
        self.addCleanup(setattr, words, "RULES_FACTORY", self._factory)

    def cmd(self, *argv):
        return run([argv[0], argv[1], "--file", str(self.path), *argv[2:]])

    def data(self):
        return json.loads(self.path.read_text())

    def cues(self):
        return self.data()["subtitles"]["cues"]

    def snapshots(self):
        return len(store.list_snapshots(str(self.path)))


class SubtitleCommandTests(CommandCase):
    def test_list_shows_film_times_and_scenes(self):
        code, out, err = self.cmd("subtitles", "list")
        self.assertEqual(code, 0, err)
        self.assertIn("c001", out)
        self.assertIn("00:00:03.20", out)
        self.assertIn("s001", out)
        self.assertIn("Now the pan is hot enough.", out)
        code, out, _ = self.cmd("subtitles", "list", "--json")
        rows = json.loads(out)
        self.assertEqual((rows[0]["id"], rows[0]["segment"]), ("c001", "s001"))
        self.assertAlmostEqual(rows[0]["start_s"], 3.2)

    def test_add_at_film_time_keeps_the_old_file_as_a_snapshot(self):
        code, out, err = self.cmd("subtitles", "add", "--at", "30", "--end", "33", "--text", "Hello there.")
        self.assertEqual(code, 0, err)
        added = [c for c in self.cues() if c["id"] == "c002"][0]
        self.assertEqual((added["anchor"], added["text"], added["origin"], added["locked"]),
                         ("s002", "Hello there.", "manual", True))
        self.assertIn("c002", out)
        self.assertEqual(self.snapshots(), 1)

    def test_add_with_a_length(self):
        code, _, err = self.cmd("subtitles", "add", "--at", "30", "--length", "2", "--text", "Two seconds.")
        self.assertEqual(code, 0, err)
        c = [c for c in self.cues() if c["id"] == "c002"][0]
        self.assertAlmostEqual(c["end_s"] - c["start_s"], 2.0, delta=0.04)

    def test_add_that_overlaps_is_refused_and_the_file_is_unchanged(self):
        before = self.path.read_text()
        code, out, err = self.cmd("subtitles", "add", "--at", "4", "--end", "7", "--text", "Overlaps c001.")
        self.assertEqual(code, 1)
        self.assertIn("V24", out + err)
        self.assertEqual(self.path.read_text(), before)

    def test_a_time_outside_the_film_is_refused(self):
        code, _, err = self.cmd("subtitles", "add", "--at", "500", "--text", "x")
        self.assertEqual(code, 1)
        self.assertIn("no scene at 500", err)

    def test_edit(self):
        code, _, err = self.cmd("subtitles", "edit", "c001", "--text", "Edited.", "--start", "2", "--end", "5")
        self.assertEqual(code, 0, err)
        c = self.cues()[0]
        self.assertEqual((c["text"], c["locked"]), ("Edited.", True))
        self.assertAlmostEqual(c["start_s"], 2.0, delta=0.04)
        code, _, err = self.cmd("subtitles", "edit", "c099", "--text", "x")
        self.assertEqual(code, 1)

    def test_remove(self):
        code, _, err = self.cmd("subtitles", "remove", "c001")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.cues(), [])
        code, _, err = self.cmd("subtitles", "remove", "c001")
        self.assertEqual(code, 1)

    def test_shift(self):
        code, _, err = self.cmd("subtitles", "shift", "1.5")
        self.assertEqual(code, 0, err)
        self.assertAlmostEqual(self.cues()[0]["start_s"], 4.7, delta=0.04)
        code, _, err = self.cmd("subtitles", "shift", "-30")
        self.assertEqual(code, 1)
        self.assertAlmostEqual(self.cues()[0]["start_s"], 4.7, delta=0.04)

    def test_scene_text(self):
        code, out, err = self.cmd("subtitles", "text", "s003", "--text",
                                  "Um, the pan is hot. We add the sausage and wait for it to cook")
        self.assertEqual(code, 0, err)
        d = self.data()
        self.assertEqual(d["segments"][2]["text"], "The pan is hot. We add the sausage and wait for it to cook.")
        self.assertGreaterEqual(len([c for c in d["subtitles"]["cues"] if c["anchor"] == "s003"]), 2)
        self.assertIn("cues", out)

    def test_scene_text_from_a_file(self):
        text = self.dir / "scene.txt"
        text.write_text("Pan on the stove.\n")
        code, _, err = self.cmd("subtitles", "text", "s003", "--text-file", str(text))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.data()["segments"][2]["text"], "Pan on the stove.")

    def test_scene_text_asks_before_it_replaces_cues_made_by_hand(self):
        self.cmd("subtitles", "text", "s003", "--text", "First version of the text.")
        self.cmd("subtitles", "edit", [c for c in self.cues() if c["anchor"] == "s003"][0]["id"], "--text", "Mine.")
        before = self.path.read_text()
        code, out, err = self.cmd("subtitles", "text", "s003", "--text", "Second version.")
        self.assertEqual(code, 1)
        self.assertIn("--replace-adjusted", err)
        self.assertEqual(self.path.read_text(), before)
        code, _, err = self.cmd("subtitles", "text", "s003", "--text", "Second version.", "--replace-adjusted")
        self.assertEqual(code, 0, err)
        self.assertEqual([c["text"] for c in self.cues() if c["anchor"] == "s003"], ["Second version."])

    def test_import_json_and_srt(self):
        f = self.dir / "cues.json"
        f.write_text(json.dumps({"cues": [{"start_s": 31, "end_s": 34, "text": "From Claude Code."}]}))
        code, _, err = self.cmd("subtitles", "import", str(f), "--replace")
        self.assertEqual(code, 0, err)
        self.assertEqual([c["text"] for c in self.cues()], ["From Claude Code."])
        s = self.dir / "cues.srt"
        s.write_text("1\n00:00:40,000 --> 00:00:43,000\nFrom an SRT file.\n")
        code, _, err = self.cmd("subtitles", "import", str(s))
        self.assertEqual(code, 0, err)
        self.assertEqual([c["text"] for c in self.cues()], ["From Claude Code.", "From an SRT file."])
        bad = self.dir / "bad.json"
        bad.write_text("{not json")
        self.assertEqual(self.cmd("subtitles", "import", str(bad))[0], 1)

    def test_export_writes_the_srt(self):
        out = self.dir / "film.en.srt"
        code, stdout, err = self.cmd("subtitles", "export", "--out", str(out))
        self.assertEqual(code, 0, err)
        self.assertEqual(words.parse_srt(out.read_text())[0]["text"], "Now the pan is hot enough.")
        code, _, err = self.cmd("subtitles", "export", "--export", "film-4k")
        self.assertEqual(code, 0, err)
        self.assertTrue((self.dir / "film-4k.en.srt").is_file())

    def test_sort(self):
        d = self.data()
        d["subtitles"]["cues"].insert(0, {"id": "c002", "anchor": "s002", "start_s": 1.0, "end_s": 3.0,
                                          "text": "Later.", "origin": "manual"})
        self.path.write_text(timeline.dumps(d))
        code, _, err = self.cmd("subtitles", "sort")
        self.assertEqual(code, 0, err)
        self.assertEqual([c["id"] for c in self.cues()], ["c001", "c002"])

    def test_check_reports_cues_that_do_not_fit(self):
        code, out, err = self.cmd("subtitles", "check")
        self.assertEqual(code, 0, err + out)
        self.assertIn("1 cue", out)
        d = self.data()
        d["subtitles"]["cues"][0]["text"] = "word " * 60
        self.path.write_text(timeline.dumps(d))
        code, out, err = self.cmd("subtitles", "check")
        self.assertEqual(code, 1)
        self.assertIn("c001", out)


class TitleCommandTests(CommandCase):
    def test_add_edit_remove_list(self):
        code, out, err = self.cmd("title", "add", "--text", "Monday kitchen", "--start", "10", "--end", "14",
                                  "--template", "lower_third", "--size", "5", "--fade", "0.4")
        self.assertEqual(code, 0, err)
        t = self.data()["titles"][-1]
        self.assertEqual((t["id"], t["text"], t["anchor"], t["start_s"], t["end_s"], t["template"], t["size_pct"],
                          t["fade_s"]), ("t002", "Monday kitchen", "film", 10, 14, "lower_third", 5, 0.4))
        code, out, _ = self.cmd("title", "list")
        self.assertIn("Monday kitchen", out)
        self.assertIn("lower_third", out)
        code, _, err = self.cmd("title", "edit", "t002", "--text", "Tuesday", "--length", "2")
        self.assertEqual(code, 0, err)
        t = self.data()["titles"][-1]
        self.assertEqual((t["text"], t["end_s"]), ("Tuesday", 12))
        code, _, err = self.cmd("title", "remove", "t002")
        self.assertEqual(code, 0, err)
        self.assertEqual([t["id"] for t in self.data()["titles"]], ["t001"])

    def test_a_title_on_a_scene(self):
        code, _, err = self.cmd("title", "add", "--text", "Pan", "--anchor", "s003", "--start", "1", "--length", "3")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.data()["titles"][-1]["anchor"], "s003")

    def test_bad_titles_are_refused(self):
        before = self.path.read_text()
        self.assertEqual(self.cmd("title", "add", "--text", "x", "--start", "5", "--end", "2")[0], 1)
        self.assertEqual(self.cmd("title", "add", "--text", "x", "--start", "1", "--template", "corner")[0], 2)    # argparse
        self.assertEqual(self.cmd("title", "add", "--text", "x", "--start", "1", "--size", "50")[0], 1)
        self.assertEqual(self.cmd("title", "remove", "t099")[0], 1)
        self.assertEqual(self.path.read_text(), before)

    def test_import(self):
        f = self.dir / "titles.json"
        f.write_text(json.dumps([{"text": "One", "start_s": 0, "end_s": 3, "template": "opening"},
                                 {"text": "Two", "start_s": 20, "length_s": 2}]))
        code, _, err = self.cmd("title", "import", str(f), "--replace")
        self.assertEqual(code, 0, err)
        self.assertEqual([(t["id"], t["text"]) for t in self.data()["titles"]], [("t001", "One"), ("t002", "Two")])


class ExportFormCommandTests(CommandCase):
    def test_set_the_form_of_an_export(self):
        code, out, err = self.cmd("export", "set", "film-4k", "--subtitles", "burned")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.data()["exports"][0]["subtitles"], "burned")
        self.assertEqual(self.cmd("export", "set", "nope", "--subtitles", "srt")[0], 1)
        self.assertEqual(self.cmd("export", "set", "film-4k", "--subtitles", "hard")[0], 2)

    def test_list_shows_the_form(self):
        code, out, _ = self.cmd("export", "list")
        self.assertIn("subtitles srt", out)

    def test_add_with_a_form(self):
        code, _, err = self.cmd("export", "add", "--preset", "film-4k", "--name", "film-b", "--output-dir",
                                str(self.dir), "--subtitles", "both")
        self.assertEqual(code, 0, err)
        self.assertEqual([e for e in self.data()["exports"] if e["name"] == "film-b"][0]["subtitles"], "both")


class AutoCommandTests(FakeWhisperCase):
    def test_subtitles_auto_writes_the_cues_and_a_report(self):
        wav = self.wav("a", 12, [(2.0, 5.0), (7.0, 9.0)])
        self.hears(12, phrase(2.0, 5.0, "Now the pan is hot enough."), phrase(7.0, 9.0, "Thank you."))
        tl = self.film([("A", wav, 400)], [("A", 30, 330)])
        path = self.root / "timeline.json"
        store.save(tl, str(path))
        code, out, err = run(["subtitles", "auto", "--file", str(path), "--work", str(self.work)])
        self.assertEqual(code, 0, err + out)
        cues = json.loads(path.read_text())["subtitles"]["cues"]
        self.assertEqual([c["text"] for c in cues], ["Now the pan is hot enough."])
        report = json.loads((self.work / "reports" / "subtitles-auto-report.json").read_text())
        self.assertEqual(report["cues"], 1)
        self.assertEqual(report["dropped"][0]["text"], "Thank you.")
        self.assertIn("dropped", out)
        code, out, err = run(["subtitles", "auto", "--file", str(path), "--work", str(self.work)])
        self.assertEqual(code, 0, err + out)
        self.assertEqual(len(json.loads(path.read_text())["subtitles"]["cues"]), 1)
        self.assertEqual(len(self.calls()), 1)

    def test_without_a_model_the_command_says_what_to_set(self):
        wav = self.wav("a", 12, [])
        path = self.root / "timeline.json"
        store.save(self.film([("A", wav, 400)], [("A", 30, 330)]), str(path))
        os.environ.pop("VET_WHISPER_MODEL")
        code, out, err = run(["subtitles", "auto", "--file", str(path), "--work", str(self.work)])
        self.assertEqual(code, 1)
        self.assertIn("VET_WHISPER_MODEL", err)


if __name__ == "__main__":
    unittest.main()
