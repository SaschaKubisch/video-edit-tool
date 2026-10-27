#!/usr/bin/env python3
"""A stand-in for whisper-cli (whisper.cpp) that the tests point VET_WHISPER_BIN at. It takes the
options the tool passes, checks them, and writes the JSON that `-ojf` writes. What it "hears" comes
from the JSON file named by FAKE_WHISPER_SCRIPT: {"<length of the WAV in whole seconds>": [phrase, ...]},
a phrase being {"from": s, "to": s, "text": str, "words": [[word, from, to, p], ...], "p": number}.
Every call is appended to the file named by FAKE_WHISPER_LOG."""

import json
import os
import sys
import wave

args = sys.argv[1:]
opts = {}
i = 0
flags = {"-ojf", "-np", "-sns", "--vad"}
while i < len(args):
    a = args[i]
    if a in flags:
        opts[a] = True
        i += 1
    else:
        opts[a] = args[i + 1]
        i += 2

with open(os.environ["FAKE_WHISPER_LOG"], "a") as log:
    log.write(json.dumps({"args": args}) + "\n")

if not os.path.isfile(opts["-m"]):
    sys.stderr.write("error: failed to load model\n")
    sys.exit(2)
with wave.open(opts["-f"]) as w:
    if (w.getnchannels(), w.getframerate(), w.getsampwidth()) != (1, 16000, 2):
        sys.stderr.write("error: audio must be 16 kHz mono 16-bit\n")
        sys.exit(3)
    seconds = round(w.getnframes() / 16000)
if os.environ.get("FAKE_WHISPER_FAIL"):
    sys.stderr.write("whisper: simulated failure\n")
    sys.exit(4)
script = json.load(open(os.environ["FAKE_WHISPER_SCRIPT"]))
transcription = []
for ph in script.get(str(seconds), []):
    tokens = [{"text": "[_BEG_]", "offsets": {"from": int(ph["from"] * 1000), "to": int(ph["from"] * 1000)},
               "id": 50365, "p": 1.0}]
    for word, a, b, p in ph.get("words", []):
        tokens.append({"text": " " + word, "offsets": {"from": int(a * 1000), "to": int(b * 1000)},
                       "id": 1000, "p": p})
    transcription.append({"offsets": {"from": int(ph["from"] * 1000), "to": int(ph["to"] * 1000)},
                          "text": " " + ph["text"], "tokens": tokens})
with open(opts["-of"] + ".json", "w") as f:
    json.dump({"systeminfo": "fake", "model": {}, "params": {"language": opts.get("-l")},
               "result": {"language": opts.get("-l")}, "transcription": transcription}, f)
