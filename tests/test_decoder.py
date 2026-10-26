"""The decoder interface (C7) and the checks of the real decoder that need no clip."""

import sys
import types
import unittest
from unittest import mock

import decoder


class InterfaceTests(unittest.TestCase):
    def test_the_interface_cannot_be_used_without_its_methods(self):
        with self.assertRaises(TypeError):
            decoder.Decoder()

    def test_a_second_decoder_only_has_to_implement_three_methods(self):
        class Second(decoder.Decoder):
            def probe(self, path):
                return decoder.ClipInfo(1, 1, 30, 1, 1)

            def write_frames(self, path, scale, sink, max_frames=None):
                return 0

            def write_audio(self, path, dest, max_frames=None):
                return None

        d = Second()
        d.check()       # the default check passes
        self.assertEqual(d.probe("x").frames, 1)


class BrawSourceCheckTests(unittest.TestCase):
    def test_missing_vapoursynth_gives_install_steps(self):
        with mock.patch.dict(sys.modules, {"vapoursynth": None}):
            with self.assertRaises(RuntimeError) as ctx:
                decoder.BrawSourceDecoder().check()
        self.assertIn("uv venv", str(ctx.exception))

    def test_missing_plugin_gives_install_steps(self):
        fake = types.ModuleType("vapoursynth")
        fake.core = types.SimpleNamespace()
        with mock.patch.dict(sys.modules, {"vapoursynth": fake}):
            with self.assertRaises(RuntimeError) as ctx:
                decoder.BrawSourceDecoder().check()
        self.assertIn("vapoursynth-brawsource", str(ctx.exception))

    def test_name_carries_the_plugin_version(self):
        self.assertTrue(decoder.BrawSourceDecoder().name.startswith("vapoursynth-brawsource"))


if __name__ == "__main__":
    unittest.main()


class FrameRangeTests(unittest.TestCase):
    """A decoder that can only decode from the start still gives an exact frame range."""

    def make(self):
        class Plain(decoder.Decoder):
            def probe(self, path):
                return decoder.ClipInfo(width=4, height=2, fps_num=30, fps_den=1, frames=10)

            def write_frames(self, path, scale, sink, max_frames=None):
                n = 10 if max_frames is None else max_frames
                for i in range(n):
                    sink.write(bytes([i]) * (4 // scale * (2 // scale) * 6))
                return n

            def write_audio(self, path, dest, max_frames=None):
                return None

        return Plain()

    def test_the_default_drops_the_frames_before_the_range(self):
        import io
        out = io.BytesIO()
        n = self.make().write_frame_range("x", 1, out, 3, 6)
        self.assertEqual(n, 3)
        frame = 4 * 2 * 6
        self.assertEqual(out.getvalue(), b"".join(bytes([i]) * frame for i in (3, 4, 5)))

    def test_a_range_outside_the_clip_is_refused(self):
        import io
        with self.assertRaises(ValueError):
            self.make().write_frame_range("x", 1, io.BytesIO(), 8, 12)
        with self.assertRaises(ValueError):
            self.make().write_frame_range("x", 1, io.BytesIO(), 5, 5)
