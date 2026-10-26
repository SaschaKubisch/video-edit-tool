"""The creative LUT (SPECS 8.2, 8.3; decision D10, "slay Film + Portrait"): finding the
LUT files, checking a .cube file, the hash-named copy in the work folder, `look.lut` in the
timeline file with its credit, and the commands `look list` and `look set`."""

import json
import re
import tempfile
import unittest
from pathlib import Path

import look
import store
import timeline
from tests.fakes import IDENTITY_CUBE
from tests.test_timeline_commands import FileCase, run
from tests.timeline_fixture import example

CREDIT = "LUTs: [slay](https://www.slaycamera.com/?utm_source=github&utm_medium=repo&utm_campaign=luts) (CC BY 4.0)"


def cube(size=2, title=None):
    lines = ([f'TITLE "{title}"'] if title else []) + [f"LUT_3D_SIZE {size}"]
    n = size
    for b in range(n):
        for g in range(n):
            for r in range(n):
                lines.append(f"{r / (n - 1):.6f} {g / (n - 1):.6f} {b / (n - 1):.6f}")
    return "\n".join(lines) + "\n"


class Folder(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.luts = self.root / "luts"
        (self.luts / "slay").mkdir(parents=True)
        (self.luts / "slay" / "slay-04-film.cube").write_text(cube(3, "slay Film"))
        (self.luts / "slay" / "slay-05-portrait.cube").write_text(cube(3, "slay Portrait"))
        (self.luts / "slay" / "CREDIT.txt").write_text(CREDIT + "\n")
        (self.luts / "slay" / "LICENSE").write_text("licence text")
        (self.luts / "plain.cube").write_text(cube(2))
        (self.luts / "notes.txt").write_text("not a lut")
        self.work = self.root / "work"


class CubeFileTests(Folder):
    def test_a_good_file_is_accepted(self):
        info = look.check_cube(self.luts / "plain.cube")
        self.assertEqual(info["size"], 2)

    def test_the_title_is_read(self):
        self.assertEqual(look.check_cube(self.luts / "slay" / "slay-04-film.cube")["title"], "slay Film")

    def test_a_file_without_a_size_is_refused(self):
        p = self.root / "bad.cube"
        p.write_text("0 0 0\n1 1 1\n")
        with self.assertRaisesRegex(look.LutError, "LUT_3D_SIZE"):
            look.check_cube(p)

    def test_a_file_with_the_wrong_number_of_entries_is_refused(self):
        p = self.root / "short.cube"
        p.write_text("LUT_3D_SIZE 3\n0 0 0\n1 1 1\n")
        with self.assertRaisesRegex(look.LutError, "27"):
            look.check_cube(p)

    def test_a_one_dimensional_lut_is_refused(self):
        p = self.root / "1d.cube"
        p.write_text("LUT_1D_SIZE 4\n0 0 0\n0.3 0.3 0.3\n0.6 0.6 0.6\n1 1 1\n")
        with self.assertRaisesRegex(look.LutError, "3D"):
            look.check_cube(p)

    def test_a_non_default_domain_is_refused(self):
        p = self.root / "dom.cube"
        p.write_text("LUT_3D_SIZE 2\nDOMAIN_MAX 2 2 2\n" + "\n".join("0 0 0" for _ in range(8)) + "\n")
        with self.assertRaisesRegex(look.LutError, "DOMAIN"):
            look.check_cube(p)


class ListingTests(Folder):
    def test_every_cube_file_below_the_folder_is_listed_with_its_credit(self):
        rows = look.list_luts(str(self.luts))
        self.assertEqual([r["id"] for r in rows],
                         ["plain.cube", "slay/slay-04-film.cube", "slay/slay-05-portrait.cube"])
        slay = {r["id"]: r for r in rows}["slay/slay-04-film.cube"]
        self.assertEqual(slay["credit"], CREDIT)
        self.assertEqual(slay["title"], "slay Film")
        self.assertIsNone({r["id"]: r for r in rows}["plain.cube"]["credit"])

    def test_a_missing_folder_gives_an_empty_list(self):
        self.assertEqual(look.list_luts(str(self.root / "nope")), [])
        self.assertEqual(look.list_luts(None), [])

    def test_a_broken_cube_file_is_listed_with_its_problem_and_cannot_be_chosen(self):
        (self.luts / "broken.cube").write_text("nothing")
        rows = {r["id"]: r for r in look.list_luts(str(self.luts))}
        self.assertIn("LUT_3D_SIZE", rows["broken.cube"]["problem"])

    def test_the_folder_comes_from_the_environment(self):
        self.assertEqual(look.lut_dir({"VET_LUT_DIR": "/x"}), "/x")
        self.assertIsNone(look.lut_dir({}))

    def test_find_by_id_stays_inside_the_folder(self):
        self.assertTrue(look.find_lut(str(self.luts), "slay/slay-04-film.cube").endswith("slay-04-film.cube"))
        for bad in ("../work", "/etc/passwd", "slay/../../x.cube", "missing.cube"):
            with self.assertRaises(look.LutError, msg=bad):
                look.find_lut(str(self.luts), bad)


class CreditTests(Folder):
    def test_the_credit_is_the_first_line_of_credit_txt_next_to_the_file(self):
        self.assertEqual(look.read_credit(self.luts / "slay" / "slay-04-film.cube"), CREDIT)
        self.assertIsNone(look.read_credit(self.luts / "plain.cube"))

    def test_the_plain_form_has_the_link_in_brackets_as_text(self):
        self.assertEqual(
            look.plain_credit(CREDIT),
            "LUTs: slay (https://www.slaycamera.com/?utm_source=github&utm_medium=repo&utm_campaign=luts) (CC BY 4.0)")
        self.assertEqual(look.plain_credit("no link here"), "no link here")


class InstallTests(Folder):
    def test_the_copy_is_named_by_the_hash_and_made_once(self):
        src = self.luts / "slay" / "slay-04-film.cube"
        entry = look.install(src, str(self.work))
        self.assertRegex(entry["path"], r"/luts/[0-9a-f]{64}\.cube$")
        self.assertEqual(entry["sha256"], look.sha256_file(src))
        self.assertEqual(Path(entry["path"]).read_bytes(), src.read_bytes())
        first = Path(entry["path"]).stat().st_mtime_ns
        again = look.install(src, str(self.work))
        self.assertEqual(again, entry)
        self.assertEqual(Path(entry["path"]).stat().st_mtime_ns, first)

    def test_the_entry_carries_interp_name_and_credit(self):
        entry = look.install(self.luts / "slay" / "slay-04-film.cube", str(self.work))
        self.assertEqual(entry["interp"], "tetrahedral")
        self.assertEqual(entry["name"], "slay-04-film.cube")
        self.assertEqual(entry["credit"], CREDIT)

    def test_a_lut_without_a_credit_has_no_credit_key(self):
        entry = look.install(self.luts / "plain.cube", str(self.work))
        self.assertNotIn("credit", entry)

    def test_a_broken_file_is_not_installed(self):
        (self.luts / "broken.cube").write_text("nothing")
        with self.assertRaises(look.LutError):
            look.install(self.luts / "broken.cube", str(self.work))
        self.assertFalse((self.work / "luts").exists())

    def test_a_work_folder_inside_a_folder_of_originals_is_refused(self):
        import safety
        with self.assertRaises(safety.OriginalsProtectedError):
            look.install(self.luts / "plain.cube", str(self.work), protected=[str(self.root)])


class TimelineLutTests(Folder):
    def data(self):
        d = example()
        d["look"].pop("lut", None)
        return d

    def test_set_lut_writes_look_lut_and_the_file_validates_with_file_checks(self):
        entry = look.install(self.luts / "slay" / "slay-04-film.cube", str(self.work))
        d = look.with_creative_lut(self.data(), entry)
        self.assertEqual(d["look"]["lut"], entry)
        found = timeline.Timeline(d).validate(file_checks=True, frame_counter=lambda p: None)
        lut_findings = [f for f in found if f.path.startswith("look.lut")]
        self.assertEqual(lut_findings, [])

    def test_name_and_credit_are_known_keys_of_the_file(self):
        entry = look.install(self.luts / "slay" / "slay-04-film.cube", str(self.work))
        d = look.with_creative_lut(self.data(), entry)
        found = timeline.Timeline(d).validate(file_checks=False)
        self.assertEqual([f for f in found if f.rule in ("V0", "V2") and "look" in f.path], [])

    def test_a_credit_that_is_not_text_is_refused(self):
        entry = look.install(self.luts / "slay" / "slay-04-film.cube", str(self.work))
        d = look.with_creative_lut(self.data(), dict(entry, credit=5))
        found = timeline.Timeline(d).validate(file_checks=False)
        self.assertTrue(any(f.rule == "V0" and f.path == "look.lut.credit" for f in found))

    def test_removing_the_lut_leaves_the_base_look(self):
        entry = look.install(self.luts / "plain.cube", str(self.work))
        d = look.with_creative_lut(look.with_creative_lut(self.data(), entry), None)
        self.assertNotIn("lut", d["look"])
        self.assertIn("base", d["look"])

    def test_the_base_look_can_be_changed_with_its_hash(self):
        base = self.luts / "plain.cube"
        d = look.with_base_lut(self.data(), str(base))
        self.assertEqual(d["look"]["base"], {"mode": "lut", "path": str(base),
                                             "sha256": look.sha256_file(base)})

    def test_the_credit_of_the_film_is_the_credit_of_its_lut(self):
        entry = look.install(self.luts / "slay" / "slay-04-film.cube", str(self.work))
        d = look.with_creative_lut(self.data(), entry)
        self.assertEqual(look.film_credit(d), look.plain_credit(CREDIT))
        self.assertIsNone(look.film_credit(self.data()))


class LookCommandTests(Folder, FileCase):
    def setUp(self):
        Folder.setUp(self)
        FileCase.setUp(self)

    def write_film(self):
        d = example()
        d["look"].pop("lut", None)
        store.save(timeline.Timeline(d), str(self.path))

    def lut(self):
        return json.loads(self.path.read_text())["look"].get("lut")

    def test_look_list_prints_the_files_of_the_folder(self):
        code, out, err = run(["look", "list", "--luts", str(self.luts)])
        self.assertEqual(code, 0, err)
        self.assertIn("slay/slay-04-film.cube", out)
        self.assertIn("slay/slay-05-portrait.cube", out)
        self.assertIn("CC BY 4.0", out)

    def test_look_set_lut_by_id_installs_the_copy_and_writes_the_file(self):
        self.write_film()
        code, out, err = run(["look", "set", "--file", str(self.path), "--work", str(self.work),
                              "--luts", str(self.luts), "--lut", "slay/slay-05-portrait.cube",
                              "--no-file-checks"])
        self.assertEqual(code, 0, err)
        lut = self.lut()
        self.assertEqual(lut["name"], "slay-05-portrait.cube")
        self.assertEqual(lut["credit"], CREDIT)
        self.assertTrue(Path(lut["path"]).is_file())
        self.assertGreaterEqual(len(store.list_snapshots(str(self.path))), 1)

    def test_look_set_lut_by_path_works_too(self):
        self.write_film()
        code, out, err = run(["look", "set", "--file", str(self.path), "--work", str(self.work),
                              "--lut", str(self.luts / "plain.cube"), "--no-file-checks"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.lut()["name"], "plain.cube")

    def test_no_lut_removes_it(self):
        self.write_film()
        run(["look", "set", "--file", str(self.path), "--work", str(self.work),
             "--lut", str(self.luts / "plain.cube"), "--no-file-checks"])
        code, out, err = run(["look", "set", "--file", str(self.path), "--no-lut", "--no-file-checks"])
        self.assertEqual(code, 0, err)
        self.assertIsNone(self.lut())

    def test_a_broken_lut_changes_nothing(self):
        self.write_film()
        before = self.path.read_bytes()
        (self.luts / "broken.cube").write_text("nothing")
        code, out, err = run(["look", "set", "--file", str(self.path), "--work", str(self.work),
                              "--lut", str(self.luts / "broken.cube"), "--no-file-checks"])
        self.assertEqual(code, 1)
        self.assertIn("LUT_3D_SIZE", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_an_unknown_name_is_exit_code_1(self):
        self.write_film()
        code, out, err = run(["look", "set", "--file", str(self.path), "--work", str(self.work),
                              "--luts", str(self.luts), "--lut", "nope.cube", "--no-file-checks"])
        self.assertEqual(code, 1)

    def test_look_set_needs_one_of_its_options(self):
        self.write_film()
        code, out, err = run(["look", "set", "--file", str(self.path)])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
