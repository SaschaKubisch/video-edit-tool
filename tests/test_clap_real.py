"""Real-data check of the clap finder (SPECS check 2.14, first part).

Needs the owner's machine; skipped, with the reason printed, when missing:

  VET_REAL_PROJECT   a project.json with matched clips whose files are reachable
  VET_WORK_DIR       a work folder on the SSD (the test writes small JSON files there)

It runs the finder on the first 10 clips. It asserts only what must hold for any
footage: every clip gets a result, and wherever the camera and the WAV agree, the
difference of their clap times equals the stored offset within CLAP_AGREE_SEC. It
does not assert where the claps are; the report in the work folder is for the
owner to read.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import clap
import config
from project import Project

PROJECT = os.environ.get("VET_REAL_PROJECT", "")
WORK = os.environ.get("VET_WORK_DIR", "")


def _need():
    if not PROJECT or not Path(PROJECT).is_file():
        return "VET_REAL_PROJECT is not set to a project.json"
    if not WORK or not Path(WORK).is_dir():
        return "VET_WORK_DIR is not set to a folder (on the SSD)"
    project = Project.load(PROJECT)
    first = project.matches[:10]
    if not first or not all(Path(m.video_path).is_file() and Path(m.audio_path).is_file()
                            for m in first):
        return "the first 10 clips of the project are not reachable"
    return ""


SKIP = _need()


@unittest.skipIf(SKIP, SKIP)
class RealClapTests(unittest.TestCase):
    def test_first_ten_clips(self):
        project = Project.load(PROJECT)
        work = tempfile.mkdtemp(prefix="vet-clap-", dir=WORK)
        self.addCleanup(shutil.rmtree, work, True)
        rows = clap.analyse_project(project, work, limit=10, log=lambda s: None)
        self.assertEqual(len(rows), 10)
        for r in rows:
            with self.subTest(clip=r["clip"]):
                self.assertIn(r["result"], ("agree", "single", "none"))
                if r["result"] == "agree":
                    self.assertLessEqual(abs(r["delta_s"]), config.CLAP_AGREE_SEC)
                    self.assertIsNotNone(r["clap_s"])
        self.assertTrue((Path(work) / "reports" / "clap-report.txt").is_file())


if __name__ == "__main__":
    unittest.main()
