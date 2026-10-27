"""Test helpers for the text in the picture: Pillow's built-in font (so a test needs no font
file) and the real Inter font where VET_FONT_DIR names it."""

import os
import unittest

import textlayout


def builtin_font(test):
    """Make textlayout use Pillow's built-in font for the rest of `test` (a TestCase)."""
    from PIL import ImageFont

    def get_font(kind, size, lang=None):
        return ImageFont.load_default(int(size))

    old = textlayout.get_font
    textlayout.get_font = get_font
    test.addCleanup(setattr, textlayout, "get_font", old)
    return old                  # the real function, for a test that wants the real lookup back


def real_font_dir():
    folder = os.environ.get("VET_FONT_DIR", "")
    return folder if folder and os.path.isfile(os.path.join(folder, "Inter-SemiBold.ttf")) else ""


requires_inter = unittest.skipUnless(
    real_font_dir(), "VET_FONT_DIR names no folder with Inter-SemiBold.ttf and Inter-Bold.ttf")


def real_noto_dir():
    """The folder with the three Noto files, or "" (VET_NOTO_FONT_DIR, SPECS 9.6)."""
    import config
    folder = os.environ.get(config.TEXT_NOTO_DIR_ENV, "")
    names = [v["font"] for v in config.TEXT_LANG_STYLE.values()]
    return folder if folder and all(os.path.isfile(os.path.join(folder, n)) for n in names) else ""


requires_noto = unittest.skipUnless(
    real_noto_dir(), "VET_NOTO_FONT_DIR names no folder with NotoSansJP-Bold.otf, NotoSansSC-Bold.otf and "
                     "NotoSansThai-SemiBold.ttf")
