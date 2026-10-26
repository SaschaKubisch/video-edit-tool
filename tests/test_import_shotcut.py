"""Check 3.7b: reading the cuts back from a Shotcut project (SPECS 8.6; decision D11,
"Tool reads cuts back"). A project written by the exporter is edited the way Shotcut edits it
(an entry trimmed, moved, split, removed, a filter added), saved, and read back: the timeline
file gets the new cut points and order, one new segment with origin "manual", one segment with
enabled false, and nothing else changes."""

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import export_shotcut
import import_shotcut
import store
import timeline
from tests.test_export_shotcut import ShotcutFixture, props
from tests.test_timeline_commands import run

MLT_FPS = 30000 / 1001


def clock(frame: int) -> str:
    """The time of a frame as Shotcut writes it: HH:MM:SS.mmm."""
    ms = round(frame / MLT_FPS * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


class ImportFixture(ShotcutFixture):
    def setUp(self):
        name = self.id().split(".")[-1]
        self.work = self.root / f"imp-{name}"
        self.work.mkdir(parents=True, exist_ok=True)
        self.path = self.work / "timeline.json"
        self.tl = self.make_tl(loud=False)
        self.tl.save(str(self.path))
        export_shotcut.write_project(self.tl, str(self.work), str(self.work / "film.mlt"),
                                     protected=[str(self.originals)], log=lambda s: None, check=False)
        self.mlt = self.work / "film.mlt"

    # ── editing the project as Shotcut would ──
    def tree(self):
        return ET.parse(self.mlt)

    def video_playlist(self, root):
        return [p for p in root.findall("playlist") if props(p).get("shotcut:video") == "1"][0]

    def entry_of(self, root, segment):
        chains = {c.get("id"): props(c) for c in root.findall("chain")}
        for e in self.video_playlist(root).findall("entry"):
            if chains[e.get("producer")].get("vet:segment") == segment:
                return e
        raise KeyError(segment)

    def save(self, tree):
        tree.write(self.mlt, encoding="unicode", xml_declaration=True)

    def read(self, **kw):
        return import_shotcut.read_back(str(self.mlt), str(self.path), **kw)

    def data(self):
        return json.loads(self.path.read_text())

    def digest(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


class ReadBackTests(ImportFixture):
    def test_a_project_nobody_touched_changes_nothing(self):
        before = self.path.read_bytes()
        report = self.read()
        self.assertTrue(report.nothing_changed)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(store.list_snapshots(str(self.path)), [])

    def test_a_trimmed_entry_changes_in_and_out_of_its_segment_only(self):
        tree = self.tree()
        e = self.entry_of(tree.getroot(), "s001")
        e.set("in", "14")
        e.set("out", "60")                       # MLT's out is the last frame used
        self.save(tree)
        report = self.read()
        d = self.data()
        s1 = d["segments"][0]
        self.assertEqual((s1["id"], s1["in"], s1["out"]), ("s001", 14, 61))
        self.assertEqual(report.changed, [{"id": "s001", "in": [10, 14], "out": [70, 61]}])
        self.assertEqual((d["segments"][1]["in"], d["segments"][1]["out"]), (5, 65))
        self.assertEqual((d["segments"][2]["in"], d["segments"][2]["out"]), (40, 100))

    def test_times_written_as_a_clock_are_read_as_frames(self):
        tree = self.tree()
        e = self.entry_of(tree.getroot(), "s001")
        e.set("in", clock(14))
        e.set("out", clock(60))
        self.save(tree)
        self.read()
        s1 = self.data()["segments"][0]
        self.assertEqual((s1["in"], s1["out"]), (14, 61))

    def test_moved_entries_change_the_order(self):
        tree = self.tree()
        pl = self.video_playlist(tree.getroot())
        entries = pl.findall("entry")
        pl.remove(entries[2])
        pl.insert(list(pl).index(entries[0]), entries[2])          # s003 to the front
        self.save(tree)
        report = self.read()
        d = self.data()
        self.assertEqual([s["id"] for s in d["segments"]], ["s003", "s001", "s002"])
        self.assertTrue(report.moved)
        self.assertEqual((d["segments"][0]["in"], d["segments"][0]["out"]), (40, 100))

    def test_a_removed_entry_disables_its_segment_and_keeps_it_in_the_file(self):
        tree = self.tree()
        pl = self.video_playlist(tree.getroot())
        pl.remove(self.entry_of(tree.getroot(), "s002"))
        self.save(tree)
        report = self.read()
        d = self.data()
        self.assertEqual([s["id"] for s in d["segments"]], ["s001", "s002", "s003"])
        self.assertIs(d["segments"][1]["enabled"], False)
        self.assertEqual((d["segments"][1]["in"], d["segments"][1]["out"]), (5, 65))
        self.assertEqual(report.removed, ["s002"])

    def test_a_split_entry_gives_the_second_half_as_a_new_manual_segment(self):
        tree = self.tree()
        root = tree.getroot()
        pl = self.video_playlist(root)
        e = self.entry_of(root, "s003")
        e.set("out", "69")                                       # the first half: frames 40 to 69
        second = ET.Element("entry", {"producer": e.get("producer"), "in": "70", "out": "99"})
        pl.insert(list(pl).index(e) + 1, second)
        self.save(tree)
        report = self.read()
        d = self.data()
        ids = [s["id"] for s in d["segments"]]
        self.assertEqual(ids, ["s001", "s002", "s003", "s004"])
        s3, s4 = d["segments"][2], d["segments"][3]
        self.assertEqual((s3["in"], s3["out"]), (40, 70))
        self.assertEqual((s4["in"], s4["out"], s4["source"], s4["origin"]), (70, 100, "A", "manual"))
        self.assertEqual(report.added, ["s004"])
        self.assertIn("s003", s4["note"])

    def test_the_combination_of_the_spec_and_a_filter_that_is_listed_and_not_read(self):
        tree = self.tree()
        root = tree.getroot()
        pl = self.video_playlist(root)
        # trim s001, split s003, remove s002, add a filter to the first entry
        e1 = self.entry_of(root, "s001")
        e1.set("in", "12")
        pl.remove(self.entry_of(root, "s002"))
        e3 = self.entry_of(root, "s003")
        e3.set("out", "59")
        pl.insert(list(pl).index(e3) + 1, ET.Element("entry", {"producer": e3.get("producer"),
                                                               "in": "60", "out": "99"}))
        f = ET.SubElement(e1, "filter")
        for k, v in (("mlt_service", "avfilter.eq"), ("av.brightness", "0.2")):
            p = ET.SubElement(f, "property", name=k)
            p.text = v
        self.save(tree)
        report = self.read()
        d = self.data()
        self.assertEqual([s["id"] for s in d["segments"]], ["s001", "s002", "s003", "s004"])
        self.assertEqual(d["segments"][0]["in"], 12)
        self.assertIs(d["segments"][1]["enabled"], False)
        self.assertEqual(d["segments"][3]["origin"], "manual")
        self.assertTrue(any("avfilter.eq" in line for line in report.ignored), report.ignored)

    def test_nothing_else_in_the_file_changes(self):
        before = self.data()
        tree = self.tree()
        self.entry_of(tree.getroot(), "s001").set("in", "11")
        self.save(tree)
        self.read()
        after = self.data()
        for key in ("version", "kind", "film", "sources", "look", "audio", "exports"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["segments"][1:], before["segments"][1:])
        s1_before, s1_after = dict(before["segments"][0]), dict(after["segments"][0])
        s1_before.pop("in"), s1_after.pop("in")
        self.assertEqual(s1_after, s1_before)

    def test_the_old_file_is_kept_as_a_snapshot_and_undo_goes_back(self):
        old = self.path.read_bytes()
        tree = self.tree()
        self.entry_of(tree.getroot(), "s001").set("in", "11")
        self.save(tree)
        self.read()
        self.assertEqual(len(store.list_snapshots(str(self.path))), 1)
        store.undo(str(self.path))
        self.assertEqual(self.path.read_bytes(), old)

    def test_a_dry_run_writes_nothing(self):
        tree = self.tree()
        self.entry_of(tree.getroot(), "s001").set("in", "11")
        self.save(tree)
        before = self.path.read_bytes()
        report = self.read(dry_run=True)
        self.assertEqual(report.changed[0]["id"], "s001")
        self.assertEqual(self.path.read_bytes(), before)

    def test_an_entry_without_in_and_out_uses_the_whole_producer(self):
        tree = self.tree()
        root = tree.getroot()
        e = self.entry_of(root, "s002")
        chain = [c for c in root.findall("chain") if c.get("id") == e.get("producer")][0]
        chain.set("in", "0")
        chain.set("out", "99")
        del e.attrib["in"]
        del e.attrib["out"]
        self.save(tree)
        self.read()
        s2 = self.data()["segments"][1]
        self.assertEqual((s2["in"], s2["out"]), (0, 100))

    def test_relative_resource_paths_are_found_from_the_root_attribute(self):
        tree = self.tree()
        root = tree.getroot()
        root.set("root", str(self.copies))
        for c in root.findall("chain"):
            p = [p for p in c.findall("property") if p.get("name") == "resource"][0]
            if str(self.copies) in p.text:
                p.text = Path(p.text).name
        self.entry_of(root, "s001").set("in", "11")
        self.save(tree)
        self.read()
        self.assertEqual(self.data()["segments"][0]["in"], 11)

    def test_a_segment_the_tool_had_switched_off_stays_off_and_in_place(self):
        tl = self.make_tl(loud=False)
        tl.data["segments"][1]["enabled"] = False
        tl.save(str(self.path))
        export_shotcut.write_project(tl, str(self.work), str(self.mlt), protected=[str(self.originals)],
                                     log=lambda s: None, check=False)
        tree = self.tree()
        self.entry_of(tree.getroot(), "s003").set("in", "50")
        self.save(tree)
        report = self.read()
        d = self.data()
        self.assertEqual([s["id"] for s in d["segments"]], ["s001", "s002", "s003"])
        self.assertIs(d["segments"][1]["enabled"], False)
        self.assertEqual(report.removed, [])
        self.assertEqual(d["segments"][2]["in"], 50)

    def test_other_fields_of_a_changed_segment_are_kept(self):
        tl = self.make_tl(loud=False)
        tl.data["segments"][0].update(label="stove", note="n", gain_db_adjust=-2.0, priority="must")
        tl.save(str(self.path))
        export_shotcut.write_project(tl, str(self.work), str(self.mlt), protected=[str(self.originals)],
                                     log=lambda s: None, check=False)
        tree = self.tree()
        self.entry_of(tree.getroot(), "s001").set("in", "11")
        self.save(tree)
        self.read()
        s1 = self.data()["segments"][0]
        self.assertEqual((s1["label"], s1["note"], s1["gain_db_adjust"], s1["priority"]),
                         ("stove", "n", -2.0, "must"))


class RefusalTests(ImportFixture):
    def test_cut_points_beyond_the_clip_are_refused_with_the_rule_and_nothing_is_written(self):
        tree = self.tree()
        self.entry_of(tree.getroot(), "s001").set("out", "150")
        self.save(tree)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(import_shotcut.ShotcutImportError, "V9.*segments\\[0\\]\\.out"):
            self.read()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(store.list_snapshots(str(self.path)), [])

    def test_a_cut_too_short_for_the_fades_is_refused(self):
        tree = self.tree()
        e = self.entry_of(tree.getroot(), "s001")
        e.set("in", "10")
        e.set("out", "12")                                       # 3 frames, the film fades for 9
        self.save(tree)
        with self.assertRaisesRegex(import_shotcut.ShotcutImportError, "V11"):
            self.read()

    def test_an_entry_from_a_file_that_is_not_a_preview_copy_of_the_film_is_refused(self):
        tree = self.tree()
        root = tree.getroot()
        chain = [c for c in root.findall("chain") if "vet:segment" in {p.get("name") for p in c.findall("property")}][0]
        [p for p in chain.findall("property") if p.get("name") == "resource"][0].text = "/somewhere/else.mp4"
        self.save(tree)
        with self.assertRaisesRegex(import_shotcut.ShotcutImportError, "not a preview copy"):
            self.read()

    def test_a_file_that_is_not_xml_is_refused(self):
        self.mlt.write_text("this is not xml")
        with self.assertRaisesRegex(import_shotcut.ShotcutImportError, "not an MLT project"):
            self.read()

    def test_a_project_without_a_video_track_is_refused(self):
        tree = self.tree()
        root = tree.getroot()
        for p in root.findall("playlist"):
            if props(p).get("shotcut:video") == "1":
                for k in list(p):
                    if k.tag == "entry":
                        p.remove(k)
        for c in root.findall("chain"):
            for pr in c.findall("property"):
                if pr.get("name") == "vet:segment":
                    c.remove(pr)
        self.save(tree)
        self.assertRaises(import_shotcut.ShotcutImportError, self.read)


class IgnoredThingsTests(ImportFixture):
    def test_the_filters_and_transitions_the_writer_made_are_not_listed(self):
        report = self.read()
        self.assertEqual(report.ignored, [])

    def test_a_filter_on_a_clip_a_transition_and_a_second_video_track_are_listed(self):
        tree = self.tree()
        root = tree.getroot()
        chain = root.findall("chain")[0]
        f = ET.SubElement(chain, "filter")
        ET.SubElement(f, "property", name="mlt_service").text = "brightness"
        tr = root.find("tractor")
        t = ET.SubElement(tr, "transition")
        ET.SubElement(t, "property", name="mlt_service").text = "luma"
        self.save(tree)
        report = self.read()
        text = "\n".join(report.ignored)
        self.assertIn("filter brightness on clip chain0", text)
        self.assertIn("transition luma", text)


class CommandTests(ImportFixture):
    def test_import_shotcut_prints_what_changed_and_what_was_not_read(self):
        tree = self.tree()
        root = tree.getroot()
        self.entry_of(root, "s001").set("in", "11")
        f = ET.SubElement(root.findall("chain")[0], "filter")
        ET.SubElement(f, "property", name="mlt_service").text = "brightness"
        self.save(tree)
        code, out, err = run(["import-shotcut", "--file", str(self.path), "--mlt", str(self.mlt)])
        self.assertEqual(code, 0, err)
        self.assertIn("s001: in 10 -> 11", out)
        self.assertIn("Not read", out)
        self.assertIn("filter brightness", out)
        self.assertEqual(self.data()["segments"][0]["in"], 11)

    def test_the_command_refuses_bad_cut_points_with_exit_code_1(self):
        tree = self.tree()
        self.entry_of(tree.getroot(), "s001").set("out", "500")
        self.save(tree)
        before = self.path.read_bytes()
        code, out, err = run(["import-shotcut", "--file", str(self.path), "--mlt", str(self.mlt)])
        self.assertEqual(code, 1)
        self.assertIn("V9", err)
        self.assertEqual(self.path.read_bytes(), before)

    def test_export_shotcut_command_writes_the_project(self):
        out_path = self.work / "cli.mlt"
        code, out, err = run(["export-shotcut", "--file", str(self.path), "--work", str(self.work),
                              "--out", str(out_path), "--no-check"])
        self.assertEqual(code, 0, err)
        self.assertTrue(out_path.is_file())
        self.assertIn("160x90", out)

    def test_round_trip_through_the_commands_keeps_the_film_valid(self):
        code, out, err = run(["export-shotcut", "--file", str(self.path), "--work", str(self.work),
                              "--out", str(self.mlt)])
        self.assertEqual(code, 0, err)
        tree = self.tree()
        self.entry_of(tree.getroot(), "s002").set("in", "8")
        self.save(tree)
        code, out, err = run(["import-shotcut", "--file", str(self.path), "--mlt", str(self.mlt)])
        self.assertEqual(code, 0, err)
        tl = timeline.Timeline.load(str(self.path))
        self.assertFalse(timeline.has_errors(tl.validate(file_checks=False)))
        self.assertEqual(tl.data["segments"][1]["in"], 8)


if __name__ == "__main__":
    unittest.main()
