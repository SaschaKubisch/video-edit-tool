"""
Line breaks for the languages that are written without spaces: Japanese, Simplified Chinese and Thai (SPECS 9.9).

A translated cue in these languages is one string with no spaces between the words, so the line cannot be cut at a
space. The earlier code cut by the count of characters and split words (ハンド|ブレンダー, 倒|进, จา|ก). This module cuts
at the boundaries of phrases only.

- The boundaries come from BudouX (github.com/google/budoux, Apache-2.0, pure Python, no other package): a small
  machine-learned model per language that splits a sentence into phrases that read as units. The model is used
  as it is; this module does not segment text itself.
- Line-start and line-end rules are applied on top of the boundaries (`allowed`): a boundary where a rule would be
  broken is not a place to cut. The rule sets are those of W3C "Requirements for Japanese Text Layout" (JLREQ,
  3.1.7, 3.1.8, 3.1.10 and the character classes of its annex A) and W3C "Requirements for Chinese Text Layout"
  (CLREQ, 6.1.1 level "basic", 6.1.2). Thai has no W3C table of such characters; its few rules are in THAI_* below.
- `break_lines` picks the cuts: the fewest lines first, then the lowest cost: after punctuation or at a space
  where there is one, and the lower line the longer when there are two lines (the same bottom-heavy rule as the
  English wrapping in words.py).
- A phrase that is wider than a line by itself is the only thing that is cut inside; the result says so
  (`Layout.split_phrase`) so that the caller can warn.
"""

import functools
import unicodedata
from dataclasses import dataclass

# ── The rules ───────────────────────────────────────────────────

# Characters that do not start a line, Japanese (JLREQ 3.1.7): closing brackets (cl-02), hyphens (cl-03), dividing
# punctuation (cl-04), middle dots (cl-05), full stops (cl-06), commas (cl-07), iteration marks (cl-09), the prolonged
# sound mark (cl-10), small kana (cl-11), and the postfixed abbreviations (cl-13, 3.1.10: kept with their numeral).
_JA_CLOSING = "’”）〕］｝〉》」』】〙〗〟｠»"
_JA_HYPHENS = "‐–〜゠"
_JA_DIVIDING = "？！‼⁇⁈⁉"
_JA_MIDDLE_DOTS = "・：；･"
_JA_STOPS = "。．"
_JA_COMMAS = "、，"
_JA_ITERATION = "ヽヾゝゞ々〻"
_JA_PROLONGED = "ーｰ"
_JA_SMALL_KANA = "ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ"
_POSTFIXED = "°′″℃￠％‰%"
# Characters that do not end a line, Japanese (JLREQ 3.1.8): opening brackets (cl-01); and the prefixed
# abbreviations (cl-12, 3.1.10: kept with their numeral).
_JA_OPENING = "‘“（〔［｛〈《「『【〘〖〝｟«"
_PREFIXED = "￥＄￡＃¥$£#"

# Chinese (CLREQ 6.1.1, level "basic", the one the document recommends): pause and stop marks, closing quotation
# marks, closing brackets and book-title marks, connector marks, the interpunct and the solidus do not start a line;
# opening quotation marks, brackets and book-title marks do not end one.
_ZH_NOT_START = "，、。．；：！？’”）〕］｝〉》」』】〗〙–‐〜～·・／/"
_ZH_NOT_END = "‘“（〔［｛〈《「『【〖〘"

# ASCII marks: the same for every language here.
_ASCII_NOT_START = ".,!?;:)]}"
_ASCII_NOT_END = "([{"

# Thai: a mark above or below a consonant (category Mn) and the vowels and signs that follow a consonant (ะ า ำ ๅ
# and the repetition and abbreviation signs ๆ ฯ) do not start a line; a vowel that is written before its consonant
# (เ แ โ ใ ไ) does not end one [inference from how the script is written; the W3C Thai layout note defers to
# the Thai Orthography Notes for line breaking and lists no characters].
THAI_NOT_START = "ะาำๅๆฯ"
THAI_NOT_END = "เแโใไ"

_NOT_START = {
    "ja": set(_JA_CLOSING + _JA_HYPHENS + _JA_DIVIDING + _JA_MIDDLE_DOTS + _JA_STOPS + _JA_COMMAS + _JA_ITERATION
              + _JA_PROLONGED + _JA_SMALL_KANA + _POSTFIXED + _ASCII_NOT_START),
    "zh-Hans": set(_ZH_NOT_START + _POSTFIXED + _ASCII_NOT_START),
    "th": set(THAI_NOT_START + _ASCII_NOT_START),
}
_NOT_END = {
    "ja": set(_JA_OPENING + _PREFIXED + _ASCII_NOT_END),
    "zh-Hans": set(_ZH_NOT_END + _PREFIXED + _ASCII_NOT_END),
    "th": set(THAI_NOT_END + _ASCII_NOT_END),
}
_STOPS = set("，。、！？；：,.!?;:．")                      # a cut after one of these is the best place for a cut
_INSEPARABLE = set("—―…‥")                               # two in a row stay together (JLREQ 3.1.10, CLREQ 6.1.2.1)
LANGUAGES = tuple(_NOT_START)

# Costs: a cut at a space or after punctuation is free; between two phrases it costs a little; inside a phrase that is
# wider than a line it costs a lot (it is only used when nothing else fits).
COST_AFTER_STOP = 0.0
COST_BETWEEN_PHRASES = 3.0
COST_INSIDE_PHRASE = 50.0
MAX_LINES_TRIED = 8


class BudouXMissing(Exception):
    """The BudouX package is not installed in the environment of the tool."""


# ── Phrases ─────────────────────────────────────────────────────

@functools.lru_cache(maxsize=None)
def _parser(lang: str):
    try:
        import budoux
    except ImportError:
        raise BudouXMissing("The package budoux is not installed. It cuts Japanese, Chinese and Thai subtitles at "
                            "phrase boundaries. Install it: uv pip install --python .venv/bin/python -r "
                            "requirements.txt") from None
    return {"ja": budoux.load_default_japanese_parser,
            "zh-Hans": budoux.load_default_simplified_chinese_parser,
            "th": budoux.load_default_thai_parser}[lang]()


def boundaries(text: str, lang: str) -> list:
    """The indexes in `text` where BudouX puts a boundary between two phrases (not 0 and not the end)."""
    out, at = [], 0
    for chunk in _parser(lang).parse(text)[:-1]:
        at += len(chunk)
        out.append(at)
    return out


# ── Where a line may end ────────────────────────────────────────

def _wordish(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in "'’-_/@&#+")


def allowed(text: str, i: int, lang: str) -> bool:
    """May a line end before text[i], with no space at the cut (a space is skipped first)?"""
    if not 0 < i < len(text) or text[i] == " ":
        return False
    p = i - 1
    while p >= 0 and text[p] == " ":
        p -= 1
    if p < 0:
        return False
    prev, nxt = text[p], text[i]
    if nxt in _NOT_START[lang] or prev in _NOT_END[lang]:
        return False
    if unicodedata.category(nxt) == "Mn":
        return False
    if p != i - 1:                                          # a space is at the cut: nothing more to check
        return True
    if _wordish(prev) and _wordish(nxt):                    # inside a Latin word, a number or a code
        return False
    if prev == nxt and prev in _INSEPARABLE:                # —— and …… are one mark
        return False
    if prev.isdigit() and (nxt in _POSTFIXED or (nxt in ".," and text[i + 1:i + 2].isdigit())):
        return False                                        # 50%, 3.5
    if prev in ".," and nxt.isdigit() and p >= 1 and text[p - 1].isdigit():
        return False
    if prev in _PREFIXED and nxt.isdigit():
        return False
    return True


# ── The layout ──────────────────────────────────────────────────

@dataclass
class Layout:
    lines: list
    split_phrase: bool = False       # a phrase wider than a line was cut inside


def _candidates(text: str, lang: str, limit: float, measure) -> dict:
    """The places where a line may end: {index: cost}. Phrase boundaries first; inside a phrase only when that
    phrase is wider than a line by itself."""
    cuts = {}
    n = len(text)
    for i in range(1, n):
        if text[i] == " ":
            continue
        if text[i - 1] == " " and allowed(text, i, lang):
            cuts[i] = COST_AFTER_STOP
    for i in boundaries(text, lang):
        j = i
        while j < n and text[j] == " ":                     # a boundary in front of a space belongs after it
            j += 1
        if j in cuts or not allowed(text, j, lang):
            continue
        prev = text[j - 1] if text[j - 1] != " " else text[:j].rstrip()[-1:]
        cuts[j] = COST_AFTER_STOP if prev in _STOPS else COST_BETWEEN_PHRASES
    for i in range(1, n):                                   # a stop mark that BudouX did not cut after
        if i not in cuts and text[i - 1] in _STOPS and allowed(text, i, lang):
            cuts[i] = COST_AFTER_STOP
    marks = sorted(cuts)
    edges = [0] + marks + [n]
    for a, b in zip(edges, edges[1:]):
        if measure(text[a:b].strip()) > limit:              # a phrase wider than a line: any allowed place inside
            for i in range(a + 1, b):
                if i not in cuts and allowed(text, i, lang):
                    cuts[i] = COST_INSIDE_PHRASE
    return cuts


def _shape(widths: list, limit: float) -> float:
    """How uneven the lines are. Two lines: the lower one may be longer (cheap), the upper one longer costs more."""
    if len(widths) == 2:
        top, bottom = widths
        return (1.0 + 3.0 * (top - bottom) / limit) if top > bottom else 0.5 * (bottom - top) / limit
    mean = sum(widths) / len(widths)
    return 4.0 * sum(((w - mean) / limit) ** 2 for w in widths) + (1.0 if widths[0] > widths[-1] else 0.0)


def _compositions(text: str, edges: list, k: int, limit: float, measure):
    """Every way to cut `text` into exactly k lines at the places in `edges` (sorted, ending with len(text)) with
    every line within `limit`. Yields the list of cut indexes, the last one len(text)."""
    n = len(text)

    def go(start, left, cuts):
        if left == 1:
            if measure(text[start:].strip()) <= limit:
                yield cuts + [n]
            return
        if measure(text[start:].strip()) > left * limit + 1e-9:
            return
        for e in edges:
            if e <= start or e >= n:
                continue
            if measure(text[start:e].strip()) > limit:
                break
            yield from go(e, left - 1, cuts + [e])

    yield from go(0, k, [])


def break_lines(text: str, lang: str, limit: float, measure) -> Layout:
    """`text` as the fewest lines of at most `limit` (as `measure` counts it) that are cut at phrase boundaries
    only, with the line-start and line-end rules kept. One line when it fits. When no cut of up to
    MAX_LINES_TRIED lines works (a word wider than a line that cannot be cut at an allowed place), the text is
    cut by the count and the result says `split_phrase`."""
    text = " ".join(str(text).split())
    if measure(text) <= limit:
        return Layout([text], False)
    cuts = _candidates(text, lang, limit, measure)
    edges = sorted(cuts)
    n = len(text)
    for k in range(2, MAX_LINES_TRIED + 1):
        best = None
        for comp in _compositions(text, edges, k, limit, measure):
            starts = [0] + comp[:-1]
            widths = [measure(text[a:b].strip()) for a, b in zip(starts, comp)]
            cost = sum(cuts[c] for c in comp[:-1]) + _shape(widths, limit)
            if best is None or cost < best[0] - 1e-9:
                best = (cost, comp)
        if best:
            comp = best[1]
            starts = [0] + comp[:-1]
            return Layout([text[a:b].strip() for a, b in zip(starts, comp)],
                          any(cuts[c] == COST_INSIDE_PHRASE for c in comp[:-1]))
    return Layout(_hard_wrap(text, lang, limit, measure), True)


def _hard_wrap(text: str, lang: str, limit: float, measure) -> list:
    """The last resort: fill each line to the limit and cut at the nearest allowed place before it, or at the limit."""
    lines, line = [], ""
    for ch in text:
        if line and measure((line + ch).strip()) > limit:
            j = len(line)
            probe = line + ch
            while j >= 1 and not allowed(probe, j, lang):
                j -= 1
            if j < 1 or measure(line[:j].strip()) == 0:             # no allowed place on this line: cut at the limit
                j = len(line)
            lines.append(line[:j].strip())
            line = line[j:].lstrip()
        line += ch
    lines.append(line.strip())
    return [l for l in lines if l]
