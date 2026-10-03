import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project import Project, SceneMatch
from syncer import MANIFEST_NAME, remove_copied_audio, sync_all_scenes


class SyncerTests(unittest.TestCase):
    def setUp(self):
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.video_dir = root / "video"
        self.audio_dir = root / "audio"
        self.video_dir.mkdir()
        self.audio_dir.mkdir()
        self.matches = []
        self.audio_bytes = {}
        for i in range(2):
            v = self.video_dir / f"IMG_{2231 + i}.MOV"
            a = self.audio_dir / f"ZOOM{i:04d}.WAV"
            v.write_bytes(b"video%d" % i)
            data = bytes(range(256)) * (10 + i)
            a.write_bytes(data)
            self.audio_bytes[a] = data
            self.matches.append(SceneMatch(
                index=i, video_path=str(v), audio_path=str(a),
                video_duration=10.0, audio_duration=10.0,
                offset=0.5, confidence=1.0))
        self.project = Project(video_dir=str(self.video_dir),
                               audio_dir=str(self.audio_dir),
                               matches=self.matches, selection=[1, 0])

    def test_copies_are_byte_identical_in_video_dir(self):
        results = sync_all_scenes(self.project)
        self.assertEqual([r["scene_index"] for r in results], [1, 0])
        for m in self.matches:
            copy = self.video_dir / (Path(m.video_path).stem + ".WAV")
            self.assertTrue(copy.exists())
            self.assertEqual(copy.read_bytes(),
                             self.audio_bytes[Path(m.audio_path)])

    def test_explicit_output_dir(self):
        out = Path(self._tmp.name) / "out"
        sync_all_scenes(self.project, str(out))
        self.assertTrue((out / "IMG_2231.WAV").exists())
        self.assertFalse((self.video_dir / "IMG_2231.WAV").exists())

    def test_manifest_written_and_merged(self):
        sync_all_scenes(self.project)
        manifest = self.video_dir / MANIFEST_NAME
        self.assertEqual(json.loads(manifest.read_text())["copies"],
                         ["IMG_2231.WAV", "IMG_2232.WAV"])
        self.project.selection = [0]
        sync_all_scenes(self.project)
        self.assertEqual(json.loads(manifest.read_text())["copies"],
                         ["IMG_2231.WAV", "IMG_2232.WAV"])

    def test_remove_copied_audio_only_removes_copies(self):
        unrelated = self.video_dir / "notes.txt"
        unrelated.write_text("keep me")
        sync_all_scenes(self.project)
        removed = remove_copied_audio(str(self.video_dir))
        self.assertEqual(sorted(Path(p).name for p in removed),
                         ["IMG_2231.WAV", "IMG_2232.WAV"])
        self.assertFalse((self.video_dir / MANIFEST_NAME).exists())
        self.assertFalse((self.video_dir / "IMG_2231.WAV").exists())
        self.assertTrue(unrelated.exists())
        for m in self.matches:
            self.assertTrue(Path(m.video_path).exists())
            self.assertEqual(Path(m.audio_path).read_bytes(),
                             self.audio_bytes[Path(m.audio_path)])

    def test_remove_without_manifest_is_noop(self):
        self.assertEqual(remove_copied_audio(str(self.video_dir)), [])

    def test_overwrite_warns_on_size_mismatch(self):
        (self.video_dir / "IMG_2231.WAV").write_bytes(b"short")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            sync_all_scenes(self.project)
        self.assertIn("overwriting", buf.getvalue())
        self.assertEqual((self.video_dir / "IMG_2231.WAV").read_bytes(),
                         self.audio_bytes[self.audio_dir / "ZOOM0000.WAV"])

    def test_no_selection_raises(self):
        self.project.selection = []
        with self.assertRaises(ValueError):
            sync_all_scenes(self.project)


if __name__ == "__main__":
    unittest.main()
