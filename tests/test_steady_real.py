"""Real-data checks of the steady scenes, the assembly and the film preview (SPECS
checks 2.6, 2.7, 2.11, 2.12 and 2.14, first part).

They need the owner's machine and are skipped, with the reason printed, when it is
missing:

  VET_REAL_PROJECT   a project.json with matched clips whose files are reachable
  VET_WORK_DIR       the work folder of the conversion: it holds conversion.json and
                     the preview copies (copies/*.mp4) of the first clips with status done.
                     The tests write only into temporary folders inside it.

They assert what must hold for any footage, not where the scenes are: the scenes lie
inside their clip, are at least the minimum long and do not overlap; the assembled
timeline passes the validator with the file checks; the film preview has as many
frames as the timeline; a segment cut from a real preview copy starts at the frame
the timeline names.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import assemble
import config
import render
import stable
import timeline
import tools
from convert import ConversionStore
from project import Project

PROJECT = os.environ.get("VET_REAL_PROJECT", "")
WORK = os.environ.get("VET_WORK_DIR", "")
CLIPS = 3


def _need():
    if not PROJECT or not Path(PROJECT).is_file():
        return "VET_REAL_PROJECT is not set to a project.json"
    if not WORK or not (Path(WORK) / config.CONVERSION_FILE).is_file():
        return "VET_WORK_DIR is not a work folder with a conversion.json"
    project = Project.load(PROJECT)
    store = ConversionStore.load(str(Path(WORK) / config.CONVERSION_FILE))
    first = project.matches[:CLIPS]
    for m in first:
        entry = store.clips.get(Path(m.video_path).stem)
        if not entry or entry.get("status") != "done" or not Path(entry["preview_copy"]).is_file():
            return f"the first {CLIPS} clips do not all have a finished preview copy"
        if not (Path(m.video_path).is_file() and Path(m.audio_path).is_file()):
            return f"the first {CLIPS} clips of the project are not reachable"
    return ""


SKIP = _need()


@unittest.skipIf(SKIP, SKIP)
class RealChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = Project.load(PROJECT)
        cls.stems = [Path(m.video_path).stem for m in cls.project.matches[:CLIPS]]
        cls.tmp = tempfile.mkdtemp(prefix="vet-scenes-", dir=WORK)
        shutil.copy(Path(WORK) / config.CONVERSION_FILE, Path(cls.tmp) / config.CONVERSION_FILE)
        for stem in cls.stems:       # the clap results the real run already made
            src = Path(WORK) / "analysis" / stem / "clap.json"
            if src.is_file():
                dst = Path(cls.tmp) / "analysis" / stem
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, dst / "clap.json")
        cls.rows = stable.analyse_project(cls.project, cls.tmp, limit=CLIPS, log=lambda s: None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_clip_gets_a_result_and_the_scenes_are_sound(self):
        self.assertEqual([r["clip"] for r in self.rows], self.stems)
        for stem in self.stems:
            with self.subTest(clip=stem):
                d = stable.load_scenes(self.tmp, stem)
                self.assertIsNotNone(d)
                min_frames = config.STABLE_MIN_RUN_SEC * d["fps"]["num"] / d["fps"]["den"]
                last_out = 0
                for run in d["runs"]:
                    self.assertGreaterEqual(run["in"], last_out)
                    self.assertGreaterEqual(run["out"] - run["in"], min_frames - 1)
                    self.assertLessEqual(run["out"], d["frames"])
                    last_out = run["out"]
                self.assertLessEqual(d["kept_seconds"], d["seconds"] + 0.01)

    def test_the_assembled_timeline_passes_the_validator_with_the_file_checks(self):
        out = Path(self.tmp) / "timeline.json"
        result = assemble.assemble_project(self.project, self.tmp, str(out), limit=CLIPS,
                                           log=lambda s: None)
        tl = timeline.Timeline.load(str(out))
        findings = tl.validate()
        self.assertFalse(timeline.has_errors(findings), [f.as_dict() for f in findings])
        for seg in tl.data["segments"]:
            src = tl.data["sources"][seg["source"]]
            self.assertTrue(0 <= seg["in"] < seg["out"] <= src["frames"])
        self.assertEqual(result.report["film"]["segments"], len(tl.data["segments"]))
        # a piece of the film preview: as many frames as the plan says
        plan_frames = sum(s["out"] - s["in"] for s in tl.data["segments"][:1])
        preview = render.render_preview(
            tl, self.tmp, name="real", settings=render.PreviewSettings(width=480, encoder="libx264"),
            film_range=(0.0, 0.5), log=lambda s: None)
        self.assertEqual(preview.frames, plan_frames)
        self.assertEqual(preview.problems, [])

    def test_a_segment_cut_from_a_real_preview_copy_starts_at_the_frame_asked_for(self):
        store = ConversionStore.load(str(Path(self.tmp) / config.CONVERSION_FILE))
        stem = self.stems[0]
        entry = store.clips[stem]
        copy = entry["preview_copy"]
        w, h = 240, 128
        n_scan = min(entry["frames"], 300)
        truth = self._decode(copy, w, h, n_scan)
        motion = np.abs(np.diff(truth.astype(np.float32), axis=0)).mean(axis=(1, 2))
        length = 24
        best = None
        for start in range(31, n_scan - length - 2):
            if start % 30 == 0:
                continue
            m = float(motion[start:start + length].mean())
            if best is None or m > best[0]:
                best = (m, start)
        if best is None or best[0] < 0.5:
            self.skipTest("the first frames of the first clip hold too little movement to tell neighbouring frames apart")
        a, b = best[1], best[1] + length
        tl = timeline.Timeline({
            "version": 1, "kind": "film",
            "film": {"fps": entry["fps"], "frame": dict(entry["size"]), "fit": "fit",
                     "fade_in_s": 0, "fade_out_s": 0,
                     "audio_join": {"duration_s": 0, "curve": "equal_power"}},
            "sources": {"S": {"original_video": "/o/x.braw", "preview_copy": copy,
                              "audio": "/a/x.wav", "audio_offset_s": 0.0, "frames": entry["frames"],
                              "fps": entry["fps"],
                              "size": dict(entry["size"]), "copy_lut_sha256": "0" * 64}},
            "segments": [{"id": "s001", "source": "S", "in": a, "out": b}],
            "look": {"base": {"mode": "none"}},
            "audio": {"sample_rate": 48000, "loudness": {"mode": "off"}}})
        plan = render.make_plan(tl, preview_width=w)
        out = Path(self.tmp) / "real-seg.mp4"
        render.render_segment(tl, plan, plan.segments[0], str(out),
                              render.PreviewSettings(width=w, encoder="libx264"))
        ph = plan.frame[1]
        got = self._decode(str(out), plan.frame[0], ph, length)
        self.assertEqual(len(got), length)
        # compare with the copy's own frames at the same size: scale the truth to the plan's frame
        truth_full = self._decode(copy, plan.frame[0], ph, b + 2)
        right = 0
        for j in range(length):
            scores = {d: self._psnr(got[j], truth_full[a + j + d]) for d in (-1, 0, 1)}
            if scores[0] >= max(scores.values()):
                right += 1
            self.assertGreater(scores[0], 25.0, f"frame {j} is not like frame {a + j} of the copy")
        self.assertGreaterEqual(right, length - 1)

    @staticmethod
    def _decode(path, w, h, frames):
        r = subprocess.run([tools.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path),
                            "-frames:v", str(frames), "-vf", f"scale={w}:{h}:flags=bicubic",
                            "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                           capture_output=True, check=True)
        return np.frombuffer(r.stdout, np.uint8).reshape(-1, h, w)

    @staticmethod
    def _psnr(a, b):
        mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
        return 99.0 if mse == 0 else 10 * np.log10(255.0 ** 2 / mse)


if __name__ == "__main__":
    unittest.main()
