"""Check 2.3, the rest of it: `undo` restores the previous version, `history` lists the
versions with their time and a one-line change, and a validate after a direct edit makes a
snapshot (SPECS 4.1 rule 3, 4.3 step 6, 10.4).

The snapshots are copies of the file before it was replaced, in timeline.history/NNNN.json.
`undo` restores the newest snapshot that differs from the file. A snapshot that equals the
file (a validate made it) is skipped. The version that undo replaces is kept in
timeline.history/undone/, so an undo never loses work."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import store
import timeline
from tests.timeline_fixture import example


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "timeline.json"
        self.history = self.dir / "timeline.history"

    def data(self, label="v0", **seg0):
        d = example()
        d["segments"][0]["label"] = label
        d["segments"][0].update(seg0)
        return d

    def save(self, label, **seg0):
        """A save the way the page or a command makes it: snapshot of the old file, new file."""
        store.save(timeline.Timeline(self.data(label, **seg0)), str(self.path))
        return self.path.read_bytes()

    def label(self):
        return timeline.Timeline.load(str(self.path)).data["segments"][0]["label"]

    def names(self, folder=None):
        """The snapshot files of a folder; the owner records beside them (NNNN.owner.json) are left out."""
        folder = folder or self.history
        return sorted(p.name for p in folder.iterdir() if not p.name.endswith(".owner.json")) if folder.exists() else []


class UndoTests(Base):
    def test_undo_restores_the_previous_version_byte_for_byte(self):
        v0 = self.save("v0")
        self.save("v1")
        result = store.undo(str(self.path))
        self.assertEqual(self.path.read_bytes(), v0)
        self.assertEqual(result["restored"], 1)
        self.assertEqual(result["hash"], timeline.file_hash(str(self.path)))

    def test_undo_twice_goes_two_versions_back(self):
        v0 = self.save("v0")
        self.save("v1")
        self.save("v2")
        store.undo(str(self.path))
        self.assertEqual(self.label(), "v1")
        store.undo(str(self.path))
        self.assertEqual(self.path.read_bytes(), v0)

    def test_the_version_that_undo_replaces_is_kept_in_the_undone_folder(self):
        self.save("v0")
        v1 = self.save("v1")
        result = store.undo(str(self.path))
        undone = self.history / "undone"
        self.assertEqual(self.names(undone), ["0001.json"])
        self.assertEqual((undone / "0001.json").read_bytes(), v1)
        self.assertEqual(Path(result["undone"]), undone / "0001.json")

    def test_undone_versions_do_not_count_as_snapshots(self):
        self.save("v0")
        self.save("v1")
        store.undo(str(self.path))
        self.assertEqual([Path(p).name for p in store.list_snapshots(str(self.path))], ["0001.json"])

    def test_nothing_to_undo_changes_nothing(self):
        v0 = self.save("v0")                       # the first save makes no snapshot
        with self.assertRaises(store.NothingToUndo):
            store.undo(str(self.path))
        self.assertEqual(self.path.read_bytes(), v0)
        self.assertFalse(self.history.exists())

    def test_a_missing_file_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            store.undo(str(self.path))

    def test_a_snapshot_equal_to_the_file_is_skipped(self):
        self.save("v0")
        v1 = self.save("v1")
        store.snapshot_if_changed(str(self.path))                # a validate: copy of v1
        self.assertEqual(len(store.list_snapshots(str(self.path))), 2)
        store.undo(str(self.path))
        self.assertEqual(self.label(), "v0")                     # not "v1 again"
        self.assertNotEqual(self.path.read_bytes(), v1)

    def test_undo_is_not_a_toggle(self):
        self.save("v0")
        self.save("v1")
        self.save("v2")
        store.snapshot_if_changed(str(self.path))
        store.undo(str(self.path))
        self.assertEqual(self.label(), "v1")
        store.undo(str(self.path))
        self.assertEqual(self.label(), "v0")                     # not v2
        with self.assertRaises(store.NothingToUndo):
            store.undo(str(self.path))

    def test_the_snapshot_that_was_restored_stays_so_a_later_direct_edit_can_be_undone(self):
        v0 = self.save("v0")
        self.save("v1")
        store.undo(str(self.path))                               # file is v0 again
        timeline.Timeline(self.data("direct edit")).save(str(self.path))
        store.snapshot_if_changed(str(self.path))                # the owner validates
        store.undo(str(self.path))
        self.assertEqual(self.path.read_bytes(), v0)

    def test_after_an_undo_the_next_save_snapshots_the_restored_version(self):
        self.save("v0")
        self.save("v1")
        store.undo(str(self.path))
        restored = self.path.read_bytes()
        self.save("v3")
        self.assertEqual(Path(store.list_snapshots(str(self.path))[-1]).read_bytes(), restored)

    def test_a_stale_hash_is_refused_and_changes_nothing(self):
        self.save("v0")
        self.save("v1")
        before = self.path.read_bytes()
        with self.assertRaises(store.StaleHash):
            store.undo(str(self.path), expected_hash="0" * 64)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.history / "undone").exists())

    def test_the_right_hash_is_accepted(self):
        self.save("v0")
        self.save("v1")
        store.undo(str(self.path), expected_hash=timeline.file_hash(str(self.path)))
        self.assertEqual(self.label(), "v0")

    def test_a_write_that_fails_leaves_the_file_and_the_history_as_they_were(self):
        self.save("v0")
        v1 = self.save("v1")
        snapshots = store.list_snapshots(str(self.path))
        with mock.patch("os.replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                store.undo(str(self.path))
        self.assertEqual(self.path.read_bytes(), v1)
        self.assertEqual(store.list_snapshots(str(self.path)), snapshots)
        self.assertEqual([p.name for p in self.dir.iterdir() if p.name.startswith(".")], [])

    def test_only_the_newest_undone_versions_are_kept(self):
        self.save("v0")
        for i in range(1, 6):
            self.save(f"v{i}")
        for _ in range(5):
            store.undo(str(self.path), keep=3)
        self.assertEqual(self.names(self.history / "undone"), ["0003.json", "0004.json", "0005.json"])
        records = sorted(p.name for p in (self.history / "undone").iterdir() if p.name.endswith(".owner.json"))
        self.assertEqual(records, ["0003.owner.json", "0004.owner.json", "0005.owner.json"])      # pruned with their copies


class SnapshotAfterAValidateTests(Base):
    def test_a_file_that_differs_from_the_newest_snapshot_is_snapshotted(self):
        self.save("v0")
        self.save("v1")
        timeline.Timeline(self.data("direct edit")).save(str(self.path))
        made = store.snapshot_if_changed(str(self.path))
        self.assertEqual(Path(made).name, "0002.json")
        self.assertEqual(Path(made).read_bytes(), self.path.read_bytes())

    def test_a_file_that_equals_the_newest_snapshot_is_not_snapshotted_again(self):
        self.save("v0")
        self.save("v1")
        store.snapshot_if_changed(str(self.path))
        self.assertIsNone(store.snapshot_if_changed(str(self.path)))
        self.assertEqual(len(store.list_snapshots(str(self.path))), 2)

    def test_with_no_snapshot_yet_the_file_is_snapshotted(self):
        self.save("v0")
        made = store.snapshot_if_changed(str(self.path))
        self.assertEqual(Path(made).name, "0001.json")

    def test_a_missing_file_gives_nothing(self):
        self.assertIsNone(store.snapshot_if_changed(str(self.path)))

    def test_the_workflow_of_the_spec_edit_validate_undo(self):
        v0 = self.save("v0")
        store.snapshot_if_changed(str(self.path))               # validate of the good version
        timeline.Timeline(self.data("by hand")).save(str(self.path))
        store.snapshot_if_changed(str(self.path))               # validate after the direct edit
        store.undo(str(self.path))                              # "returns to the version before"
        self.assertEqual(self.path.read_bytes(), v0)


class HistoryTests(Base):
    def test_the_versions_are_listed_oldest_first_and_the_file_last(self):
        self.save("v0")
        self.save("v1")
        self.save("v2")
        rows = store.history(str(self.path))
        self.assertEqual([r["number"] for r in rows], [1, 2, None])
        self.assertEqual([r["current"] for r in rows], [False, False, True])
        self.assertEqual(rows[-1]["hash"], timeline.file_hash(str(self.path)))
        self.assertEqual(rows[0]["hash"], timeline.file_hash(store.list_snapshots(str(self.path))[0]))
        for r in rows:
            self.assertIsInstance(r["time"], float)

    def test_a_row_says_what_changed_against_the_version_before(self):
        self.save("v0")
        self.save("v1", **{"in": 68})
        rows = store.history(str(self.path))
        self.assertEqual(rows[0]["change"], "first version kept")
        self.assertIn("s001", rows[1]["change"])
        self.assertIn("in 38 -> 68", rows[1]["change"])
        self.assertIn("label", rows[1]["change"])

    def test_an_identical_neighbour_says_so(self):
        self.save("v0")
        self.save("v1")
        store.snapshot_if_changed(str(self.path))
        rows = store.history(str(self.path))
        self.assertEqual(rows[-1]["change"], "no change")

    def test_no_file_and_no_snapshots_give_an_empty_list(self):
        self.assertEqual(store.history(str(self.path)), [])

    def test_a_file_that_cannot_be_read_is_listed_with_a_reason(self):
        self.save("v0")
        self.save("v1")
        self.path.write_text("not json")
        rows = store.history(str(self.path))
        self.assertIn("not a timeline file", rows[-1]["change"])


class ChangeLineTests(unittest.TestCase):
    def change(self, edit):
        new = example()
        edit(new)
        return store.describe_change(example(), new)

    def test_no_difference(self):
        self.assertEqual(store.describe_change(example(), example()), "no change")

    def test_a_trim(self):
        self.assertEqual(self.change(lambda d: d["segments"][1].update({"out": 2300})),
                         "s002 out 2380 -> 2300")

    def test_a_scene_removed_and_restored(self):
        self.assertEqual(self.change(lambda d: d["segments"][1].update({"enabled": False})),
                         "s002 removed from the film")
        old = example()
        old["segments"][1]["enabled"] = False
        self.assertEqual(store.describe_change(old, example()), "s002 back in the film")

    def test_a_label(self):
        self.assertEqual(self.change(lambda d: d["segments"][0].update({"label": "new"})),
                         "s001 label")

    def test_a_move(self):
        def move(d):
            d["segments"].insert(0, d["segments"].pop(2))
        self.assertEqual(self.change(move), "order changed")

    def test_segments_added_and_taken_out(self):
        def add(d):
            d["segments"].append({"id": "s004", "source": "A002_01060615_C083", "in": 0, "out": 100})
        self.assertEqual(self.change(add), "s004 added")
        self.assertEqual(store.describe_change(
            self.apply(add), example()), "s004 taken out of the file")

    def apply(self, edit):
        d = example()
        edit(d)
        return d

    def test_an_offset_typed_by_hand_and_taken_away(self):
        src = "A002_01011540_C002"
        self.assertEqual(self.change(lambda d: d["sources"][src].update(
            {"audio_offset_hand_s": 0.5})), f"{src} offset set by hand to 0.500 s")
        old = example()
        old["sources"][src]["audio_offset_hand_s"] = 0.5
        self.assertEqual(store.describe_change(old, example()),
                         f"{src} offset set by hand removed")
        new = example()
        new["sources"][src]["audio_offset_hand_s"] = 0.7
        self.assertEqual(store.describe_change(old, new),
                         f"{src} offset set by hand 0.500 -> 0.700 s")

    def test_other_parts_of_the_file(self):
        self.assertEqual(self.change(lambda d: d["film"].update({"fade_in_s": 2.0})), "film changed")
        self.assertEqual(self.change(lambda d: d["audio"]["loudness"].update({"mode": "off"})),
                         "audio changed")

    def test_several_changes_are_joined_and_long_lists_are_cut(self):
        def many(d):
            for s in d["segments"]:
                s["in"] += 1
                s["label"] = "x"
            d["film"]["fade_in_s"] = 3.0
        line = self.change(many)
        self.assertIn("; ", line)
        self.assertRegex(line, r"\(\+\d+ more\)$")
        self.assertLess(len(line), 200)


if __name__ == "__main__":
    unittest.main()
