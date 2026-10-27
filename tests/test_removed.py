"""Decision D4 (2026-10-27): the Resolve push and the audio copy step (requirements R1 and R2) are removed,
with their commands, code and tests. The git history keeps the code. These tests keep them removed: no module,
no command, no endpoint, no page element, no help text."""

import contextlib
import io
import re
import unittest
from pathlib import Path

import main
from tests.test_server_timeline import ServerCase

ROOT = Path(__file__).resolve().parent.parent


def parses(args):
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            main.build_parser().parse_args(args)
            return True
        except SystemExit:
            return False


class ModulesAndCommandsAreGone(unittest.TestCase):
    def test_the_modules_are_gone(self):
        self.assertFalse((ROOT / "syncer.py").exists())
        self.assertFalse((ROOT / "resolve_integration.py").exists())

    def test_the_tests_of_the_modules_are_gone(self):
        self.assertFalse((ROOT / "tests" / "test_syncer.py").exists())
        self.assertFalse((ROOT / "tests" / "test_resolve_integration.py").exists())

    def test_the_commands_are_gone(self):
        self.assertFalse(parses(["sync"]))
        self.assertFalse(parses(["resolve"]))
        self.assertFalse(parses(["resolve", "--list"]))

    def test_match_has_no_output_folder_for_copies(self):
        self.assertTrue(parses(["match", "--video", "v", "--audio", "a"]))
        self.assertFalse(parses(["match", "--video", "v", "--audio", "a", "--output", "o"]))

    def test_the_other_commands_are_still_there(self):
        for args in (["match", "--video", "v", "--audio", "a"], ["rename"], ["convert", "--work", "w"],
                     ["assemble"], ["timeline", "validate"], ["scenes", "index"], ["render", "--all"]):
            self.assertTrue(parses(args), args)

    def test_the_help_of_the_tool_does_not_mention_them(self):
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            try:
                main.build_parser().parse_args(["--help"])
            except SystemExit:
                pass
        text = sink.getvalue() + (main.__doc__ or "")
        for word in ("DaVinci", "Resolve", "ambient_audio_copies", "audio copies"):
            self.assertNotIn(word, text)


class SourcesAreClean(unittest.TestCase):
    """No leftover in the code or the page. Resolve's LUT folder (config.RESOLVE_LUT_DIR, where the base LUT is
    read from) is not the push and stays."""

    def sources(self):
        names = [p for p in ROOT.glob("*.py")] + [ROOT / "ui.html"]
        return {p.name: p.read_text(encoding="utf-8") for p in names}

    def test_no_code_or_page_refers_to_the_push_or_the_copy(self):
        for name, text in self.sources().items():
            for word in ("resolve_integration", "syncer", "create_resolve_project", "ambient_audio_copies",
                         "pushToResolve", "startSync", "/api/sync", "/api/resolve", "sync_all_scenes",
                         "remove_copied_audio", "DaVinciResolveScript", "Push to DaVinci"):
                self.assertNotIn(word, text, f"{name} still mentions {word}")

    def test_the_page_has_no_sync_tab(self):
        page = self.sources()["ui.html"]
        self.assertNotIn("Sync &amp; Export", page)
        self.assertNotIn("Copy &amp; Rename", page)
        steps = re.findall(r'class="step-tab[^"]*" data-step="(\d+)"', page)
        self.assertEqual(steps, ["1", "2", "3"])
        panels = re.findall(r'class="step-panel[^"]*" id="step(\d+)"', page)
        self.assertEqual(panels, ["1", "2", "3"])

    def test_the_project_has_no_output_folder_for_copies(self):
        from project import Project
        self.assertFalse(hasattr(Project(), "output_dir"))


class ServerHasNoRoutes(ServerCase):
    def test_the_two_endpoints_answer_404(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self.start(str(Path(tmp) / "timeline.json"), tmp)
            for path in ("/api/sync", "/api/resolve"):
                status, _, _ = self.request("POST", path, body={})
                self.assertEqual(status, 404, path)


if __name__ == "__main__":
    unittest.main()
