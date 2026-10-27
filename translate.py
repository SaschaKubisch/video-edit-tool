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
- Each translation is wrapped again for its language: 42 characters per line for pt-BR, de and ru, 35 for Thai
  (tone marks and vowel marks not counted), 16 for Simplified Chinese, 13 full-width characters for Japanese;
  two lines at most. A line of Chinese, Japanese or Thai has no spaces, so it is cut at the phrase boundaries that
  BudouX gives, with the line-start and line-end rules of W3C JLREQ and CLREQ kept (linebreak.py). The limits are
  those of the Netflix Timed Text Style Guide of each language (config.TRANSLATE_LANGUAGE_RULES).
"""

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
    """A request that failed in a way that may pass: a network error, a server error, an unusable answer."""


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


# ── The service ─────────────────────────────────────────────────

class Client:
    """OpenRouter's chat completions API. The key goes into one header and nowhere else."""

    def __init__(self, key: str = None, url: str = None, timeout: float = None):
        self._key = key if key is not None else (find_key()[0] or "")
        self.url = (url or base_url()).rstrip("/")
        self.timeout = timeout or config.TRANSLATE_TIMEOUT_SEC

    def chat(self, model: str, messages: list) -> dict:
        body = json.dumps({"model": model, "messages": messages, "temperature": 0,
                           "response_format": {"type": "json_object"}, "usage": {"include": True}}).encode("utf-8")
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
    return [{"role": "system", "content": SHORTEN_SYSTEM.format(name=rule["name"], unit=_UNIT_NOTES.get(rule["unit"], ""))},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]


def parse_answer(content, ids: list) -> dict:
    """The translations in an answer: {id: text}. Raises _Retry when it is not what was asked for."""
    if not isinstance(content, str):
        raise _Retry("the answer has no text")
    text = content.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        answer = json.loads(text)
    except ValueError:
        raise _Retry("the answer is not JSON")
    if isinstance(answer, dict) and len(answer) == 1 and isinstance(next(iter(answer.values())), dict) \
            and set(answer) != set(ids):
        answer = next(iter(answer.values()))
    if not isinstance(answer, dict):
        raise _Retry("the answer is not a JSON object")
    missing = [i for i in ids if i not in answer]
    extra = [i for i in answer if i not in ids]
    if missing or extra:
        raise _Retry(f"the answer misses {missing} and has {extra} that were not asked for")
    out = {}
    for i in ids:
        v = answer[i]
        if not isinstance(v, str) or not v.strip():
            raise _Retry(f"the translation of {i} is empty or not a text")
        out[i] = flat(v)
    return out


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


def _ask(client, model: str, messages: list, ids: list, report: dict, follow_up: bool = False):
    """One request, tried config.TRANSLATE_TRIES times. Returns (the answer {id: text}, "") or (None, why it failed).
    The tokens of every answer that comes back are added to the report, also those of an answer that cannot be used;
    a rejected key or no credit raises TranslateError at once."""
    why = ""
    for attempt in range(config.TRANSLATE_TRIES):
        if follow_up:
            report["follow_up_requests"] += 1
        try:
            resp = client.chat(model, messages)
            usage = resp.get("usage") or {}
            report["tokens_in"] += int(usage.get("prompt_tokens") or 0)
            report["tokens_out"] += int(usage.get("completion_tokens") or 0)
            choice = (resp.get("choices") or [{}])[0].get("message", {}).get("content")
            return parse_answer(choice, ids), ""
        except _Retry as e:
            why = str(e)
            if attempt + 1 < config.TRANSLATE_TRIES:
                time.sleep(config.TRANSLATE_RETRY_WAIT_SEC[min(attempt, len(config.TRANSLATE_RETRY_WAIT_SEC) - 1)])
    return None, why


def _shorten(client, model: str, lang: str, spans: list, firsts: dict, cache: dict, work_dir: str, report: dict,
             log, on_batch) -> dict:
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
        answer, why = _ask(client, model, build_shorten_messages(lang, items), ids, report, follow_up=True)
        on_batch(f"{lang}: asked for shorter texts of {min(b + len(batch), len(pending))} of {len(pending)} cues")
        report["follow_up_cues"] += len(batch)
        if answer is None:
            for s, english, limit, key in batch:
                failed[key] = why
            continue
        for s, english, limit, key in batch:
            cache[key] = {"lang": lang, "model": model, "kind": "short", "max_chars": limit, "text": answer[s["id"]]}
        save_cache(work_dir, cache)
    for s, english, limit, key, found in plan:
        cid = s["id"]
        if key not in cache:
            report["warnings"].append(f"{lang} {cid}: no shorter translation, the follow-up request failed "
                                      f"({failed.get(key, 'no answer')})")
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
                   dry_run: bool = False, log=lambda s: None, shorten: bool = True) -> dict:
    """Translate the film's cues into `langs` and write the SRT files next to the export's file. Returns the report.
    With `shorten`, a translation that does not fit its cue is asked for once more, shorter (see the module text).
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
              "follow_up_requests": 0, "follow_up_cues": 0, "shortened": []}
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
            answer, why = _ask(client, model, messages, ids, report)
            if answer is None:
                raise TranslateError(
                    f"The translation into {lang} stopped: {config.TRANSLATE_TRIES} tries failed for the cues "
                    f"{', '.join(ids)} ({why}). The cues translated before are kept; run the command again. "
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
            texts = _shorten(client, model, lang, spans, firsts, cache, work_dir, report, log, on_batch)
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
    try:
        langs = parse_languages(args.lang)
        entry = pick_export(tl.data, args.export)
        report = translate_film(tl, work, entry, langs, model=args.model, dry_run=args.dry_run,
                                shorten=not getattr(args, "no_shorten", False),
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
    if report["follow_up_cues"]:
        print(f"{report['follow_up_cues']} cue(s) asked for a shorter text in {report['follow_up_requests']} request(s) "
              f"(counted in the tokens above)")
    if report["shortened"]:
        print(f"{len(report['shortened'])} cue(s) have a shorter translation than the first one:")
        for item in report["shortened"]:
            print(f"  {item['lang']} {item['id']}: {item['before_chars']:g} -> {item['after_chars']:g} characters "
                  f"(at most {item['max_chars']})")
    for path in report["written"]:
        print(f"  {path}")
    for w in report["warnings"]:
        print(f"  warning: {w}")
