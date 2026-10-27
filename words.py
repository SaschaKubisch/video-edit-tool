"""
Words: subtitles and titles (SPECS section 9; requirements W1 to W8).

This module is the text and timing side. It has no ffmpeg and no Pillow in it (the font comes
in through `textlayout.LineRules`, so the tests can use fake widths).

1. Cleaning (9.4): `clean_text` is the local cleanup of the text a person typed or dictated
   into a scene's box: filler words out, spacing, capitals, end punctuation. No rewording, no
   network.
2. Wrapping and splitting (9.3): `wrap` breaks a text into at most two lines at natural phrase
   points, bottom-heavy, by the measured width; `split_cues` cuts a text into cues that each fit.
3. Spreading (9.4): `spread` puts the cues of a scene across the scene in time, in proportion
   to their length, with a gap, a minimum and a maximum length, and a report when the text
   needs more time than the scene has.
4. The film (9.8): cues and titles are anchored to segments; this module maps them to film
   time, applies the operations the page, the commands and Claude Code use, retimes cues when a
   scene is trimmed, and reads and writes SRT files.

The readability rules (about 42 characters per line, two lines at most, 17 characters per
second, 1 to 7 s per cue, a gap, a bottom-heavy shape, breaks after punctuation and before
conjunctions and prepositions) are those of the Netflix English Timed Text Style Guide that the
owner asked for; the numbers are in config.py and SPECS 9.3.
"""

import itertools
import math
import re
from fractions import Fraction

import config


class WordsError(Exception):
    """A request about subtitles or titles that cannot be done. The message says why."""


# ── 1. Cleaning ─────────────────────────────────────────────────

SENTENCE_END = ".!?…"
CLOSERS = "\"')]}”’"
OPENERS = "\"'([{“‘"
ABBREVIATIONS = {"e.g.", "i.e.", "etc.", "vs.", "mr.", "mrs.", "ms.", "dr.", "st.", "prof.",
                 "no.", "approx.", "a.m.", "p.m.", "jr.", "sr.", "inc.", "ltd.", "u.s.", "u.k."}
_PRONOUN_I = re.compile(r"^i(?:'(?:m|ll|ve|d))?$", re.I)


def _core(token: str) -> str:
    return token.strip("".join(SENTENCE_END + ",;:" + CLOSERS + OPENERS) + "-—–")


def _ends_sentence(token: str) -> bool:
    t = token.rstrip(CLOSERS)
    if not t or t[-1] not in SENTENCE_END:
        return False
    low = t.lower()
    if low in ABBREVIATIONS:
        return False
    if re.fullmatch(r"[A-Z]\.", t):            # an initial: J. K. Rowling
        return False
    return True


def clean_text(text: str) -> tuple:
    """The local cleanup of a scene's text (SPECS 9.4). Returns (cleaned text, report).

    - filler words (config.SUBTITLE_FILLERS) go, with one comma next to them;
    - spaces before punctuation go, a missing space after punctuation is added (not inside
      numbers, times and abbreviations);
    - the first word and each word after a full stop, question mark or exclamation mark get a
      capital, and the word "i" is "I";
    - a text that ends without . ! ? or an ellipsis gets a full stop.
    Nothing is reworded. Paragraphs (blank lines) stay; single line breaks become spaces.
    """
    fillers = set(config.SUBTITLE_FILLERS)
    removed = 0
    paragraphs = []
    for para in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        para = re.sub(r"[\s ]+", " ", para).strip()
        if not para:
            continue
        # spacing
        para = re.sub(r"\s+([,;:!?.…])", r"\1", para)
        para = re.sub(r"(?<=[,;!?])(?=[A-Za-z])", " ", para)
        para = re.sub(r"(?<=[a-z]{2})\.(?=[A-Z])", ". ", para)
        para = re.sub(r"(?<=[a-zA-Z]):(?=[A-Za-z])", ": ", para)
        tokens = para.split(" ")
        # fillers
        kept = []
        for tok in tokens:
            if _core(tok).lower() in fillers:
                removed += 1
                trail = tok[len(tok.rstrip(SENTENCE_END + ",;:" + CLOSERS)):]
                if trail and trail[-1] in SENTENCE_END and kept:
                    kept[-1] = kept[-1].rstrip(",;:") + trail[-1] \
                        if not _ends_sentence(kept[-1]) else kept[-1]
                continue
            kept.append(tok)
        tokens = [t for t in kept if t]
        if not tokens:
            continue
        # capitals and "I"
        cap = True
        out = []
        for tok in tokens:
            if _PRONOUN_I.match(_core(tok)) and _core(tok)[0] == "i":
                tok = tok.replace(_core(tok), "I" + _core(tok)[1:], 1)
            if cap:
                for k, ch in enumerate(tok):
                    if ch.isalpha():
                        tok = tok[:k] + ch.upper() + tok[k + 1:]
                        break
                    if ch.isdigit():
                        break
            cap = _ends_sentence(tok)
            out.append(tok)
        para = " ".join(out)
        # the end
        stripped = para.rstrip(CLOSERS)
        closers = para[len(stripped):]
        if stripped and stripped[-1] in ",;:-—–":
            stripped = stripped[:-1].rstrip()
        if stripped and stripped[-1] not in SENTENCE_END:
            para = stripped + closers + "."
        else:
            para = stripped + closers
        paragraphs.append(para)
    cleaned = "\n\n".join(paragraphs)
    return cleaned, {"fillers_removed": removed, "paragraphs": len(paragraphs),
                     "changed": cleaned != text}


# ── 2. Wrapping and splitting ───────────────────────────────────

GLUE = "\u0001"           # marks a piece of a cut word: it joins the piece before it without a space

_ARTICLES = {"a", "an", "the", "this", "these", "those", "my", "your", "his", "her", "its",
             "our", "their", "some", "any", "no", "every", "each", "another"}
_PREPOSITIONS = {"to", "of", "in", "on", "at", "for", "with", "by", "from", "into", "onto",
                 "over", "under", "about", "as", "than", "through", "without", "between"}
_CONJUNCTIONS = {"and", "but", "or", "nor", "so", "because", "that", "which", "who", "whom",
                 "whose", "when", "while", "if", "though", "although", "since", "until",
                 "unless", "before", "after", "where", "whether", "then"}
_AUXILIARIES = {"is", "are", "was", "were", "am", "be", "been", "being", "have", "has", "had",
                "do", "does", "did", "will", "would", "can", "could", "should", "shall", "may",
                "might", "must", "not", "never"}
_SUBJECTS = {"i", "you", "he", "she", "we", "they", "it"}


def join_tokens(tokens) -> str:
    out = ""
    for t in tokens:
        if t.startswith(GLUE):
            out += t[1:]
        else:
            out += (" " if out else "") + t
    return out


def _word(token: str) -> str:
    return token.lstrip(GLUE).strip(".,;:!?…\"'()[]{}“”‘’").lower()


def break_cost(prev: str, nxt: str) -> float:
    """What it costs to end a line (or a cue) after `prev` and start the next with `nxt`.
    Negative is good. After punctuation and before conjunctions and prepositions is good; after
    an article, a preposition, an auxiliary or a subject pronoun, or between two names, is bad
    (Netflix English Timed Text Style Guide, line treatment)."""
    p, q = _word(prev), _word(nxt)
    bare = prev.rstrip(CLOSERS)
    cost = 0.0
    if nxt.startswith(GLUE):
        return 100.0
    if bare and bare[-1] in SENTENCE_END and _ends_sentence(prev):
        cost -= 5.0
    elif bare and bare[-1] in ",;—–-":
        cost -= 4.5
    elif bare and bare[-1] == ":":
        cost -= 3.0
    if q in _CONJUNCTIONS:
        cost -= 2.0
    elif q in _PREPOSITIONS:
        cost -= 1.0
    if p in _ARTICLES:
        cost += 7.0
    if p in _PREPOSITIONS and not bare.endswith(tuple(",;:")):
        cost += 4.0
    if p in _AUXILIARIES and not bare.endswith(tuple(",;:")):
        cost += 3.0
    if p in _SUBJECTS:
        cost += 4.0
    if q in ("not", "n't") or nxt.lower().startswith("n't"):
        cost += 3.0
    if prev[:1].isupper() and nxt[:1].isupper() and not _ends_sentence(prev) and p not in _SUBJECTS:
        cost += 3.0
    if re.match(r"^[—–\-.,;:!?…)\]}]", nxt):
        cost += 10.0
    return cost


def tokens(text: str, rules) -> list:
    """The words of `text`. A word wider than a line is cut into pieces that end with a hyphen
    (the pieces after the first carry GLUE, so they join without a space)."""
    out = []
    for word in text.split():
        if _fits(rules, word):
            out.append(word)
            continue
        piece, first = "", True
        for ch in word:
            trial = piece + ch
            if piece and not _fits(rules, trial + "-"):
                out.append(("" if first else GLUE) + piece + "-")
                piece, first = ch, False
            else:
                piece = trial
        if piece:
            out.append(("" if first else GLUE) + piece)
    return out


def _fits(rules, s: str) -> bool:
    return len(s) <= rules.max_chars and rules.measure(s) <= rules.max_width + 1e-6


def _wrap(toks, rules, max_lines):
    """(lines, cost) for the tokens, or None when they do not fit in `max_lines` lines."""
    whole = join_tokens(toks)
    if _fits(rules, whole):
        return [whole], 0.0
    n = len(toks)
    for count in range(2, min(max_lines, 3) + 1):
        best = None
        if count == 2:
            candidates = ((k,) for k in range(1, n))
        else:
            candidates = itertools.combinations(range(1, n), count - 1)
        for cuts in candidates:
            bounds = (0,) + tuple(cuts) + (n,)
            parts = [toks[a:b] for a, b in zip(bounds, bounds[1:])]
            strs = [join_tokens(p) for p in parts]
            if not _fits(rules, strs[0]):
                if count == 2:
                    break
                continue
            if not all(_fits(rules, s) for s in strs):
                continue
            cost = sum(break_cost(toks[c - 1], toks[c]) for c in cuts)
            widths = [rules.measure(s) for s in strs]
            if count == 2:
                a, b = widths
                cost += (2.0 + 6.0 * (a - b) / rules.max_width) if a > b else 1.5 * (b - a) / rules.max_width
                if len(parts[0]) <= 2 and n > 4:
                    cost += 3.0
                if len(parts[1]) <= 1 and n > 4:
                    cost += 3.0
            else:
                mean = sum(widths) / count
                cost += sum(((w - mean) / rules.max_width) ** 2 for w in widths) * 6.0
            if best is None or cost < best[1] - 1e-9 or (
                    abs(cost - best[1]) <= 1e-9 and widths[-1] > best[2]):
                best = (strs, cost, widths[-1])
        if best:
            return best[0], best[1]
    if max_lines > 3:                       # more lines than that are filled one after the other
        lines, line = [], []
        for tok in toks:
            if line and not _fits(rules, join_tokens(line + [tok])):
                lines.append(line)
                line = []
            line.append(tok)
        lines.append(line)
        strs = [join_tokens(l) for l in lines]
        if len(strs) <= max_lines and all(_fits(rules, s) for s in strs):
            return strs, 50.0
    return None


def wrap(text, rules, max_lines=None):
    """`text` (a string, or the list of words `tokens` made) as at most `max_lines` lines that each
    fit `rules`, or None when it does not fit. One line when it fits one line; otherwise the break
    with the lowest cost: after punctuation, before conjunctions and prepositions, not after an
    article, a preposition, an auxiliary or a subject pronoun, bottom-heavy."""
    toks = tokens(text, rules) if isinstance(text, str) else list(text)
    if not toks:
        return []
    found = _wrap(toks, rules, max_lines or rules.max_lines)
    return None if found is None else found[0]


def _paragraphs(text: str) -> list:
    return [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def split_cues(text: str, rules) -> tuple:
    """Cut `text` into cues. Each cue is one text with its line breaks and fits `rules` in
    `rules.max_lines` lines. Cuts fall at the ends of sentences first, then after commas and
    before conjunctions, so a cue reads as a unit; short sentences share a cue when they fit one
    line. Every word is kept. Returns (cues, notes); a note says that a word was cut."""
    cues, notes = [], []
    for para in _paragraphs(text):
        toks = tokens(para, rules)
        if any(t.startswith(GLUE) for t in toks):
            notes.append("a word is wider than a line and was cut with a hyphen")
        if not toks:
            continue
        n = len(toks)
        ends = [_ends_sentence(t) for t in toks]
        limit = rules.max_chars * rules.max_lines + 12
        best = [math.inf] * (n + 1)
        back = [0] * (n + 1)
        wrapped = {}
        best[0] = 0.0
        for i in range(1, n + 1):
            chars = 0
            for j in range(i - 1, -1, -1):
                chars += len(toks[j]) + 1
                if chars > limit and j < i - 1:
                    break
                if best[j] == math.inf:
                    continue
                found = _wrap(toks[j:i], rules, rules.max_lines)
                if found is None:
                    continue
                lines, shape = found
                inner = sum(1 for k in range(j, i - 1) if ends[k])
                cost = 8.0 + shape + 3.0 * inner
                if len(lines) > 1:
                    cost += 1.5
                if inner and len(join_tokens(toks[j:i])) > rules.max_chars:
                    cost += 6.0
                if chars < 14 and not (j == 0 and i == n):
                    cost += 4.0
                if i < n:
                    cost += break_cost(toks[i - 1], toks[i]) if not toks[i].startswith(GLUE) else 20.0
                if best[j] + cost < best[i]:
                    best[i], back[i] = best[j] + cost, j
                    wrapped[(j, i)] = lines
        if best[n] == math.inf:                     # cannot happen after tokens(); keep the words anyway
            cues.append(join_tokens(toks))
            notes.append("a paragraph could not be fitted")
            continue
        pieces, i = [], n
        while i > 0:
            j = back[i]
            pieces.append("\n".join(wrapped[(j, i)]))
            i = j
        cues.extend(reversed(pieces))
    return cues, notes


# ── 3. Spreading ────────────────────────────────────────────────

def _chars(text: str) -> int:
    return max(1, len(text.replace("\n", " ").strip()))


def spread(texts, scene_s: float, fps, gap=None, min_s=None, max_s=None, max_cps=None) -> tuple:
    """Put cues across a scene of `scene_s` seconds. Returns (spans, report): a list of
    (start_s, end_s) in seconds from the start of the scene, on frames of `fps`, and a report
    {overflow, needed_s, available_s, cues}.

    Each cue gets a share of the scene in proportion to its length in characters, but at least
    the time to read it (max(min_s, characters / max_cps) plus the gap), and shows for at most
    `max_s` at the start of its share. When the cues need more time than the scene has, none is
    dropped: all are shortened in proportion, they still do not overlap, none ends after the
    scene, and the report says `overflow` with the time needed.
    """
    gap = config.SUBTITLE_GAP_SEC if gap is None else gap
    min_s = config.SUBTITLE_MIN_SEC if min_s is None else min_s
    max_s = config.SUBTITLE_MAX_SEC if max_s is None else max_s
    max_cps = config.SUBTITLE_MAX_CPS if max_cps is None else max_cps
    fps = Fraction(fps)
    frame = float(1 / fps)
    n = len(texts)
    report = {"overflow": False, "needed_s": 0.0, "available_s": float(scene_s), "cues": n}
    if n == 0:
        return [], report
    chars = [_chars(t) for t in texts]
    dmin = [min(max_s, max(min_s, c / max_cps)) for c in chars]
    gap = max(gap, config.SUBTITLE_MIN_GAP_FRAMES * frame)
    needed = sum(dmin) + (n - 1) * gap
    report["needed_s"] = round(needed, 3)
    total_frames = int(math.floor(scene_s / frame + 1e-9))

    if needed > scene_s + 1e-9:
        report["overflow"] = True
        g = min(gap, 0.25 * scene_s / max(1, n - 1)) if n > 1 else 0.0
        avail = scene_s - (n - 1) * g
        durs = [avail * d / sum(dmin) for d in dmin]
        starts, at = [], 0.0
        for d in durs:
            starts.append(at)
            at += d + g
        return _to_frames(starts, [s + d for s, d in zip(starts, durs)], fps, total_frames,
                          min_gap_frames=1 if g < config.SUBTITLE_MIN_GAP_FRAMES * frame else None), report

    # slot_i = max(dmin_i + gap, lam * chars_i), the slots add up to the scene plus one gap
    target = scene_s + gap
    lo, hi = 0.0, target / min(chars)
    for _ in range(80):
        lam = (lo + hi) / 2.0
        if sum(max(d + gap, lam * c) for d, c in zip(dmin, chars)) > target:
            hi = lam
        else:
            lo = lam
    slots = [max(d + gap, lo * c) for d, c in zip(dmin, chars)]
    starts, ends, at = [], [], 0.0
    for slot in slots:
        starts.append(at)
        ends.append(at + min(max_s, slot - gap))
        at += slot
    return _to_frames(starts, ends, fps, total_frames,
                      max_frames=int(math.floor(max_s / frame + 1e-9))), report


def _to_frames(starts, ends, fps, total_frames, min_gap_frames=None, max_frames=None):
    """Seconds on whole frames. Starts are rounded, ends are kept at least the minimum gap before
    the next start, and nothing ends after the scene."""
    frame = float(1 / fps)
    gap_frames = min_gap_frames if min_gap_frames is not None else \
        max(config.SUBTITLE_MIN_GAP_FRAMES, int(round(config.SUBTITLE_GAP_SEC / frame)))
    n = len(starts)
    s = [min(total_frames - 1, max(0, int(round(a / frame)))) for a in starts]
    for i in range(1, n):                       # starts strictly rise
        s[i] = max(s[i], s[i - 1] + 1)
    e = []
    for i in range(n):
        end = int(round(ends[i] / frame))
        limit = (s[i + 1] - gap_frames) if i + 1 < n else total_frames
        end = min(end, limit, total_frames)
        if max_frames:
            end = min(end, s[i] + max_frames)
        e.append(max(end, s[i] + 1))
    for i in range(n - 1, 0, -1):               # overflow cases: pull a start back instead of overlapping
        if e[i - 1] > s[i] - 1:
            e[i - 1] = max(s[i - 1] + 1, s[i] - 1)
    return [(round(float(a * fps ** -1), 4), round(float(b * fps ** -1), 4)) for a, b in zip(s, e)]


# ── 4. The film: cues and titles on segments ────────────────────

class OperationError(Exception):
    """An operation is unknown or malformed, or cannot be done. The page gets HTTP 400."""


class WordsError(OperationError):
    """A request about subtitles or titles that cannot be done. The message says why."""


class NeedsConfirmation(WordsError):
    """The scene has cues that the owner made or moved by hand. `cues` lists them; send the
    request again with `replace_adjusted` true to replace them."""

    def __init__(self, message, cues):
        super().__init__(message)
        self.cues = cues


def _fps(data) -> Fraction:
    f = data["film"]["fps"]
    return Fraction(f["num"], f["den"])


def default_rules(data):
    """The line rules of the film's frame, measured with the real font when there is one."""
    import textlayout
    f = data["film"]["frame"]
    return textlayout.rules((f["width"], f["height"]))


RULES_FACTORY = default_rules          # the tests replace this to work without a font


def _subtitles(data, create=False):
    subs = data.get("subtitles")
    if subs is None and create:
        subs = data["subtitles"] = {"language": config.SUBTITLE_LANGUAGE, "cues": []}
    return subs


def _cues(data) -> list:
    subs = data.get("subtitles")
    return subs["cues"] if subs and isinstance(subs.get("cues"), list) else []


def _segment(data, seg_id):
    for s in data["segments"]:
        if s.get("id") == seg_id:
            return s
    raise WordsError(f"there is no scene {seg_id!r}")


def film_segments(data) -> list:
    """The enabled segments in film order: [{seg, index, start_frame, frames, start_s, length_s}]."""
    fps = _fps(data)
    out, at = [], 0
    for i, seg in enumerate(data["segments"]):
        if not seg.get("enabled", True):
            continue
        n = seg["out"] - seg["in"]
        out.append({"seg": seg, "index": i, "start_frame": at, "frames": n,
                    "start_s": float(at / fps), "length_s": float(n / fps)})
        at += n
    return out


def locate(data, film_s: float):
    """The film-segment row (see film_segments) that plays at film second `film_s`, and the second
    from the start of that segment. Raises WordsError outside the film."""
    rows = film_segments(data)
    for row in rows:
        if row["start_s"] - 1e-6 <= film_s < row["start_s"] + row["length_s"] - 1e-6:
            return row, max(0.0, film_s - row["start_s"])
    total = rows[-1]["start_s"] + rows[-1]["length_s"] if rows else 0.0
    raise WordsError(f"there is no scene at {film_s:.2f} s of the film (the film is {total:.2f} s long)")


def cue_spans(data) -> list:
    """The cues that are in the film, with film times (the part inside the scene), in film order:
    [{id, cue, segment, start_s, end_s, text}]. A cue on a scene that is switched off is not in the
    film and is not listed."""
    rows = {r["seg"]["id"]: r for r in film_segments(data)}
    out = []
    for c in _cues(data):
        row = rows.get(c.get("anchor"))
        if row is None:
            continue
        a, b = max(0.0, c["start_s"]), min(row["length_s"], c["end_s"])
        if b <= a:
            continue
        out.append({"id": c["id"], "cue": c, "segment": row["seg"]["id"],
                    "start_s": row["start_s"] + a, "end_s": row["start_s"] + b, "text": c["text"]})
    out.sort(key=lambda s: (s["start_s"], s["end_s"]))
    return out


def next_id(items, prefix, width=3) -> str:
    top = 0
    for it in items:
        m = re.fullmatch(prefix + r"(\d+)", str(it.get("id", "")))
        if m:
            top = max(top, int(m.group(1)))
    return f"{prefix}{top + 1:0{width}d}"


def sort_cues(data) -> None:
    """Keep the cues in film order: by scene (the order of the segments), then by start."""
    subs = data.get("subtitles")
    if not subs or not isinstance(subs.get("cues"), list):
        return
    position = {s.get("id"): i for i, s in enumerate(data["segments"])}
    subs["cues"].sort(key=lambda c: (position.get(c.get("anchor"), 10 ** 6), c.get("start_s", 0)))


def _frame_s(data, seconds: float) -> float:
    """`seconds` moved to the nearest frame of the film, as a number with four decimals."""
    fps = _fps(data)
    return round(float(Fraction(round(seconds * fps)) / fps), 4)


def _new_cue(data, anchor, start, end, text, origin, locked):
    c = {"id": None, "anchor": anchor, "start_s": start, "end_s": end, "text": text, "origin": origin}
    if locked:
        c["locked"] = True
    return c


def _assign_ids(data, new_cues):
    cues = _cues(data)
    taken = list(cues)
    for c in new_cues:
        c["id"] = next_id(taken, "c")
        taken.append(c)


def _fit_text(text: str, rules) -> list:
    """The text of one cue as cues that fit: [text] when it fits two lines (an explicit line break is
    kept when both lines fit), else the text split into several."""
    text = "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.strip().split("\n"))
    lines = text.split("\n")
    if len(lines) <= rules.max_lines and all(_fits(rules, line) for line in lines):
        return [text]
    flat = re.sub(r"\s+", " ", text).strip()
    cues, _ = split_cues(flat, rules)
    return cues


def _place(data, anchor_row, rel_start, rel_end, texts, rules, origin, locked, notes) -> list:
    """Cues for `texts` inside [rel_start, rel_end] of the scene (seconds from its start)."""
    if len(texts) == 1:
        spans = [(rel_start, rel_end)]
    else:
        spans, rep = spread(texts, rel_end - rel_start, _fps(data))
        spans = [(round(rel_start + a, 4), round(rel_start + b, 4)) for a, b in spans]
        if notes is not None:
            notes.append(f"the text is long, so it became {len(texts)} cues")
            if rep["overflow"]:
                notes.append(f"the cues need {rep['needed_s']:.1f} s and only "
                             f"{rep['available_s']:.1f} s are free, so they are shorter than a person can read")
    return [_new_cue(data, anchor_row["seg"]["id"], a, b, t, origin, locked)
            for t, (a, b) in zip(texts, spans)]


def _clean_cue_text(text) -> str:
    if not isinstance(text, str) or not text.strip():
        raise WordsError("the cue text must not be empty")
    return text


def _default_end(data, start, text, scene_len):
    need = max(config.SUBTITLE_MIN_SEC, min(config.SUBTITLE_MAX_SEC, _chars(text) / config.SUBTITLE_MAX_CPS))
    return min(scene_len, _frame_s(data, start + need))


def _num(op, key, required=True):
    v = op.get(key)
    if v is None:
        if required:
            raise WordsError(f"operation {op.get('op')!r} needs {key!r}, a number of seconds")
        return None
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
        raise WordsError(f"{key!r} must be a number of seconds")
    return float(v)


def add_cue(data, film_start_s, film_end_s=None, text="", origin="manual", locked=True,
            notes=None, rules=None) -> list:
    """Add a cue at film time (the part of the film is found, the scene is the anchor). The end
    defaults to the time the text needs. A text that does not fit two lines becomes several cues
    spread over the time. Returns the ids of the cues made."""
    text = _clean_cue_text(text)
    rules = rules or RULES_FACTORY(data)
    row, rel = locate(data, film_start_s)
    rel = _frame_s(data, rel)
    if film_end_s is None:
        end = _default_end(data, rel, text, row["length_s"])
    else:
        end = _frame_s(data, film_end_s - row["start_s"])
        if end > row["length_s"] + 1e-6:
            end = _frame_s(data, row["length_s"])
            if notes is not None:
                notes.append(f"the cue was shortened to end with its scene ({row['seg']['id']})")
    if end <= rel:
        raise WordsError("the cue must end after it starts")
    made = _place(data, row, rel, end, _fit_text(text, rules), rules, origin, locked, notes)
    _assign_ids(data, made)
    _subtitles(data, create=True)["cues"].extend(made)
    sort_cues(data)
    return [c["id"] for c in made]


def _find_cue(data, cue_id):
    for c in _cues(data):
        if c.get("id") == cue_id:
            return c
    raise WordsError(f"there is no cue {cue_id!r}")


def remove_cue(data, cue_id) -> None:
    c = _find_cue(data, cue_id)
    _cues(data).remove(c)


def edit_cue(data, cue_id, text=None, film_start_s=None, film_end_s=None, notes=None, rules=None):
    """Change a cue: its text and/or its film times. The cue is marked as changed by hand (locked).
    A new start in another scene moves the cue there. A text that does not fit becomes several cues."""
    cue = _find_cue(data, cue_id)
    rules = rules or RULES_FACTORY(data)
    rows = {r["seg"]["id"]: r for r in film_segments(data)}
    row = rows.get(cue["anchor"])
    start, end = cue["start_s"], cue["end_s"]
    if film_start_s is not None or film_end_s is not None:
        if row is None:
            raise WordsError(f"cue {cue_id} is on a scene that is switched off, so it has no film time")
        f0 = row["start_s"] + start if film_start_s is None else film_start_s
        f1 = row["start_s"] + end if film_end_s is None else film_end_s
        if f1 <= f0:
            raise WordsError("the cue must end after it starts")
        new_row, rel = locate(data, f0)
        start = _frame_s(data, rel)
        end = _frame_s(data, f1 - new_row["start_s"])
        if end > new_row["length_s"] + 1e-6:
            end = _frame_s(data, new_row["length_s"])
            if notes is not None:
                notes.append(f"the cue was shortened to end with its scene ({new_row['seg']['id']})")
        cue["anchor"], row = new_row["seg"]["id"], new_row
    cue["start_s"], cue["end_s"] = start, end
    if text is not None:
        text = _clean_cue_text(text)
        parts = _fit_text(text, rules)
        if len(parts) == 1:
            cue["text"] = parts[0]
        else:
            if row is None:
                raise WordsError(f"cue {cue_id} is on a scene that is switched off; its text must fit two lines")
            made = _place(data, row, start, end, parts, rules, cue.get("origin", "manual"), True, notes)
            cue["start_s"], cue["end_s"], cue["text"] = made[0]["start_s"], made[0]["end_s"], made[0]["text"]
            extra = made[1:]
            _assign_ids(data, extra)
            _cues(data).extend(extra)
    cue["locked"] = True
    sort_cues(data)


def shift_cues(data, seconds, ids=None, from_s=None, to_s=None, notes=None) -> int:
    """Move cues by `seconds` of film time (negative: earlier). Without `ids` all cues in the film
    move, or those that start within [from_s, to_s]. Every cue must stay inside the film; a cue that
    would run past the end of its scene is shortened. All or none. Returns how many moved."""
    spans = cue_spans(data)
    chosen = []
    for s in spans:
        if ids is not None and s["id"] not in ids:
            continue
        if from_s is not None and s["start_s"] < from_s - 1e-9:
            continue
        if to_s is not None and s["start_s"] > to_s + 1e-9:
            continue
        chosen.append(s)
    if ids is not None:
        missing = set(ids) - {s["id"] for s in chosen}
        if missing:
            raise WordsError("these cues are not in the film or do not exist: " + ", ".join(sorted(missing)))
    moves = []
    for s in chosen:
        row, rel = locate(data, s["start_s"] + seconds)
        start = _frame_s(data, rel)
        end = min(_frame_s(data, s["end_s"] + seconds - row["start_s"]), _frame_s(data, row["length_s"]))
        if end <= start:
            raise WordsError(f"cue {s['id']} would not fit in its scene after the shift")
        moves.append((s["cue"], row["seg"]["id"], start, end))
    for cue, anchor, start, end in moves:
        cue["anchor"], cue["start_s"], cue["end_s"] = anchor, start, end
    sort_cues(data)
    return len(moves)


def _scene_cues(data, seg_id):
    return [c for c in _cues(data) if c.get("anchor") == seg_id]


def _adjusted(c) -> bool:
    """A cue that a person made or changed: not generated, or locked."""
    return c.get("origin", "manual") == "manual" or bool(c.get("locked"))


def regenerate_scene(data, seg_id, rules=None, notes=None) -> dict:
    """Replace the generated cues of a scene (origin text or auto, not locked) by cues made from the
    scene's text, spread across the scene. Returns the report of `spread`."""
    seg = _segment(data, seg_id)
    rules = rules or RULES_FACTORY(data)
    keep = [c for c in _cues(data) if c.get("anchor") != seg_id]
    text = seg.get("text", "")
    report = {"overflow": False, "needed_s": 0.0, "available_s": 0.0, "cues": 0}
    made = []
    if text.strip():
        pieces, split_notes = split_cues(text, rules)
        scene_s = float((seg["out"] - seg["in"]) / _fps(data))
        spans, report = spread(pieces, scene_s, _fps(data))
        made = [_new_cue(data, seg_id, a, b, t, "text", False) for t, (a, b) in zip(pieces, spans)]
        if notes is not None:
            notes.extend(split_notes)
            if report["overflow"]:
                notes.append(f"scene {seg_id}: the text needs {report['needed_s']:.1f} s and the scene is "
                             f"{report['available_s']:.1f} s long, so the cues are shorter than a person "
                             f"can read; shorten the text or lengthen the scene")
    subs = _subtitles(data, create=True)
    subs["cues"] = keep
    _assign_ids(data, made)
    subs["cues"].extend(made)
    sort_cues(data)
    return report


def set_scene_text(data, seg_id, text, replace_adjusted=False, notes=None, rules=None) -> dict:
    """The scene's text box (SPECS 9.4): clean the text, split it into cues, spread them across the
    scene. Cues that a person made or moved by hand are not replaced unless `replace_adjusted`;
    without it NeedsConfirmation lists them and nothing changes. Returns the report."""
    seg = _segment(data, seg_id)
    cleaned, clean_report = clean_text(text if isinstance(text, str) else "")
    old = _scene_cues(data, seg_id)
    adjusted = [c for c in old if _adjusted(c)]
    if cleaned == seg.get("text", "") and (old or not cleaned):
        if notes is not None:
            notes.append(f"scene {seg_id}: the text did not change")
        return {"overflow": False, "needed_s": 0.0, "available_s": 0.0, "cues": len(old),
                "unchanged": True, **clean_report}
    if adjusted and not replace_adjusted:
        raise NeedsConfirmation(
            f"scene {seg_id} has {len(adjusted)} cue(s) that were made or moved by hand. "
            f"Replace them with cues from the new text?", [c["id"] for c in adjusted])
    if cleaned:
        seg["text"] = cleaned
    else:
        seg.pop("text", None)
    report = regenerate_scene(data, seg_id, rules=rules, notes=notes)
    report.update(clean_report)
    return report


def retime_segment(data, seg_id, old_in, old_out, notes=None, rules=None) -> None:
    """The scene `seg_id` was trimmed (its in and out changed from `old_in` and `old_out`): keep the
    cues with the picture they belong to. Cues that came from the scene's text and were not moved by
    hand are made again across the new length. All other cues move with their picture and are cut at
    the new ends; a cue with less than config.SUBTITLE_CLIP_KEEP_SEC left is dropped."""
    seg = _segment(data, seg_id)
    cues = _scene_cues(data, seg_id)
    if not cues:
        return
    fps = _fps(data)
    shift = float(Fraction(seg["in"] - old_in) / fps)
    length = float(Fraction(seg["out"] - seg["in"]) / fps)
    text_cues = [c for c in cues if c.get("origin") == "text"]
    if seg.get("text") and text_cues and not any(_adjusted(c) for c in text_cues):
        subs = _subtitles(data)
        subs["cues"] = [c for c in subs["cues"] if c not in text_cues]
        regenerate_scene(data, seg_id, rules=rules, notes=notes)
        cues = [c for c in _scene_cues(data, seg_id) if c.get("origin") != "text"]
    dropped = []
    for c in cues:
        a, b = c["start_s"] - shift, c["end_s"] - shift
        ca, cb = max(0.0, a), min(length, b)
        if cb - ca <= 1e-9 or (cb - ca < config.SUBTITLE_CLIP_KEEP_SEC - 1e-9 and (ca > a or cb < b)):
            dropped.append(c)
            continue
        c["start_s"], c["end_s"] = _frame_s(data, ca), _frame_s(data, cb)
    if dropped:
        subs = _subtitles(data)
        subs["cues"] = [c for c in subs["cues"] if c not in dropped]
        if notes is not None:
            notes.append(f"scene {seg_id}: {len(dropped)} cue(s) fell outside the trimmed scene and were removed")
    sort_cues(data)


# ── Titles ──────────────────────────────────────────────────────

def _title_times(data, anchor, start, end):
    """Seconds from the start of the anchor. For the film they are film seconds."""
    if anchor != "film":
        _segment(data, anchor)
    if end <= start:
        raise WordsError("the title must end after it starts")
    return round(start, 4), round(end, 4)


def add_title(data, text, start_s, end_s=None, length_s=None, anchor="film", template=None,
              size_pct=None, fade_s=None) -> str:
    if not isinstance(text, str) or not text.strip():
        raise WordsError("the title text must not be empty")
    if end_s is None:
        end_s = start_s + (4.0 if length_s is None else length_s)
    start_s, end_s = _title_times(data, anchor, start_s, end_s)
    title = {"id": next_id(data.get("titles") or [], "t"), "text": text.strip(), "anchor": anchor,
             "start_s": start_s, "end_s": end_s}
    if template is not None:
        title["template"] = template
    if size_pct is not None:
        title["size_pct"] = size_pct
    if fade_s is not None:
        title["fade_s"] = fade_s
    data.setdefault("titles", []).append(title)
    return title["id"]


def _find_title(data, title_id):
    for t in data.get("titles") or []:
        if t.get("id") == title_id:
            return t
    raise WordsError(f"there is no title {title_id!r}")


def edit_title(data, title_id, text=None, start_s=None, end_s=None, length_s=None, anchor=None,
               template=None, size_pct=None, fade_s=None) -> None:
    t = _find_title(data, title_id)
    if text is not None:
        if not isinstance(text, str) or not text.strip():
            raise WordsError("the title text must not be empty")
        t["text"] = text.strip()
    a = t["anchor"] if anchor is None else anchor
    s = t["start_s"] if start_s is None else start_s
    e = t["end_s"] if end_s is None else end_s
    if length_s is not None:
        e = s + length_s
    s, e = _title_times(data, a, s, e)
    t["anchor"], t["start_s"], t["end_s"] = a, s, e
    for key, value in (("template", template), ("size_pct", size_pct), ("fade_s", fade_s)):
        if value is not None:
            t[key] = value


def remove_title(data, title_id) -> None:
    t = _find_title(data, title_id)
    data["titles"].remove(t)
    if not data["titles"]:
        data.pop("titles")


# ── Importing what Claude Code or a file supplies ───────────────

def import_cues(data, items, replace=False, notes=None, rules=None) -> list:
    """Add cues from a list of {start_s, end_s, text} in film seconds; an item with `anchor` (a scene)
    has times from the start of that scene. The cues are marked as made by hand. With `replace` every
    cue is removed first. The caller validates; a result that breaks a rule is refused."""
    if not isinstance(items, list):
        raise WordsError("the cues must be a list")
    if replace and data.get("subtitles"):
        data["subtitles"]["cues"] = []
    made = []
    for n, it in enumerate(items):
        if not isinstance(it, dict):
            raise WordsError(f"cue {n + 1} must be an object with start_s, end_s and text")
        start, end = _num(it, "start_s"), _num(it, "end_s", required=False)
        if "anchor" in it:
            row = {r["seg"]["id"]: r for r in film_segments(data)}.get(it["anchor"])
            if row is None:
                raise WordsError(f"cue {n + 1}: the scene {it['anchor']!r} is not in the film")
            start += row["start_s"]
            end = None if end is None else end + row["start_s"]
        made += add_cue(data, start, end, it.get("text", ""), origin="manual", locked=True,
                        notes=notes, rules=rules)
    return made


def import_titles(data, items, replace=False) -> list:
    if not isinstance(items, list):
        raise WordsError("the titles must be a list")
    if replace:
        data.pop("titles", None)
    made = []
    for n, it in enumerate(items):
        if not isinstance(it, dict):
            raise WordsError(f"title {n + 1} must be an object with text, start_s and end_s")
        made.append(add_title(data, it.get("text"), _num(it, "start_s"), _num(it, "end_s", False),
                              _num(it, "length_s", False), it.get("anchor", "film"),
                              it.get("template"), it.get("size_pct"), it.get("fade_s")))
    return made


# ── SRT ─────────────────────────────────────────────────────────

def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_text(spans, rules=None) -> str:
    """The text of an SRT file for `spans` (cue_spans). A cue whose lines do not fit `rules` is
    wrapped again; the cues are numbered from 1 in film order."""
    blocks = []
    for n, s in enumerate(spans, start=1):
        text = s["text"].strip()
        if rules is not None:
            lines = text.split("\n")
            if len(lines) > rules.max_lines or not all(_fits(rules, line) for line in lines):
                wrapped = wrap(re.sub(r"\s+", " ", text), rules)
                if wrapped:
                    text = "\n".join(wrapped)
        a, b = srt_time(s["start_s"]), srt_time(s["end_s"])
        if a == b:
            b = srt_time(s["end_s"] + 0.001)
        blocks.append(f"{n}\n{a} --> {b}\n{text}\n")
    return "\n".join(blocks)


_SRT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


def parse_srt(text: str) -> list:
    """The cues of an SRT file as [{start_s, end_s, text}]. Raises WordsError for a block it cannot read."""
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = [l for l in block.split("\n")]
        if not lines or not "".join(lines).strip():
            continue
        if re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]
        if not lines or "-->" not in lines[0]:
            raise WordsError(f"SRT block without a time line: {block[:40]!r}")
        a, b = (x.strip() for x in lines[0].split("-->")[:2])
        times = []
        for part in (a, b):
            m = _SRT_TIME.match(part)
            if not m:
                raise WordsError(f"SRT time not understood: {part!r}")
            h, mi, s, ms = m.groups()
            times.append(int(h) * 3600 + int(mi) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0)
        out.append({"start_s": times[0], "end_s": times[1], "text": "\n".join(lines[1:]).strip()})
    return out


# ── The operations of the page and the commands ─────────────────

def _op_set_scene_text(data, op, notes):
    if not isinstance(op.get("segment"), str):
        raise WordsError("operation 'set_scene_text' needs 'segment' (a scene id)")
    if not isinstance(op.get("text"), str):
        raise WordsError("operation 'set_scene_text' needs 'text'")
    set_scene_text(data, op["segment"], op["text"], replace_adjusted=op.get("replace_adjusted") is True,
                   notes=notes)


def _op_add_cue(data, op, notes):
    add_cue(data, _num(op, "film_start_s"), _num(op, "film_end_s", False), op.get("text", ""), notes=notes)


def _op_edit_cue(data, op, notes):
    if not isinstance(op.get("cue"), str):
        raise WordsError("operation 'edit_cue' needs 'cue' (a cue id)")
    edit_cue(data, op["cue"], text=op.get("text"), film_start_s=_num(op, "film_start_s", False),
             film_end_s=_num(op, "film_end_s", False), notes=notes)


def _op_remove_cue(data, op, notes):
    ids = op.get("cues") if "cues" in op else [op.get("cue")]
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
        raise WordsError("operation 'remove_cue' needs 'cue' (a cue id) or 'cues' (a list)")
    for i in ids:
        _find_cue(data, i)
    for i in ids:
        remove_cue(data, i)


def _op_shift_cues(data, op, notes):
    ids = op.get("cues")
    if ids is not None and not (isinstance(ids, list) and all(isinstance(i, str) for i in ids)):
        raise WordsError("'cues' must be a list of cue ids")
    shift_cues(data, _num(op, "seconds"), ids=ids, from_s=_num(op, "from_s", False),
               to_s=_num(op, "to_s", False), notes=notes)


def _op_sort_cues(data, op, notes):
    sort_cues(data)


def _op_set_export_subtitles(data, op, notes):
    form = op.get("form")
    if form not in config.SUBTITLE_FORMS:
        raise WordsError(f"'form' must be one of {', '.join(config.SUBTITLE_FORMS)}")
    for e in data.get("exports") or []:
        if e.get("name") == op.get("export"):
            e["subtitles"] = form
            return
    raise WordsError(f"there is no export {op.get('export')!r}")


def _op_import_cues(data, op, notes):
    import_cues(data, op.get("cues"), replace=op.get("replace") is True, notes=notes)


def _title_args(op):
    return dict(start_s=_num(op, "start_s", False), end_s=_num(op, "end_s", False),
                length_s=_num(op, "length_s", False), template=op.get("template"),
                size_pct=_num(op, "size_pct", False), fade_s=_num(op, "fade_s", False))


def _op_add_title(data, op, notes):
    a = _title_args(op)
    if a["start_s"] is None:
        raise WordsError("operation 'add_title' needs 'start_s'")
    add_title(data, op.get("text"), a.pop("start_s"), anchor=op.get("anchor", "film"), **a)


def _op_edit_title(data, op, notes):
    if not isinstance(op.get("title"), str):
        raise WordsError("operation 'edit_title' needs 'title' (a title id)")
    edit_title(data, op["title"], text=op.get("text"), anchor=op.get("anchor"), **_title_args(op))


def _op_remove_title(data, op, notes):
    if not isinstance(op.get("title"), str):
        raise WordsError("operation 'remove_title' needs 'title' (a title id)")
    remove_title(data, op["title"])


def _op_import_titles(data, op, notes):
    import_titles(data, op.get("titles"), replace=op.get("replace") is True)


OPERATIONS = {
    "set_scene_text": _op_set_scene_text,
    "add_cue": _op_add_cue,
    "edit_cue": _op_edit_cue,
    "remove_cue": _op_remove_cue,
    "shift_cues": _op_shift_cues,
    "sort_cues": _op_sort_cues,
    "import_cues": _op_import_cues,
    "set_export_subtitles": _op_set_export_subtitles,
    "add_title": _op_add_title,
    "edit_title": _op_edit_title,
    "remove_title": _op_remove_title,
    "import_titles": _op_import_titles,
}
