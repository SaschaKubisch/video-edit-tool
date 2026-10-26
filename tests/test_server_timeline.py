"""Check 2.13: the page's endpoints for the film (SPECS 12, 4.4, 15). A server on an
ephemeral port answers: the timeline with its hash; an edit with the right hash is
saved (with a snapshot); a stale hash gives 409; an invalid result gives 422 with the
field path; an unknown operation gives 400; a wrong Host gives 403; a preview request
with a path outside the previews folder gives 404; a range request returns the bytes
asked for; the jobs (assemble, preview) share one slot and answer 409 when it is taken."""

import copy
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

import render
import server
import store
import timeline
from project import Project
from tests import media
from tests.test_assemble_project import ProjectFiles
from tests.test_preview import PreviewFixture, W, H
from tests.timeline_fixture import example


class ServerCase(unittest.TestCase):
    """Starts the server on an ephemeral port with the given timeline, work folder and project."""

    timeline_path = None
    work_dir = None
    project = None

    def start(self, timeline_path, work_dir, project=None):
        self.timeline_path, self.work_dir = timeline_path, work_dir
        server.configure(timeline_path=str(timeline_path), work_dir=str(work_dir),
                         project=project or Project(),
                         preview_settings=render.PreviewSettings(width=W, encoder="libx264"))
        server.reset_state()
        self.httpd = server.make_server(0)
        self.port = self.httpd.server_address[1]
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()

        def stop():
            self.httpd.shutdown()
            self.httpd.server_close()
            server.configure(timeline_path=None, work_dir=None, project=Project(),
                             preview_settings=None)
            server.reset_state()
        self.addCleanup(stop)

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        hdrs = {"Host": f"127.0.0.1:{self.port}"}
        hdrs.update(headers or {})
        data = raw if raw is not None else (json.dumps(body) if body is not None else None)
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        content = resp.read()
        conn.close()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, content

    def get_json(self, path, **kw):
        status, headers, content = self.request("GET", path, **kw)
        return status, json.loads(content or b"null")

    def post_json(self, path, body=None, **kw):
        status, headers, content = self.request("POST", path, body=body, **kw)
        return status, json.loads(content or b"null")

    def wait_for(self, steps, seconds=240):
        end = time.time() + seconds
        while time.time() < end:
            _, s = self.get_json("/api/status")
            if s["step"] in steps or s["step"] == "error":
                return s
            time.sleep(0.2)
        self.fail(f"the job did not finish; status {s}")


class TimelineFileCase(ServerCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "timeline.json"
        self.work = self.root / "work"
        (self.work / "previews").mkdir(parents=True)
        timeline.Timeline(example()).save(str(self.path))
        self.start(self.path, self.work)

    def file_hash(self):
        return timeline.file_hash(str(self.path))


class GetTimelineTests(TimelineFileCase):
    def test_the_timeline_with_its_hash_and_the_film_times(self):
        status, body = self.get_json("/api/timeline")
        self.assertEqual(status, 200)
        self.assertEqual(body["hash"], self.file_hash())
        s = body["summary"]
        self.assertEqual((s["segments"], s["enabled"], s["film_frames"]), (3, 3, 2542))
        self.assertEqual([r["id"] for r in s["rows"]], ["s001", "s002", "s003"])
        self.assertEqual(s["rows"][1]["film_start_frame"], 862)
        self.assertEqual(s["rows"][1]["source_frames"], 2915)
        self.assertEqual(body["name"], "timeline.json")
        self.assertIn("preview", body)

    def test_no_preview_yet(self):
        _, body = self.get_json("/api/timeline")
        self.assertFalse(body["preview"]["exists"])
        self.assertFalse(body["preview"]["up_to_date"])

    def test_a_missing_timeline_file_is_404_with_a_hint(self):
        self.path.unlink()
        status, body = self.get_json("/api/timeline")
        self.assertEqual(status, 404)
        self.assertIn("assemble", body["error"])

    def test_a_file_that_is_not_a_timeline_is_422(self):
        self.path.write_text("this is not json")
        status, body = self.get_json("/api/timeline")
        self.assertEqual(status, 422)
        self.assertIn("error", body)

    def test_the_review_flags_of_the_assembly_report_are_attached_to_segments(self):
        (self.work / "reports").mkdir()
        (self.work / "reports" / "assembly-report.json").write_text(json.dumps({"clips": [
            {"clip": "A002_01011540_C002", "status": "used", "segments": ["s001", "s002"],
             "review": True, "review_reasons": ["no clap found"]},
            {"clip": "A002_01060615_C083", "status": "used", "segments": ["s003"],
             "review": False, "review_reasons": []}]}))
        _, body = self.get_json("/api/timeline")
        rows = {r["id"]: r for r in body["summary"]["rows"]}
        self.assertEqual(rows["s001"]["review"], ["no clap found"])
        self.assertEqual(rows["s002"]["review"], ["no clap found"])
        self.assertNotIn("review", rows["s003"])

    def test_a_wrong_host_is_403(self):
        status, _, _ = self.request("GET", "/api/timeline", headers={"Host": "evil.example:80"})
        self.assertEqual(status, 403)


class EditTests(TimelineFileCase):
    def edit(self, ops, h=None, **kw):
        return self.post_json("/api/timeline/edit",
                              {"hash": h if h is not None else self.file_hash(), "operations": ops},
                              **kw)

    def test_a_trim_with_the_right_hash_is_saved_and_answered_with_the_new_hash(self):
        old = self.file_hash()
        status, body = self.edit([{"op": "trim_in", "segment": "s001", "frames": 30}])
        self.assertEqual(status, 200, body)
        self.assertNotEqual(body["hash"], old)
        self.assertEqual(body["hash"], self.file_hash())
        self.assertEqual(body["summary"]["rows"][0]["in"], 68)
        self.assertEqual(json.loads(self.path.read_text())["segments"][0]["in"], 68)

    def test_the_old_file_is_kept_as_a_snapshot(self):
        before = self.path.read_bytes()
        self.edit([{"op": "trim_out", "segment": "s003", "frames": -10}])
        snaps = store.list_snapshots(str(self.path))
        self.assertEqual(len(snaps), 1)
        self.assertEqual(Path(snaps[0]).read_bytes(), before)

    def test_a_stale_hash_is_409_and_changes_nothing(self):
        before = self.path.read_bytes()
        status, body = self.edit([{"op": "trim_in", "segment": "s001", "frames": 1}], h="0" * 64)
        self.assertEqual(status, 409)
        self.assertEqual(body["hash"], self.file_hash())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(store.list_snapshots(str(self.path)), [])

    def test_a_change_made_by_someone_else_in_the_meantime_is_409(self):
        h = self.file_hash()
        d = example()
        d["segments"][0]["label"] = "edited by Claude Code"
        timeline.Timeline(d).save(str(self.path))
        status, _ = self.edit([{"op": "trim_in", "segment": "s001", "frames": 1}], h=h)
        self.assertEqual(status, 409)
        self.assertIn("Claude Code", self.path.read_text())

    def test_an_invalid_result_is_422_with_the_field_path_and_changes_nothing(self):
        before = self.path.read_bytes()
        status, body = self.edit([{"op": "trim_out", "segment": "s001", "frames": 10000}])
        self.assertEqual(status, 422)
        paths = [f["path"] for f in body["findings"]]
        self.assertIn("segments[0].out", paths)
        self.assertEqual(body["findings"][0]["rule"], "V9")
        self.assertEqual(self.path.read_bytes(), before)

    def test_removing_every_segment_is_422(self):
        ops = [{"op": "set_enabled", "segment": s, "enabled": False} for s in ("s001", "s002", "s003")]
        status, body = self.edit(ops)
        self.assertEqual(status, 422)
        self.assertEqual(body["findings"][0]["rule"], "V10")

    def test_a_scene_that_is_too_short_for_its_fade_is_422(self):
        # s003 is the last segment: it needs the 2 s fade-out plus half a join
        status, body = self.edit([{"op": "trim_in", "segment": "s003", "frames": 600}])
        self.assertEqual(status, 422)
        self.assertEqual(body["findings"][0]["rule"], "V11")

    def test_remove_restore_move_and_label(self):
        s, b = self.edit([{"op": "set_enabled", "segment": "s002", "enabled": False}])
        self.assertEqual(s, 200)
        self.assertEqual(b["summary"]["enabled"], 2)
        self.assertEqual(b["summary"]["film_frames"], 862 + 620)
        s, b = self.edit([{"op": "set_enabled", "segment": "s002", "enabled": True}], h=b["hash"])
        self.assertEqual(b["summary"]["enabled"], 3)
        s, b = self.edit([{"op": "move", "segment": "s003", "to_index": 0}], h=b["hash"])
        self.assertEqual([r["id"] for r in b["summary"]["rows"]], ["s003", "s001", "s002"])
        s, b = self.edit([{"op": "set_label", "segment": "s001", "label": "new label"}], h=b["hash"])
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(self.path.read_text())["segments"][1]["label"], "new label")

    def test_several_operations_are_all_or_nothing(self):
        before = self.path.read_bytes()
        status, _ = self.edit([{"op": "trim_in", "segment": "s001", "frames": 5},
                               {"op": "trim_in", "segment": "s999", "frames": 5}])
        self.assertEqual(status, 400)
        self.assertEqual(self.path.read_bytes(), before)

    def test_an_unknown_operation_is_400(self):
        status, body = self.edit([{"op": "explode", "segment": "s001"}])
        self.assertEqual(status, 400)
        self.assertIn("unknown operation", body["error"])

    def test_malformed_bodies_are_400(self):
        status, _, _ = self.request("POST", "/api/timeline/edit", raw="{not json")
        self.assertEqual(status, 400)
        status, _ = self.post_json("/api/timeline/edit", {"operations": []})
        self.assertEqual(status, 400)
        status, _ = self.post_json("/api/timeline/edit", {"hash": self.file_hash()})
        self.assertEqual(status, 400)
        status, _ = self.post_json("/api/timeline/edit", {"hash": self.file_hash(), "operations": []})
        self.assertEqual(status, 400)
        status, _ = self.post_json("/api/timeline/edit", [1, 2])
        self.assertEqual(status, 400)

    def test_the_page_cannot_change_what_it_may_not(self):
        # sources, film, look and the segment ids cannot be reached with these operations
        status, _ = self.edit([{"op": "set_label", "segment": "s001", "label": "x",
                                "source": "/etc/passwd", "id": "s999"}])
        self.assertEqual(status, 200)
        d = json.loads(self.path.read_text())
        self.assertEqual(d["segments"][0]["id"], "s001")
        self.assertEqual(d["sources"], example()["sources"])
        self.assertNotIn("passwd", self.path.read_text())

    def test_a_missing_timeline_is_404(self):
        h = self.file_hash()
        self.path.unlink()
        status, _ = self.edit([{"op": "set_label", "segment": "s001", "label": "x"}], h=h)
        self.assertEqual(status, 404)

    def test_a_foreign_origin_is_403(self):
        status, _ = self.edit([{"op": "set_label", "segment": "s001", "label": "x"}],
                              headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_two_edits_at_once_never_overwrite_each_other_silently(self):
        h = self.file_hash()
        results = []

        def go(label):
            results.append(self.post_json("/api/timeline/edit", {
                "hash": h, "operations": [{"op": "set_label", "segment": "s001", "label": label}]})[0])

        threads = [threading.Thread(target=go, args=(f"label {i}",)) for i in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), [200, 409, 409, 409])


class PreviewFileTests(TimelineFileCase):
    def setUp(self):
        super().setUp()
        self.data = bytes(range(256)) * 40            # 10240 bytes
        (self.work / "previews" / "preview.mp4").write_bytes(self.data)

    def test_the_whole_file_with_accept_ranges(self):
        status, h, content = self.request("GET", "/api/preview/preview.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(content, self.data)
        self.assertEqual(h["accept-ranges"], "bytes")
        self.assertEqual(h["content-type"], "video/mp4")
        self.assertEqual(int(h["content-length"]), len(self.data))

    def test_a_range_returns_the_bytes_asked_for(self):
        status, h, content = self.request("GET", "/api/preview/preview.mp4",
                                          headers={"Range": "bytes=100-199"})
        self.assertEqual(status, 206)
        self.assertEqual(content, self.data[100:200])
        self.assertEqual(h["content-range"], f"bytes 100-199/{len(self.data)}")
        self.assertEqual(int(h["content-length"]), 100)

    def test_an_open_ended_range_and_a_suffix_range(self):
        status, h, content = self.request("GET", "/api/preview/preview.mp4",
                                          headers={"Range": "bytes=10000-"})
        self.assertEqual((status, content), (206, self.data[10000:]))
        status, h, content = self.request("GET", "/api/preview/preview.mp4",
                                          headers={"Range": "bytes=-50"})
        self.assertEqual((status, content), (206, self.data[-50:]))

    def test_a_range_past_the_end_is_416(self):
        status, h, _ = self.request("GET", "/api/preview/preview.mp4",
                                    headers={"Range": "bytes=20000-30000"})
        self.assertEqual(status, 416)
        self.assertEqual(h["content-range"], f"bytes */{len(self.data)}")

    def test_a_range_that_runs_past_the_end_is_cut_to_the_end(self):
        status, h, content = self.request("GET", "/api/preview/preview.mp4",
                                          headers={"Range": "bytes=10000-99999"})
        self.assertEqual((status, content), (206, self.data[10000:]))

    def test_a_missing_file_is_404(self):
        status, _, _ = self.request("GET", "/api/preview/nothing.mp4")
        self.assertEqual(status, 404)

    def test_paths_outside_the_previews_folder_are_404(self):
        (self.work / "secret.txt").write_text("secret")
        (self.root / "outside.txt").write_text("outside")
        for path in ("/api/preview/../secret.txt", "/api/preview/..%2Fsecret.txt",
                     "/api/preview/%2e%2e/secret.txt", "/api/preview//etc/passwd",
                     "/api/preview/..%2F..%2Foutside.txt", "/api/preview/%2Fetc%2Fpasswd",
                     "/api/preview/sub/../../secret.txt", "/api/preview/"):
            with self.subTest(path=path):
                status, _, content = self.request("GET", path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"secret", content)
                self.assertNotIn(b"outside", content)

    def test_a_symbolic_link_out_of_the_folder_is_404(self):
        (self.root / "outside.txt").write_text("outside")
        os.symlink(self.root / "outside.txt", self.work / "previews" / "link.txt")
        status, _, content = self.request("GET", "/api/preview/link.txt")
        self.assertEqual(status, 404)
        self.assertNotIn(b"outside", content)

    def test_a_folder_is_404(self):
        (self.work / "previews" / "sub").mkdir()
        status, _, _ = self.request("GET", "/api/preview/sub")
        self.assertEqual(status, 404)

    def test_a_wrong_host_is_403(self):
        status, _, _ = self.request("GET", "/api/preview/preview.mp4", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)

    def test_without_a_work_folder_every_request_is_404(self):
        server.configure(work_dir="")
        server.configure(project=Project(work_dir=""))
        status, _, _ = self.request("GET", "/api/preview/preview.mp4")
        self.assertEqual(status, 404)


class PreviewJobTests(PreviewFixture, ServerCase):
    """POST /api/preview renders the film preview in the background."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.render_dir = self.root / "work"
        self.render_dir.mkdir()
        self.path = self.root / "timeline.json"
        self.tl = self.make_tl()
        self.tl.save(str(self.path))
        self.start(self.path, self.render_dir)

    def test_a_job_renders_the_preview_and_the_page_can_tell_it_is_up_to_date(self):
        status, body = self.post_json("/api/preview", {})
        self.assertEqual(status, 200, body)
        s = self.wait_for({"previewed"})
        self.assertEqual(s["step"], "previewed", s)
        self.assertEqual(s["progress"], 100)
        out = self.render_dir / "previews" / "preview.mp4"
        self.assertEqual(media.video_frames(out), 180)
        _, tlbody = self.get_json("/api/timeline")
        self.assertTrue(tlbody["preview"]["exists"])
        self.assertTrue(tlbody["preview"]["up_to_date"])
        self.assertEqual(tlbody["preview"]["frames"], 180)
        self.assertEqual(tlbody["preview"]["file"], "preview.mp4")
        # the file is served with ranges
        status, h, content = self.request("GET", "/api/preview/preview.mp4", headers={"Range": "bytes=0-7"})
        self.assertEqual(status, 206)
        self.assertEqual(content[4:8], b"ftyp")
        # an edit makes it out of date
        status, edit = self.post_json("/api/timeline/edit", {
            "hash": tlbody["hash"], "operations": [{"op": "trim_in", "segment": "s002", "frames": 2}]})
        self.assertEqual(status, 200, edit)
        _, tlbody2 = self.get_json("/api/timeline")
        self.assertTrue(tlbody2["preview"]["exists"])
        self.assertFalse(tlbody2["preview"]["up_to_date"])

    def test_a_second_request_while_a_job_runs_is_409(self):
        self.assertTrue(server._try_start_job())
        try:
            status, body = self.post_json("/api/preview", {})
            self.assertEqual(status, 409)
            self.assertIn("running", body["error"])
        finally:
            server._end_job()

    def test_without_a_timeline_file_it_is_404(self):
        self.path.unlink()
        status, body = self.post_json("/api/preview", {})
        self.assertEqual(status, 404)

    def test_an_invalid_timeline_is_422_and_no_job_starts(self):
        d = copy.deepcopy(self.tl.data)
        d["segments"][0]["out"] = 5000
        self.path.write_text(json.dumps(d))
        status, body = self.post_json("/api/preview", {})
        self.assertEqual(status, 422)
        self.assertEqual(body["findings"][0]["rule"], "V9")
        _, s = self.get_json("/api/status")
        self.assertEqual(s["step"], "idle")

    def test_a_render_that_fails_ends_in_the_error_status_and_frees_the_slot(self):
        d = copy.deepcopy(self.tl.data)
        d["sources"]["B"]["preview_copy"] = str(self.render_dir / "gone.mp4")
        self.path.write_text(json.dumps(d))
        status, _ = self.post_json("/api/preview", {})
        self.assertEqual(status, 200)
        s = self.wait_for(set())
        self.assertEqual(s["step"], "error")
        self.assertIn("B", s["message"])
        self.assertTrue(server._try_start_job())
        server._end_job()

    def test_a_foreign_origin_is_403(self):
        status, _ = self.post_json("/api/preview", {}, headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)


class AssembleJobTests(ProjectFiles, ServerCase):
    """POST /api/assemble builds the timeline from the project in the background."""

    def setUp(self):
        ProjectFiles.setUp(self)
        self.start(self.out, self.work, project=self.project)

    def test_a_job_assembles_the_timeline(self):
        status, body = self.post_json("/api/assemble", {})
        self.assertEqual(status, 200, body)
        s = self.wait_for({"assembled"})
        self.assertEqual(s["step"], "assembled", s)
        self.assertTrue(self.out.is_file())
        self.assertEqual(len(timeline.Timeline.load(str(self.out)).data["segments"]), 6)
        status, tlbody = self.get_json("/api/timeline")
        self.assertEqual(status, 200)
        self.assertEqual(tlbody["summary"]["segments"], 6)

    def test_an_existing_timeline_is_409_unless_replace_is_sent(self):
        self.assertEqual(self.post_json("/api/assemble", {})[0], 200)
        self.wait_for({"assembled"})
        before = self.out.read_bytes()
        status, body = self.post_json("/api/assemble", {})
        self.assertEqual(status, 409)
        self.assertIn("replace", body["error"])
        self.assertEqual(self.out.read_bytes(), before)
        status, _ = self.post_json("/api/assemble", {"replace": True, "limit": 1})
        self.assertEqual(status, 200)
        self.wait_for({"assembled"})
        self.assertEqual(len(timeline.Timeline.load(str(self.out)).data["segments"]), 2)
        self.assertEqual(len(store.list_snapshots(str(self.out))), 1)

    def test_a_busy_server_is_409(self):
        self.assertTrue(server._try_start_job())
        try:
            status, body = self.post_json("/api/assemble", {})
            self.assertEqual(status, 409)
            self.assertIn("running", body["error"])
        finally:
            server._end_job()

    def test_a_project_without_clips_is_400(self):
        server.configure(project=Project())
        status, body = self.post_json("/api/assemble", {})
        self.assertEqual(status, 400)

    def test_a_server_without_a_work_folder_is_400(self):
        server.configure(work_dir="")
        server.configure(project=Project(matches=self.project.matches, work_dir=""))
        status, body = self.post_json("/api/assemble", {})
        self.assertEqual(status, 400)
        self.assertIn("work", body["error"])

    def test_a_failing_assembly_ends_in_the_error_status(self):
        del self.conv.data["base_lut"]
        self.conv.save()
        self.assertEqual(self.post_json("/api/assemble", {})[0], 200)
        s = self.wait_for(set())
        self.assertEqual(s["step"], "error")
        self.assertIn("base_lut", s["message"])
        self.assertFalse(self.out.exists())

    def test_the_page_cannot_name_a_path(self):
        status, _ = self.post_json("/api/assemble", {"output": "/tmp/evil.json", "work": "/tmp"})
        self.assertEqual(status, 200)
        self.wait_for({"assembled"})
        self.assertTrue(self.out.is_file())
        self.assertFalse(Path("/tmp/evil.json").exists())


class PageTests(TimelineFileCase):
    def test_the_page_is_served_and_has_the_film_tab(self):
        status, h, content = self.request("GET", "/")
        self.assertEqual(status, 200)
        page = content.decode()
        for needle in ('id="filmPlayer"', 'id="filmTable"', "/api/timeline", "/api/timeline/edit",
                       "/api/preview", "/api/assemble", 'data-step="4"'):
            self.assertIn(needle, page)

    def test_the_script_of_the_page_is_valid_javascript(self):
        import re
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        page = (Path(server.__file__).parent / "ui.html").read_text()
        script = re.search(r"<script>(.*)</script>", page, re.S).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            js = Path(tmp) / "ui.js"
            js.write_text(script)
            r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_status_names_the_new_steps_when_a_job_runs(self):
        server._set_status("previewing", "Rendering", 30)
        _, s = self.get_json("/api/status")
        self.assertEqual(s["step"], "previewing")
        server.reset_state()


if __name__ == "__main__":
    unittest.main()


class PageLogicTests(TimelineFileCase):
    """The script of the page, run in Node against this server with a stub for the DOM:
    the Film tab loads the film, builds the table and sends the operations."""

    def test_the_film_tab_loads_edits_and_shows_the_servers_refusals(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        harness = Path(__file__).parent / "ui_harness.js"
        ui = Path(server.__file__).parent / "ui.html"
        r = subprocess.run([node, str(harness), str(ui), f"http://127.0.0.1:{self.port}"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(out["loaded"])
        self.assertEqual(out["rows0"], [["s001", 38, 900, True], ["s002", 1320, 2380, True],
                                        ["s003", 30, 650, True]])
        for sid in ("s001", "s002", "s003"):
            self.assertIn(f'id="filmRow_{sid}"', out["table"])
        self.assertIn("3 of 3 scenes", out["summaryLine"])
        self.assertEqual(out["badge"], "no preview")
        # a trim is saved and the table shows the new in point
        self.assertEqual(out["afterTrim"][0], ["s001", 68, 900, True])
        self.assertNotEqual(out["hash0"], out["hash1"])
        self.assertEqual(out["errorAfterTrim"], "")
        saved = {s["id"]: s for s in json.loads(self.path.read_text())["segments"]}
        self.assertEqual(saved["s001"]["in"], 68)
        # removing a scene: it stays in the list, marked, with no film time
        self.assertEqual([r[:2] for r in out["afterRemove"]],
                         [["s001", True], ["s002", False], ["s003", True]])
        self.assertIsNone(out["afterRemove"][1][2])
        self.assertIn("removed", out["tableAfterRemove"])
        self.assertEqual(out["afterMove"], ["s003", "s001", "s002"])
        # an invalid change is refused with the field path, and the table keeps the saved values
        self.assertIn("segments[1].out", out["errorInvalid"])
        self.assertEqual(out["rowsAfterInvalid"][1], ["s001", 900])
        # a stale hash: the server says so and the page reloads the file as it is
        self.assertTrue(out["errorStale"])
        self.assertEqual(out["hashAfterStale"], timeline.file_hash(str(self.path)))
