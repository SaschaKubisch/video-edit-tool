"""The tests.

They never read the owner's macOS keychain: the keychain lookup of the OpenRouter key is switched off for the whole
run (VET_OPENROUTER_KEYCHAIN_SERVICE empty), so a test that expects "no key" does not find the real one.
tests/test_translate_keychain.py replaces the `security` program with a mock; the test with the real service
(tests.test_translate.RealOpenRouterTests, run with VET_REAL_OPENROUTER=1) turns the lookup on again.
"""

import os

os.environ.setdefault("VET_OPENROUTER_KEYCHAIN_SERVICE", "")
