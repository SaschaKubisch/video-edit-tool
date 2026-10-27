"""Check 4.1 to 4.3: automatic subtitles from the sound (SPECS 9.2, 9.3). A fake whisper-cli (tests/fake_whisper.py,
started as a real program) stands in for whisper.cpp; the WAV files are synthetic. Tests of the real
program are in tests/test_transcribe_real.py."""

import copy
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

import config
import textlayout
import timeline
import transcribe
import words
from tests import media

FPS = (30000, 1001)
HERE = Path(__file__).resolve().parent


def phrase(a, b, text, p=0.9):
    ws = text.split()
    step = (b - a) / len(ws)
    return {"from": a, "to": b, "text": text, "p": p,
            "words": [[w, a + i * step, a + (i + 1) * step, p] for i, w in enumerate(ws)]}


def speechlike_wav(path, seconds, bursts, rate=48000):
    """Quiet noise, with a loud tone over each (start, end) in `bursts`: the sound of a voice for the filter."""
    rng = np.random.default_rng(3)
    x = 0.002 * rng.standard_normal(int(seconds * rate))
    t = np.arange(len(x)) / rate
    for a, b in bursts:
        m = (t >= a) & (t < b)
        x[m] += 0.25 * np.sin(2 * np.pi * 220 * t[m]) * (0.7 + 0.3 * np.sin(2 * np.pi * 4 * t[m]))
    media.write_wav(path, np.stack([x, x], axis=-1), rate)


class FakeWhisperCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.work = self.root / "work"
        self.work.mkdir()
        self.model = self.root / "ggml-fake.bin"
        self.model.write_bytes(b"model")
        self.log = self.root / "calls.log"
        self.script = self.root / "script.json"
        self.script.write_text("{}")
        env = {"VET_WHISPER_BIN": str(HERE / "fake_whisper.py"), "VET_WHISPER_MODEL": str(self.model),
               "FAKE_WHISPER_LOG": str(self.log), "FAKE_WHISPER_SCRIPT": str(self.script)}
        self._old = {k: os.environ.get(k) for k in list(env) + ["VET_WHISPER_VAD_MODEL", "FAKE_WHISPER_FAIL"]}
        os.environ.update(env)
        os.environ.pop("VET_WHISPER_VAD_MODEL", None)
        os.environ.pop("FAKE_WHISPER_FAIL", None)
        self.addCleanup(self._restore)
        self._factory = words.RULES_FACTORY
        words.RULES_FACTORY = lambda data: textlayout.char_rules(42)
        self.addCleanup(setattr, words, "RULES_FACTORY", self._factory)
        os.chmod(HERE / "fake_whisper.py", os.stat(HERE / "fake_whisper.py").st_mode | stat.S_IXUSR)

    def _restore(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def hears(self, seconds, *phrases):
        script = json.loads(self.script.read_text())
        script[str(seconds)] = list(phrases)
        self.script.write_text(json.dumps(script))

    def wav(self, name, seconds, bursts):
        path = self.root / f"{name}.wav"
        speechlike_wav(path, seconds, bursts)
        return str(path)

    def film(self, clips, segments, offset=0.25):
        sources = {}
        for name, wav, frames in clips:
            sources[name] = {"original_video": "/o/x.braw", "preview_copy": "/o/x.mp4", "audio": wav,
                             "audio_offset_s": offset, "frames": frames, "fps": {"num": FPS[0], "den": FPS[1]},
                             "size": {"width": 640, "height": 360}, "copy_lut_sha256": "0" * 64}
        return timeline.Timeline({
            "version": 1, "kind": "film",
            "film": {"fps": {"num": FPS[0], "den": FPS[1]}, "frame": {"width": 640, "height": 360}, "fit": "fit"},
            "sources": sources,
            "segments": [{"id": f"s{i + 1:03d}", "source": s, "in": a, "out": b}
                         for i, (s, a, b) in enumerate(segments)],
            "look": {"base": {"mode": "none"}},
            "audio": {"sample_rate": 48000, "loudness": {"mode": "off"}}})


class CommandTests(FakeWhisperCase):
    def test_the_command_line(self):
        cmd = transcribe.command("/x/a.wav", "/x/out")
        self.assertEqual(cmd[0], str(HERE / "fake_whisper.py"))
        pairs = dict(zip(cmd[1::2], cmd[2::2]))
        self.assertEqual(pairs["-m"], str(self.model))
        self.assertEqual(pairs["-l"], "en")
        self.assertEqual(pairs["-mc"], "0")               # no text carried over: stops the same phrase repeating
        self.assertIn("-ojf", cmd)
        self.assertIn("-sns", cmd)
        self.assertNotIn("--vad", cmd)

    def test_a_voice_activity_model_is_used_when_the_owner_provides_one(self):
        vad = self.root / "vad.bin"
        vad.write_bytes(b"v")
        os.environ["VET_WHISPER_VAD_MODEL"] = str(vad)
        cmd = transcribe.command("/x/a.wav", "/x/out")
        self.assertIn("--vad", cmd)
        self.assertEqual(cmd[cmd.index("-vm") + 1], str(vad))
        os.environ["VET_WHISPER_VAD_MODEL"] = str(self.root / "missing.bin")
        with self.assertRaises(transcribe.WhisperError):
            transcribe.command("/x/a.wav", "/x/out")

    def test_no_model_and_no_program_say_what_to_set(self):
        os.environ.pop("VET_WHISPER_MODEL")
        with self.assertRaises(transcribe.WhisperError) as ctx:
            transcribe.model_path()
        self.assertIn("VET_WHISPER_MODEL", str(ctx.exception))
        os.environ["VET_WHISPER_MODEL"] = str(self.root / "nope.bin")
        with self.assertRaises(transcribe.WhisperError):
            transcribe.model_path()
        os.environ["VET_WHISPER_BIN"] = str(self.root / "nope")
        with self.assertRaises(transcribe.WhisperError) as ctx:
            transcribe.whisper_bin()
        self.assertIn("VET_WHISPER_BIN", str(ctx.exception))


class ParseAndFilterTests(FakeWhisperCase):
    def test_parse_builds_words_from_tokens(self):
        raw = {"transcription": [{"offsets": {"from": 1000, "to": 3000}, "text": " Hello there.", "tokens": [
            {"text": "[_BEG_]", "offsets": {"from": 1000, "to": 1000}, "p": 1.0},
            {"text": " Hel", "offsets": {"from": 1100, "to": 1300}, "p": 0.8},
            {"text": "lo", "offsets": {"from": 1300, "to": 1600}, "p": 0.9},
            {"text": " there", "offsets": {"from": 1700, "to": 2200}, "p": 0.7},
            {"text": ".", "offsets": {"from": 2200, "to": 2300}, "p": 0.99}]}]}
        (p,) = transcribe.parse(raw)
        self.assertEqual((p.start_s, p.end_s, p.text), (1.0, 3.0, "Hello there."))
        self.assertEqual([w[0] for w in p.words], ["Hello", "there."])
        self.assertAlmostEqual(p.words[0][1], 1.1)
        self.assertAlmostEqual(p.words[0][2], 1.6)
        self.assertAlmostEqual(p.prob, (0.8 + 0.9 + 0.7 + 0.99) / 4)

    def ph(self, text, prob=0.9, activity=0.8):
        return transcribe.Phrase(0, 2, text, prob, activity, [])

    def test_the_reasons(self):
        reasons = lambda text, **k: transcribe.drop_reason(self.ph(text, **k))
        self.assertEqual(reasons("Now the pan is hot enough."), "")
        self.assertIn("brackets", reasons("*Pain*"))
        self.assertIn("brackets", reasons("[Music]"))
        self.assertIn("brackets", reasons("(upbeat music)"))
        self.assertIn("not sure", reasons("Pass me the salt", prob=0.4))
        self.assertIn("noise floor", reasons("Pass me the salt", activity=0.02))
        self.assertIn("stock phrase", reasons("Thank you."))
        self.assertIn("stock phrase", reasons("I'll see you next time"))
        self.assertIn("no words", reasons("..."))
        self.assertEqual(transcribe.drop_reason(self.ph("Thank you."), stock=False), "")

    def test_a_phrase_that_repeats_is_a_loop(self):
        ps = [transcribe.Phrase(i * 2, i * 2 + 2, "Let's do it a little bit more.", 0.9, 0.8, []) for i in range(5)]
        ps.insert(0, transcribe.Phrase(0, 1, "Something real here.", 0.9, 0.8, []))
        transcribe.mark_dropped(ps)
        self.assertEqual(ps[0].dropped, "")
        self.assertTrue(all("loop" in p.dropped for p in ps[1:]))
        two = [transcribe.Phrase(0, 2, "Again please.", 0.9, 0.8, []), transcribe.Phrase(2, 4, "Again please.", 0.9, 0.8, [])]
        transcribe.mark_dropped(two)
        self.assertEqual([p.dropped for p in two], ["", ""])

    def test_one_phrase_of_a_few_words_over_and_over(self):
        text = "I love you " * 8
        p = transcribe.Phrase(0, 20, text.strip(), 0.9, 0.8, [(w, i, i + 1, 0.9) for i, w in enumerate(text.split())])
        transcribe.mark_dropped([p])
        self.assertIn("repeated", p.dropped)


class TranscriptTests(FakeWhisperCase):
    def test_a_clip_is_transcribed_once_and_kept(self):
        wav = self.wav("a", 12, [(2.0, 5.0)])
        self.hears(12, phrase(2.0, 5.0, "Now the pan is hot enough."), phrase(7.0, 9.0, "Thank you."))
        t = transcribe.transcribe_clip(str(self.work), "A", wav)
        self.assertFalse(t["cached"])
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual([p["dropped"] == "" for p in t["phrases"]], [True, False])
        self.assertTrue((self.work / "analysis" / "A" / "transcript.json").is_file())
        self.assertEqual(t["model"], "ggml-fake.bin")
        again = transcribe.transcribe_clip(str(self.work), "A", wav)
        self.assertTrue(again["cached"])
        self.assertEqual(len(self.calls()), 1)
        transcribe.transcribe_clip(str(self.work), "A", wav, redo=True)
        self.assertEqual(len(self.calls()), 2)

    def test_the_sound_under_a_phrase_decides_whether_it_is_trusted(self):
        wav = self.wav("a", 12, [(2.0, 5.0)])
        self.hears(12, phrase(2.0, 5.0, "Speech over a voice."), phrase(7.0, 10.0, "Words over silence here."))
        t = transcribe.transcribe_clip(str(self.work), "A", wav)
        self.assertGreater(t["phrases"][0]["activity"], 0.5)
        self.assertLess(t["phrases"][1]["activity"], 0.1)
        self.assertEqual(t["phrases"][0]["dropped"], "")
        self.assertIn("noise floor", t["phrases"][1]["dropped"])

    def test_a_changed_wav_or_model_is_transcribed_again(self):
        wav = self.wav("a", 12, [(2.0, 5.0)])
        self.hears(12, phrase(2.0, 5.0, "Now the pan is hot enough."))
        transcribe.transcribe_clip(str(self.work), "A", wav)
        speechlike_wav(Path(wav), 12, [(2.0, 6.0)])
        transcribe.transcribe_clip(str(self.work), "A", wav)
        self.assertEqual(len(self.calls()), 2)
        other = self.root / "ggml-other.bin"
        other.write_bytes(b"other model")
        os.environ["VET_WHISPER_MODEL"] = str(other)
        transcribe.transcribe_clip(str(self.work), "A", wav)
        self.assertEqual(len(self.calls()), 3)

    def test_the_program_failing_is_an_error_with_its_message(self):
        wav = self.wav("a", 12, [])
        os.environ["FAKE_WHISPER_FAIL"] = "1"
        with self.assertRaises(transcribe.WhisperError) as ctx:
            transcribe.transcribe_clip(str(self.work), "A", wav)
        self.assertIn("simulated failure", str(ctx.exception))
        self.assertFalse((self.work / "analysis" / "A" / "transcript.json").exists())

    def test_no_scratch_files_are_left(self):
        wav = self.wav("a", 12, [(2.0, 5.0)])
        self.hears(12, phrase(2.0, 5.0, "Now the pan is hot enough."))
        transcribe.transcribe_clip(str(self.work), "A", wav)
        self.assertEqual([p.name for p in (self.work / "analysis").iterdir()], ["A"])

    def test_the_wav_is_prepared_as_mono_16k(self):
        wav = self.wav("a", 3, [])
        out = self.root / "p.wav"
        seconds = transcribe.prepare_wav(wav, str(out))
        info = media.probe(out)
        s = info["streams"][0]
        self.assertEqual((s["channels"], int(s["sample_rate"]), s["codec_name"]), (1, 16000, "pcm_s16le"))
        self.assertAlmostEqual(seconds, 3.0, delta=0.01)


class CueTests(FakeWhisperCase):
    def setUp(self):
        super().setUp()
        # clip A: 12 s of WAV; the scene s001 uses frames 30..330 of the picture (about 10 s)
        self.wav_a = self.wav("a", 12, [(2.0, 5.0), (7.0, 9.0)])
        self.hears(12, phrase(2.0, 5.0, "Now the pan is hot enough."), phrase(7.0, 9.0, "We add the sausage."))
        self.tl = self.film([("A", self.wav_a, 400)], [("A", 30, 330)], offset=0.25)

    def run_auto(self, tl=None, **kw):
        return transcribe.auto_subtitles(tl or self.tl, str(self.work), log=lambda s: None, **kw)

    def test_cues_are_in_the_time_of_the_scene(self):
        data, report = self.run_auto()
        cues = data["subtitles"]["cues"]
        self.assertEqual([c["text"] for c in cues], ["Now the pan is hot enough.", "We add the sausage."])
        # the scene's sound starts in the WAV at in/fps + offset = 30 * 1001 / 30000 + 0.25 = 1.251 s
        s0 = 30 * 1001 / 30000 + 0.25
        self.assertAlmostEqual(cues[0]["start_s"], 2.0 - s0, delta=0.04)
        self.assertAlmostEqual(cues[1]["start_s"], 7.0 - s0, delta=0.04)
        self.assertEqual({c["origin"] for c in cues}, {"auto"})
        self.assertEqual([c["id"] for c in cues], ["c001", "c002"])
        self.assertEqual(report["cues"], 2)
        self.assertEqual(timeline.has_errors(timeline.validate(data, file_checks=False)), False)

    def test_an_offset_the_owner_typed_moves_the_cues(self):
        tl = copy.deepcopy(self.tl)
        tl.data["sources"]["A"]["audio_offset_hand_s"] = 0.75
        data, _ = self.run_auto(tl)
        s0 = 30 * 1001 / 30000 + 0.75
        self.assertAlmostEqual(data["subtitles"]["cues"][0]["start_s"], 2.0 - s0, delta=0.04)

    def test_a_cue_lasts_long_enough_to_read_and_has_a_gap(self):
        data, _ = self.run_auto()
        a, b = data["subtitles"]["cues"]
        self.assertGreaterEqual(a["end_s"] - a["start_s"], config.SUBTITLE_MIN_SEC - 0.04)
        self.assertGreaterEqual(b["start_s"] - a["end_s"], config.SUBTITLE_MIN_GAP_FRAMES * 1001 / 30000 - 0.001)

    def test_a_second_run_does_not_duplicate(self):
        data, _ = self.run_auto()
        again, report = self.run_auto(timeline.Timeline(data))
        self.assertEqual(len(again["subtitles"]["cues"]), 2)
        self.assertEqual([c["text"] for c in again["subtitles"]["cues"]], [c["text"] for c in data["subtitles"]["cues"]])
        self.assertEqual(len(self.calls()), 1)              # the transcript was kept
        self.assertTrue(all(c["cached"] for c in report["clips"].values()))

    def test_cues_changed_by_hand_survive_a_second_run(self):
        data, _ = self.run_auto()
        data["subtitles"]["cues"][0].update(text="Changed by hand.", locked=True)
        data["subtitles"]["cues"].append({"id": "c003", "anchor": "s001", "start_s": 8.5, "end_s": 9.5,
                                          "text": "A cue of my own.", "origin": "manual", "locked": True})
        again, report = self.run_auto(timeline.Timeline(data))
        texts = {c["text"] for c in again["subtitles"]["cues"]}
        self.assertIn("Changed by hand.", texts)
        self.assertIn("A cue of my own.", texts)
        self.assertIn("We add the sausage.", texts)
        self.assertNotIn("Now the pan is hot enough.", texts)       # it overlapped the locked cue
        self.assertEqual(report["scenes"]["s001"]["overlapping_cues_made_by_hand"], 1)
        self.assertEqual(timeline.has_errors(timeline.validate(again, file_checks=False)), False)

    def test_a_scene_with_a_text_of_its_own_is_left_alone(self):
        tl = copy.deepcopy(self.tl)
        tl.data["segments"][0]["text"] = "Typed by the owner."
        data, report = self.run_auto(tl)
        self.assertEqual(data.get("subtitles", {}).get("cues", []), [])
        self.assertIn("text of its own", report["scenes"]["s001"]["skipped"])

    def test_a_phrase_that_the_scene_cuts_in_the_middle_is_dropped_and_counted(self):
        self.hears(12, phrase(0.0, 3.0, "Said before the scene started."), phrase(7.0, 9.0, "We add the sausage."))
        data, report = self.run_auto()
        self.assertEqual([c["text"] for c in data["subtitles"]["cues"]], ["We add the sausage."])
        self.assertEqual(report["scenes"]["s001"]["cut_mid_phrase"], 1)

    def test_a_phrase_mostly_inside_the_scene_is_kept_and_clipped(self):
        self.hears(12, phrase(0.9, 3.0, "Said just before the scene."))
        data, _ = self.run_auto()
        c = data["subtitles"]["cues"][0]
        self.assertEqual(c["start_s"], 0.0)

    def test_two_scenes_of_one_clip_share_one_transcript(self):
        tl = self.film([("A", self.wav_a, 400)], [("A", 30, 150), ("A", 180, 330)])
        data, report = self.run_auto(tl)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(list(report["clips"]), ["A"])
        self.assertEqual({c["anchor"] for c in data["subtitles"]["cues"]} <= {"s001", "s002"}, True)

    def test_only_some_scenes(self):
        tl = self.film([("A", self.wav_a, 400)], [("A", 30, 150), ("A", 180, 330)])
        data, report = self.run_auto(tl, only=["s002"])
        self.assertEqual({c["anchor"] for c in data["subtitles"]["cues"]}, {"s002"})
        with self.assertRaises(transcribe.WhisperError):
            self.run_auto(tl, only=["s099"])

    def test_a_scene_that_is_switched_off_is_not_transcribed(self):
        tl = self.film([("A", self.wav_a, 400)], [("A", 30, 330)])
        tl.data["segments"][0]["enabled"] = False
        tl.data["segments"].append({"id": "s002", "source": "A", "in": 340, "out": 390})
        data, report = self.run_auto(tl)
        self.assertNotIn("s001", report["scenes"])

    def test_a_long_phrase_becomes_several_cues_timed_by_its_words(self):
        text = ("When the pan is hot enough and the oil starts to move we add the sausage and turn it every "
                "minute until it is brown on all sides")
        self.hears(12, phrase(1.5, 9.0, text))
        data, _ = self.run_auto()
        cues = data["subtitles"]["cues"]
        self.assertGreaterEqual(len(cues), 2)
        self.assertEqual(timeline.has_errors(timeline.validate(data, file_checks=False)), False)
        for a, b in zip(cues, cues[1:]):
            self.assertLessEqual(a["end_s"], b["start_s"])
        self.assertEqual(" ".join(c["text"].replace("\n", " ") for c in cues), text)

    def test_the_report_lists_what_was_dropped_and_why(self):
        self.hears(12, phrase(2.0, 5.0, "Now the pan is hot enough."), phrase(7.0, 9.0, "Thank you."),
                   phrase(10.0, 11.0, "*Pain*"))
        _, report = self.run_auto()
        reasons = {d["text"]: d["reason"] for d in report["dropped"]}
        self.assertIn("stock phrase", reasons["Thank you."])
        self.assertIn("brackets", reasons["*Pain*"])
        self.assertEqual(report["clips"]["A"]["phrases"], 3)
        self.assertEqual(report["clips"]["A"]["kept"], 1)


if __name__ == "__main__":
    unittest.main()
