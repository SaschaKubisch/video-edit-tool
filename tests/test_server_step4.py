"""The page's part of step 4 (SPECS 9.8, 12): the subtitles and titles in the answer, the operations of the text box
and the cues, the SRT file, and the two jobs (Whisper and the translation). A server on an ephemeral port; the
example of SPECS 3.3 is the film for the operations; a fake whisper-cli and a fake OpenRouter answer the jobs."""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import config
import server
import store
import textlayout
import timeline
import words
from tests.fake_openrouter import FakeOpenRouter
from tests.test_server_timeline import ServerCase, TimelineFileCase
from tests.test_transcribe import FakeWhisperCase, phrase
from tests.timeline_fixture import example

KEY = "sk-or-test-key-abc123"


class WordsCase(TimelineFileCase):
    def setUp(self):
        self._factory = words.RULES_FACTORY
        words.RULES_FACTORY = lambda data: textlayout.char_rules(42)
        self.addCleanup(setattr, words, "RULES_FACTORY", self._factory)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.path = self.root / "timeline.json"
        self.work = self.root / "work"
        (self.work / "previews").mkdir(parents=True)
        d = example()
        d["exports"][0]["output"] = str(self.root / "renders" / "film-4k.mp4")
        timeline.Timeline(d).save(str(self.path))
        self.start(self.path, self.work)

    def edit(self, *ops, h=None):
        return self.post_json("/api/timeline/edit", {"hash": h or self.file_hash(), "operations": list(ops)})

    def data(self):
        return json.loads(self.path.read_text())


class AnswerTests(WordsCase):
    def test_the_answer_carries_the_subtitles_the_titles_and_the_forms(self):
        status, body = self.get_json("/api/timeline")
        w = body["words"]
        self.assertEqual(status, 200)
        self.assertEqual(w["cues"][0]["id"], "c001")
        self.assertEqual((w["cues"][0]["film_start_s"], w["cues"][0]["film_end_s"]), (3.2, 6.0))
        self.assertTrue(w["cues"][0]["in_film"])
        self.assertEqual(w["titles"][0]["text"], "Sunday kitchen")
        self.assertEqual(w["forms"][0]["form"], "srt")
        self.assertTrue(w["forms"][0]["srt"].endswith("film-4k.en.srt"))
        self.assertEqual(w["form_choices"], ["srt", "burned", "both", "none"])
        self.assertEqual(w["limits"]["max_chars"], 42)
        self.assertIn("available", w["font"])
        self.assertIn("available", w["whisper"])
        self.assertEqual([l["code"] for l in w["translate"]["languages"]], list(config.TRANSLATE_LANGUAGES))
        self.assertEqual(body["notes"], [])

    def test_a_cue_on_a_scene_that_is_switched_off_says_so(self):
        self.edit({"op": "set_enabled", "segment": "s001", "enabled": False})
        _, body = self.get_json("/api/timeline")
        self.assertFalse(body["words"]["cues"][0]["in_film"])
        self.assertIsNone(body["words"]["cues"][0]["film_start_s"])

    def test_the_scene_text_is_in_the_summary(self):
        self.edit({"op": "set_scene_text", "segment": "s003", "text": "Pan on the stove."})
        _, body = self.get_json("/api/timeline")
        self.assertEqual(body["summary"]["rows"][2]["text"], "Pan on the stove.")

    def test_the_answer_never_carries_a_key(self):
        os.environ["OPENROUTER_API_KEY"] = KEY
        self.addCleanup(os.environ.pop, "OPENROUTER_API_KEY", None)
        _, body = self.get_json("/api/timeline")
        self.assertTrue(body["words"]["translate"]["key"])
        self.assertNotIn(KEY, json.dumps(body))


class OperationTests(WordsCase):
    def test_the_text_box_of_a_scene(self):
        status, body = self.edit({"op": "set_scene_text", "segment": "s003",
                                  "text": "Um, the pan is hot. We add the sausage and wait for it to cook"})
        self.assertEqual(status, 200)
        cues = [c for c in body["words"]["cues"] if c["segment"] == "s003"]
        self.assertGreaterEqual(len(cues), 2)
        self.assertTrue(all(c["origin"] == "text" and not c["locked"] for c in cues))
        self.assertEqual(self.data()["segments"][2]["text"], "The pan is hot. We add the sausage and wait for it to cook.")
        self.assertEqual(body["hash"], self.file_hash())
        self.assertEqual(len(store.list_snapshots(str(self.path))), 1)

    def test_text_that_is_too_long_for_the_scene_is_noted(self):
        self.edit({"op": "trim_out", "segment": "s003", "frames": -530})
        status, body = self.edit({"op": "set_scene_text", "segment": "s003",
                                  "text": " ".join(["The pan is hot enough now and we add the sausage and wait."] * 6)})
        self.assertEqual(status, 200)
        self.assertTrue(any("shorter than a person can read" in n for n in body["notes"]), body["notes"])

    def test_hand_made_cues_need_a_confirmation_and_nothing_changes_without_it(self):
        self.edit({"op": "set_scene_text", "segment": "s003", "text": "First version of the text."})
        cue = [c for c in self.data()["subtitles"]["cues"] if c["anchor"] == "s003"][0]
        _, body = self.get_json("/api/timeline")
        start = [c for c in body["words"]["cues"] if c["id"] == cue["id"]][0]["film_start_s"]
        self.edit({"op": "edit_cue", "cue": cue["id"], "film_start_s": start + 0.5, "film_end_s": start + 3.0})
        before = self.path.read_bytes()
        status, body = self.edit({"op": "set_scene_text", "segment": "s003", "text": "Second version."})
        self.assertEqual(status, 409)
        self.assertEqual(body["needs_confirmation"]["cues"], [cue["id"]])
        self.assertNotIn("hash", body)                       # not a stale hash: the page keeps what it shows
        self.assertEqual(self.path.read_bytes(), before)
        status, body = self.edit({"op": "set_scene_text", "segment": "s003", "text": "Second version.",
                                  "replace_adjusted": True})
        self.assertEqual(status, 200)
        self.assertEqual([c["text"] for c in body["words"]["cues"] if c["segment"] == "s003"], ["Second version."])

    def test_cues_at_film_time(self):
        status, body = self.edit({"op": "add_cue", "film_start_s": 30.0, "film_end_s": 33.0, "text": "Hello."})
        self.assertEqual(status, 200)
        new = [c for c in body["words"]["cues"] if c["id"] == "c002"][0]
        self.assertEqual((new["segment"], new["locked"], new["origin"]), ("s002", True, "manual"))
        status, body = self.edit({"op": "edit_cue", "cue": "c002", "film_start_s": 31.0, "film_end_s": 34.0, "text": "Hi."})
        self.assertEqual(status, 200)
        status, body = self.edit({"op": "shift_cues", "seconds": 0.5, "cues": ["c002"]})
        self.assertEqual(status, 200)
        status, body = self.edit({"op": "remove_cue", "cue": "c002"})
        self.assertEqual(status, 200)
        self.assertEqual([c["id"] for c in body["words"]["cues"]], ["c001"])

    def test_a_cue_that_breaks_a_rule_is_422_and_nothing_is_saved(self):
        before = self.path.read_bytes()
        status, body = self.edit({"op": "add_cue", "film_start_s": 4.0, "film_end_s": 7.0, "text": "Overlaps c001."})
        self.assertEqual(status, 422)
        self.assertEqual(body["findings"][0]["rule"], "V24")
        self.assertEqual(self.path.read_bytes(), before)

    def test_bad_requests_are_400(self):
        self.assertEqual(self.edit({"op": "add_cue", "film_start_s": 900.0, "text": "x"})[0], 400)
        self.assertEqual(self.edit({"op": "add_cue", "text": "x"})[0], 400)
        self.assertEqual(self.edit({"op": "set_scene_text", "segment": "s099", "text": "x"})[0], 400)
        self.assertEqual(self.edit({"op": "set_scene_text", "text": "x"})[0], 400)
        self.assertEqual(self.edit({"op": "remove_cue", "cue": "c099"})[0], 400)
        self.assertEqual(self.edit({"op": "edit_cue", "cue": "c001", "film_start_s": "soon"})[0], 400)

    def test_the_form_of_an_export(self):
        status, body = self.edit({"op": "set_export_subtitles", "export": "film-4k", "form": "both"})
        self.assertEqual(status, 200)
        self.assertEqual(body["words"]["forms"][0]["form"], "both")
        self.assertEqual(self.data()["exports"][0]["subtitles"], "both")
        self.assertEqual(self.edit({"op": "set_export_subtitles", "export": "film-4k", "form": "hard"})[0], 400)

    def test_titles(self):
        status, body = self.edit({"op": "add_title", "text": "Monday", "start_s": 10, "length_s": 3, "template": "lower_third"})
        self.assertEqual(status, 200)
        self.assertEqual(body["words"]["titles"][-1]["id"], "t002")
        status, body = self.edit({"op": "edit_title", "title": "t002", "text": "Tuesday", "size_pct": 6})
        self.assertEqual(body["words"]["titles"][-1]["size_pct"], 6)
        status, body = self.edit({"op": "remove_title", "title": "t002"})
        self.assertEqual([t["id"] for t in body["words"]["titles"]], ["t001"])
        self.assertEqual(self.edit({"op": "add_title", "text": "x", "start_s": 1, "template": "corner"})[0], 422)

    def test_a_trim_through_the_page_keeps_the_cues_with_their_picture(self):
        status, body = self.edit({"op": "trim_in", "segment": "s001", "frames": 60})
        self.assertEqual(status, 200)
        c = body["words"]["cues"][0]
        self.assertAlmostEqual(c["start_s"], 3.2 - 60 * 1001 / 30000, delta=0.04)

    def test_a_stale_hash_is_409_with_the_hash(self):
        status, body = self.post_json("/api/timeline/edit", {"hash": "0" * 64, "operations": [
            {"op": "remove_cue", "cue": "c001"}]})
        self.assertEqual(status, 409)
        self.assertEqual(body["hash"], self.file_hash())


class SrtEndpointTests(WordsCase):
    def test_the_srt_file_is_written_next_to_the_export(self):
        status, body = self.post_json("/api/subtitles/srt", {"export": "film-4k"})
        self.assertEqual(status, 200)
        out = Path(body["file"])
        self.assertEqual(out, self.root / "renders" / "film-4k.en.srt")
        self.assertEqual(words.parse_srt(out.read_text())[0]["text"], "Now the pan is hot enough.")
        self.assertEqual(body["cues"], 1)

    def test_an_unknown_export_is_400(self):
        self.assertEqual(self.post_json("/api/subtitles/srt", {"export": "nope"})[0], 400)

    def test_the_page_cannot_name_a_file(self):
        status, body = self.post_json("/api/subtitles/srt", {"export": "film-4k", "out": "/etc/passwd", "file": "/tmp/x"})
        self.assertEqual(status, 200)
        self.assertEqual(Path(body["file"]).parent, self.root / "renders")

    def test_a_foreign_origin_is_403(self):
        for path in ("/api/subtitles/srt", "/api/subtitles/auto", "/api/subtitles/translate"):
            status, _ = self.post_json(path, {}, headers={"Origin": "http://evil.example"})
            self.assertEqual(status, 403, path)


class AutoEndpointTests(FakeWhisperCase, ServerCase):
    def setUp(self):
        super().setUp()
        wav = self.wav("a", 12, [(2.0, 5.0)])
        self.hears(12, phrase(2.0, 5.0, "Now the pan is hot enough."), phrase(7.0, 9.0, "Thank you."))
        self.path = self.root / "timeline.json"
        self.film([("A", wav, 400)], [("A", 30, 330)]).save(str(self.path))
        self.start(self.path, self.work)

    def test_whisper_job_saves_the_cues_and_a_report(self):
        status, body = self.post_json("/api/subtitles/auto", {})
        self.assertEqual((status, body["status"]), (200, "started"))
        s = self.wait_for({"transcribed"})
        self.assertEqual(s["step"], "transcribed")
        self.assertIn("1 cue", s["message"])
        data = json.loads(self.path.read_text())
        self.assertEqual([c["text"] for c in data["subtitles"]["cues"]], ["Now the pan is hot enough."])
        report = json.loads((self.work / "reports" / "subtitles-auto-report.json").read_text())
        self.assertEqual(report["dropped"][0]["text"], "Thank you.")
        self.assertEqual(len(store.list_snapshots(str(self.path))), 1)

    def test_a_second_job_does_not_duplicate(self):
        for _ in range(2):
            self.post_json("/api/subtitles/auto", {})
            self.wait_for({"transcribed"})
            server.reset_state()
        self.assertEqual(len(json.loads(self.path.read_text())["subtitles"]["cues"]), 1)
        self.assertEqual(len(self.calls()), 1)

    def test_only_some_scenes(self):
        status, body = self.post_json("/api/subtitles/auto", {"only": "s001"})
        self.assertEqual(status, 400)
        status, body = self.post_json("/api/subtitles/auto", {"only": ["s001"]})
        self.assertEqual(status, 200)
        self.wait_for({"transcribed"})

    def test_without_a_model_it_is_400_and_says_what_to_set(self):
        os.environ.pop("VET_WHISPER_MODEL")
        status, body = self.post_json("/api/subtitles/auto", {})
        self.assertEqual(status, 400)
        self.assertIn("VET_WHISPER_MODEL", body["error"])

    def test_a_job_that_is_running_gives_409(self):
        self.assertTrue(server._try_start_job())
        self.addCleanup(server.reset_state)
        self.assertEqual(self.post_json("/api/subtitles/auto", {})[0], 409)

    def test_the_file_changed_while_whisper_ran(self):
        # the fake takes no time, so change the file from the progress callback of the job
        original = server.store.save if hasattr(server, "store") else None
        import transcribe
        real = transcribe.auto_subtitles

        def slow(tl, work, **kw):
            result = real(tl, work, **kw)
            data = json.loads(self.path.read_text())
            data["segments"][0]["label"] = "changed by someone else"
            self.path.write_text(timeline.dumps(data))
            return result
        transcribe.auto_subtitles = slow
        self.addCleanup(setattr, transcribe, "auto_subtitles", real)
        self.post_json("/api/subtitles/auto", {})
        s = self.wait_for({"transcribed"})
        self.assertEqual(s["step"], "error")
        self.assertIn("changed while Whisper was listening", s["message"])
        self.assertNotIn("subtitles", json.loads(self.path.read_text()))


class TranslateEndpointTests(WordsCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeOpenRouter(KEY)
        self.addCleanup(self.fake.stop)
        self._old = {k: os.environ.get(k) for k in ("OPENROUTER_API_KEY", "VET_OPENROUTER_URL")}
        os.environ["OPENROUTER_API_KEY"] = KEY
        os.environ["VET_OPENROUTER_URL"] = self.fake.url
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_the_job_writes_the_files(self):
        status, body = self.post_json("/api/subtitles/translate", {"languages": ["de", "ja"], "export": "film-4k"})
        self.assertEqual(status, 200)
        s = self.wait_for({"translated"})
        self.assertEqual(s["step"], "translated")
        for lang in ("en", "de", "ja"):
            self.assertTrue((self.root / "renders" / f"film-4k.{lang}.srt").is_file(), lang)
        _, answer = self.get_json("/api/timeline")
        langs = {l["code"]: l for l in answer["words"]["translate"]["languages"]}
        self.assertEqual((langs["de"]["translated"], langs["de"]["file"]), (1, True))
        self.assertNotIn(KEY, json.dumps(answer) + json.dumps(s))

    def test_no_key_is_400_and_nothing_is_sent(self):
        os.environ.pop("OPENROUTER_API_KEY")
        status, body = self.post_json("/api/subtitles/translate", {})
        self.assertEqual(status, 400)
        self.assertIn("OPENROUTER_API_KEY", body["error"])
        self.assertEqual(self.fake.count, 0)

    def test_unknown_language_or_export_is_400(self):
        self.assertEqual(self.post_json("/api/subtitles/translate", {"languages": ["fr"]})[0], 400)
        self.assertEqual(self.post_json("/api/subtitles/translate", {"export": "nope"})[0], 400)

    def test_a_failed_translation_is_an_error_status_with_the_cues(self):
        self.fake.script = ["http500"] * config.TRANSLATE_TRIES
        translate_sleep = __import__("translate").time
        old = translate_sleep.sleep
        translate_sleep.sleep = lambda s: None
        self.addCleanup(setattr, translate_sleep, "sleep", old)
        self.post_json("/api/subtitles/translate", {"languages": ["de"]})
        s = self.wait_for({"translated"})
        self.assertEqual(s["step"], "error")
        self.assertIn("c001", s["message"])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PageLogicStep4Tests(WordsCase):
    def test_the_cards_show_the_scenes_the_cues_the_forms_and_the_titles_and_send_their_changes(self):
        os.environ.pop("OPENROUTER_API_KEY", None)
        harness = Path(__file__).parent / "ui_harness_step4.js"
        ui = Path(server.__file__).parent / "ui.html"
        r = subprocess.run(["node", str(harness), str(ui), f"http://127.0.0.1:{self.port}"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["cardsShown"], ["block", "block", "block"])
        self.assertIn("film-4k", out["formRows"])
        self.assertIn('class="scene-text" id="sceneText_s003"', out["boxes0"])
        self.assertIn("Now the pan is hot enough.", out["boxes0"])
        self.assertIn('id="cueBlock_c001"', out["boxes0"])
        # saving the box: the text is cleaned, the cues are there, the draft is gone
        self.assertEqual(out["sceneText"], "The pan is hot. We add the sausage and wait for it to cook.")
        self.assertGreaterEqual(out["cueCount1"], 2)
        self.assertFalse(out["draftKept"])
        self.assertIn('class="cue-block text', out["boxes1"])
        # a drag is a pure function: it stays in the scene and keeps the length
        move = json.loads(out["dragMove"])
        self.assertGreater(move["start"], 64.13 + 1.9)
        clamp = json.loads(out["dragClamp"])
        self.assertAlmostEqual(clamp["end"], 25, delta=0.04)
        self.assertAlmostEqual(clamp["end"] - clamp["start"], 2, delta=0.05)
        left = json.loads(out["dragLeft"])
        self.assertAlmostEqual(left["start"], 12 - 0.3, delta=0.04)
        right = json.loads(out["dragRight"])
        self.assertAlmostEqual(right["end"], 10 + 0.3, delta=0.04)
        # a cue moved by hand is locked, and saving the box again asks
        self.assertTrue(out["lockedAfterMove"])
        self.assertIn("made or moved by hand", out["question"])
        self.assertEqual(out["textAfterRefusal"], "The pan is hot. We add the sausage and wait for it to cook.")
        self.assertEqual(out["draftAfterRefusal"], "A different text.")
        self.assertEqual(out["textAfterConfirm"], "A different text.")
        self.assertEqual(json.loads(out["cuesAfterConfirm"]), ["A different text."])
        self.assertIn("numbers of film seconds", out["badTimeError"])
        self.assertEqual(json.loads(out["formsBefore"])[0]["form"], "srt")
        self.assertEqual(json.loads(out["formsAfter"]), ["burned", "srt"])
        self.assertEqual(json.loads(out["titles"])[-1][1:], ["Monday kitchen", "lower_third", 10, 13])
        self.assertIn("Monday kitchen", out["titleTable"])
        self.assertEqual(out["titleEdited"], "Tuesday")
        self.assertEqual(out["titlesAfterRemove"], 1)
        self.assertEqual((out["cuesAfterAdd"], out["cuesAfterRemove"]), (1, 0))
        self.assertEqual(out["caption"], "Now the pan is hot enough.")
        self.assertEqual(out["captionNone"], "")
        self.assertIn("film-4k.en.srt", out["srtMsg"])
        self.assertIn("OPENROUTER_API_KEY", out["transMsg"])
        self.assertTrue(out["transDisabled"])
        self.assertIn('id="transLang_pt-BR"', out["langs"])
        self.assertIn('id="transLang_zh-Hans"', out["langs"])


if __name__ == "__main__":
    unittest.main()
