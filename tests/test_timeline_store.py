"""Check 2.3 (the part built so far): saving the timeline file keeps numbered earlier
versions (SPECS 4.1 rule 3), a stale hash is refused (rule 4), and a write that is
interrupted halfway leaves the old file whole (rule 2)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import store
import timeline
from tests.timeline_fixture import example


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "timeline.json"

    def tl(self, **changes):
        d = example()
        d.update(changes)
        return timeline.Timeline(d)

    def test_the_first_save_makes_no_snapshot(self):
        store.save(self.tl(), str(self.path))
        self.assertTrue(self.path.exists())
        self.assertFalse((self.dir / "timeline.history").exists())

    def test_a_save_over_an_existing_file_keeps_the_old_one_as_snapshot_0001(self):
        store.save(self.tl(), str(self.path))
        old = self.path.read_bytes()
        d = example()
        d["segments"][0]["label"] = "changed"
        store.save(timeline.Timeline(d), str(self.path))
        snaps = sorted((self.dir / "timeline.history").iterdir())
        self.assertEqual([p.name for p in snaps], ["0001.json", "0001.owner.json"])   # the copy and the record of its file
        self.assertEqual(snaps[0].read_bytes(), old)
        self.assertIn("changed", self.path.read_text())

    def test_snapshot_numbers_rise_and_only_the_newest_are_kept(self):
        store.save(self.tl(), str(self.path))
        for i in range(7):
            d = example()
            d["segments"][0]["label"] = f"v{i}"
            store.save(timeline.Timeline(d), str(self.path), keep=5)
        found = sorted(p.name for p in (self.dir / "timeline.history").iterdir())
        names = [n for n in found if not n.endswith(".owner.json")]
        self.assertEqual(names, [f"{n:04d}.json" for n in range(3, 8)])
        self.assertEqual([n for n in found if n.endswith(".owner.json")],               # a record goes with its snapshot
                         [f"{n:04d}.owner.json" for n in range(3, 8)])
        self.assertEqual(store.list_snapshots(str(self.path)),
                         [str(self.dir / "timeline.history" / n) for n in names])

    def test_a_stale_hash_is_refused_and_changes_nothing(self):
        store.save(self.tl(), str(self.path))
        h = timeline.file_hash(str(self.path))
        d = example()
        d["segments"][0]["label"] = "mine"
        # someone else changed the file meanwhile
        other = example()
        other["segments"][0]["label"] = "theirs"
        timeline.Timeline(other).save(str(self.path))
        theirs = self.path.read_bytes()
        with self.assertRaises(store.StaleHash):
            store.save(timeline.Timeline(d), str(self.path), expected_hash=h)
        self.assertEqual(self.path.read_bytes(), theirs)
        self.assertFalse((self.dir / "timeline.history").exists())

    def test_the_right_hash_is_accepted(self):
        store.save(self.tl(), str(self.path))
        h = timeline.file_hash(str(self.path))
        d = example()
        d["segments"][0]["label"] = "mine"
        new_hash = store.save(timeline.Timeline(d), str(self.path), expected_hash=h)
        self.assertEqual(new_hash, timeline.file_hash(str(self.path)))
        self.assertNotEqual(new_hash, h)

    def test_a_write_interrupted_halfway_leaves_the_old_file_whole(self):
        store.save(self.tl(), str(self.path))
        old = self.path.read_bytes()
        d = example()
        d["segments"][0]["label"] = "never written"
        with mock.patch("os.replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                store.save(timeline.Timeline(d), str(self.path))
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual([p.name for p in self.dir.iterdir() if p.name.startswith(".timeline-")], [])

    def test_a_file_that_is_not_there_has_no_hash_and_no_stale_check(self):
        self.assertIsNone(store.current_hash(str(self.path)))
        store.save(self.tl(), str(self.path), expected_hash=None)
        self.assertEqual(store.current_hash(str(self.path)), timeline.file_hash(str(self.path)))


if __name__ == "__main__":
    unittest.main()
