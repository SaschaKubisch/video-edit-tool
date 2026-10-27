"""CLAUDE.md tells Claude Code which commands to run for a request to change the film (SPECS 7.7, 4.3, 10.3). The
commands it names must exist, with the options it gives, so the file cannot drift from the tool. No emojis."""

import re
import unittest
from pathlib import Path

import main

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

PLACEHOLDERS = {"FILE": "x.json", "DIR": "work", "SCENE": "s001", "ID": "s001", "S": "1", "WORDS": "salad"}


COMMAND_WORDS = ("scenes", "timeline", "subtitles")


def commands():
    """Every command in a code span of CLAUDE.md, as (the span, the arguments the parser can read). A span is a
    command when it starts with `python main.py` or with one of COMMAND_WORDS."""
    found = []
    for span in re.findall(r"`([^`]+)`", TEXT):
        tokens = span.split()
        if tokens[:2] == ["python", "main.py"]:
            tokens = tokens[2:]
        elif not tokens or tokens[0] not in COMMAND_WORDS:
            continue
        args = []
        for t in tokens:
            if t == "...":
                continue
            if t.startswith("ID["):
                t = "s001"
            t = t.strip("[]")
            if t.startswith("<"):
                continue
            args.append(PLACEHOLDERS.get(t, t))
        if len(args) >= 2:                       # a lone word such as `scenes` is a name, not a command
            found.append((span, args))
    return found


def parses(args):
    """True when the parser accepts the arguments; for a bare `scenes cut` that names only the command,
    True when `--help` of that command works."""
    import contextlib
    import io
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            main.build_parser().parse_args(args)
            return True
        except SystemExit:
            pass
        if len(args) == 2:
            try:
                main.build_parser().parse_args(args + ["--help"])
            except SystemExit as e:
                return e.code == 0
    return False


class ClaudeMdTests(unittest.TestCase):
    def test_every_command_it_names_is_a_command_of_the_tool_with_those_options(self):
        found = commands()
        self.assertGreaterEqual(len(found), 10)
        for span, args in found:
            self.assertTrue(parses(args), f"CLAUDE.md names a command the parser refuses: {span!r} read as {args}")

    def test_it_names_the_commands_of_the_loop(self):
        for needle in ("scenes index", "scenes frames", "--dry-run", "timeline validate", "timeline undo",
                       "scenes restore", "scenes keep", "scenes cut", "scenes remove"):
            self.assertIn(needle, TEXT)

    def test_it_says_that_the_owner_confirms_before_the_change(self):
        self.assertIn("Ask the owner to confirm", TEXT)
        self.assertLess(TEXT.index("Dry run."), TEXT.index("Do it."))
        self.assertLess(TEXT.index("Ask the owner to confirm"), TEXT.index("Dry run."))

    def test_it_has_no_emoji_and_only_plain_characters(self):
        bad = [c for c in TEXT if ord(c) > 126]
        self.assertEqual(bad, [], "CLAUDE.md must hold plain ASCII text, no emojis")


if __name__ == "__main__":
    unittest.main()
