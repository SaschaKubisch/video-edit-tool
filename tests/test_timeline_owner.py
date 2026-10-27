"""Check 2.15: the snapshots of one timeline file are not mixed up with those of another (SPECS 4.1).

All timeline files of one folder share one `timeline.history` folder. A snapshot used to be a bare copy
`NNNN.json`, so `timeline undo --file A` took the newest snapshot that differed from A, even one made from
B, and wrote B's film into A. Now every snapshot has a record `NNNN.owner.json` that names the file it
was made from (its resolved absolute path), and `undo`, `history`, `snapshot_if_changed` and the pruning use
only the snapshots of the file they are called for. A snapshot without a record (made before this change)
belongs to no file: no command restores it, `history` lists it apart with a note."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import main
import store
import timeline
from tests.timeline_fixture import example


def run(argv):
    args = main.build_parser().parse_args(argv)
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args.func(args)
        except SystemExit as e:
            code = e.code or 0
    return code, out.getvalue(), err.getvalue()


class TwoFiles(unittest.TestCase):
    """Two timeline files in one folder, as in step3 of the first shoot."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name).resolve()
        self.a = self.dir / "timeline-a.json"
        self.b = self.dir / "timeline-b.json"
        self.history = self.dir / "timeline.history"

    def save(self, path, label):
        d = example()
        d["segments"][0]["label"] = label
        store.save(timeline.Timeline(d), str(path))
        return path.read_bytes()

    def label(self, path):
        return json.loads(path.read_text())["segments"][0]["label"]

    def owner(self, number, folder=None):
        return json.loads(((folder or self.history) / f"{number:04d}.owner.json").read_text())

    def legacy(self, number, label):
        """A snapshot as the tool made it before the owner record: a bare copy."""
        d = example()
        d["segments"][0]["label"] = label
        self.history.mkdir(exist_ok=True)
        timeline.Timeline(d).save(str(self.history / f"{number:04d}.json"))


class OwnerRecordTests(TwoFiles):
    def test_a_snapshot_records_the_file_it_was_made_from(self):
        self.save(self.a, "a0")
        self.save(self.a, "a1")
        record = self.owner(1)
        self.assertEqual(record["file"], str(self.a))
        self.assertEqual(record["name"], "timeline-a.json")

    def test_the_record_holds_the_resolved_absolute_path_whatever_way_the_file_was_named(self):
        link = self.dir / "link"
        link.symlink_to(self.dir)
        self.save(self.dir / "timeline-a.json", "a0")
        self.save(link / "timeline-a.json", "a1")                   # the same file through a link
        self.assertEqual(self.owner(1)["file"], os.path.realpath(self.a))
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        os.chdir(self.dir)
        self.save(Path("timeline-a.json"), "a2")                    # a relative name
        os.chdir(here)
        self.assertEqual(self.owner(2)["file"], os.path.realpath(self.a))
        self.assertEqual(len(store.list_snapshots(str(self.a))), 2)

    def test_the_record_is_not_a_snapshot(self):
        self.save(self.a, "a0")
        self.save(self.a, "a1")
        self.assertEqual(sorted(p.name for p in self.history.iterdir()), ["0001.json", "0001.owner.json"])
        self.assertEqual([Path(p).name for p in store.list_snapshots(str(self.a))], ["0001.json"])

    def test_the_snapshot_stays_a_byte_for_byte_copy(self):
        a0 = self.save(self.a, "a0")
        self.save(self.a, "a1")
        self.assertEqual((self.history / "0001.json").read_bytes(), a0)

    def test_numbers_rise_across_the_files_of_the_folder(self):
        self.save(self.a, "a0")
        self.save(self.b, "b0")
        self.save(self.a, "a1")                                      # snapshot 1 (of a)
        self.save(self.b, "b1")                                      # snapshot 2 (of b)
        self.save(self.a, "a2")                                      # snapshot 3 (of a)
        self.assertEqual([Path(p).name for p in store.list_snapshots(str(self.a))], ["0001.json", "0003.json"])
        self.assertEqual([Path(p).name for p in store.list_snapshots(str(self.b))], ["0002.json"])

    def test_a_validate_snapshot_records_the_file_too(self):
        self.save(self.a, "a0")
        made = store.snapshot_if_changed(str(self.a))
        self.assertEqual(self.owner(int(Path(made).stem))["file"], str(self.a))


class UndoTests(TwoFiles):
    def test_undo_of_one_file_never_takes_a_snapshot_of_another(self):
        a0 = self.save(self.a, "a0")
        self.save(self.b, "b0")
        self.save(self.a, "a1")                                      # snapshot 1: a0
        b1 = self.save(self.b, "b1")                                 # snapshot 2: b0, the newest of the folder
        store.undo(str(self.a))
        self.assertEqual(self.a.read_bytes(), a0)                    # a goes back to a0, not to b0
        self.assertEqual(self.b.read_bytes(), b1)                    # b is untouched
        self.assertEqual(self.label(self.a), "a0")

    def test_each_file_is_undone_by_its_own_snapshots(self):
        self.save(self.a, "a0")
        b0 = self.save(self.b, "b0")
        self.save(self.a, "a1")
        self.save(self.b, "b1")
        store.undo(str(self.b))
        self.assertEqual(self.b.read_bytes(), b0)
        self.assertEqual(self.label(self.a), "a1")                   # a is as it was

    def test_two_undos_go_two_versions_back_in_the_same_file_only(self):
        self.save(self.a, "a0")
        self.save(self.a, "a1")
        self.save(self.b, "b0")
        self.save(self.b, "b1")
        self.save(self.a, "a2")
        store.undo(str(self.a))
        self.assertEqual(self.label(self.a), "a1")
        store.undo(str(self.a))
        self.assertEqual(self.label(self.a), "a0")
        self.assertEqual(self.label(self.b), "b1")

    def test_only_the_snapshots_of_this_file_that_are_newer_are_removed(self):
        self.save(self.a, "a0")
        self.save(self.a, "a1")                                      # 1: a0
        self.save(self.b, "b0")
        self.save(self.b, "b1")                                      # 2: b0
        self.save(self.a, "a2")                                      # 3: a1
        store.undo(str(self.a))                                      # restores 3, nothing of a is newer
        store.undo(str(self.a))                                      # restores 1, removes 3
        self.assertEqual([Path(p).name for p in store.list_snapshots(str(self.a))], ["0001.json"])
        self.assertEqual([Path(p).name for p in store.list_snapshots(str(self.b))], ["0002.json"])
        self.assertTrue((self.history / "0002.owner.json").exists())
        self.assertFalse((self.history / "0003.owner.json").exists())      # the record goes with its snapshot

    def test_the_replaced_version_is_kept_with_its_owner(self):
        self.save(self.a, "a0")
        a1 = self.save(self.a, "a1")
        result = store.undo(str(self.a))
        self.assertEqual(Path(result["undone"]).read_bytes(), a1)
        self.assertEqual(self.owner(1, self.history / "undone")["file"], str(self.a))

    def test_nothing_to_undo_when_only_another_file_has_snapshots(self):
        self.save(self.b, "b0")
        self.save(self.b, "b1")
        a0 = self.save(self.a, "a0")                                 # a was saved once: no snapshot of a
        with self.assertRaises(store.NothingToUndo) as ctx:
            store.undo(str(self.a))
        self.assertEqual(self.a.read_bytes(), a0)
        self.assertIn("1 snapshot of other timeline files", str(ctx.exception))
        self.assertFalse((self.history / "undone").exists())

    def test_a_snapshot_of_another_file_with_the_same_bytes_is_not_used(self):
        self.save(self.b, "same")
        self.save(self.b, "same2")                                   # snapshot 1 is b's "same"
        self.save(self.a, "same")                                    # a holds the bytes of snapshot 1
        a_bytes = self.a.read_bytes()
        with self.assertRaises(store.NothingToUndo):
            store.undo(str(self.a))
        self.assertEqual(self.a.read_bytes(), a_bytes)


class SnapshotAfterAValidateTests(TwoFiles):
    def test_another_files_newest_snapshot_does_not_hide_a_change(self):
        self.save(self.a, "x")
        self.save(self.b, "x")
        self.save(self.b, "y")                                       # snapshot 1 is a copy of b "x": the bytes a has
        made = store.snapshot_if_changed(str(self.a))
        self.assertIsNotNone(made)
        self.assertEqual(self.owner(int(Path(made).stem))["file"], str(self.a))

    def test_the_newest_snapshot_of_the_same_file_still_hides_a_repeat(self):
        self.save(self.a, "x")
        store.snapshot_if_changed(str(self.a))
        self.save(self.b, "z")
        self.save(self.b, "w")
        self.assertIsNone(store.snapshot_if_changed(str(self.a)))


class PruningTests(TwoFiles):
    def test_the_limit_counts_the_snapshots_of_one_file(self):
        self.save(self.b, "b0")
        self.save(self.b, "b1")                                      # 1: b0
        for i in range(5):
            d = example()
            d["segments"][0]["label"] = f"a{i}"
            store.save(timeline.Timeline(d), str(self.a), keep=2)
        mine = [Path(p).name for p in store.list_snapshots(str(self.a))]
        self.assertEqual(len(mine), 2)
        self.assertEqual([Path(p).name for p in store.list_snapshots(str(self.b))], ["0001.json"])
        self.assertTrue((self.history / "0001.owner.json").exists())
        pruned = sorted(p.name for p in self.history.iterdir() if p.name.endswith(".owner.json"))
        self.assertEqual(len(pruned), 3)                             # b's record and the two records of a


class HistoryTests(TwoFiles):
    def test_the_history_lists_the_snapshots_of_this_file_only(self):
        self.save(self.a, "a0")
        self.save(self.b, "b0")
        self.save(self.a, "a1")
        self.save(self.b, "b1")
        self.save(self.a, "a2")
        rows = store.history(str(self.a))
        self.assertEqual([r["number"] for r in rows], [1, 3, None])
        self.assertEqual(rows[1]["hash"], timeline.file_hash(str(self.history / "0003.json")))
        self.assertEqual([r["number"] for r in store.history(str(self.b))], [2, None])

    def test_the_change_is_against_the_version_before_of_the_same_file(self):
        self.save(self.a, "a0")
        self.save(self.b, "completely different")
        self.save(self.a, "a1")
        self.save(self.b, "b1")
        rows = store.history(str(self.a))
        self.assertEqual(rows[0]["change"], "first version kept")
        self.assertIn("label", rows[1]["change"])                    # a0 to a1, not b's "completely different"


class LegacySnapshotTests(TwoFiles):
    """The real folder step3/timeline.history holds snapshots 0001 to 0031 from several files and no records."""

    def setUp(self):
        super().setUp()
        for n in (1, 2, 3):
            self.legacy(n, f"old{n}")
        self.save(self.a, "a0")

    def test_a_snapshot_without_a_record_belongs_to_no_file(self):
        self.assertEqual(store.list_snapshots(str(self.a)), [])
        self.assertEqual(store.list_snapshots(str(self.b)), [])
        others = store.other_snapshots(str(self.a))
        self.assertEqual([Path(p).name for p in others["unowned"]], ["0001.json", "0002.json", "0003.json"])
        self.assertEqual(others["other_files"], {})

    def test_undo_does_not_use_it(self):
        a0 = self.a.read_bytes()
        with self.assertRaises(store.NothingToUndo) as ctx:
            store.undo(str(self.a))
        self.assertEqual(self.a.read_bytes(), a0)
        self.assertIn("3 snapshots without a recorded file", str(ctx.exception))
        self.assertFalse((self.history / "undone").exists())

    def test_history_does_not_list_it_as_a_version_of_the_file(self):
        rows = store.history(str(self.a))
        self.assertEqual([r["number"] for r in rows], [None])

    def test_it_is_left_as_it_is_and_new_snapshots_are_numbered_after_it(self):
        before = {p.name: p.read_bytes() for p in self.history.iterdir()}
        self.save(self.a, "a1")                                      # snapshot 4 of a
        self.assertEqual([Path(p).name for p in store.list_snapshots(str(self.a))], ["0004.json"])
        for name, content in before.items():
            self.assertEqual((self.history / name).read_bytes(), content, name)
        self.assertFalse((self.history / "0001.owner.json").exists())      # no record is invented for it
        store.undo(str(self.a))
        self.assertEqual(self.label(self.a), "a0")
        for n in (1, 2, 3):
            self.assertTrue((self.history / f"{n:04d}.json").exists())

    def test_pruning_never_removes_it(self):
        for i in range(4):
            d = example()
            d["segments"][0]["label"] = f"a{i + 1}"
            store.save(timeline.Timeline(d), str(self.a), keep=1)
        for n in (1, 2, 3):
            self.assertTrue((self.history / f"{n:04d}.json").exists())

    def test_a_record_that_cannot_be_read_makes_the_snapshot_unowned(self):
        self.save(self.a, "a1")
        (self.history / "0004.owner.json").write_text("not json")
        self.assertEqual(store.list_snapshots(str(self.a)), [])
        (self.history / "0004.owner.json").write_text(json.dumps({"version": 1}))
        self.assertEqual(store.list_snapshots(str(self.a)), [])


class OtherFilesTests(TwoFiles):
    def test_the_other_files_are_named_with_their_snapshots(self):
        self.save(self.a, "a0")
        self.save(self.a, "a1")
        self.save(self.b, "b0")
        self.save(self.b, "b1")
        others = store.other_snapshots(str(self.a))
        self.assertEqual(list(others["other_files"]), [str(self.b)])
        self.assertEqual([Path(p).name for p in others["other_files"][str(self.b)]], ["0002.json"])
        self.assertEqual(others["unowned"], [])


class CommandTests(TwoFiles):
    def test_history_command_shows_this_files_versions_and_says_what_it_leaves_out(self):
        self.legacy(1, "old1")
        self.legacy(2, "old2")
        self.save(self.a, "a0")
        self.save(self.a, "a1")                                      # snapshot 3
        self.save(self.b, "b0")
        self.save(self.b, "b1")                                      # snapshot 4
        code, out, err = run(["timeline", "history", "--file", str(self.a)])
        self.assertEqual(code, 0, err)
        self.assertIn("timeline-a.json: 1 earlier version(s)", out)
        self.assertIn("0003", out)
        self.assertNotIn("0004", out.split("Not shown")[0])
        self.assertIn("Not shown", out)
        self.assertIn("timeline-b.json", out)                        # the other file is named
        self.assertIn("without a recorded file", out)
        self.assertIn("0001", out)
        self.assertIn("0002", out)

    def test_history_command_with_only_other_snapshots_says_so(self):
        self.save(self.b, "b0")
        self.save(self.b, "b1")
        self.save(self.a, "a0")
        code, out, err = run(["timeline", "history", "--file", str(self.a)])
        self.assertEqual(code, 0, err)
        self.assertIn("no earlier version is kept yet", out)
        self.assertIn("timeline-b.json", out)

    def test_history_json_stays_a_list_of_the_rows_of_this_file(self):
        self.legacy(1, "old1")
        self.save(self.a, "a0")
        self.save(self.a, "a1")
        self.save(self.b, "b0")
        self.save(self.b, "b1")
        code, out, err = run(["timeline", "history", "--file", str(self.a), "--json"])
        self.assertEqual(code, 0, err)
        rows = json.loads(out)
        self.assertEqual([r["number"] for r in rows], [2, None])

    def test_the_history_command_writes_nothing(self):
        self.legacy(1, "old1")
        self.save(self.a, "a0")
        before = {p.name: p.read_bytes() for p in self.history.iterdir()}
        files = {p.name: p.read_bytes() for p in self.dir.iterdir() if p.is_file()}
        run(["timeline", "history", "--file", str(self.a)])
        self.assertEqual({p.name: p.read_bytes() for p in self.history.iterdir()}, before)
        self.assertEqual({p.name: p.read_bytes() for p in self.dir.iterdir() if p.is_file()}, files)

    def test_undo_command_acts_on_the_file_named_by_file(self):
        a0 = self.save(self.a, "a0")
        self.save(self.b, "b0")
        self.save(self.a, "a1")
        b1 = self.save(self.b, "b1")
        code, out, err = run(["timeline", "undo", "--file", str(self.a)])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.a.read_bytes(), a0)
        self.assertEqual(self.b.read_bytes(), b1)
        self.assertIn("timeline-a.json", out)

    def test_undo_command_with_nothing_of_its_own_names_the_others(self):
        self.save(self.b, "b0")
        self.save(self.b, "b1")
        a0 = self.save(self.a, "a0")
        code, out, err = run(["timeline", "undo", "--file", str(self.a)])
        self.assertEqual(code, 1)
        self.assertIn("Nothing to undo", err)
        self.assertIn("other timeline files", err)
        self.assertEqual(self.a.read_bytes(), a0)


if __name__ == "__main__":
    unittest.main()
