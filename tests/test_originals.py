"""Check 1.4: the tool never writes into a folder that holds originals (M9)."""

import os
import tempfile
import unittest
from pathlib import Path

import safety
from project import Project, SceneMatch


class AssertNotInsideTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.originals = self.root / "Recordings"
        self.originals.mkdir()

    def test_path_inside_is_refused(self):
        with self.assertRaises(safety.OriginalsProtectedError):
            safety.assert_not_inside(self.originals / "copies" / "a.mp4",
                                     [self.originals])

    def test_the_folder_itself_is_refused(self):
        with self.assertRaises(safety.OriginalsProtectedError):
            safety.assert_not_inside(self.originals, [self.originals])

    def test_sibling_with_the_same_prefix_is_allowed(self):
        sibling = self.root / "Recordings-work"
        safety.assert_not_inside(sibling / "a.mp4", [self.originals])

    def test_path_outside_is_allowed(self):
        safety.assert_not_inside(self.root / "work" / "a.mp4", [self.originals])

    def test_dotdot_cannot_escape_the_check(self):
        sneaky = self.root / "work" / ".." / "Recordings" / "a.mp4"
        with self.assertRaises(safety.OriginalsProtectedError):
            safety.assert_not_inside(sneaky, [self.originals])

    def test_symlink_into_the_originals_is_refused(self):
        link = self.root / "link"
        os.symlink(self.originals, link)
        with self.assertRaises(safety.OriginalsProtectedError):
            safety.assert_not_inside(link / "a.mp4", [self.originals])

    def test_path_below_a_missing_folder_is_still_checked(self):
        with self.assertRaises(safety.OriginalsProtectedError):
            safety.assert_not_inside(self.originals / "new" / "deep" / "a.mp4",
                                     [self.originals])

    def test_error_names_the_path_and_the_folder(self):
        with self.assertRaises(safety.OriginalsProtectedError) as ctx:
            safety.assert_not_inside(self.originals / "a.mp4", [self.originals])
        self.assertIn("a.mp4", str(ctx.exception))
        self.assertIn("Recordings", str(ctx.exception))


class OriginalsFoldersTests(unittest.TestCase):
    def test_collects_the_folders_of_all_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "v").mkdir()
            (root / "a").mkdir()
            (root / "v2").mkdir()
            matches = [
                SceneMatch(index=0, video_path=str(root / "v" / "x.braw"),
                           audio_path=str(root / "a" / "z.WAV"),
                           video_duration=1, audio_duration=1, offset=0,
                           confidence=1),
                SceneMatch(index=1, video_path=str(root / "v2" / "y.braw"),
                           audio_path=str(root / "a" / "z2.WAV"),
                           video_duration=1, audio_duration=1, offset=0,
                           confidence=1),
            ]
            project = Project(video_dir=str(root / "v"), audio_dir=str(root / "a"),
                              matches=matches)
            folders = safety.originals_folders(project)
            self.assertEqual(set(folders),
                             {str(root / "v"), str(root / "a"), str(root / "v2")})


class ConvertLeavesOriginalsAloneTests(unittest.TestCase):
    """Check 1.4: a convert run on fixture originals changes nothing in their folder."""

    def setUp(self):
        from tests.fakes import Workspace
        self.ws = Workspace(self)

    def converter(self, work_dir=None, **kw):
        import convert
        return convert.Converter(
            work_dir=str(work_dir or self.ws.work), decoder=self.ws.decoder,
            settings=convert.PreviewSettings(encoder="libx264"),
            lut_path=str(self.ws.lut), originals_folders=[str(self.ws.src)],
            log=lambda line: None, free_bytes=lambda p: 10 ** 12, **kw)

    def refs(self):
        import convert
        return [convert.ClipRef(stem=n, path=p) for n, p in self.ws.refs]

    def test_originals_are_unchanged_after_a_run_and_a_second_run(self):
        before = self.ws.listing(self.ws.src)
        self.assertEqual(len(before), 2)
        self.converter().run(self.refs())
        self.converter().run(self.refs())
        self.assertEqual(self.ws.listing(self.ws.src), before)

    def test_everything_new_lies_inside_the_work_directory(self):
        before = {p for p in self.ws.root.rglob("*")}
        self.converter().run(self.refs())
        new = {p for p in self.ws.root.rglob("*")} - before
        self.assertTrue(new)
        for p in new:
            self.assertTrue(self.ws.work in p.parents or p == self.ws.work, p)

    def test_a_work_directory_inside_the_originals_is_refused_before_anything_is_made(self):
        before = self.ws.listing(self.ws.src)
        for bad in (self.ws.src, self.ws.src / "work"):
            with self.assertRaises(safety.OriginalsProtectedError):
                self.converter(work_dir=bad).run(self.refs())
        self.assertEqual(self.ws.listing(self.ws.src), before)
        self.assertEqual(self.ws.decoder.calls, [])


if __name__ == "__main__":
    unittest.main()
