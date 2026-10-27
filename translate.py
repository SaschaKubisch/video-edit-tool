"""
Translating the subtitles (SPECS 9.9; the owner's answers of 2026-10-27).

`translate_film` takes the English cues that are in the film, sends them to an OpenRouter model, and
writes one SRT file per language next to the film: <film>.pt-BR.srt, <film>.zh-Hans.srt, <film>.th.srt,
<film>.de.srt, <film>.ja.srt, <film>.ru.srt (the language codes YouTube uses), and the English one as
<film>.en.srt. A translated cue has the times of its English cue, one for one: no merging, no splitting, so
the files line up.

- The service is OpenRouter's chat completions API (OpenAI-compatible). The model is a setting:
  VET_TRANSLATE_MODEL, or `--model`; the starting default is config.TRANSLATE_MODEL.
- The key comes from the environment variable OPENROUTER_API_KEY, or, when that is not set, from the macOS login
  keychain (`security find-generic-password -s SERVICE -w`; the service name is config.TRANSLATE_KEYCHAIN_SERVICE,
  or VET_OPENROUTER_KEYCHAIN_SERVICE) and from nowhere else. It is put in one request header. It is not written to
  a file, a log, a report or a message.
- Cues go in batches of config.TRANSLATE_BATCH with the cues around them as context. The answer must be a JSON
  object that maps every id of the batch, and only those, to a text that is not empty. A batch that fails
  (a network error, an answer that is not valid) is tried again config.TRANSLATE_TRIES times; then the run stops and names the
  cues. English is never put in a file of another language.
- Translations are kept by the hash of the English text, the language and the model
  (<work>/translations/cache.json), so a second run, or a run after one cue was edited, sends only the cues
  that are new.
- A translation that does not fit its cue (more than two lines at phrase boundaries, or read faster than its language
  allows) is sent once more in a follow-up request, with the English text, the translation and a maximum number of
  characters: the smaller of (2 lines x the line limit) and (reading speed x the cue's duration). The shorter text is
  kept when it fits better; otherwise the first stays and the warning stays. The follow-up is counted in the tokens and
  the cost and is cached under a key of its own (the maximum is part of the key). `shorten=False` or
  `--no-shorten` switches it off.
- The answer of the model is read by `parse_answer`: the one JSON object that maps every id to a text, also among
  prose or in a fence that is not the whole text; nothing is repaired. With `save_raw` (`--save-raw DIR`) the text of
  every answer that cannot be read is written to a file there, for finding out what the model sent.
- Every request has `max_tokens` (`max_tokens_for`). An answer that ended at that limit is a failed try even when its
  JSON can be read; it is tried once more, a second one ends the request. A request makes up to 3 tries, waiting 1 s and
  3 s; HTTP 429 and 503 wait for Retry-After (at most 60 s) or 5, 15 and 30 s and make up to 4 tries (`_ask`). The report
  lists every try (`requests`) with its tokens.
- Each translation is wrapped again for its language: 42 characters per line for pt-BR, de and ru, 35 for Thai
  (tone marks and vowel marks not counted), 16 for Simplified Chinese, 13 full-width characters for Japanese;
  two lines at most. A line of Chinese, Japanese or Thai has no spaces, so it is cut at the phrase boundaries that
  BudouX gives, with the line-start and line-end rules of W3C JLREQ and CLREQ kept (linebreak.py). The limits are
  those of the Netflix Timed Text Style Guide of each language (config.TRANSLATE_LANGUAGE_RULES).
- Burning a translation into a film (step 6): `final_texts` gives the text each cue ended up with, read from the cache
  without a request (the first translation, or the shorter one when the follow-up found one that fits better), and
  `localize` returns a copy of a film or a short whose cues have those texts and whose `subtitles.language` is the
  language. The picture is drawn from that copy (textlayer.py).
"""

import collections
import email.utils
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import unicodedata
import urllib.error
import urllib.request

import config
import fsutil
import linebreak
import words


class TranslateError(Exception):
    """The translation cannot be done or stopped. The message says why and what to do; it never holds the key."""


class _Fatal(TranslateError):
    """An error that trying again does not help (a rejected key, no credit)."""


class _Retry(Exception):
    """A request that failed in a way that may pass: a network error, a server error, an unusable answer.
    `rate` is set for HTTP 429 and 503 (waits of their own), `retry_after` is the wait the answer asked for, in
    seconds (already cut at config.TRANSLATE_RETRY_AFTER_MAX_SEC), `cut_off` for an answer that ended at max_tokens."""

    def __init__(self, message: str = "", rate: bool = False, retry_after=None, cut_off: bool = False):
        super().__init__(message)
        self.rate, self.retry_after, self.cut_off = rate, retry_after, cut_off


CUT_OFF = "the answer was cut off at max_tokens"
_now = time.time                           # the clock for an HTTP date in Retry-After; the tests replace it


# ── Settings ────────────────────────────────────────────────────

def model_name(explicit: str = None) -> str:
    return explicit or os.environ.get("VET_TRANSLATE_MODEL") or config.TRANSLATE_MODEL


def base_url() -> str:
    return (os.environ.get("VET_OPENROUTER_URL") or config.TRANSLATE_URL).rstrip("/")


KEY_ENV = "OPENROUTER_API_KEY"
KEYCHAIN_ENV = "VET_OPENROUTER_KEYCHAIN_SERVICE"
_keys = {}                       # service name -> the key found there, for the run of the program


def keychain_service() -> str:
    """The service name of the keychain item. An empty VET_OPENROUTER_KEYCHAIN_SERVICE switches the lookup off."""
    value = os.environ.get(KEYCHAIN_ENV)
    return config.TRANSLATE_KEYCHAIN_SERVICE if value is None else value.strip()


def forget_key() -> None:
    """Drop the key kept from the keychain, so the next use reads the keychain again."""
    _keys.clear()


def _security(args: list):
    """Run macOS `security` with `args`. Returns (the finished process, None), or (None, why it did not run).
    The output is captured: a password never goes to the terminal."""
    program = shutil.which("security") or "/usr/bin/security"
    try:
        return subprocess.run([program, *args], capture_output=True, text=True,
                              timeout=config.TRANSLATE_KEYCHAIN_TIMEOUT_SEC), None
    except FileNotFoundError:
        return None, "there is no `security` program on this computer (the keychain is a macOS feature)"
    except subprocess.TimeoutExpired:
        return None, (f"the keychain did not answer within {config.TRANSLATE_KEYCHAIN_TIMEOUT_SEC} s "
                      "(a dialog may be waiting for you to allow the access)")
    except OSError as e:
        return None, f"`security` could not be started ({type(e).__name__})"


def _why_not(done, service: str) -> str:
    """What went wrong with a keychain lookup that did not give a key. Never holds a password."""
    first = (done.stderr or "").strip().splitlines()[:1]
    if done.returncode == 44 or "could not be found" in (done.stderr or ""):
        return f"the login keychain has no item with the service name '{service}'"
    return f"the keychain lookup was refused (security exit {done.returncode}" + (f": {first[0]}" if first else "") + ")"


def find_key() -> tuple:
    """The OpenRouter key and, when there is none, why: (key, None) or (None, reason). First the environment
    variable OPENROUTER_API_KEY; then the keychain item named by keychain_service(). The key found in the keychain is kept
    for the run. The reason never holds a key."""
    env = os.environ.get(KEY_ENV, "").strip()
    if env:
        return env, None
    service = keychain_service()
    if not service:
        return None, f"{KEY_ENV} is not set and the keychain lookup is switched off ({KEYCHAIN_ENV} is empty)"
    if service in _keys:
        return _keys[service], None
    done, problem = _security(["find-generic-password", "-s", service, "-w"])
    if done is None:
        return None, problem
    key = (done.stdout or "").strip() if done.returncode == 0 else ""
    if not key:
        return None, _why_not(done, service) if done.returncode != 0 else \
            f"the keychain item '{service}' has an empty password"
    _keys[service] = key
    return key, None


def has_key() -> bool:
    """Whether a key is available, for the page. The keychain is asked whether the item exists, not for its
    password, so no dialog opens and the password is not read."""
    if os.environ.get(KEY_ENV, "").strip():
        return True
    service = keychain_service()
    if not service:
        return False
    if service in _keys:
        return True
    done, _ = _security(["find-generic-password", "-s", service])
    return done is not None and done.returncode == 0


def check_ready() -> None:
    """Raises TranslateError, before any request, when there is no key."""
    key, problem = find_key()
    if key is None:
        raise TranslateError(
            f"No OpenRouter key: {problem}. Set the environment variable {KEY_ENV} (export {KEY_ENV}=... in the "
            f"shell that starts the tool), or add the key to the login keychain with: security add-generic-password "
            f"-a {keychain_service() or KEY_ENV} -s {keychain_service() or KEY_ENV} -w (it asks for the key). "
            f"The service name is the setting {KEYCHAIN_ENV}. The key is never written to a file.")


def parse_languages(value) -> list:
    """The language codes asked for, in canonical spelling. Nothing or an empty text means all six."""
    if value is None or value == "" or value == []:
        return list(config.TRANSLATE_LANGUAGES)
    items = value if isinstance(value, list) else [x for x in re.split(r"[,\s]+", str(value)) if x]
    by_lower = {c.lower(): c for c in config.TRANSLATE_LANGUAGES}
    out, bad = [], []
    for item in items:
        code = by_lower.get(str(item).strip().lower())
        if code is None:
            bad.append(str(item))
        elif code not in out:
            out.append(code)
    if bad:
        raise TranslateError(f"Unknown language {', '.join(bad)}. The languages are: "
                             f"{', '.join(config.TRANSLATE_LANGUAGES)}.")
    return out


def pick_export(data: dict, name=None) -> dict:
    """The export whose film the files belong to: the one named, or the first."""
    exports = data.get("exports") or []
    if not exports:
        raise TranslateError("The timeline file has no export, so the SRT files have no film to lie next to. "
                             "Add one: python main.py export add --preset film-4k")
    if not name:
        return exports[0]
    for e in exports:
        if e.get("name") == name:
            return e
    raise TranslateError(f"The timeline file has no export {name!r} (exports: "
                         f"{', '.join(e.get('name', '?') for e in exports)}).")


def srt_file(entry: dict, lang: str) -> str:
    return os.path.splitext(entry["output"])[0] + f".{lang}.srt"


# ── Measuring and wrapping a translation ────────────────────────

def visual_len(text: str, lang: str) -> float:
    """The length of a line in the unit of its language: characters; for Thai without the tone marks and the
    vowel marks above and below; for Japanese a full-width character is 1 and a half-width one is 0.5."""
    unit = config.TRANSLATE_LANGUAGE_RULES[lang]["unit"]
    if unit == "thai":
        return float(sum(1 for ch in text if unicodedata.category(ch) != "Mn"))
    if unit == "width":
        return sum(1.0 if unicodedata.east_asian_width(ch) in "FWA" else 0.5 for ch in text)
    return float(len(text))


def layout_translation(text: str, lang: str):
    """The lines of a translated cue, and whether a phrase had to be cut inside: (lines, split_phrase).
    One line when it fits. For pt-BR, de and ru, two lines at a natural place (words.wrap), else as many as it
    takes. For Chinese, Japanese and Thai, the fewest lines that can be cut at phrase boundaries (BudouX) with the
    line-start and line-end rules kept, balanced (linebreak.py). The limit is that of the language
    (config.TRANSLATE_LANGUAGE_RULES)."""
    import textlayout
    rule = config.TRANSLATE_LANGUAGE_RULES[lang]
    limit = rule["max_chars"]
    text = " ".join(str(text).split())
    if visual_len(text, lang) <= limit:
        return [text], False
    if rule["unit"] == "char":
        rules = textlayout.LineRules(max_chars=limit, max_width=float(limit * 10), measure=lambda s: 10.0 * len(s),
                                     exact=False, max_lines=2)
        lines = words.wrap(text, rules, max_lines=2) or words.wrap(text, rules, max_lines=8)
        return (lines or [text]), False
    found = linebreak.break_lines(text, lang, limit, lambda s: visual_len(s, lang))
    return found.lines, found.split_phrase


def wrap_translation(text: str, lang: str) -> list:
    """The lines of a translated cue (layout_translation without the flag)."""
    return layout_translation(text, lang)[0]


def max_chars_for(lang: str, duration_s: float) -> int:
    """The most characters a translation may have to fit its cue: the smaller of (lines x the line limit) and
    (the reading speed of the language x the duration), rounded down, in the unit of the language (visual_len)."""
    rule = config.TRANSLATE_LANGUAGE_RULES[lang]
    return int(math.floor(min(config.SUBTITLE_MAX_LINES * rule["max_chars"], rule["cps"] * duration_s) + 1e-9))


def assess(text: str, lang: str, duration_s: float) -> dict:
    """How a translation fits its cue: {lines, speed, problems, excess}. `problems` holds "lines" (more lines than
    the film allows), "speed" (read faster than the language allows; the speed counts the characters of the text, not
    the break between lines) and "split" (a phrase wider than a line was cut inside). `excess` is how far over the
    limits it is, to compare two texts that both have problems."""
    text = flat(text)
    rule = config.TRANSLATE_LANGUAGE_RULES[lang]
    lines, split = layout_translation(text, lang)
    speed = visual_len(text, lang) / max(1e-6, duration_s)
    problems = []
    if len(lines) > config.SUBTITLE_MAX_LINES:
        problems.append("lines")
    if speed > rule["cps"] + 1e-9:
        problems.append("speed")
    if split:
        problems.append("split")
    excess = max(0, len(lines) - config.SUBTITLE_MAX_LINES) + max(0.0, speed / rule["cps"] - 1.0)
    return {"lines": lines, "speed": speed, "problems": problems, "excess": excess}


def better(candidate: dict, first: dict) -> bool:
    """Does the candidate (an `assess` result) fit better than the first? Fewer problems, then less excess. Equal is
    not better: the first translation stays."""
    return (len(candidate["problems"]), candidate["excess"]) < (len(first["problems"]), first["excess"] - 1e-9)


# ── The cache ───────────────────────────────────────────────────

def cache_path(work_dir: str) -> str:
    return os.path.join(work_dir, "translations", "cache.json")


def cache_key(model: str, lang: str, text: str) -> str:
    return hashlib.sha256(f"{model}\x00{lang}\x00{text}".encode("utf-8")).hexdigest()


def short_cache_key(model: str, lang: str, text: str, max_chars: int) -> str:
    """The key of the shorter translation of `text` that asked for at most `max_chars` characters. It is not the key of
    the first translation, so the first one is never taken for the shorter one."""
    return hashlib.sha256(f"{model}\x00{lang}\x00short\x00{max_chars}\x00{text}".encode("utf-8")).hexdigest()


def load_cache(work_dir: str) -> dict:
    try:
        with open(cache_path(work_dir), encoding="utf-8") as f:
            data = json.load(f)
        return data.get("entries", {}) if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(work_dir: str, entries: dict) -> None:
    fsutil.write_json_atomic(cache_path(work_dir), {"version": 1, "entries": entries})


def flat(text: str) -> str:
    return " ".join(text.split())


# ── Burning a translation: the text each cue ended up with ───────

def final_texts(spans: list, work_dir: str, lang: str, model: str = None) -> dict:
    """The text of every cue of `spans` (words.cue_spans) in `lang`, as `subtitles translate` left it in the cache:
    the first translation, or the shorter one when the cue needs one (it has a problem the follow-up can mend) and the
    shorter one fits better (`better`): the same choice that `_shorten` made when it wrote the SRT file. Nothing is
    sent and nothing is written. Returns {cue id: text}. A cue with no translation (never translated, another model, a
    text that changed since) is an error that names the cues and the command: no English is put into another
    language."""
    if lang not in config.TRANSLATE_LANGUAGES:
        raise TranslateError(f"Unknown language {lang!r}. The languages are: {', '.join(config.TRANSLATE_LANGUAGES)}.")
    model = model_name(model)
    cache = load_cache(work_dir)
    out, missing = {}, []
    for s in spans:
        english = flat(s["text"])
        found = cache.get(cache_key(model, lang, english))
        if found is None:
            missing.append(s["id"])
            continue
        first = found["text"]
        duration = max(1e-6, s["end_s"] - s["start_s"])
        problems = assess(first, lang, duration)
        limit = max_chars_for(lang, duration)
        text = first
        if problems["problems"] and limit >= config.TRANSLATE_SHORTEN_MIN_CHARS:
            shorter = cache.get(short_cache_key(model, lang, english, limit))
            if shorter is not None and better(assess(shorter["text"], lang, duration), problems):
                text = shorter["text"]
        out[s["id"]] = text
    if missing:
        raise TranslateError(
            f"No translation into {lang} (model {model}) for the cues {', '.join(missing)}. Translate them first: "
            f"python main.py subtitles translate --lang {lang}")
    return out


def localize(data: dict, work_dir: str, lang: str, film=None, model: str = None):
    """A copy of the timeline data `data` (a film, or a short) whose cues are in `lang`, and a list of notes. The
    copy has `subtitles.language` set to `lang` and, for each cue that is in the film, the text that `final_texts`
    gives; the times and the scenes are the cue's own. It is for drawing, never saved: `data` is not changed.
    English returns `data` itself.

    A short has the pieces that the 9:16 layout made of one cue of the film, each naming its cue (`x_film_cue`). The
    pieces of one cue that follow each other on one scene become one cue again, from the start of the first to the end
    of the last, with the text of the film's cue; the layout for the narrow frame is done again when the picture is
    drawn. `film` is the film's data; when it is not given, the file named by `derived_from.film` is read. A cue of a
    short that names no cue of the film cannot be translated and is an error."""
    import copy
    import timeline
    if lang == config.SUBTITLE_LANGUAGE:
        return data, []
    out = copy.deepcopy(data)
    subs = out.setdefault("subtitles", {"language": config.SUBTITLE_LANGUAGE, "cues": []})
    notes = []
    if data.get("kind") == "short":
        path = (data.get("derived_from") or {}).get("film", "")
        if film is None:
            try:
                film = timeline.Timeline.load(path).data
            except (OSError, ValueError, KeyError, timeline.TimelineError) as e:
                raise TranslateError(f"The short was made from the film {path}, and that file cannot be read "
                                     f"({e}). A short in another language takes its text from the cues of the film.")
        final = final_texts(words.cue_spans(film), work_dir, lang, model)
        cues, unnamed = [], []
        for c in (data.get("subtitles") or {}).get("cues") or []:
            name = c.get("x_film_cue")
            if name is None:
                unnamed.append(c["id"])
                continue
            if name not in final:
                raise TranslateError(f"The cue {c['id']} of the short comes from the cue {name} of the film, and the "
                                     f"film ({path or 'the film file'}) has no such cue.")
            piece = dict(copy.deepcopy(c), text=final[name])
            if cues and cues[-1].get("x_film_cue") == name and cues[-1]["anchor"] == c["anchor"]:
                cues[-1]["end_s"] = max(cues[-1]["end_s"], c["end_s"])
                continue
            cues.append(piece)
        if unnamed:
            raise TranslateError(f"The cue(s) {', '.join(unnamed)} of the short name no cue of the film (x_film_cue), "
                                 f"so they cannot be translated. A short that was planned before this key existed "
                                 f"must be planned again: python main.py short --file <film> ...")
        subs["cues"] = cues
    else:
        spans = words.cue_spans(data)
        final = final_texts(spans, work_dir, lang, model)
        by_id = {s["id"]: s for s in spans}
        subs["cues"] = [dict(c, text=final[c["id"]]) for c in subs.get("cues") or [] if c["id"] in by_id]
    subs["language"] = lang
    return out, notes


# ── The service ─────────────────────────────────────────────────

def retry_after_seconds(value):
    """The wait in a Retry-After header: a number of seconds or an HTTP date, cut at config.TRANSLATE_RETRY_AFTER_MAX_SEC
    (60 s). None when there is no header or it is not a time (an empty text, a word, a negative number, a number that
    is not finite); a date in the past is 0."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
        if seconds < 0:
            return None
    except ValueError:
        try:
            seconds = max(0.0, email.utils.parsedate_to_datetime(text).timestamp() - _now())
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    if not math.isfinite(seconds):
        return None
    return min(seconds, config.TRANSLATE_RETRY_AFTER_MAX_SEC)


def max_tokens_for(kind: str, n_cues: int) -> int:
    """The output limit of a request with `n_cues` cues: "translate" (the first request) or "shorten" (the follow-up)."""
    size = config.TRANSLATE_MAX_TOKENS_BASE + config.TRANSLATE_MAX_TOKENS_PER_CUE[kind] * n_cues
    return max(config.TRANSLATE_MAX_TOKENS_FLOOR, size)


class Client:
    """OpenRouter's chat completions API. The key goes into one header and nowhere else."""

    def __init__(self, key: str = None, url: str = None, timeout: float = None):
        self._key = key if key is not None else (find_key()[0] or "")
        self.url = (url or base_url()).rstrip("/")
        self.timeout = timeout or config.TRANSLATE_TIMEOUT_SEC

    def scrub(self, text: str) -> str:
        """`text` without the key, for a text that is written to a file."""
        return text.replace(self._key, "***") if self._key else text

    def chat(self, model: str, messages: list, max_tokens: int = None) -> dict:
        request = {"model": model, "messages": messages, "temperature": 0,
                   "response_format": {"type": "json_object"}, "usage": {"include": True}}
        if max_tokens:
            request["max_tokens"] = int(max_tokens)
        body = json.dumps(request).encode("utf-8")
        req = urllib.request.Request(self.url + "/chat/completions", data=body, method="POST", headers={
            "Authorization": f"Bearer {self._key}", "Content-Type": "application/json",
            "X-Title": "video-edit-tool"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8")).get("error", {}).get("message", "")
            except Exception:
                pass
            detail = detail.replace(self._key, "***") if self._key else detail
            if e.code in (401, 403):
                forget_key()
                raise _Fatal(f"OpenRouter rejected the key (HTTP {e.code}). Check OPENROUTER_API_KEY or the keychain item.")
            if e.code == 402:
                raise _Fatal("OpenRouter says there is no credit left (HTTP 402). Add credit to the account.")
            if e.code in (429, 503):
                raise _Retry(f"HTTP {e.code} {detail}".strip(), rate=True,
                             retry_after=retry_after_seconds(e.headers.get("Retry-After") if e.headers else None))
            raise _Retry(f"HTTP {e.code} {detail}".strip())
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise _Retry(f"network error: {type(e).__name__}")
        except ValueError:
            raise _Retry("the answer was not JSON")


SYSTEM = """You translate subtitles for a film. The target language is {name}.
You get one JSON object with "target_language", "translate" (a list of {{"id", "text"}} to translate),
and "context_before" and "context_after" (neighbouring subtitles in English, for context only: do not translate them).
Answer with one JSON object and nothing else: it maps every id in "translate" to its translation, as a string.
Rules:
- Translate the meaning in natural, spoken {name}, as a good subtitler would. Keep names and numbers.
- One translation for each id, in one line, without line breaks, no longer than the English unless the language needs it.
- Use every id exactly once and no other id. Never leave a translation empty. Do not explain, do not add notes.
- The film is a quiet cooking and ambient film; the speaker talks informally."""


def build_messages(lang: str, batch: list, before: list, after: list) -> list:
    rule = config.TRANSLATE_LANGUAGE_RULES[lang]
    user = {"target_language": lang,
            "translate": [{"id": c["id"], "text": c["text"]} for c in batch],
            "context_before": [{"id": c["id"], "text": c["text"]} for c in before],
            "context_after": [{"id": c["id"], "text": c["text"]} for c in after]}
    return [{"role": "system", "content": SYSTEM.format(name=rule["name"])},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


SHORTEN_SYSTEM = """You shorten subtitle translations for a film. The target language is {name}.
You get one JSON object with "target_language" and "shorten": a list of {{"id", "english", "translation", "max_chars"}}.
For each item, write a shorter translation of the English text in {name}: at most "max_chars" characters.{unit}
Answer with one JSON object and nothing else: it maps every id in "shorten" to the shorter translation, as a string.
For the ids in this request the answer looks like this: {example}
No text before or after it and no markdown fence.
Rules:
- Keep what matters most of the meaning, and keep names and numbers. Leave out detail first.
- Natural, spoken {name}, as a good subtitler would write it. One line, without line breaks.
- Never more than "max_chars" characters. Use every id exactly once and no other id. Never leave a translation empty.
  Do not explain, do not add notes.
- The film is a quiet cooking and ambient film; the speaker talks informally."""

_UNIT_NOTES = {
    "width": " A full-width character (kana, kanji, full-width punctuation) counts 1; a half-width character "
             "(Latin letters, digits, spaces) counts 0.5.",
    "thai": " The tone marks and vowel marks written above or below a consonant are not counted.",
}


def build_shorten_messages(lang: str, items: list) -> list:
    """The follow-up request: `items` is a list of {id, english, translation, max_chars}."""
    rule = config.TRANSLATE_LANGUAGE_RULES[lang]
    user = {"target_language": lang, "shorten": items}
    example = "{" + ", ".join(f'"{i["id"]}": "<shorter translation>"' for i in items[:2]) + "}"
    return [{"role": "system", "content": SHORTEN_SYSTEM.format(name=rule["name"], unit=_UNIT_NOTES.get(rule["unit"], ""),
                                                                example=example)},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


_FENCE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)```", re.S)


def _json_values(text: str) -> list:
    """The JSON values that can be read from the text of an answer, in this order: the whole text; each fenced block
    (``` ... ```) wherever it is; each object or list that stands among other text. `strict=False` lets a text
    hold a line break or a tab, which strict JSON forbids inside a string. Nothing is repaired: a cut-off value, a
    trailing comma and single quotes are not read."""
    decoder = json.JSONDecoder(strict=False)
    try:
        return [decoder.decode(text)]
    except ValueError:
        pass
    found = []
    for block in _FENCE.finditer(text):
        try:
            found.append(decoder.decode(block.group(1).strip()))
        except ValueError:
            pass
    i = 0
    while i < len(text):
        if text[i] in "{[":
            try:
                value, end = decoder.raw_decode(text, i)
            except ValueError:
                i += 1
                continue
            found.append(value)
            i = end
        else:
            i += 1
    return found


def _items_to_object(items: list) -> dict:
    """A list of {"id", "translation"} (the item as the follow-up request sends it) or {"id", "text"} (the item of
    the first request) as an object that maps id to text. Exactly one of the two fields must be there."""
    out = {}
    for item in items:
        if not (isinstance(item, dict) and isinstance(item.get("id"), str)):
            raise _Retry("the answer is not a JSON object")
        fields = [k for k in ("translation", "text") if k in item]
        if len(fields) != 1:
            raise _Retry(f"the item of {item['id']} is not clear: it needs exactly one of 'translation' and 'text'")
        if item["id"] in out:
            raise _Retry(f"the id {item['id']} is in the answer more than once")
        out[item["id"]] = item[fields[0]]
    return out


def _as_texts(value, ids: list) -> dict:
    """{id: text} from one JSON value, or _Retry saying what is wrong with it."""
    if isinstance(value, dict) and len(value) == 1 and set(value) != set(ids) \
            and isinstance(next(iter(value.values())), (dict, list)):
        value = next(iter(value.values()))                        # {"translations": {...}}, {"shorten": [...]}
    if isinstance(value, list):
        value = _items_to_object(value)
    if not isinstance(value, dict):
        raise _Retry("the answer is not a JSON object")
    missing = [i for i in ids if i not in value]
    extra = [i for i in value if i not in ids]
    if missing or extra:
        raise _Retry(f"the answer misses {missing} and has {extra} that were not asked for")
    out = {}
    for i in ids:
        v = value[i]
        if not isinstance(v, str) or not v.strip():
            raise _Retry(f"the translation of {i} is empty or not a text")
        out[i] = flat(v)
    return out


def parse_answer(content, ids: list) -> dict:
    """The texts in an answer: {id: text}. The answer is the one JSON object that maps every id to its text; it may
    stand among prose or in a fence that is not the whole text, and the tool's own shapes are read too (an object or
    list around it, a list of {"id", "translation"}). The first value that has exactly the asked ids is taken.
    Raises _Retry, with the reason, when there is none."""
    if not isinstance(content, str):
        raise _Retry("the answer has no text")
    values = _json_values(content.strip())
    if not values:
        raise _Retry("the answer is not JSON")
    first = None
    for value in values:
        try:
            return _as_texts(value, ids)
        except _Retry as e:
            first = first or e
    raise first


# ── The run ─────────────────────────────────────────────────────

def estimate_cost(n_cues: int, langs: list, model: str):
    """About what the cues cost in US dollars, from the price in config.TRANSLATE_PRICES; None for a model
    whose price is not known. [estimate: 30 tokens in and 40 tokens out for each cue and language, 400 tokens
    of instructions for each batch]"""
    price = config.TRANSLATE_PRICES.get(model)
    if price is None:
        return None
    batches = -(-n_cues // config.TRANSLATE_BATCH)
    tokens_in = len(langs) * (n_cues * 30 + batches * 400)
    tokens_out = len(langs) * n_cues * 40
    return round((tokens_in * price[0] + tokens_out * price[1]) / 1e6, 4)


def _cost(model: str, tokens_in: int, tokens_out: int):
    price = config.TRANSLATE_PRICES.get(model)
    return None if price is None else round((tokens_in * price[0] + tokens_out * price[1]) / 1e6, 5)


def _warnings(spans, texts, lang) -> list:
    """Readability warnings for one language: lines over the limit, more than two lines, a phrase cut inside, reading
    faster than the limit of the language. The times are the English cue's, so they are reported, not changed."""
    rule = config.TRANSLATE_LANGUAGE_RULES[lang]
    out = []
    for s in spans:
        found = assess(texts[s["id"]], lang, s["end_s"] - s["start_s"])
        if "lines" in found["problems"]:
            out.append(f"{lang} {s['id']}: {len(found['lines'])} lines")
        if "split" in found["problems"]:
            out.append(f"{lang} {s['id']}: a phrase is wider than a line and was cut inside")
        if "speed" in found["problems"]:
            out.append(f"{lang} {s['id']}: {found['speed']:.1f} characters per second, more than the {rule['cps']:g} "
                       f"that the Netflix guide for {lang} allows")
    return out


def _save_raw(save_raw, client, content, lang: str, kind: str, ids: list, attempt: int, report: dict, reason: str):
    """Write the text of an answer that could not be read to a file in the folder `save_raw` (if there is one) and
    note it in the report. Only the text of the answer is written, never a header, and the key, if it were in the
    text, is replaced. Returns the path, or None."""
    if not save_raw:
        return None
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    scrub = getattr(client, "scrub", None)
    if scrub:
        text = scrub(text)
    n = len(report["raw_answers"]) + 1
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{n:02d}-{lang}-{kind}-{ids[0]}-{ids[-1]}-try{attempt + 1}.txt"
    path = os.path.join(save_raw, name)
    fsutil.write_text_atomic(path, text)
    report["raw_answers"].append({"lang": lang, "kind": kind, "ids": list(ids), "try": attempt + 1, "reason": reason,
                                  "file": path})
    return path


Asked = collections.namedtuple("Asked", "answer why tries")


def _cut_off(finish, native, tokens_out: int, max_tokens: int) -> bool:
    """Did the answer end because it reached max_tokens? `finish_reason` "length", the provider's own reason
    ("MAX_TOKENS"), or, when the answer names no reason, as many tokens out as max_tokens allowed."""
    if str(finish).lower() == "length":
        return True
    if str(native).lower() in ("max_tokens", "length", "max_output_tokens"):
        return True
    return finish is None and native is None and bool(max_tokens) and tokens_out >= max_tokens


def _ask(client, model: str, messages: list, ids: list, report: dict, follow_up: bool = False, lang: str = "",
         kind: str = "translate", save_raw=None) -> Asked:
    """One request with its tries. Returns Asked(answer {id: text} or None, why the last try failed, tries made).
    Each request carries max_tokens (max_tokens_for). A try fails on an answer that cannot be read, a network error,
    an HTTP error, or an answer that ended at max_tokens even when the JSON in it can be read (CUT_OFF).
    - HTTP 429 and 503 wait for Retry-After, or config.TRANSLATE_RATE_WAIT_SEC (5, 15, 30 s) without one, and make up
      to config.TRANSLATE_RATE_TRIES (4) tries. Any other failure waits config.TRANSLATE_RETRY_WAIT_SEC (1, 3 s) and
      makes up to config.TRANSLATE_TRIES (3) tries; once a 429 or 503 has come, the limit of 4 holds.
    - A second cut-off ends the request at once (config.TRANSLATE_CUTOFF_TRIES): the answer would be the same.
    There is no wait after the last try. The tokens of every answer that comes back are added to the report, also
    those of an answer that cannot be read, and every try is listed in report["requests"]. A rejected key or no
    credit raises TranslateError at once. The text of every answer that cannot be read goes to `save_raw`."""
    max_tokens = max_tokens_for(kind, len(ids))
    why, tries, cut_offs, rate_failures = "", 0, 0, 0
    while True:
        tries += 1
        if follow_up:
            report["follow_up_requests"] += 1
        entry = {"lang": lang, "kind": kind, "cues": len(ids), "try": tries, "max_tokens": max_tokens,
                 "tokens_in": 0, "tokens_out": 0, "finish_reason": None, "native_finish_reason": None, "result": ""}
        report["requests"].append(entry)
        failure = None
        try:
            resp = client.chat(model, messages, max_tokens)
            usage = resp.get("usage") or {}
            entry["tokens_in"] = int(usage.get("prompt_tokens") or 0)
            entry["tokens_out"] = int(usage.get("completion_tokens") or 0)
            report["tokens_in"] += entry["tokens_in"]
            report["tokens_out"] += entry["tokens_out"]
            choice = (resp.get("choices") or [{}])[0]
            entry["finish_reason"] = choice.get("finish_reason")
            entry["native_finish_reason"] = choice.get("native_finish_reason")
            content = (choice.get("message") or {}).get("content")
            try:
                if _cut_off(entry["finish_reason"], entry["native_finish_reason"], entry["tokens_out"], max_tokens):
                    raise _Retry(CUT_OFF, cut_off=True)
                answer = parse_answer(content, ids)
            except _Retry as e:
                _save_raw(save_raw, client, content, lang, kind, ids, tries - 1, report, str(e))
                raise
            entry["result"] = "ok"
            return Asked(answer, "", tries)
        except _Retry as e:
            failure = e
        why = entry["result"] = str(failure)
        cut_offs += failure.cut_off
        rate_failures += failure.rate
        if cut_offs >= config.TRANSLATE_CUTOFF_TRIES or tries >= (config.TRANSLATE_RATE_TRIES if rate_failures
                                                                  else config.TRANSLATE_TRIES):
            return Asked(None, why, tries)
        if failure.rate:
            waits = config.TRANSLATE_RATE_WAIT_SEC
            wait = failure.retry_after if failure.retry_after is not None else waits[min(rate_failures - 1, len(waits) - 1)]
        else:
            waits = config.TRANSLATE_RETRY_WAIT_SEC
            wait = waits[min(tries - 1, len(waits) - 1)]
        time.sleep(wait)


def _shorten(client, model: str, lang: str, spans: list, firsts: dict, cache: dict, work_dir: str, report: dict,
             log, on_batch, save_raw=None) -> dict:
    """The final text of every cue of one language: the first translation, or a shorter one when the first does not
    fit the cue and the shorter one fits better. Asks the model once for each cue that does not fit and has no
    shorter text in the cache. Returns {cue id: text}."""
    final = dict(firsts)
    plan = []                                           # [(span, english, max_chars, cache key, assessment of the first)]
    for s in spans:
        duration = max(1e-6, s["end_s"] - s["start_s"])
        found = assess(firsts[s["id"]], lang, duration)
        limit = max_chars_for(lang, duration)
        if found["problems"] and limit >= config.TRANSLATE_SHORTEN_MIN_CHARS:
            english = flat(s["text"])
            plan.append((s, english, limit, short_cache_key(model, lang, english, limit), found))
    pending, seen = [], set()
    for s, english, limit, key, found in plan:
        if key not in cache and key not in seen:
            seen.add(key)
            pending.append((s, english, limit, key))
    failed = {}                                         # cache key -> why the follow-up request failed
    for b in range(0, len(pending), config.TRANSLATE_BATCH):
        batch = pending[b:b + config.TRANSLATE_BATCH]
        items = [{"id": s["id"], "english": english, "translation": firsts[s["id"]], "max_chars": limit}
                 for s, english, limit, key in batch]
        ids = [i["id"] for i in items]
        saved_before = len(report["raw_answers"])
        asked = _ask(client, model, build_shorten_messages(lang, items), ids, report, follow_up=True, lang=lang,
                     kind="shorten", save_raw=save_raw)
        answer = asked.answer
        on_batch(f"{lang}: asked for shorter texts of {min(b + len(batch), len(pending))} of {len(pending)} cues")
        report["follow_up_cues"] += len(batch)
        if answer is None:
            for s, english, limit, key in batch:
                failed[key] = (asked.why, asked.tries)
            files = [e["file"] for e in report["raw_answers"][saved_before:]]
            if files:
                report["warnings"].append(f"{lang}: the follow-up request for {len(batch)} cue(s) failed; the answers "
                                          f"that could not be read are saved in: {', '.join(files)}")
            continue
        for s, english, limit, key in batch:
            cache[key] = {"lang": lang, "model": model, "kind": "short", "max_chars": limit, "text": answer[s["id"]]}
        save_cache(work_dir, cache)
    for s, english, limit, key, found in plan:
        cid = s["id"]
        if key not in cache:
            why, tries = failed.get(key, ("no answer", 0))
            report["warnings"].append(f"{lang} {cid}: no shorter translation, the follow-up request failed after "
                                      f"{tries} tries ({why})")
            continue
        candidate = cache[key]["text"]
        duration = max(1e-6, s["end_s"] - s["start_s"])
        if better(assess(candidate, lang, duration), found):
            final[cid] = candidate
            report["shortened"].append({"lang": lang, "id": cid, "max_chars": limit, "before": firsts[cid],
                                        "after": candidate, "before_chars": visual_len(firsts[cid], lang),
                                        "after_chars": visual_len(candidate, lang)})
            log(f"{lang} {cid}: shortened from {visual_len(firsts[cid], lang):g} to {visual_len(candidate, lang):g} "
                f"characters (at most {limit})")
    return final


def translate_film(tl, work_dir: str, entry: dict, langs: list, model: str = None, progress=None, client=None,
                   dry_run: bool = False, log=lambda s: None, shorten: bool = True, save_raw: str = None) -> dict:
    """Translate the film's cues into `langs` and write the SRT files next to the export's file. Returns the report.
    With `shorten`, a translation that does not fit its cue is asked for once more, shorter (see the module text).
    With `save_raw` (a folder), the text of every answer of the model that cannot be read is written there.
    Raises TranslateError (the message names the language and the cues that failed)."""
    spans = words.cue_spans(tl.data)
    if not spans:
        raise TranslateError("The film has no subtitles to translate. Write them first (the text box of a scene, "
                             "subtitles add, or subtitles auto).")
    cues = [{"id": s["id"], "text": flat(s["text"])} for s in spans]
    model = model_name(model)
    cache = load_cache(work_dir)
    todo = {lang: [c for c in cues if cache_key(model, lang, c["text"]) not in cache] for lang in langs}
    report = {"version": 1, "model": model, "export": entry["name"], "languages": {}, "written": [], "warnings": [],
              "sent_cues": 0, "cached_cues": 0, "to_send_cues": sum(len(v) for v in todo.values()),
              "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "estimate_usd": None, "dry_run": dry_run,
              "follow_up_requests": 0, "follow_up_cues": 0, "shortened": [], "raw_answers": [], "requests": []}
    report["estimate_usd"] = _estimate_todo(todo, model)
    if dry_run:
        for lang in langs:
            report["languages"][lang] = {"cues": len(cues), "to_send": len(todo[lang])}
        return report
    check_ready()
    client = client or Client()
    started = time.time()
    progress_state = {"done": 0, "total": sum(-(-len(v) // config.TRANSLATE_BATCH) for v in todo.values())}
    index = {c["id"]: i for i, c in enumerate(cues)}
    english = srt_file(entry, "en")
    os.makedirs(os.path.dirname(os.path.abspath(english)), exist_ok=True)
    rules = words.RULES_FACTORY(tl.data)

    def step(message):
        progress_state["done"] += 1
        if progress:
            progress(progress_state["done"], progress_state["total"], message)

    for lang in langs:
        sent = 0
        pending = todo[lang]
        for b in range(0, len(pending), config.TRANSLATE_BATCH):
            batch = pending[b:b + config.TRANSLATE_BATCH]
            first, last = index[batch[0]["id"]], index[batch[-1]["id"]]
            before = cues[max(0, first - config.TRANSLATE_CONTEXT):first]
            after = cues[last + 1:last + 1 + config.TRANSLATE_CONTEXT]
            messages = build_messages(lang, batch, before, after)
            ids = [c["id"] for c in batch]
            saved_before = len(report["raw_answers"])
            asked = _ask(client, model, messages, ids, report, lang=lang, kind="translate", save_raw=save_raw)
            answer, why = asked.answer, asked.why
            if answer is None:
                files = [e["file"] for e in report["raw_answers"][saved_before:]]
                saved = f" The answers that could not be read are saved in: {', '.join(files)}." if files else ""
                raise TranslateError(
                    f"The translation into {lang} stopped: {asked.tries} tries failed for the cues "
                    f"{', '.join(ids)} ({why}).{saved} The cues translated before are kept; run the command again. "
                    f"No file of {lang} was written.")
            for c in batch:
                cache[cache_key(model, lang, c["text"])] = {"lang": lang, "model": model, "text": answer[c["id"]]}
            save_cache(work_dir, cache)
            sent += len(batch)
            step(f"{lang}: {sent} of {len(pending)} cues")
            log(f"{lang}: {sent} of {len(pending)} cues translated")
        firsts = {c["id"]: cache[cache_key(model, lang, c["text"])]["text"] for c in cues}
        shortened_before = len(report["shortened"])
        if shorten:
            def on_batch(message):
                progress_state["total"] += 1
                step(message)
                log(message)
            texts = _shorten(client, model, lang, spans, firsts, cache, work_dir, report, log, on_batch, save_raw)
        else:
            texts = firsts
        out = [dict(s, text="\n".join(wrap_translation(texts[s["id"]], lang))) for s in spans]
        if english not in report["written"]:        # the English file comes with the first language that is done
            fsutil.write_text_atomic(english, words.srt_text(spans, rules))
            report["written"].append(english)
        path = srt_file(entry, lang)
        fsutil.write_text_atomic(path, words.srt_text(out))
        report["written"].append(path)
        warns = _warnings(spans, texts, lang)
        report["warnings"] += warns
        report["languages"][lang] = {"cues": len(cues), "sent": sent, "cached": len(cues) - sent, "file": path,
                                     "warnings": len(warns), "shortened": len(report["shortened"]) - shortened_before}
        report["sent_cues"] += sent
        report["cached_cues"] += len(cues) - sent
    report["cost_usd"] = _cost(model, report["tokens_in"], report["tokens_out"]) or 0.0
    report["elapsed_s"] = round(time.time() - started, 2)
    fsutil.write_json_atomic(os.path.join(work_dir, "reports", "translate-report.json"), report)
    return report


def _estimate_todo(todo: dict, model: str):
    price = config.TRANSLATE_PRICES.get(model)
    if price is None:
        return None
    total = 0.0
    for cues in todo.values():
        n = len(cues)
        batches = -(-n // config.TRANSLATE_BATCH)
        total += (n * 30 + batches * 400) * price[0] + n * 40 * price[1]
    return round(total / 1e6, 4)


# ── For the page and the command ────────────────────────────────

def page_state(tl, work_dir: str, exports: list) -> dict:
    """What the page shows: whether there is a key (never the key), the model, and for each language how many of the
    film's cues are translated and whether the file is there. Reads the cache; sends nothing."""
    spans = words.cue_spans(tl.data)
    model = model_name()
    cache = load_cache(work_dir) if work_dir else {}
    cues = [flat(s["text"]) for s in spans]
    entry = exports[0] if exports else None
    langs = []
    for lang in config.TRANSLATE_LANGUAGES:
        done = sum(1 for c in cues if cache_key(model, lang, c) in cache)
        langs.append({"code": lang, "name": config.TRANSLATE_LANGUAGE_RULES[lang]["name"], "cues": len(cues),
                      "translated": done, "file": bool(entry and os.path.isfile(srt_file(entry, lang)))})
    return {"key": has_key(), "model": model, "price": config.TRANSLATE_PRICES.get(model), "languages": langs,
            "estimate_usd": estimate_cost(len(cues), list(config.TRANSLATE_LANGUAGES), model)}


def cli(args, timeline_path: str, work_dir_of) -> None:
    """`subtitles translate`."""
    import sys

    import timeline

    def fail(message, code=1):
        print(f"Error: {message}", file=sys.stderr)
        sys.exit(code)

    try:
        tl = timeline.Timeline.load(timeline_path)
    except FileNotFoundError:
        fail(f"timeline file not found: {timeline_path}", 2)
    except timeline.TimelineError as e:
        fail(str(e))
    work = work_dir_of(args, lambda m, code=2: fail(m, code))
    save_raw = getattr(args, "save_raw", None)
    if save_raw:
        import safety
        from project import Project
        project_file = getattr(args, "project", None) or config.PROJECT_FILE
        if os.path.exists(project_file):
            try:
                safety.assert_not_inside(save_raw, safety.originals_folders(Project.load(project_file)))
            except safety.OriginalsProtectedError as e:
                fail(str(e))
    try:
        langs = parse_languages(args.lang)
        entry = pick_export(tl.data, args.export)
        report = translate_film(tl, work, entry, langs, model=args.model, dry_run=args.dry_run,
                                shorten=not getattr(args, "no_shorten", False), save_raw=save_raw,
                                log=lambda s: print(f"{time.strftime('%H:%M:%S')}  {s}", flush=True))
    except TranslateError as e:
        fail(str(e))
    if args.dry_run:
        est = report["estimate_usd"]
        print(f"{report['to_send_cues']} cues to send in {len(langs)} language(s) with {report['model']}"
              + (f", about ${est:.3f} (an estimate)" if est is not None else ", price not known for this model"))
        return
    print(f"{report['sent_cues']} cue(s) sent, {report['cached_cues']} taken from the cache; {report['tokens_in']} "
          f"tokens in, {report['tokens_out']} out" + (f", about ${report['cost_usd']:.4f}" if report["cost_usd"] else ""))
    failed = [r for r in report["requests"] if r["result"] != "ok"]
    if failed:
        print(f"{len(failed)} of {len(report['requests'])} tries failed (every try is in the report): "
              + "; ".join(f"{r['lang']} {r['kind']} try {r['try']}: {r['result']} ({r['tokens_out']} tokens out)"
                          for r in failed))
    if report["follow_up_cues"]:
        print(f"{report['follow_up_cues']} cue(s) asked for a shorter text in {report['follow_up_requests']} request(s) "
              f"(counted in the tokens above)")
    if report["shortened"]:
        print(f"{len(report['shortened'])} cue(s) have a shorter translation than the first one:")
        for item in report["shortened"]:
            print(f"  {item['lang']} {item['id']}: {item['before_chars']:g} -> {item['after_chars']:g} characters "
                  f"(at most {item['max_chars']})")
    if report["raw_answers"]:
        print(f"{len(report['raw_answers'])} answer(s) of the model that could not be read are saved in {save_raw}:")
        for item in report["raw_answers"]:
            print(f"  {item['file']}  ({item['lang']} {item['kind']}, try {item['try']}: {item['reason']})")
    for path in report["written"]:
        print(f"  {path}")
    for w in report["warnings"]:
        print(f"  warning: {w}")
