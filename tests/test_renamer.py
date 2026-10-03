import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project import Project, SceneMatch
from renamer import rename_matches, undo_renames


class RenamerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.video_dir = root / "video"
        self.audio_dir = root / "audio"
        self.video_dir.mkdir()
        self.audio_dir.mkdir()
        matches = []
        for i in range(2):
            v = self.video_dir / f"IMG_{i}.MOV"
            a = self.audio_dir / f"ZOOM{i}.WAV"
            v.write_bytes(b"v%d" % i)
            a.write_bytes(b"a%d" % i)
            matches.append(SceneMatch(
                index=i, video_path=str(v), audio_path=str(a),
                video_duration=1.0, audio_duration=1.0,
                offset=0.0, confidence=1.0))
        self.project = Project(video_dir=str(self.video_dir),
                               audio_dir=str(self.audio_dir), matches=matches)

    def _names(self):
        return (sorted(p.name for p in self.video_dir.iterdir()),
                sorted(p.name for p in self.audio_dir.iterdir()))

    def _quiet(self, fn, *a, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*a, **kw)

    def test_collision_aborts_without_renaming(self):
        (self.audio_dir / "scene_01.WAV").write_bytes(b"existing")
        before = self._names()
        with self.assertRaises(FileExistsError):
            self._quiet(rename_matches, self.project)
        self.assertEqual(self._names(), before)
        self.assertFalse((self.video_dir / "rename_log.json").exists())
        self.assertEqual((self.audio_dir / "scene_01.WAV").read_bytes(), b"existing")

    def test_duplicate_targets_abort(self):
        before = self._names()
        with self.assertRaises(FileExistsError):
            self._quiet(rename_matches, self.project, pattern="same")
        self.assertEqual(self._names(), before)

    def test_dry_run_changes_nothing(self):
        before = self._names()
        self._quiet(rename_matches, self.project, dry_run=True)
        self.assertEqual(self._names(), before)

    def test_rename_and_undo_roundtrip(self):
        before = self._names()
        ops = self._quiet(rename_matches, self.project)
        self.assertEqual(len(ops), 2)
        self.assertEqual(self._names(),
                         (["rename_log.json", "scene_00.MOV", "scene_01.MOV"],
                          ["scene_00.WAV", "scene_01.WAV"]))
        self.assertTrue(self.project.matches[0].video_path.endswith("scene_00.MOV"))
        log = self.video_dir / "rename_log.json"
        self.assertEqual(len(json.loads(log.read_text())), 2)
        self._quiet(undo_renames, str(log))
        names = self._names()
        self.assertEqual(names[1], before[1])
        self.assertEqual([n for n in names[0] if n != "rename_log.json"], before[0])
        self.assertEqual((self.video_dir / "IMG_0.MOV").read_bytes(), b"v0")


if __name__ == "__main__":
    unittest.main()
