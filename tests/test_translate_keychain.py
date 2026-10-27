"""The OpenRouter key from the macOS keychain (SPECS 9.9, "Key"). The key comes from the environment variable
OPENROUTER_API_KEY when it is set, else from the login keychain through `security find-generic-password -s SERVICE -w`.
The `security` program is replaced by a mock here: no test reads the real keychain. The key is made up, and the
tests check that it reaches the request header and no file, report, message, page answer or command output."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
import main
import translate
from tests.fake_openrouter import FakeOpenRouter
from tests.test_translate import TranslateCase, film, run

KEYCHAIN_KEY = "sk-or-from-the-keychain-xyz789"


def completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(["security"], returncode, stdout, stderr)


class KeychainCase(unittest.TestCase):
    """No key in the environment, the default keychain service, and a mock for `security`."""

    def setUp(self):
        self._old = {k: os.environ.get(k) for k in (translate.KEY_ENV, translate.KEYCHAIN_ENV)}
        os.environ.pop(translate.KEY_ENV, None)
        os.environ.pop(translate.KEYCHAIN_ENV, None)           # the default service name
        self.addCleanup(self._restore)
        translate.forget_key()
        self.addCleanup(translate.forget_key)
        self.calls = []
        self.answer = completed(KEYCHAIN_KEY + "\n")
        patcher = mock.patch.object(translate.subprocess, "run", side_effect=self._run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _run(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


class FindKeyTests(KeychainCase):
    def test_a_key_in_the_environment_is_used_and_the_keychain_is_not_asked(self):
        os.environ[translate.KEY_ENV] = "  sk-or-from-the-environment  "
        self.assertEqual(translate.find_key()[0], "sk-or-from-the-environment")
        self.assertEqual(self.calls, [])

    def test_without_one_the_key_comes_from_the_keychain_through_security(self):
        key, problem = translate.find_key()
        self.assertEqual(key, KEYCHAIN_KEY)                          # the newline of the output is cut off
        self.assertIsNone(problem)
        (cmd, kwargs), = self.calls
        self.assertEqual(cmd[1:], ["find-generic-password", "-s", "OPENROUTER_API_KEY", "-w"])
        self.assertTrue(cmd[0].endswith("security"))
        self.assertTrue(kwargs.get("capture_output"), "the password must not go to the terminal")
        self.assertTrue(kwargs.get("timeout"))

    def test_the_service_name_is_a_setting(self):
        os.environ[translate.KEYCHAIN_ENV] = "My OpenRouter"
        translate.find_key()
        self.assertEqual(self.calls[0][0][3], "My OpenRouter")
        self.assertEqual(config.TRANSLATE_KEYCHAIN_SERVICE, "OPENROUTER_API_KEY")

    def test_an_empty_service_name_switches_the_keychain_off(self):
        os.environ[translate.KEYCHAIN_ENV] = ""
        key, problem = translate.find_key()
        self.assertIsNone(key)
        self.assertEqual(self.calls, [])
        self.assertIn("switched off", problem)

    def test_a_key_found_once_is_kept_for_the_run(self):
        translate.find_key()
        translate.find_key()
        self.assertEqual(len(self.calls), 1)
        translate.forget_key()
        translate.find_key()
        self.assertEqual(len(self.calls), 2)

    def test_a_missing_item_is_none_and_names_the_service_not_a_key(self):
        self.answer = completed("", 44, "security: SecKeychainSearchCopyNext: The specified item could not be found in the keychain.")
        key, problem = translate.find_key()
        self.assertIsNone(key)
        self.assertIn("OPENROUTER_API_KEY", problem)
        self.assertIn("keychain", problem)

    def test_an_empty_password_counts_as_missing(self):
        self.answer = completed("\n")
        self.assertIsNone(translate.find_key()[0])

    def test_a_refused_lookup_says_so(self):
        self.answer = completed("", 36, "security: User interaction is not allowed.")
        key, problem = translate.find_key()
        self.assertIsNone(key)
        self.assertIn("refused", problem)

    def test_no_security_program_is_a_clear_problem(self):
        self.answer = FileNotFoundError("security")
        key, problem = translate.find_key()
        self.assertIsNone(key)
        self.assertIn("security", problem)

    def test_a_lookup_that_hangs_on_a_dialog_is_stopped(self):
        self.answer = subprocess.TimeoutExpired("security", 20)
        key, problem = translate.find_key()
        self.assertIsNone(key)
        self.assertIn("did not answer", problem)


class ReadyTests(KeychainCase):
    def test_has_key_in_the_environment_needs_no_keychain(self):
        os.environ[translate.KEY_ENV] = "sk-or-x"
        self.assertTrue(translate.has_key())
        self.assertEqual(self.calls, [])

    def test_has_key_asks_the_keychain_without_reading_the_password(self):
        self.assertTrue(translate.has_key())
        (cmd, kwargs), = self.calls
        self.assertNotIn("-w", cmd, "the page only needs to know that the item is there")
        self.assertNotIn("-g", cmd)

    def test_has_key_is_false_when_the_item_is_missing(self):
        self.answer = completed("", 44, "could not be found")
        self.assertFalse(translate.has_key())

    def test_check_ready_names_the_variable_and_the_service_and_no_key(self):
        self.answer = completed("", 44, "could not be found")
        with self.assertRaises(translate.TranslateError) as ctx:
            translate.check_ready()
        text = str(ctx.exception)
        self.assertIn("OPENROUTER_API_KEY", text)
        self.assertIn("keychain", text)
        self.assertIn("security add-generic-password", text)         # how to add it
        self.assertNotIn(KEYCHAIN_KEY, text)

    def test_check_ready_passes_with_the_keychain_item(self):
        translate.check_ready()

    def test_the_client_takes_the_key_from_the_keychain(self):
        self.assertEqual(translate.Client()._key, KEYCHAIN_KEY)


class UseTests(TranslateCase):
    """A whole translation with the key from the keychain, against the fake OpenRouter."""

    def setUp(self):
        super().setUp()
        os.environ.pop("OPENROUTER_API_KEY")
        old = os.environ.pop(translate.KEYCHAIN_ENV, None)           # the default service: the mock answers for it
        self.addCleanup(lambda: os.environ.__setitem__(translate.KEYCHAIN_ENV, old) if old is not None else None)
        translate.forget_key()
        self.addCleanup(translate.forget_key)
        self.calls = []

        def fake(cmd, **kwargs):
            self.calls.append(cmd)
            return completed(KEY_FOR_SERVER + "\n")

        patcher = mock.patch.object(translate.subprocess, "run", side_effect=fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_key_goes_to_the_service_and_nowhere_else(self):
        report = self.go(langs="de,ja")
        self.assertGreater(self.server.count, 0)
        self.assertTrue(all(r["auth_ok"] for r in self.server.requests), "the key of the keychain is the header")
        self.assertNotIn(KEY_FOR_SERVER, self.everything_written())
        self.assertNotIn(KEY_FOR_SERVER, json.dumps(report, default=str))
        self.assertTrue((self.out.parent / "film.de.srt").is_file())

    def test_the_command_prints_no_key_and_works(self):
        path = self.root / "timeline.json"
        film(3, output=str(self.out)).save(str(path))
        code, out, err = run(["subtitles", "translate", "--file", str(path), "--work", str(self.work), "--lang", "de"])
        self.assertEqual(code, 0, err + out)
        self.assertNotIn(KEY_FOR_SERVER, out + err)
        self.assertNotIn(KEY_FOR_SERVER, self.everything_written())

    def test_a_rejected_key_message_does_not_hold_the_key(self):
        self.server.script = ["http401"]
        with self.assertRaises(translate.TranslateError) as ctx:
            self.go(langs="de")
        self.assertNotIn(KEY_FOR_SERVER, str(ctx.exception))

    def test_the_page_state_says_there_is_a_key_and_never_shows_it(self):
        tl = film(3, output=str(self.out))
        state = translate.page_state(tl, str(self.work), tl.data["exports"])
        self.assertTrue(state["key"])
        self.assertNotIn(KEY_FOR_SERVER, json.dumps(state))
        self.assertTrue(all("-w" not in c for c in self.calls), "the page does not read the password")


KEY_FOR_SERVER = "sk-or-test-key-abc123"          # the key the fake OpenRouter of tests/test_translate.py accepts


if __name__ == "__main__":
    unittest.main()
