"""
Automatic subtitles from the recorder's sound: whisper.cpp (SPECS 9.2; decision D12, "large-v3-turbo").

`subtitles auto` runs `whisper-cli` on the WAV of each clip the film uses. The WAV is the film's sound:
a scene's sound is the WAV at the clip's offset (`Timeline.source_audio_start`, which includes an offset the
owner typed), so a phrase heard at WAV time t is at t - start of the scene's sound in the scene. The
transcript of a clip does not depend on how the clip is cut, so it is made once and kept in
`analysis/<clip>/transcript.json`; trimming, moving or re-running only makes the cues again.

Whisper models write stock phrases ("Thank you.", "*Pain*") for sound without speech [measured on the first
shoot, SPECS 9.0]. The output is filtered, and everything dropped is listed with the reason in the report:
phrases in brackets or asterisks, phrases whose words Whisper was not sure of, phrases over sound that is
barely above the noise floor, the same phrase over and over, and a short list of stock phrases. A voice-
activity model (`VET_WHISPER_VAD_MODEL`) is used when the owner provides one.

The cues are made by words.py from the phrases: split to fit two lines, timed by the words.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import config
import fsutil
import tools
import words

TRANSCRIPT_VERSION = 1
SHOTCUT_WHISPER = "/Applications/Shotcut.app/Contents/MacOS/whisper-cli"
CUE_MIN_OVERLAP = 0.6              # a cue with less of its time inside the scene is dropped (SPECS 9.3)
STOCK_PHRASES = {
    "thank you", "thanks", "thank you for watching", "thanks for watching", "bye", "bye bye", "goodbye",
    "you", "ok", "okay", "i'll see you next time", "see you next time", "see you in the next video",
    "we'll be right back", "please subscribe", "subscribe", "the end", "music", "applause",
    "subtitles by the amara.org community", "so", "oh", "uh", "um", "hmm", "mm", "mhm"}


class WhisperError(Exception):
    """Whisper cannot run: no program, no model, or the program failed. The message says what to set."""


# ── Where the program and the models are ────────────────────────

def whisper_bin() -> str:
    explicit = os.environ.get("VET_WHISPER_BIN")
    if explicit:
        if not os.path.isfile(explicit):
            raise WhisperError(f"VET_WHISPER_BIN names {explicit}, which does not exist")
        return explicit
    if os.path.isfile(SHOTCUT_WHISPER):
        return SHOTCUT_WHISPER
    found = shutil.which("whisper-cli")
    if found:
        return found
    raise WhisperError("No whisper-cli program. Set VET_WHISPER_BIN, or install Shotcut (its folder holds "
                       "whisper-cli).")


def model_path() -> str:
    path = os.environ.get("VET_WHISPER_MODEL", "")
    if not path:
        raise WhisperError("No Whisper model. Set VET_WHISPER_MODEL to the model file "
                           "(ggml-large-v3-turbo.bin, decision D12).")
    if not os.path.isfile(path):
        raise WhisperError(f"The Whisper model {path} does not exist. VET_WHISPER_MODEL must name the file.")
    return path


def vad_model_path():
    path = os.environ.get("VET_WHISPER_VAD_MODEL", "")
    if not path:
        return None
    if not os.path.isfile(path):
        raise WhisperError(f"The voice-activity model {path} does not exist (VET_WHISPER_VAD_MODEL).")
    return path


# ── Running whisper ─────────────────────────────────────────────

@dataclass
class Phrase:
    start_s: float                 # in the time of the WAV
    end_s: float
    text: str
    prob: float = 1.0              # the mean probability of the words
    activity: float = 1.0          # the share of the phrase that is above the noise floor
    words: list = field(default_factory=list)       # [text, start_s, end_s, prob]
    dropped: str = ""              # why the filter dropped it; "" if it is kept

    def as_dict(self) -> dict:
        return {"start_s": round(self.start_s, 3), "end_s": round(self.end_s, 3), "text": self.text,
                "prob": round(self.prob, 3), "activity": round(self.activity, 3),
                "words": [[w, round(a, 3), round(b, 3), round(p, 3)] for w, a, b, p in self.words],
                "dropped": self.dropped}

    @classmethod
    def from_dict(cls, d: dict) -> "Phrase":
        return cls(d["start_s"], d["end_s"], d["text"], d.get("prob", 1.0), d.get("activity", 1.0),
                   [tuple(w) for w in d.get("words", [])], d.get("dropped", ""))


def prepare_wav(src: str, dest: str, ffmpeg: str = None) -> float:
    """The WAV as mono 16 kHz 16-bit, the form whisper.cpp reads. Returns its length in seconds."""
    ffmpeg = ffmpeg or tools.ffmpeg_path()
    r = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", str(dest)], capture_output=True, text=True)
    if r.returncode != 0:
        raise WhisperError(f"ffmpeg could not read {src}: {r.stderr.strip()[-300:]}")
    return os.path.getsize(dest) / 32000.0


def command(wav16: str, out_prefix: str, model: str = None, threads: int = None, vad: str = None) -> list:
    """The whisper-cli command line: English, JSON with token times, no text carried from one window to
    the next (that stops the same phrase being repeated), non-speech tokens suppressed."""
    cmd = [whisper_bin(), "-m", model or model_path(), "-f", wav16, "-l", "en", "-ojf", "-of", out_prefix,
           "-np", "-t", str(threads or config.WHISPER_THREADS), "-mc", "0", "-sns"]
    vad = vad if vad is not None else vad_model_path()
    if vad:
        cmd += ["--vad", "-vm", vad]
    return cmd


def run_whisper(wav16: str, scratch: str, log=lambda s: None) -> dict:
    """Run whisper-cli on a 16 kHz WAV and return its JSON. Raises WhisperError."""
    prefix = os.path.join(scratch, "out")
    cmd = command(wav16, prefix)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise WhisperError(f"whisper-cli failed (exit {r.returncode}): {(r.stderr or r.stdout).strip()[-400:]}")
    try:
        with open(prefix + ".json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise WhisperError(f"whisper-cli wrote no readable JSON: {e}")


def _is_special(token: dict) -> bool:
    t = token.get("text", "")
    return t.startswith("[_") or t.startswith("<|")


def parse(raw: dict) -> list:
    """The phrases of whisper.cpp's JSON (output-json-full). Words are built from the tokens: a token
    that starts with a space begins a word."""
    out = []
    for seg in raw.get("transcription", []):
        text = seg.get("text", "").strip()
        a, b = seg["offsets"]["from"] / 1000.0, seg["offsets"]["to"] / 1000.0
        ws, probs = [], []
        for tok in seg.get("tokens", []):
            if _is_special(tok):
                continue
            t0, t1 = tok["offsets"]["from"] / 1000.0, tok["offsets"]["to"] / 1000.0
            probs.append(tok.get("p", 1.0))
            piece = tok["text"]
            if piece.startswith(" ") or not ws:
                ws.append([piece.strip(), t0, t1, [tok.get("p", 1.0)]])
            else:
                ws[-1][0] += piece
                ws[-1][2] = t1
                ws[-1][3].append(tok.get("p", 1.0))
        word_list = [(w, t0, t1, sum(p) / len(p)) for w, t0, t1, p in ws if w]
        if not text:
            continue
        out.append(Phrase(a, b, text, sum(probs) / len(probs) if probs else 1.0, 1.0, word_list))
    return out


# ── The sound under a phrase ────────────────────────────────────

def level_track(wav16: str) -> tuple:
    """(dB per 20 ms frame, noise floor in dB): the loudness track of a 16 kHz WAV and its 10th
    percentile, which stands for the noise floor of the recording."""
    import wave
    with wave.open(wav16) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768.0
    hop = 320
    k = len(x) // hop
    if k == 0:
        return np.zeros(0), -90.0
    rms = np.sqrt((x[:k * hop].reshape(k, hop) ** 2).mean(axis=1))
    db = 20.0 * np.log10(rms + 1e-9)
    return db, float(np.percentile(db, 10))


def activity(db, floor: float, a: float, b: float, margin_db: float = 8.0) -> float:
    """The share of the time a..b (seconds) where the sound is more than `margin_db` over the floor."""
    i, j = int(a / 0.02), max(int(a / 0.02) + 1, int(b / 0.02))
    seg = db[i:j]
    return float((seg > floor + margin_db).mean()) if len(seg) else 0.0


# ── Filtering ───────────────────────────────────────────────────

def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9' ]+", "", text.lower()).strip()


def drop_reason(p: Phrase, stock: bool = True) -> str:
    """Why a phrase is not trusted ("" when it is): see the module text."""
    text = p.text.strip()
    if not re.search(r"[A-Za-z]{2}", text):
        return "no words"
    if re.fullmatch(r"[\[\(\*♪].*[\]\)\*♪]", text):
        return "a sound description in brackets or asterisks, not speech"
    if p.prob < config.WHISPER_MIN_MEAN_PROB:
        return f"Whisper was not sure of the words (mean probability {p.prob:.2f})"
    if p.activity < config.WHISPER_MIN_ACTIVITY:
        return f"the sound under it is at the noise floor (activity {p.activity:.2f})"
    if stock and _normal(text) in STOCK_PHRASES:
        return "a stock phrase that Whisper writes for sound without speech"
    return ""


def mark_dropped(phrases: list, stock: bool = True) -> list:
    """Sets `dropped` on every phrase that is not trusted. A phrase that repeats the one before it
    config.WHISPER_REPEAT_RUN times or more is a loop and all of the run is dropped. Returns `phrases`."""
    for p in phrases:
        p.dropped = drop_reason(p, stock)
    run = 1
    for i in range(1, len(phrases) + 1):
        same = i < len(phrases) and _normal(phrases[i].text) == _normal(phrases[i - 1].text)
        if same:
            run += 1
            continue
        if run >= config.WHISPER_REPEAT_RUN:
            for p in phrases[i - run:i]:
                p.dropped = p.dropped or "the same phrase over and over (a loop of the model)"
        run = 1
    # a long phrase that is mostly one word again and again
    for p in phrases:
        ws = [w[0].lower().strip(".,!?") for w in p.words]
        if len(ws) >= 8 and len(set(ws)) <= max(2, len(ws) // 5):
            p.dropped = p.dropped or "a few words repeated over and over (a loop of the model)"
        if not p.dropped and len(p.text) > 40 and len(zlib.compress(p.text.encode())) / len(p.text.encode()) < 0.3:
            p.dropped = "text that repeats itself (a loop of the model)"
    return phrases


# ── The transcript of a clip ────────────────────────────────────

def transcript_path(work_dir: str, source_id: str) -> str:
    return os.path.join(work_dir, "analysis", source_id, "transcript.json")


def _identity(wav: str, model: str) -> dict:
    st = os.stat(wav)
    ms = os.stat(model)
    return {"wav": wav, "wav_size": st.st_size, "wav_mtime_ns": st.st_mtime_ns,
            "model": os.path.basename(model), "model_size": ms.st_size,
            "vad": bool(vad_model_path()), "version": TRANSCRIPT_VERSION}


def load_transcript(work_dir: str, source_id: str, wav: str, model: str):
    """The kept transcript of a clip when it was made from this WAV with this model, else None."""
    try:
        with open(transcript_path(work_dir, source_id), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    ident = _identity(wav, model)
    if any(data.get(k) != v for k, v in ident.items()):
        return None
    return data


def transcribe_clip(work_dir: str, source_id: str, wav: str, redo: bool = False, stock: bool = True,
                    log=lambda s: None) -> dict:
    """The transcript of the clip's WAV (made once, kept in analysis/<clip>/transcript.json): the phrases
    with the words, how sure Whisper was and how much of the sound is above the noise floor, and which the
    filter dropped. Returns the transcript dict (with `cached` True when it was not made now)."""
    model = model_path()
    if not redo:
        old = load_transcript(work_dir, source_id, wav, model)
        if old is not None:
            phrases = mark_dropped([Phrase.from_dict(p) for p in old["phrases"]], stock)
            old["phrases"] = [p.as_dict() for p in phrases]
            old["cached"] = True
            return old
    started = time.time()
    os.makedirs(os.path.join(work_dir, "analysis"), exist_ok=True)
    scratch = tempfile.mkdtemp(prefix="whisper-", dir=os.path.join(work_dir, "analysis"))
    try:
        wav16 = os.path.join(scratch, "speech-16k.wav")
        seconds = prepare_wav(wav, wav16)
        raw = run_whisper(wav16, scratch, log)
        phrases = parse(raw)
        db, floor = level_track(wav16)
        for p in phrases:
            p.activity = activity(db, floor, p.start_s, p.end_s)
        mark_dropped(phrases, stock)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    elapsed = time.time() - started
    data = {**_identity(wav, model), "language": "en", "seconds": round(seconds, 2),
            "elapsed_s": round(elapsed, 2), "noise_floor_db": round(floor, 1),
            "phrases": [p.as_dict() for p in phrases], "cached": False}
    fsutil.write_json_atomic(transcript_path(work_dir, source_id), data)
    return data


# ── Cues from phrases ───────────────────────────────────────────

def phrase_cues(p: Phrase, rules) -> list:
    """[(start_s, end_s, text)] in WAV time for one trusted phrase: the text split to fit, each cue timed by
    its first and last word (or by its share of the characters when the words are missing)."""
    text = p.text.strip()
    pieces, _ = words.split_cues(text, rules)
    if not pieces:
        return []
    ws = list(p.words)
    counts = [len(c.replace("\n", " ").split()) for c in pieces]
    if ws and sum(counts) == len(ws) and all(w[2] >= w[1] for w in ws):
        out, at = [], 0
        for c, n in zip(pieces, counts):
            first, last = ws[at], ws[at + n - 1]
            out.append((first[1], max(last[2], first[1] + 0.2), c))
            at += n
        return out
    total = sum(words._chars(c) for c in pieces)
    out, t = [], p.start_s
    for c in pieces:
        d = (p.end_s - p.start_s) * words._chars(c) / total
        out.append((t, t + d, c))
        t += d
    return out


def retime(cues: list, scene_s: float, fps, min_s=None, gap=None) -> list:
    """Cues [(start, end, text)] in the time of the scene, in order: each lasts at least the time to read it
    where the next cue allows, and at most config.SUBTITLE_MAX_SEC; there is a gap before the next cue; times fall on
    frames. Returns [(start, end, text)]."""
    from fractions import Fraction
    fps = Fraction(fps)
    frame = float(1 / fps)
    gap = max(config.SUBTITLE_GAP_SEC, config.SUBTITLE_MIN_GAP_FRAMES * frame) if gap is None else gap
    out = []
    n = len(cues)
    for i, (a, b, text) in enumerate(cues):
        need = max(config.SUBTITLE_MIN_SEC, words._chars(text) / config.SUBTITLE_MAX_CPS)
        nxt = cues[i + 1][0] if i + 1 < n else scene_s + gap
        b = max(b, min(a + need, nxt - gap))
        b = min(b, a + config.SUBTITLE_MAX_SEC, scene_s, nxt - gap)
        if b - a < 0.2:
            continue
        out.append((round(round(a / frame) * frame, 4), round(round(b / frame) * frame, 4), text))
    return out


def scene_cues(tl, seg: dict, transcript: dict, rules, report: dict) -> list:
    """The cues of one scene from the clip's transcript: [(start_s, end_s, text)] from the start of the
    scene. A cue with less than CUE_MIN_OVERLAP of its time inside the scene is dropped and counted."""
    rate = tl.sample_rate
    s0 = tl.source_audio_start(seg) / rate           # WAV time at which the scene's sound starts
    length = float((seg["out"] - seg["in"]) / tl.fps)
    cues = []
    for d in transcript["phrases"]:
        if d.get("dropped"):
            continue
        p = Phrase.from_dict(d)
        for a, b, text in phrase_cues(p, rules):
            a, b = a - s0, b - s0
            inside = max(0.0, min(b, length) - max(a, 0.0))
            if b <= 0 or a >= length or inside < CUE_MIN_OVERLAP * (b - a):
                report["cut_mid_phrase"] = report.get("cut_mid_phrase", 0) + (1 if b > 0 and a < length else 0)
                continue
            cues.append((max(a, 0.0), min(b, length), text))
    return retime(cues, length, tl.fps)


def auto_subtitles(tl, work_dir: str, only=None, redo: bool = False, stock: bool = True, rules=None,
                   log=lambda s: None, progress=None) -> tuple:
    """Whisper subtitles for the scenes of the film. Returns (new data, report).

    - clips: every clip an enabled scene (of `only`, a list of scene ids, when given) uses, once each;
    - a scene that has a text of its own (words.set_scene_text) is left alone;
    - the cues of the scene that an earlier run made and nobody changed (origin auto, not locked) are
      replaced, so a second run does not duplicate; cues made or changed by hand stay, and a new cue that
      would overlap one of them is dropped and counted;
    - the report lists the phrases that were dropped and why, the time, and the speed.
    """
    import copy
    data = copy.deepcopy(tl.data)
    rules = rules or words.RULES_FACTORY(data)
    segments = [s for s in data["segments"] if s.get("enabled", True) and (only is None or s["id"] in only)]
    if only is not None:
        missing = set(only) - {s["id"] for s in data["segments"]}
        if missing:
            raise WhisperError("no scene " + ", ".join(sorted(missing)))
    clips = []
    for s in segments:
        if s["source"] not in clips:
            clips.append(s["source"])
    report = {"model": os.path.basename(model_path()), "clips": {}, "scenes": {}, "dropped": [], "seconds": 0.0,
              "transcribe_s": 0.0, "audio_s": 0.0, "cues": 0}
    started = time.time()
    transcripts = {}
    for n, cid in enumerate(clips, start=1):
        wav = data["sources"][cid]["audio"]
        t0 = time.time()
        tr = transcribe_clip(work_dir, cid, wav, redo=redo, stock=stock, log=log)
        transcripts[cid] = tr
        report["clips"][cid] = {"seconds": tr["seconds"], "phrases": len(tr["phrases"]),
                                "kept": sum(1 for p in tr["phrases"] if not p["dropped"]),
                                "cached": tr["cached"], "elapsed_s": round(time.time() - t0, 2)}
        if not tr["cached"]:
            report["transcribe_s"] += tr["elapsed_s"]
            report["audio_s"] += tr["seconds"]
        for p in tr["phrases"]:
            if p["dropped"]:
                report["dropped"].append({"clip": cid, "start_s": p["start_s"], "end_s": p["end_s"],
                                          "text": p["text"], "reason": p["dropped"]})
        log(f"[{n}/{len(clips)}] {cid}: {report['clips'][cid]['kept']} of {len(tr['phrases'])} phrases kept"
            + (" (kept from an earlier run)" if tr["cached"] else f" in {tr['elapsed_s']:.0f} s"))
        if progress:
            progress(n, len(clips))
    subs = data.setdefault("subtitles", {"language": config.SUBTITLE_LANGUAGE, "cues": []})
    new_cues = []
    for seg in segments:
        sid = seg["id"]
        if seg.get("text", "").strip():
            report["scenes"][sid] = {"skipped": "the scene has a text of its own"}
            continue
        mine = {"cut_mid_phrase": 0}
        found = scene_cues(tl, seg, transcripts[seg["source"]], rules, mine)
        # replace this scene's earlier automatic cues; keep everything else
        subs["cues"] = [c for c in subs["cues"]
                        if not (c.get("anchor") == sid and c.get("origin") == "auto" and not c.get("locked"))]
        kept_here = [c for c in subs["cues"] if c.get("anchor") == sid]
        placed, clashed = [], 0
        for a, b, text in found:
            if any(a < c["end_s"] and b > c["start_s"] for c in kept_here):
                clashed += 1
                continue
            placed.append({"id": None, "anchor": sid, "start_s": a, "end_s": b, "text": text, "origin": "auto"})
        new_cues += placed
        report["scenes"][sid] = {"cues": len(placed), "cut_mid_phrase": mine["cut_mid_phrase"],
                                 "overlapping_cues_made_by_hand": clashed}
    words._assign_ids(data, new_cues)
    subs["cues"].extend(new_cues)
    words.sort_cues(data)
    report["cues"] = len(new_cues)
    report["seconds"] = round(time.time() - started, 2)
    report["transcribe_s"] = round(report["transcribe_s"], 2)
    report["audio_s"] = round(report["audio_s"], 2)
    if report["audio_s"] > 0:
        report["seconds_per_minute_of_audio"] = round(report["transcribe_s"] / (report["audio_s"] / 60.0), 2)
    return data, report
