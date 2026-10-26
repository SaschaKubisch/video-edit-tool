"""Tests for project.py: the field work_dir (SPECS 6.9)."""

import json
import tempfile
import unittest
from pathlib import Path

from project import Project


class WorkDirTests(unittest.TestCase):
    def test_work_dir_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            Project(video_dir="/v", audio_dir="/a", work_dir="/w").save(str(path))
            self.assertEqual(Project.load(str(path)).work_dir, "/w")

    def test_file_without_work_dir_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            path.write_text(json.dumps({"video_dir": "/v", "audio_dir": "/a",
                                        "output_dir": "", "matches": [],
                                        "selection": [], "selection_trims": {}}))
            self.assertEqual(Project.load(str(path)).work_dir, "")


if __name__ == "__main__":
    unittest.main()
