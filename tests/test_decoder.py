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
