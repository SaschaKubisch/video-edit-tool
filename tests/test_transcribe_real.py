"""Checks 4.1 and 4.2 with the real whisper-cli and the real model (VET_REAL_WHISPER=1 and VET_WHISPER_MODEL).
A sentence made with the macOS `say` command is transcribed; noise and a steady tone give no trusted
phrase. These start the program and load a 1.6 GB model, so they run only on request."""

import difflib
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import tools
import transcribe
from tests import media

REAL = os.environ.get("VET_REAL_WHISPER") == "1" and os.environ.get("VET_WHISPER_MODEL", "")
SENTENCE = "The pan is hot enough now. We can add the sausage and wait for it to cook."


@unittest.skipUnless(REAL and os.path.isfile(os.environ.get("VET_WHISPER_MODEL", "")) and shutil.which("say"),
                     "VET_REAL_WHISPER=1, VET_WHISPER_MODEL and the macOS say command are needed")
class RealWhisperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()

    def test_a_spoken_sentence_is_transcribed(self):
        aiff = self.root / "say.aiff"
        subprocess.run(["say", "-o", str(aiff), SENTENCE], check=True)
        wav = self.root / "say.wav"
        subprocess.run([tools.ffmpeg_path(), "-v", "error", "-y", "-i", str(aiff), "-ac", "2", "-ar", "48000", str(wav)],
                       check=True)
        t = transcribe.transcribe_clip(str(self.work), "SAY", str(wav))
        kept = " ".join(p["text"] for p in t["phrases"] if not p["dropped"])
        norm = lambda s: re.sub(r"[^a-z ]+", "", s.lower()).split()
        want, got = norm(SENTENCE), norm(kept)
        ratio = difflib.SequenceMatcher(a=want, b=got).ratio()
        print(f"\n  heard: {kept!r}  ({t['seconds']} s of sound in {t['elapsed_s']} s)")
        self.assertGreaterEqual(ratio, 0.8, f"heard {kept!r}")

    def test_noise_and_a_tone_give_no_phrase_with_text(self):
        rate = 48000
        rng = np.random.default_rng(1)
        t = np.arange(30 * rate) / rate
        x = 0.01 * rng.standard_normal(len(t)) + 0.05 * np.sin(2 * np.pi * 440 * t)
        wav = self.root / "noise.wav"
        media.write_wav(wav, np.stack([x, x], axis=-1), rate)
        tr = transcribe.transcribe_clip(str(self.work), "NOISE", str(wav))
        kept = [p["text"] for p in tr["phrases"] if not p["dropped"]]
        print(f"\n  phrases: {[(p['text'], p['dropped']) for p in tr['phrases']]}")
        self.assertEqual(kept, [])


if __name__ == "__main__":
    unittest.main()
