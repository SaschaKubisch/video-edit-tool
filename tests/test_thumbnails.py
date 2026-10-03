"""Tests for thumbnails.py: undecodable video (e.g. .braw) must not abort a run."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import thumbnails
from project import Project, SceneMatch


def _scene(i, name):
    return SceneMatch(index=i, video_path=f"/v/{name}", audio_path=f"/a/{i}.WAV",
                      video_duration=10.0, audio_duration=10.0, offset=0.0,
                      confidence=1.0)


class ThumbnailToleranceTests(unittest.TestCase):
    def test_extract_thumbnail_raises_runtime_error_on_ffmpeg_failure(self):
        fail = mock.Mock(returncode=1, stderr="no decoder found for 'none'")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(thumbnails.subprocess, "run", return_value=fail):
            with self.assertRaises(RuntimeError):
                thumbnails.extract_thumbnail("/v/C1.braw", str(Path(tmp) / "t.jpg"))

    def test_extract_all_continues_after_failing_file(self):
        project = Project(matches=[_scene(0, "A.braw"), _scene(1, "B.mov")])

        def fake(video, out, *a, **k):
            if video.endswith(".braw"):
                raise RuntimeError("no decoder found")
            return Path(out)

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(thumbnails, "extract_thumbnail", fake):
            result = thumbnails.extract_all_thumbnails(project, tmp)
        self.assertEqual(result.matches[0].thumbnail_path, "")
        self.assertTrue(result.matches[1].thumbnail_path.endswith("thumb_001.jpg"))


if __name__ == "__main__":
    unittest.main()
