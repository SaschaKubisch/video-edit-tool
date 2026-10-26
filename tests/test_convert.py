"""Check 1.1: the ffmpeg command line, the LUT handling and the conversion store
of the `convert` command. No media and no real decoder: a fake decoder."""

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import config
import convert
from decoder import AudioInfo, ClipInfo

C083 = ClipInfo(width=4096, height=2160, fps_num=30000, fps_den=1001, frames=805,
                timecode_start="06:15:34:16", colour_science_gen="5")
AUDIO = AudioInfo(sample_rate=48000, channels=2, samples=1289288)


def settings(**kw):
    return convert.PreviewSettings(**{**dict(
        scale=2, quality=73, gop=30, audio_kbps=192,
        encoder="h264_videotoolbox"), **kw})


class FilterPathTests(unittest.TestCase):
    def test_plain_path_is_quoted(self):
        self.assertEqual(convert.escape_filter_path("/a/b.cube"), "'/a/b.cube'")

    def test_spaces_stay_inside_the_quotes(self):
        p = "/Library/Application Support/Blackmagic Design/x.cube"
        self.assertEqual(convert.escape_filter_path(p), f"'{p}'")

    def test_quote_and_colon_are_escaped_for_both_levels(self):
        self.assertEqual(convert.escape_filter_path("/a/it's.cube"),
                         "'/a/it\\'\\''s.cube'")
        self.assertEqual(convert.escape_filter_path("/a/b:c.cube"),
                         "'/a/b\\:c.cube'")

    def test_real_ffmpeg_accepts_awkward_names(self):
        # Identity LUT in a folder whose name has a space, a colon, a comma and a quote.
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "Application Support: it's, odd"
            d.mkdir()
            lut = d / "id.cube"
            lut.write_text(IDENTITY_CUBE)
            vf = "lut3d=file=" + convert.escape_filter_path(str(lut)) + ":interp=tetrahedral"
            r = subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                 "color=c=red:s=64x64:d=0.2:r=10", "-vf", vf, "-f", "null", "-"],
                capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


IDENTITY_CUBE = "LUT_3D_SIZE 2\n" + "\n".join(
    f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)) + "\n"


class FindLutTests(unittest.TestCase):
    def test_explicit_path_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            lut = Path(tmp) / "mine.cube"
            lut.write_text(IDENTITY_CUBE)
            self.assertEqual(convert.find_base_lut(str(lut), environ={}, lut_dir=tmp),
                             str(lut))

    def test_explicit_path_that_is_missing_raises(self):
        with self.assertRaises(FileNotFoundError):
            convert.find_base_lut("/no/such.cube", environ={}, lut_dir="/no")

    def test_environment_variable_is_used_next(self):
        with tempfile.TemporaryDirectory() as tmp:
            lut = Path(tmp) / "env.cube"
            lut.write_text(IDENTITY_CUBE)
            self.assertEqual(
                convert.find_base_lut(None, environ={"VET_BASE_LUT": str(lut)},
                                      lut_dir="/no"), str(lut))

    def test_default_name_is_found_in_a_subfolder_of_the_lut_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "Blackmagic Design"
            sub.mkdir()
            lut = sub / config.BASE_LUT_FILE
            lut.write_text(IDENTITY_CUBE)
            self.assertEqual(convert.find_base_lut(None, environ={}, lut_dir=tmp),
                             str(lut))

    def test_nothing_found_raises_with_a_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError) as ctx:
                convert.find_base_lut(None, environ={}, lut_dir=tmp)
            self.assertIn("--lut", str(ctx.exception))

    def test_sha256_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "a.cube"
            f.write_bytes(b"abc")
            self.assertEqual(convert.sha256_file(str(f)),
                             hashlib.sha256(b"abc").hexdigest())


class CommandTests(unittest.TestCase):
    def assertRun(self, cmd, run):
        """`run` appears in `cmd` as consecutive items."""
        n = len(run)
        if not any(cmd[i:i + n] == run for i in range(len(cmd) - n + 1)):
            self.fail(f"{run} not found in the command")

    def build(self, **kw):
        args = dict(ffmpeg="ffmpeg", info=C083, audio=AUDIO,
                    audio_raw="/w/tmp/C083.s32le",
                    out_path="/w/copies/C083.mp4.partial",
                    lut_path="/lut dir/Gen 5.cube", settings=settings())
        args.update(kw)
        return convert.build_ffmpeg_command(**args)

    def test_preview_size_is_half_of_the_decoded_size(self):
        self.assertEqual(convert.preview_size(C083, 2), (2048, 1080))
        wide = ClipInfo(width=4096, height=1720, fps_num=30000, fps_den=1001, frames=1)
        self.assertEqual(convert.preview_size(wide, 2), (2048, 860))

    def test_picture_input_is_raw_16_bit_rgb_at_the_preview_size_and_clip_rate(self):
        cmd = self.build()
        self.assertRun(cmd, ["-f", "rawvideo", "-pix_fmt", "gbrp16le", "-s", "2048x1080",
                       "-r", "30000/1001", "-i", "-"])

    def test_audio_input_is_the_raw_camera_audio(self):
        cmd = self.build()
        self.assertRun(cmd, ["-f", "s32le", "-ar", "48000", "-ac", "2", "-i",
                       "/w/tmp/C083.s32le"])
        self.assertRun(cmd, ["-map", "0:v:0", "-map", "1:a:0"])

    def test_filter_chain_is_lut_then_conversion_then_pixel_format(self):
        cmd = self.build()
        vf = cmd[cmd.index("-vf") + 1]
        self.assertEqual(
            vf,
            "lut3d=file='/lut dir/Gen 5.cube':interp=tetrahedral,"
            "scale=out_color_matrix=bt709:out_range=tv:"
            "flags=accurate_rnd+full_chroma_int+bicubic,format=yuv420p")

    def test_no_lut_leaves_out_the_lut_filter(self):
        cmd = self.build(lut_path=None)
        vf = cmd[cmd.index("-vf") + 1]
        self.assertTrue(vf.startswith("scale=out_color_matrix=bt709"))
        self.assertNotIn("lut3d", vf)

    def test_videotoolbox_encoder_arguments(self):
        cmd = self.build()
        self.assertRun(cmd, ["-c:v", "h264_videotoolbox", "-profile:v", "high",
                       "-allow_sw", "0", "-g", "30", "-tag:v", "avc1",
                       "-q:v", "73"])

    def test_libx264_encoder_arguments(self):
        cmd = self.build(settings=settings(encoder="libx264"))
        c = cmd[cmd.index("-c:v") + 1]
        self.assertEqual(c, "libx264")
        self.assertIn("-crf", cmd)
        self.assertNotIn("-allow_sw", cmd)

    def test_colour_tags_audio_timecode_and_bitstream_filter(self):
        cmd = self.build()
        self.assertRun(cmd, ["-color_primaries", "bt709", "-color_trc", "bt709",
                       "-colorspace", "bt709", "-color_range", "tv"])
        self.assertRun(cmd, ["-c:a", "aac", "-b:a", "192k"])
        self.assertEqual(cmd[cmd.index("-timecode") + 1], "06:15:34:16")
        self.assertEqual(
            cmd[cmd.index("-bsf:v") + 1],
            "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
            "matrix_coefficients=1:video_full_range_flag=0")

    def test_output_is_mp4_with_moov_first_and_no_inherited_metadata(self):
        cmd = self.build()
        self.assertRun(cmd, ["-map_metadata", "-1"])
        self.assertRun(cmd, ["-movflags", "+faststart"])
        self.assertEqual(cmd[-3:], ["-f", "mp4", "/w/copies/C083.mp4.partial"])

    def test_clip_without_audio_has_no_audio_input_or_audio_codec(self):
        cmd = self.build(audio=None, audio_raw=None)
        self.assertNotIn("s32le", cmd)
        self.assertNotIn("-c:a", cmd)
        self.assertNotIn("1:a:0", cmd)

    def test_clip_without_timecode_has_no_timecode_option(self):
        info = ClipInfo(width=4096, height=2160, fps_num=30000, fps_den=1001, frames=5)
        self.assertNotIn("-timecode", self.build(info=info))

    def test_scale_one_keeps_the_full_size(self):
        cmd = self.build(settings=settings(scale=1))
        self.assertIn("4096x2160", cmd)


if __name__ == "__main__":
    unittest.main()
