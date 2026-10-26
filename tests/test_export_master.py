"""Check 3.10: the ProRes 422 HQ master (SPECS 5.6, 8.5; requirement E6) and the presets.
The master is off until the owner ticks it, the free space is checked first, and a render
gives a .mov that ffprobe reads as ProRes HQ, 10-bit 4:2:2, with 24-bit PCM sound."""

import subprocess
import unittest
from fractions import Fraction

import export
import timeline
import tools
from tests import media
from tests.test_export import ExportFixture, FlatDecoder, W, H


def prores_works() -> bool:
    r = subprocess.run([tools.ffmpeg_path(), "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=0.1",
                        "-c:v", "prores_videotoolbox", "-f", "null", "-"], capture_output=True)
    return r.returncode == 0


class PresetTests(unittest.TestCase):
    frame = {"width": 4096, "height": 2160}

    def test_the_default_film_is_mp4_h264_dci_4k_at_45_mbit_with_the_hardware_encoder(self):
        e = export.preset_entry("film-4k", "/Volumes/SSD/renders", self.frame)
        self.assertEqual(e["name"], "film-4k")
        self.assertEqual(e["output"], "/Volumes/SSD/renders/film-4k.mp4")
        self.assertEqual((e["width"], e["height"]), (4096, 2160))
        self.assertEqual(e["video"], {"codec": "h264_videotoolbox", "rate_control": {"mode": "bitrate", "mbps": 45}})
        self.assertEqual(e["audio"], {"codec": "aac", "bitrate_kbps": 384, "sample_rate": 48000})
        self.assertNotIn("enabled", e)

    def test_the_software_encoder_is_a_second_preset(self):
        e = export.preset_entry("film-4k-x264", "/r", self.frame)
        self.assertEqual(e["video"]["codec"], "libx264")
        self.assertEqual(e["video"]["preset"], "slow")
        self.assertEqual(e["video"]["rate_control"], {"mode": "bitrate", "mbps": 45})

    def test_the_master_is_a_mov_with_prores_hq_and_is_switched_off(self):
        e = export.preset_entry("film-master", "/r", self.frame)
        self.assertEqual(e["output"], "/r/film-master.mov")
        self.assertIs(e["enabled"], False)
        self.assertEqual(e["video"], {"codec": "prores_videotoolbox", "profile": "hq"})
        self.assertEqual(e["audio"]["codec"], "pcm_s24le")

    def test_a_custom_resolution_names_the_entry_after_its_height(self):
        e = export.preset_entry("film-4k", "/r", self.frame, width=2048, height=1080)
        self.assertEqual((e["width"], e["height"], e["name"]), (2048, 1080, "film-1080p"))
        self.assertEqual(e["output"], "/r/film-1080p.mp4")

    def test_width_without_height_is_refused(self):
        with self.assertRaises(export.ExportError):
            export.preset_entry("film-4k", "/r", self.frame, width=2048)

    def test_every_preset_passes_the_rules_of_the_timeline_file(self):
        from tests.timeline_fixture import example
        for preset in export.PRESETS:
            d = example()
            d["exports"] = [export.preset_entry(preset, "/Volumes/SSD/film/renders", d["film"]["frame"])]
            errors = [f for f in timeline.Timeline(d).validate(file_checks=False) if f.level == "error"]
            self.assertEqual(errors, [], preset)

    def test_the_estimate_of_the_master_is_about_the_figure_of_the_specification(self):
        e = export.preset_entry("film-master", "/r", self.frame)
        fps = Fraction(30000, 1001)
        frames = round(20 * 60 * fps)
        gb = export.estimate_bytes(e, frames, fps) / 1e9
        self.assertAlmostEqual(gb, 141.0, delta=3.0)       # SPECS 5.6: 141 GB for 20 minutes at 29.97

    def test_the_estimate_of_the_delivery_file_follows_the_data_rate(self):
        e = export.preset_entry("film-4k", "/r", self.frame)
        fps = Fraction(30000, 1001)
        gb = export.estimate_bytes(e, round(20 * 60 * fps), fps) / 1e9
        self.assertAlmostEqual(gb, 20 * 60 * (45e6 + 384e3) / 8 / 1e9, delta=0.2)

    def test_enabling_and_adding_work_on_copies(self):
        from tests.timeline_fixture import example
        d = example()
        n = len(d["exports"])
        e = export.preset_entry("film-4k", "/r", d["film"]["frame"], name="extra")
        new = export.with_export(d, e)
        self.assertEqual(len(d["exports"]), n)
        self.assertEqual(len(new["exports"]), n + 1)
        with self.assertRaises(export.ExportError):
            export.with_export(new, e)
        on = export.with_export_enabled(new, "film-master", True)
        self.assertTrue(export.find_export(on, "film-master")["enabled"])
        self.assertFalse(export.find_export(new, "film-master")["enabled"])


@unittest.skipUnless(prores_works(), "prores_videotoolbox is not available on this machine")
class MasterRenderTests(ExportFixture):
    def master(self):
        e = export.preset_entry("film-master", str(self.out), {"width": W, "height": H})
        return e

    def test_a_switched_off_master_makes_no_file(self):
        tl = self.make_tl(segments=[("A", 10, 40)], exports=[self.master()])
        with self.assertRaises(export.ExportDisabled):
            self.run_export(tl, "film-master")
        self.assertFalse((self.out / "film-master.mov").exists())

    def test_the_master_is_refused_when_the_space_is_below_1_1_times_the_estimate(self):
        e = self.master()
        e["enabled"] = True
        tl = self.make_tl(segments=[("A", 10, 40)], exports=[e])
        fps = Fraction(30000, 1001)
        need = export.estimate_bytes(e, 30, fps)
        with self.assertRaises(export.NotEnoughSpace):
            self.run_export(tl, "film-master", work=self.root / "w-master-1", free_bytes=lambda p: int(need * 1.05))

    def test_an_enabled_master_renders_prores_hq_422_10_bit_with_pcm_sound(self):
        e = self.master()
        e["enabled"] = True
        tl = self.make_tl(segments=[("A", 10, 70), ("B", 5, 65)], exports=[e], loud=False)
        r = self.run_export(tl, "film-master", work=self.root / "w-master-2")
        self.assertTrue(r.ok, r.problems)
        streams = media.probe(r.path)["streams"]
        v = [s for s in streams if s["codec_type"] == "video"][0]
        a = [s for s in streams if s["codec_type"] == "audio"][0]
        self.assertEqual((v["codec_name"], v["profile"], v["pix_fmt"]), ("prores", "HQ", "yuv422p10le"))
        self.assertEqual((a["codec_name"], int(a["sample_rate"])), ("pcm_s24le", 48000))
        self.assertEqual(len(media.read_yuv_frames(r.path, W, H)), 120)
        self.assertEqual((v["color_space"], v["color_transfer"], v["color_primaries"]), ("bt709", "bt709", "bt709"))

    def test_the_master_has_the_pictures_of_the_segments_in_ten_bit(self):
        e = self.master()
        e["enabled"] = True
        tl = self.make_tl(segments=[("A", 10, 40)], exports=[e], loud=False, fade_in=0, fade_out=0)
        r = self.run_export(tl, "film-master", work=self.root / "w-master-3")
        r2 = subprocess.run([tools.ffmpeg_path(), "-v", "error", "-i", r.path, "-frames:v", "1",
                             "-f", "rawvideo", "-pix_fmt", "yuv422p10le", "-"], capture_output=True, check=True)
        import numpy as np
        y = np.frombuffer(r2.stdout, "<u2")[:W * H].astype(float)
        want10 = 4 * media.limited_luma(media.frame_value(10))
        self.assertAlmostEqual(float(y.mean()), want10, delta=8)


if __name__ == "__main__":
    unittest.main()
