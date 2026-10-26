"""The page's part of step 3 (SPECS 8.7, 12): the creative LUT, the loudness, the exports and the
render job. A server on an ephemeral port with a timeline of flat synthetic clips and a decoder that
makes flat frames; the page's script is run in Node against it. Nothing here opens a browser."""

import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

import export
import look
import server
import store
import timeline
from tests.test_export import ExportFixture, FlatDecoder, W, H
from tests.test_look import CREDIT, cube
from tests.test_server_timeline import ServerCase


class Step3Case(ExportFixture, ServerCase):
    decoder_class = FlatDecoder

    def setUp(self):
        name = self.id().split(".")[-1]
        self.dir = self.root / f"s3-{name}"
        self.dir.mkdir()
        self.path = self.dir / "timeline.json"
        self.work = self.dir / "work"
        self.work.mkdir()
        self.lutdir = self.dir / "luts"
        (self.lutdir / "slay").mkdir(parents=True)
        (self.lutdir / "slay" / "slay-04-film.cube").write_text(cube(3, "slay Film"))
        (self.lutdir / "slay" / "slay-05-portrait.cube").write_text(cube(3, "slay Portrait"))
        (self.lutdir / "slay" / "CREDIT.txt").write_text(CREDIT + "\n")
        (self.lutdir / "plain.cube").write_text(cube(2))
        self.decoder = FlatDecoder(self.clips)
        self.make_tl(segments=[("A", 10, 70), ("B", 5, 65)], exports=[]).save(str(self.path))
        server.configure(decoder=self.decoder, lut_dir=str(self.lutdir))
        self.addCleanup(server.configure, decoder=None, lut_dir=None)
        self.start(self.path, self.work)

    def hash(self):
        return timeline.file_hash(str(self.path))

    def data(self):
        return json.loads(self.path.read_text())


class AnswerTests(Step3Case):
    def test_the_answer_carries_the_look_the_loudness_the_exports_and_the_free_space(self):
        status, body = self.get_json("/api/timeline")
        self.assertEqual(status, 200)
        self.assertIsNone(body["look"]["lut"])
        self.assertEqual([l["id"] for l in body["look"]["luts"]],
                         ["plain.cube", "slay/slay-04-film.cube", "slay/slay-05-portrait.cube"])
        self.assertEqual(body["look"]["luts"][1]["credit"], CREDIT)
        self.assertEqual(body["loudness"]["settings"]["mode"], "segment_then_film")
        self.assertEqual(body["exports"], [])
        self.assertGreater(body["free_bytes"], 0)

    def test_the_lut_folder_is_not_a_path_the_page_can_change(self):
        _, body = self.get_json("/api/timeline")
        self.assertEqual(body["look"]["folder"], str(self.lutdir))


class LookEndpointTests(Step3Case):
    def test_choosing_a_lut_writes_it_with_its_credit_and_keeps_the_old_file(self):
        h = self.hash()
        status, body = self.post_json("/api/look", {"hash": h, "lut": "slay/slay-04-film.cube"})
        self.assertEqual(status, 200)
        lut = self.data()["look"]["lut"]
        self.assertEqual((lut["name"], lut["credit"], lut["interp"]),
                         ("slay-04-film.cube", CREDIT, "tetrahedral"))
        self.assertTrue(lut["path"].startswith(str(self.work / "luts")))
        self.assertEqual(body["look"]["lut"]["name"], "slay-04-film.cube")
        self.assertEqual(body["look"]["lut"]["id"], "slay/slay-04-film.cube")
        self.assertIn("slay", body["look"]["lut"]["credit"])
        self.assertEqual(body["hash"], self.hash())
        self.assertEqual(len(store.list_snapshots(str(self.path))), 1)

    def test_none_removes_the_lut(self):
        self.post_json("/api/look", {"hash": self.hash(), "lut": "plain.cube"})
        status, body = self.post_json("/api/look", {"hash": self.hash(), "lut": None})
        self.assertEqual(status, 200)
        self.assertNotIn("lut", self.data()["look"])
        self.assertIsNone(body["look"]["lut"])

    def test_the_page_cannot_name_a_path_outside_the_lut_folder(self):
        outside = self.dir / "outside.cube"
        outside.write_text(cube(2))
        before = self.path.read_bytes()
        for bad in (str(outside), "../outside.cube", "slay/../../outside.cube", "/etc/passwd", "nope.cube"):
            status, body = self.post_json("/api/look", {"hash": self.hash(), "lut": bad})
            self.assertEqual(status, 400, bad)
        self.assertEqual(self.path.read_bytes(), before)

    def test_a_broken_cube_file_is_400_with_the_reason(self):
        (self.lutdir / "broken.cube").write_text("nothing")
        status, body = self.post_json("/api/look", {"hash": self.hash(), "lut": "broken.cube"})
        self.assertEqual(status, 400)
        self.assertIn("LUT_3D_SIZE", body["error"])

    def test_a_stale_hash_is_409(self):
        status, body = self.post_json("/api/look", {"hash": "0" * 64, "lut": "plain.cube"})
        self.assertEqual(status, 409)
        self.assertEqual(body["hash"], self.hash())

    def test_a_body_without_a_hash_or_a_lut_is_400(self):
        self.assertEqual(self.post_json("/api/look", {"lut": "plain.cube"})[0], 400)
        self.assertEqual(self.post_json("/api/look", {"hash": self.hash()})[0], 400)
        self.assertEqual(self.post_json("/api/look", {"hash": self.hash(), "lut": 5})[0], 400)

    def test_a_foreign_origin_is_403(self):
        status, body = self.post_json("/api/look", {"hash": self.hash(), "lut": None},
                                      headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)


class LoudnessEndpointTests(Step3Case):
    def test_the_mode_is_saved_with_the_values_of_the_decision(self):
        status, body = self.post_json("/api/loudness", {"hash": self.hash(), "mode": "film_only"})
        self.assertEqual(status, 200)
        # The values of the decision fill what the file does not have; max_gain_db is the file's own.
        self.assertEqual(self.data()["audio"]["loudness"],
                         {"mode": "film_only", "target_lufs": -14.0, "true_peak_dbtp": -1.0, "max_gain_db": 20.0})
        self.assertEqual(body["loudness"]["settings"]["mode"], "film_only")

    def test_off_and_back(self):
        self.post_json("/api/loudness", {"hash": self.hash(), "mode": "off"})
        self.assertEqual(self.data()["audio"]["loudness"], {"mode": "off"})

    def test_an_unknown_mode_is_400(self):
        before = self.path.read_bytes()
        self.assertEqual(self.post_json("/api/loudness", {"hash": self.hash(), "mode": "loud"})[0], 400)
        self.assertEqual(self.path.read_bytes(), before)

    def test_a_target_the_rules_refuse_is_422_with_the_field(self):
        status, body = self.post_json("/api/loudness", {"hash": self.hash(), "mode": "film_only",
                                                        "target_lufs": -3})
        self.assertEqual(status, 422)
        self.assertEqual(body["findings"][0]["rule"], "V17")

    def test_a_target_that_is_not_a_number_is_400(self):
        self.assertEqual(self.post_json("/api/loudness", {"hash": self.hash(), "mode": "film_only",
                                                          "target_lufs": "loud"})[0], 400)


class RenderJobTests(Step3Case):
    def start_render(self, **body):
        return self.post_json("/api/render", body)

    def test_a_preset_adds_the_export_and_renders_it_and_the_status_tells(self):
        status, body = self.start_render(preset="film-4k", name="film-page")
        self.assertEqual(status, 200)
        s = self.wait_for(("rendered",))
        self.assertEqual(s["step"], "rendered", s)
        self.assertIn("film-page.mp4", s["message"])
        self.assertIn("LUFS", s["message"])
        out = Path(self.data()["exports"][0]["output"])
        self.assertTrue(out.is_file())
        self.assertEqual(self.data()["exports"][0]["video"]["codec"], "h264_videotoolbox")
        _, answer = self.get_json("/api/timeline")
        last = answer["exports"][0]["last"]
        self.assertTrue(last["ok"])
        self.assertAlmostEqual(last["output_loudness"]["lufs"], -14.0, delta=0.5)

    def test_an_export_of_the_file_is_rendered_by_its_name(self):
        e = self.entry("film-named")
        e["output"] = str(self.dir / "film-named.mp4")
        tl = timeline.Timeline.load(str(self.path))
        tl.data["exports"] = [e]
        tl.save(str(self.path))
        status, body = self.start_render(export="film-named")
        self.assertEqual(status, 200)
        self.assertEqual(self.wait_for(("rendered",))["step"], "rendered")
        self.assertTrue((self.dir / "film-named.mp4").is_file())

    def test_the_progress_is_shown_while_it_runs(self):
        class Slow(FlatDecoder):
            def write_frame_range(inner, path, scale, sink, first, last):
                time.sleep(0.6)
                return super().write_frame_range(path, scale, sink, first, last)

        server.configure(decoder=Slow(self.clips))
        self.start_render(preset="film-4k", name="film-slow")
        seen = set()
        end = time.time() + 60
        while time.time() < end:
            _, s = self.get_json("/api/status")
            seen.add(s["step"])
            if s["step"] in ("rendered", "error"):
                break
            time.sleep(0.1)
        self.assertIn("rendering", seen)
        self.assertEqual(s["step"], "rendered", s)

    def test_a_second_request_while_a_job_runs_is_409(self):
        server._try_start_job()
        try:
            status, body = self.start_render(preset="film-4k")
            self.assertEqual(status, 409)
        finally:
            server.reset_state()

    def test_a_switched_off_export_needs_the_tick(self):
        e = self.entry("film-off")
        e["enabled"] = False
        tl = timeline.Timeline.load(str(self.path))
        tl.data["exports"] = [e]
        tl.save(str(self.path))
        status, body = self.start_render(export="film-off")
        self.assertEqual(status, 422)
        self.assertIn("switched off", body["error"])
        status, body = self.start_render(export="film-off", enable=True)
        self.assertEqual(status, 200)
        self.assertEqual(self.wait_for(("rendered",))["step"], "rendered")
        self.assertIs(self.data()["exports"][0]["enabled"], True)

    def test_an_unknown_export_and_an_unknown_preset_are_400(self):
        self.assertEqual(self.start_render(export="nope")[0], 400)
        self.assertEqual(self.start_render(preset="film-8k")[0], 400)
        self.assertEqual(self.start_render()[0], 400)

    def test_without_a_work_folder_it_is_400(self):
        server.configure(work_dir="", project=server.Project())
        server.configure(work_dir=None)
        self.assertEqual(self.start_render(preset="film-4k")[0], 400)

    def test_a_missing_timeline_is_404(self):
        self.path.unlink()
        self.assertEqual(self.start_render(preset="film-4k")[0], 404)

    def test_a_decoder_that_crashes_ends_in_the_error_status_and_frees_the_slot(self):
        server.configure(decoder=FlatDecoder(self.clips, fail_on=str(self.originals / "B.braw")))
        self.start_render(preset="film-4k", name="film-crash")
        s = self.wait_for(("rendered",))
        self.assertEqual(s["step"], "error")
        self.assertIn("crashed", s["message"])
        status, _ = self.start_render(preset="film-4k", name="film-crash")
        self.assertEqual(status, 200)                       # the slot is free again
        self.wait_for(("rendered",))

    def test_the_page_cannot_name_a_path(self):
        status, body = self.start_render(export="film-4k", output="/etc/x.mp4")
        self.assertIn(status, (400, 200))
        self.assertFalse(Path("/etc/x.mp4").exists())

    def test_a_foreign_origin_is_403(self):
        status, body = self.post_json("/api/render", {"preset": "film-4k"},
                                      headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_the_credit_file_appears_next_to_the_film_when_the_lut_has_a_credit(self):
        self.post_json("/api/look", {"hash": self.hash(), "lut": "slay/slay-04-film.cube"})
        self.start_render(preset="film-4k", name="film-credit")
        self.assertEqual(self.wait_for(("rendered",))["step"], "rendered")
        out = Path(self.data()["exports"][0]["output"])
        credit = out.with_suffix(".credit.txt")
        self.assertIn("slay", credit.read_text())


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PageLogicStep3Tests(Step3Case):
    def test_the_cards_show_the_look_the_loudness_and_the_exports_and_send_their_changes(self):
        harness = Path(__file__).parent / "ui_harness_step3.js"
        ui = Path(server.__file__).parent / "ui.html"
        r = subprocess.run(["node", str(harness), str(ui), f"http://127.0.0.1:{self.port}"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIn("none (the base look only)", out["lutOptions"])
        self.assertIn("slay-04-film.cube", out["lutOptions"])
        self.assertEqual(out["lutValueBefore"], "")
        self.assertEqual(out["lutValueAfter"], "slay/slay-04-film.cube")
        self.assertIn("slay", out["creditAfter"])
        self.assertEqual(out["lutValueNone"], "")
        self.assertEqual(out["creditNone"], "")
        self.assertEqual(out["loudBefore"], "segment_then_film")
        self.assertEqual(out["loudAfter"], "film_only")
        self.assertIn("target -14 LUFS", out["loudTarget"])
        self.assertIn("No export yet", out["exportsEmpty"])
        self.assertEqual(out["finalStatus"]["step"], "rendered", out["finalStatus"])
        self.assertIn("film-4k", out["exportsAfter"])
        self.assertIn("GB", out["exportsAfter"])
        self.assertIn('class="film-ok">ok', out["exportsAfter"])
        self.assertNotIn("disabled", out["exportsAfter"])
        self.assertIn("-14", out["readoutAfterRender"])
        self.assertIn("Last export film-4k", out["readoutAfterRender"])
        self.assertTrue(out["jobStarted"])
        self.assertEqual(out["errorBadLut"] != "", True)


if __name__ == "__main__":
    unittest.main()
