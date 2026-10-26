"""Check 3.1 and 3.2: the LUT in the real filter chain of the final film (SPECS 8.3).
The chain is the one export.segment_filter builds; the tests feed it 16-bit RGB frames through
ffmpeg and read the luma and chroma that come out, then compare them with what the cube file
says they should be (computed here in numpy with tetrahedral interpolation)."""

import itertools
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import export
import look
import render
import timeline
import tools

RATE = 48000


def cube_text(table, size):
    """A .cube file from a table[b][g][r] -> (r, g, b) array of shape (size, size, size, 3)."""
    lines = [f"LUT_3D_SIZE {size}"]
    for b in range(size):
        for g in range(size):
            for r in range(size):
                lines.append("{:.6f} {:.6f} {:.6f}".format(*table[b, g, r]))
    return "\n".join(lines) + "\n"


def grid(size, f):
    ax = np.linspace(0, 1, size)
    b, g, r = np.meshgrid(ax, ax, ax, indexing="ij")
    return f(np.stack([r, g, b], -1))


def tetrahedral(table, rgb):
    """Tetrahedral interpolation of table[b][g][r] at rgb (n, 3) in 0..1: the reference."""
    n = table.shape[0] - 1
    p = np.clip(rgb, 0, 1) * n
    i0 = np.minimum(np.floor(p).astype(int), n - 1)
    f = p - i0
    out = np.zeros((len(rgb), 3))
    for k in range(len(rgb)):
        r0, g0, b0 = i0[k]
        fr, fg, fb = f[k]
        c = lambda dr, dg, db: table[b0 + db, g0 + dg, r0 + dr]
        c000, c111 = c(0, 0, 0), c(1, 1, 1)
        if fr >= fg >= fb:
            v = c000 + fr * (c(1, 0, 0) - c000) + fg * (c(1, 1, 0) - c(1, 0, 0)) + fb * (c111 - c(1, 1, 0))
        elif fr >= fb >= fg:
            v = c000 + fr * (c(1, 0, 0) - c000) + fb * (c(1, 0, 1) - c(1, 0, 0)) + fg * (c111 - c(1, 0, 1))
        elif fb >= fr >= fg:
            v = c000 + fb * (c(0, 0, 1) - c000) + fr * (c(1, 0, 1) - c(0, 0, 1)) + fg * (c111 - c(1, 0, 1))
        elif fg >= fr >= fb:
            v = c000 + fg * (c(0, 1, 0) - c000) + fr * (c(1, 1, 0) - c(0, 1, 0)) + fb * (c111 - c(1, 1, 0))
        elif fg >= fb >= fr:
            v = c000 + fg * (c(0, 1, 0) - c000) + fb * (c(0, 1, 1) - c(0, 1, 0)) + fr * (c111 - c(0, 1, 1))
        else:
            v = c000 + fb * (c(0, 0, 1) - c000) + fg * (c(0, 1, 1) - c(0, 0, 1)) + fr * (c111 - c(0, 1, 1))
        out[k] = v
    return out


def bt709_limited(rgb):
    """Y, U, V code values (8 bit, video range) for full-range RGB in 0..1, BT.709."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    u = (b - y) / 1.8556
    v = (r - y) / 1.5748
    return 16 + 219 * y, 128 + 224 * u, 128 + 224 * v


class Chain:
    """Runs a frame of 16-bit RGB through the segment filter of the final film."""

    def __init__(self, testcase, base=None, lut=None, size=(64, 32)):
        tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.size = size
        look_ = {"base": {"mode": "none"}}
        for key, text in (("base", base), ("lut", lut)):
            if text is None:
                continue
            p = self.dir / f"{key}.cube"
            p.write_text(text)
            entry = {"path": str(p), "sha256": look.sha256_file(p)}
            if key == "base":
                look_["base"] = dict(entry, mode="lut")
            else:
                look_["lut"] = dict(entry, interp="tetrahedral")
        w, h = size
        self.tl = timeline.Timeline({
            "version": 1, "kind": "film",
            "film": {"fps": {"num": 30000, "den": 1001}, "frame": {"width": w, "height": h}, "fit": "fit"},
            "sources": {"A": {"original_video": "/o/A.braw", "preview_copy": "/p/A.mp4", "audio": "/a/A.wav",
                              "audio_offset_s": 0, "frames": 10, "fps": {"num": 30000, "den": 1001},
                              "size": {"width": w, "height": h}, "copy_lut_sha256": "0" * 64}},
            "segments": [{"id": "s001", "source": "A", "in": 0, "out": 1}],
            "look": look_, "audio": {"sample_rate": RATE, "loudness": {"mode": "off"}}})
        self.plan = render.make_plan(self.tl, preview_width=w)
        self.entry = {"width": w, "height": h, "video": {"codec": "libx264"}, "audio": {"codec": "aac"}}

    def run(self, rgb16):
        """rgb16: (h, w, 3) uint16. Returns the Y, U, V planes (uint8) of the result."""
        w, h = self.size
        seg = self.plan.segments[0]
        cmd = export.segment_command(tools.ffmpeg_path(), self.tl, seg, {"width": w, "height": h},
                                     self.entry, (w, h))
        raw = (rgb16[..., 1].astype("<u2").tobytes() + rgb16[..., 2].astype("<u2").tobytes()
               + rgb16[..., 0].astype("<u2").tobytes())
        r = subprocess.run(cmd, input=raw, capture_output=True, check=True)
        data = np.frombuffer(r.stdout, np.uint8)
        y = data[:w * h].reshape(h, w)
        u = data[w * h:w * h + (w // 2) * (h // 2)].reshape(h // 2, w // 2)
        v = data[w * h + (w // 2) * (h // 2):].reshape(h // 2, w // 2)
        return y, u, v

    def filter(self):
        seg = self.plan.segments[0]
        w, h = self.size
        return export.segment_filter(self.tl, seg, {"width": w, "height": h}, "yuv420p", (w, h))


ID17 = cube_text(grid(17, lambda c: c), 17)
INVERT17 = cube_text(grid(17, lambda c: 1 - c), 17)
CURVE17_TABLE = grid(17, lambda c: np.stack([c[..., 0] ** 2.2, c[..., 1] ** 0.6 * 0.9 + 0.05,
                                             0.5 * c[..., 2] + 0.5 * c[..., 0] * c[..., 1]], -1))
CURVE17 = cube_text(CURVE17_TABLE, 17)


def gradient_frame(w=1024, h=32):
    """A 10-bit grey ramp: column k is level k of 1023, in 16 bit."""
    k = np.arange(w)
    v16 = np.round(k / (w - 1) * 65535).astype(np.uint16)
    return np.repeat(np.repeat(v16[None, :, None], h, axis=0), 3, axis=2)


def bars_frame(w=64, h=32):
    """Colour bars (white, yellow, cyan, green, magenta, red, blue, black) and a grey ramp."""
    colours = [(1, 1, 1), (1, 1, 0), (0, 1, 1), (0, 1, 0), (1, 0, 1), (1, 0, 0), (0, 0, 1), (0, 0, 0)]
    f = np.zeros((h, w, 3), np.uint16)
    bw = w // 8
    for i, c in enumerate(colours):
        f[:h // 2, i * bw:(i + 1) * bw] = np.array(c) * 65535
    for i in range(8):
        f[h // 2:, i * bw:(i + 1) * bw] = round(i / 7 * 65535)
    return f


class LutPrecisionTests(unittest.TestCase):
    def test_an_identity_cube_changes_no_8_bit_value_by_more_than_1(self):
        c = Chain(self, lut=ID17, size=(1024, 32))
        frame = gradient_frame()
        y, u, v = c.run(frame)
        # The reference is the same chain without the cube: the conversion to video levels has
        # its own rounding, the cube must add nothing to it.
        y0, u0, v0 = Chain(self, size=(1024, 32)).run(frame)
        for got, want in ((y, y0), (u, u0), (v, v0)):
            self.assertLessEqual(int(np.abs(got.astype(int) - want.astype(int)).max()), 1)
        # And the ramp itself is where the BT.709 conversion puts it, within 2 codes.
        expect = 16 + 219 * np.arange(1024) / 1023
        self.assertLessEqual(float(np.abs(y[10].astype(float) - expect).max()), 2.0)
        self.assertLessEqual(float(np.abs(u.astype(float) - 128).max()), 1.0)

    def test_an_inverting_cube_gives_255_minus_the_input_within_2(self):
        c = Chain(self, lut=INVERT17, size=(1024, 32))
        y, _, _ = c.run(gradient_frame())
        expect = 16 + 219 * (1 - np.arange(1024) / 1023)
        self.assertLessEqual(float(np.abs(y[10].astype(float) - expect).max()), 2.0)

    def test_a_cube_with_curves_matches_the_numpy_tetrahedral_reference_within_2(self):
        c = Chain(self, lut=CURVE17, size=(256, 64))
        w, h = c.size
        frame = bars_frame(w, h)
        y, u, v = c.run(frame)
        rgb_in = (frame.reshape(-1, 3).astype(float) / 65535)
        # Each bar has one colour; look at the middle of each bar so that chroma subsampling does not matter.
        bw = w // 8
        for row, part in (("colours", slice(2, h // 2 - 2)), ("greys", slice(h // 2 + 2, h - 2))):
            for i in range(8):
                x = i * bw + bw // 2
                yy = (part.start + part.stop) // 2
                src = frame[yy, x].astype(float)[None, :] / 65535
                want = bt709_limited(tetrahedral(CURVE17_TABLE, src))
                got_y = float(y[yy, x])
                got_u = float(u[yy // 2, x // 2])
                got_v = float(v[yy // 2, x // 2])
                self.assertLessEqual(abs(got_y - float(want[0][0])), 2.0, f"{row} bar {i}: Y")
                self.assertLessEqual(abs(got_u - float(want[1][0])), 2.0, f"{row} bar {i}: U")
                self.assertLessEqual(abs(got_v - float(want[2][0])), 2.0, f"{row} bar {i}: V")


class FilterStringTests(unittest.TestCase):
    def test_the_base_lut_comes_before_the_creative_lut_and_both_are_tetrahedral(self):
        c = Chain(self, base=ID17, lut=INVERT17)
        f = c.filter()
        self.assertEqual(f.count("interp=tetrahedral"), 2)
        self.assertLess(f.index("base.cube"), f.index("lut.cube"))
        self.assertLess(f.index("lut.cube"), f.index("out_color_matrix=bt709"))

    def test_no_creative_lut_means_one_lut_filter_and_no_base_lut_means_none(self):
        self.assertEqual(Chain(self, base=ID17).filter().count("lut3d"), 1)
        self.assertEqual(Chain(self).filter().count("lut3d"), 0)

    def test_the_conversion_to_video_levels_comes_last_and_names_the_matrix(self):
        f = Chain(self, lut=ID17).filter()
        self.assertIn("scale=out_color_matrix=bt709:out_range=tv", f)
        self.assertTrue(f.endswith("format=yuv420p"))

    def test_a_path_with_spaces_quotes_and_colons_works_in_the_real_filter(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        odd = Path(tmp.name) / "Application Support" / "it's: a \"LUT\", really"
        odd.mkdir(parents=True)
        cube = odd / "Film to Video.cube"
        cube.write_text(INVERT17)
        c = Chain(self, size=(64, 32))
        c.tl.data["look"]["lut"] = {"path": str(cube), "sha256": look.sha256_file(cube),
                                    "interp": "tetrahedral"}
        y, _, _ = c.run(bars_frame(64, 32))
        self.assertAlmostEqual(float(y[4, 4]), 16.0, delta=1.5)         # white inverted to black
        self.assertAlmostEqual(float(y[4, 60]), 235.0, delta=1.5)       # black inverted to white

    def test_the_hash_named_copy_is_a_path_without_special_characters(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src = Path(tmp.name) / "Film to Video.cube"
        src.write_text(ID17)
        entry = look.install(src, str(Path(tmp.name) / "work"))
        self.assertRegex(Path(entry["path"]).name, r"^[0-9a-f]{64}\.cube$")


class PlacementInTheChainTests(unittest.TestCase):
    def test_a_wide_picture_is_padded_in_rgb_before_the_conversion(self):
        c = Chain(self, size=(64, 32))
        c.tl.data["sources"]["A"]["size"] = {"width": 64, "height": 20}
        c.plan = render.make_plan(c.tl, preview_width=64)
        f = export.segment_filter(c.tl, c.plan.segments[0], {"width": 64, "height": 20}, "yuv420p", (64, 32))
        self.assertIn("pad=64:32:0:6:color=black", f)
        self.assertLess(f.index("pad="), f.index("out_color_matrix"))

    def test_a_fill_window_is_cropped_and_scaled(self):
        c = Chain(self, size=(32, 64))
        c.tl.data["film"]["fit"] = "fill"
        c.tl.data["sources"]["A"]["size"] = {"width": 128, "height": 64}
        c.plan = render.make_plan(c.tl, preview_width=32)
        f = export.segment_filter(c.tl, c.plan.segments[0], {"width": 128, "height": 64}, "yuv420p", (32, 64))
        self.assertIn("crop=32:64:'48':0", f)               # the middle of the width
        self.assertIn("scale=32:64:flags=bicubic", f)


if __name__ == "__main__":
    unittest.main()
