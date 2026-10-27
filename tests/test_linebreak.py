"""Check 4.18: line breaks of Japanese, Simplified Chinese and Thai subtitles (SPECS 9.9). The cuts fall at phrase
boundaries that BudouX gives, the line-start and line-end rules of W3C JLREQ and CLREQ are kept, and the lines are
balanced within the limit of the language. The strings are those of the first real translation of 11 cues
(2026-10-27, model google/gemini-2.5-flash-lite), whose lines were cut inside words: ハン|ドブレンダー, 倒|进, จา|ก,
and a line that began with ー."""

import os
import unicodedata
import unittest
from unittest import mock

import budoux

import config
import linebreak
import translate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (id, English source, duration in seconds, ja, zh-Hans, th) of timeline-full-translate.json, first real run.
REAL = [
    ("c001", "She holds a hand blender in a tall jar.", 6.9736,
     "彼女は背の高い容器にハンドブレンダーを持っている。", "她手里拿着一个高罐子里的手持搅拌棒。",
     "เธอใช้เครื่องปั่นมือถือในโถสูง"),
    ("c002", "The dressing turns pale and smooth.", 6.9737,
     "ドレッシングは淡く滑らかになる。", "酱汁变得颜色变浅，变得顺滑。", "น้ำสลัดเปลี่ยนเป็นสีอ่อนและเนียน"),
    ("c003", "Small bowls of ingredients wait on the counter.", 6.9737,
     "材料の入った小さなボウルがカウンターに並んでいる。", "小碗的配料放在台面上。",
     "ส่วนผสมในถ้วยเล็กๆ วางรออยู่บนเคาน์เตอร์"),
    ("c004", "She pours a pale yellow dressing from the jar into a small white teapot.", 6.9736,
     "彼女は淡い黄色のドレッシングを容器から小さな白いティーポットに注ぐ。",
     "她把罐子里淡黄色的酱汁倒进一个小小的白色茶壶里。", "เธอเทน้ำสลัดสีเหลืองอ่อนจากโถลงในกาน้ำชาสีขาวใบเล็ก"),
    ("c005", "A clay mortar and a cutting board stand ready.", 6.64,
     "粘土のすり鉢とまな板が準備されている。", "一个陶土研钵和一个砧板准备就绪。", "ครกดินและเขียงตั้งเตรียมไว้"),
    ("c006", "The table is laid with a plate, a spoon and a fork.", 5.0717,
     "テーブルには皿、スプーン、フォークが並べられている。", "桌子上摆着一个盘子、一把勺子和一把叉子。",
     "บนโต๊ะมีจาน ช้อน และส้อมวางอยู่"),
    ("c007", "A hand puts the green salad bowl on the table.", 4.5379,
     "手が緑のサラダボウルをテーブルに置く。", "一只手把绿色的沙拉碗放到桌子上。",
     "มีมือหนึ่งวางชามสลัดสีเขียวลงบนโต๊ะ"),
    ("c008", "A round wooden tray stands beside it.", 3.637,
     "その横には丸い木製のトレイが置かれている。", "旁边放着一个圆形的木托盘。", "ถาดไม้ทรงกลมวางอยู่ข้างๆ"),
    ("c009", "The red blender runs with a purple mixture inside.", 3.5702,
     "赤いブレンダーが紫色の混ぜ物が入った状態で動いている。", "红色的搅拌机在运转，里面是紫色的混合物。",
     "เครื่องปั่นสีแดงทำงานกับส่วนผสมสีม่วงข้างใน"),
    ("c010", "She keeps one hand on the lid.", 2.0688,
     "彼女は蓋に片手を添えている。", "她一只手扶着盖子。", "เธอใช้มือหนึ่งจับฝาไว้"),
    ("c011", "A glass of water stands on the counter.", 2.7694,
     "カウンターには水の入ったグラスが置かれている。", "台面上放着一杯水。", "แก้วน้ำวางอยู่บนเคาน์เตอร์"),
]
COLUMN = {"ja": 3, "zh-Hans": 4, "th": 5}


def real(lang):
    return {row[0]: row[COLUMN[lang]] for row in REAL}


def phrase_ends(text, lang):
    """The lengths, with the spaces left out, at which BudouX ends a phrase."""
    parser = {"ja": budoux.load_default_japanese_parser, "zh-Hans": budoux.load_default_simplified_chinese_parser,
              "th": budoux.load_default_thai_parser}[lang]()
    ends, total = set(), 0
    for chunk in parser.parse(text):
        total += len(chunk.replace(" ", ""))
        ends.add(total)
    return ends


def lines_of(text, lang):
    return translate.wrap_translation(text, lang)


class DependencyTests(unittest.TestCase):
    def test_budoux_is_pinned_in_requirements_and_installed_at_that_version(self):
        with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
            pins = [l.strip() for l in f if l.strip().startswith("budoux")]
        self.assertEqual(len(pins), 1, pins)
        self.assertRegex(pins[0], r"^budoux==\d+\.\d+\.\d+$")
        self.assertEqual(pins[0].split("==")[1], budoux.__version__)

    def test_a_missing_package_gives_a_message_that_says_what_to_install(self):
        linebreak._parser.cache_clear()
        self.addCleanup(linebreak._parser.cache_clear)
        with mock.patch.dict("sys.modules", {"budoux": None}):
            with self.assertRaises(linebreak.BudouXMissing) as ctx:
                linebreak.boundaries("彼女は背の高い容器に", "ja")
        self.assertIn("requirements.txt", str(ctx.exception))


class RealStringsTests(unittest.TestCase):
    """The lines of the real strings: no word is split, the rules of the language hold, every cut is a phrase boundary."""

    def test_the_japanese_word_hand_blender_is_not_split(self):
        lines = lines_of(real("ja")["c001"], "ja")
        self.assertTrue(any("ハンドブレンダー" in l for l in lines), lines)

    def test_a_japanese_line_does_not_start_with_the_prolonged_sound_mark(self):
        lines = lines_of(real("ja")["c004"], "ja")
        self.assertTrue(any("ティーポット" in l for l in lines), lines)
        for l in lines:
            self.assertNotEqual(l[0], "ー", lines)

    def test_a_japanese_adjective_stem_and_its_ending_stay_together(self):
        lines = lines_of(real("ja")["c002"], "ja")
        self.assertTrue(any("淡く" in l for l in lines), lines)

    def test_the_chinese_word_pour_into_is_not_split(self):
        lines = lines_of(real("zh-Hans")["c004"], "zh-Hans")
        self.assertTrue(any("倒进" in l for l in lines), lines)

    def test_the_thai_word_from_is_not_split(self):
        lines = lines_of(real("th")["c004"], "th")
        self.assertTrue(any("จาก" in l for l in lines), lines)

    def test_thai_repetition_mark_does_not_start_a_line(self):
        for text in real("th").values():
            for l in lines_of(text, "th")[1:]:
                self.assertNotIn(l[0], "ๆฯ", l)

    def test_every_line_fits_and_nothing_is_lost(self):
        for lang in ("ja", "zh-Hans", "th"):
            limit = config.TRANSLATE_LANGUAGE_RULES[lang]["max_chars"]
            for cue, text in real(lang).items():
                lines = lines_of(text, lang)
                with self.subTest(lang=lang, cue=cue):
                    self.assertEqual("".join(lines).replace(" ", ""), text.replace(" ", ""))
                    for l in lines:
                        self.assertLessEqual(translate.visual_len(l, lang), limit, lines)

    def test_every_cut_is_a_phrase_boundary(self):
        for lang in ("ja", "zh-Hans", "th"):
            for cue, text in real(lang).items():
                ends = phrase_ends(text, lang)
                lines = lines_of(text, lang)
                with self.subTest(lang=lang, cue=cue):
                    total = 0
                    for l in lines[:-1]:
                        total += len(l.replace(" ", ""))
                        self.assertIn(total, ends, lines)

    def test_the_line_start_and_line_end_rules_hold_for_every_real_line(self):
        for lang in ("ja", "zh-Hans"):
            for cue, text in real(lang).items():
                lines = lines_of(text, lang)
                with self.subTest(lang=lang, cue=cue):
                    for l in lines[1:]:
                        self.assertNotIn(l[0], linebreak._NOT_START[lang], lines)
                    for l in lines[:-1]:
                        self.assertNotIn(l[-1], linebreak._NOT_END[lang], lines)

    def test_a_text_that_fits_one_line_stays_one_line(self):
        self.assertEqual(lines_of("淡く滑らかになる。", "ja"), ["淡く滑らかになる。"])
        self.assertEqual(lines_of(real("zh-Hans")["c010"], "zh-Hans"), [real("zh-Hans")["c010"]])
        self.assertEqual(lines_of(real("th")["c001"], "th"), [real("th")["c001"]])

    def test_the_exact_lines(self):
        """The lines of every real string that needs more than one line, with BudouX 0.10.1 (pinned)."""
        want = {
            ("ja", "c001"): ["彼女は背の高い容器に", "ハンドブレンダーを", "持っている。"],
            ("ja", "c002"): ["ドレッシングは", "淡く滑らかになる。"],
            ("ja", "c003"): ["材料の入った小さなボウルが", "カウンターに並んでいる。"],
            ("ja", "c004"): ["彼女は淡い黄色の", "ドレッシングを", "容器から小さな白い", "ティーポットに注ぐ。"],
            ("ja", "c005"): ["粘土のすり鉢とまな", "板が準備されている。"],
            ("ja", "c006"): ["テーブルには皿、スプーン、", "フォークが並べられている。"],
            ("ja", "c007"): ["手が緑のサラダボウルを", "テーブルに置く。"],
            ("ja", "c008"): ["その横には丸い木製の", "トレイが置かれている。"],
            ("ja", "c009"): ["赤いブレンダーが", "紫色の混ぜ物が入った", "状態で動いている。"],
            ("ja", "c010"): ["彼女は蓋に", "片手を添えている。"],
            ("ja", "c011"): ["カウンターには水の入った", "グラスが置かれている。"],
            ("zh-Hans", "c001"): ["她手里拿着一个", "高罐子里的手持搅拌棒。"],
            ("zh-Hans", "c004"): ["她把罐子里淡黄色的酱汁", "倒进一个小小的白色茶壶里。"],
            ("zh-Hans", "c006"): ["桌子上摆着一个盘子、", "一把勺子和一把叉子。"],
            ("zh-Hans", "c009"): ["红色的搅拌机在运转，", "里面是紫色的混合物。"],
            ("th", "c004"): ["เธอเทน้ำสลัดสีเหลืองอ่อน", "จากโถลงในกาน้ำชาสีขาวใบเล็ก"],
        }
        for (lang, cue), lines in want.items():
            with self.subTest(lang=lang, cue=cue):
                self.assertEqual(lines_of(real(lang)[cue], lang), lines)

    def test_a_line_that_fits_is_one_line_in_every_language(self):
        for lang in ("zh-Hans", "th"):
            limit = config.TRANSLATE_LANGUAGE_RULES[lang]["max_chars"]
            for cue, text in real(lang).items():
                if translate.visual_len(text, lang) <= limit:
                    with self.subTest(lang=lang, cue=cue):
                        self.assertEqual(lines_of(text, lang), [text])


class RuleTests(unittest.TestCase):
    """`allowed` and the rule sets, one character at a time."""

    def cut_before(self, char, lang="ja"):
        return linebreak.allowed("あい" + char + "う", 2, lang)

    def cut_after(self, char, lang="ja"):
        return linebreak.allowed("あい" + char + "う", 3, lang)

    def test_japanese_line_start_prohibition(self):
        for ch in "、。，．！？）」』】ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶー・":
            with self.subTest(ch=ch):
                self.assertFalse(self.cut_before(ch), ch)

    def test_japanese_line_end_prohibition(self):
        for ch in "（「『【〔［｛〈《":
            with self.subTest(ch=ch):
                self.assertFalse(self.cut_after(ch), ch)

    def test_an_ordinary_place_is_allowed(self):
        self.assertTrue(self.cut_before("か"))
        self.assertTrue(self.cut_after("か"))
        self.assertTrue(self.cut_after("、"))

    def test_chinese_basic_prohibition(self):
        for ch in "，、。；：！？）」』】》〉”’·":
            with self.subTest(ch=ch):
                self.assertFalse(self.cut_before(ch, "zh-Hans"), ch)
        for ch in "（「『【《〈“‘":
            with self.subTest(ch=ch):
                self.assertFalse(self.cut_after(ch, "zh-Hans"), ch)

    def test_a_double_dash_and_an_ellipsis_are_not_cut(self):
        self.assertFalse(linebreak.allowed("你好——再见", 3, "zh-Hans"))
        self.assertFalse(linebreak.allowed("你好……再见", 3, "zh-Hans"))
        self.assertTrue(linebreak.allowed("你好——再见", 4, "zh-Hans"))

    def test_a_latin_word_and_a_number_are_not_cut(self):
        self.assertFalse(linebreak.allowed("これはblenderです", 6, "ja"))
        self.assertFalse(linebreak.allowed("価格は3.5ドル", 4, "ja"))
        self.assertFalse(linebreak.allowed("価格は3.5ドル", 5, "ja"))
        self.assertFalse(linebreak.allowed("割引は50%です", 5, "ja"))
        self.assertFalse(linebreak.allowed("価格は$30です", 4, "ja"))
        self.assertTrue(linebreak.allowed("これはblenderです", 3, "ja"))

    def test_thai_marks_and_leading_vowels(self):
        text = "กินเข้าไป"
        self.assertFalse(linebreak.allowed("เก", 1, "th"))                          # a leading vowel stays with its consonant
        self.assertFalse(linebreak.allowed("ก" + "ั" + "ก", 1, "th"))              # a mark does not start a line
        self.assertFalse(linebreak.allowed("ข้างๆ", 4, "th"))                      # the repetition mark does not start a line
        self.assertTrue(linebreak.allowed(text, 3, "th"))

    def test_a_cut_at_a_space_is_allowed_when_the_word_after_it_may_start_a_line(self):
        self.assertTrue(linebreak.allowed("จาน ช้อน", 4, "th"))
        self.assertFalse(linebreak.allowed("สวัสดี ๆ", 7, "th"))


class EveryBoundaryTests(unittest.TestCase):
    """Whatever BudouX says, the rules decide: with a boundary after every character the lines still obey them."""

    TEXTS = {
        "ja": ["彼女は淡い黄色のドレッシングを容器から小さな白いティーポットに注ぐ。",
               "テーブルには皿、スプーン、フォークが並べられている。",
               "「これはペンです」と彼は言った。ちょっとした贈り物です！"],
        "zh-Hans": ["她把罐子里淡黄色的酱汁倒进一个小小的白色茶壶里，然后盖上盖子。",
                    "“你好吗？”他问道。我很好，谢谢你——再见……"],
    }

    def test_rules_hold_with_a_boundary_between_every_two_characters(self):
        for lang, texts in self.TEXTS.items():
            limit = config.TRANSLATE_LANGUAGE_RULES[lang]["max_chars"]
            for text in texts:
                every = list(range(1, len(text)))
                with mock.patch.object(linebreak, "boundaries", return_value=every):
                    layout = linebreak.break_lines(text, lang, limit, lambda s: translate.visual_len(s, lang))
                with self.subTest(lang=lang, text=text):
                    self.assertEqual("".join(layout.lines), text)
                    for l in layout.lines[1:]:
                        self.assertNotIn(l[0], linebreak._NOT_START[lang], layout.lines)
                    for l in layout.lines[:-1]:
                        self.assertNotIn(l[-1], linebreak._NOT_END[lang], layout.lines)

    def test_thai_marks_hold_with_a_boundary_between_every_two_characters(self):
        text = "เธอเทน้ำสลัดสีเหลืองอ่อนจากโถลงในกาน้ำชาสีขาวใบเล็กๆ"
        with mock.patch.object(linebreak, "boundaries", return_value=list(range(1, len(text)))):
            layout = linebreak.break_lines(text, "th", 35, lambda s: translate.visual_len(s, "th"))
        for l in layout.lines[1:]:
            self.assertNotEqual(unicodedata.category(l[0]), "Mn", layout.lines)
            self.assertNotIn(l[0], "ๆฯะาำๅ", layout.lines)
        for l in layout.lines[:-1]:
            self.assertNotIn(l[-1], "เแโใไ", layout.lines)


class LayoutTests(unittest.TestCase):
    def measure(self, lang):
        return lambda s: translate.visual_len(s, lang)

    def test_two_lines_are_balanced_with_the_lower_one_longer(self):
        layout = linebreak.break_lines(real("ja")["c010"], "ja", 13, self.measure("ja"))     # 14 wide
        self.assertEqual(layout.lines, ["彼女は蓋に", "片手を添えている。"])

    def test_a_cut_after_punctuation_is_preferred_to_a_cut_between_phrases(self):
        text = "锅已经够热了，我们现在放香肠然后等它煎熟吧"      # 21 characters, a comma after the 6th
        layout = linebreak.break_lines(text, "zh-Hans", 16, self.measure("zh-Hans"))
        self.assertEqual(layout.lines[0], "锅已经够热了，")

    def test_the_fewest_lines_that_fit_at_phrase_boundaries(self):
        # 25 wide: two lines of 13 would hold it, but no phrase boundary splits it into two lines that fit.
        layout = linebreak.break_lines(real("ja")["c001"], "ja", 13, self.measure("ja"))
        self.assertEqual(len(layout.lines), 3)
        self.assertFalse(layout.split_phrase)

    def test_a_phrase_wider_than_a_line_is_cut_inside_and_reported(self):
        text = "ก" * 30 + "เ" + "ก" * 30
        with mock.patch.object(linebreak, "boundaries", return_value=[]):         # one phrase of 61 characters
            layout = linebreak.break_lines(text, "th", 35, self.measure("th"))
        self.assertTrue(layout.split_phrase)
        self.assertEqual("".join(layout.lines), text)
        for l in layout.lines:
            self.assertFalse(l.endswith("เ"))
            self.assertLessEqual(translate.visual_len(l, "th"), 35)

    def test_a_latin_run_wider_than_a_line_is_cut_by_the_count_as_the_last_resort(self):
        text = "x" * 40
        layout = linebreak.break_lines(text, "ja", 13, self.measure("ja"))
        self.assertTrue(layout.split_phrase)
        self.assertEqual("".join(layout.lines), text)

    def test_spaces_in_the_text_are_places_to_cut_and_are_not_kept(self):
        text = "กระทะร้อนพอแล้ว เราใส่ไส้กรอกลงไปแล้วรอให้สุก"
        layout = linebreak.break_lines(text, "th", 35, self.measure("th"))
        self.assertEqual(layout.lines[0], "กระทะร้อนพอแล้ว")
        for l in layout.lines:
            self.assertEqual(l, l.strip())

    def test_one_line_text_is_returned_as_it_is(self):
        layout = linebreak.break_lines("Pan 12 ok", "ja", 13, self.measure("ja"))
        self.assertEqual(layout.lines, ["Pan 12 ok"])
        self.assertFalse(layout.split_phrase)


if __name__ == "__main__":
    unittest.main()
