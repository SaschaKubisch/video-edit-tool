"""Check 3.11: the resume of a long render, the parts that need no program (SPECS 5.3, step 6). The film is cut into
chunks of whole scenes of about two minutes; each chunk has a key that changes when anything that shapes its pictures
changes; a finished chunk with a matching key is kept and not rendered again; the free space needed is about 2.1 times
the file; the join of the chunks needs the same parameter sets in all of them."""

import os
import tempfile
import unittest
from pathlib import Path

import chunks
import export
import render
from tests.test_export import ExportFixture


def seg(i, frames):
    class S:
        pass
    s = S()
    s.id, s.length_frames = f"s{i:03d}", frames
    return s


class PlanChunksTests(unittest.TestCase):
    def sizes(self, lengths, chunk):
        return [[s.length_frames for s in c] for c in chunks.plan_chunks([seg(i, n) for i, n in enumerate(lengths)], chunk)]

    def test_whole_scenes_are_gathered_until_a_chunk_is_long_enough(self):
        self.assertEqual(self.sizes([100, 100, 100, 100, 100, 100], 250), [[100, 100, 100], [100, 100, 100]])
        self.assertEqual(self.sizes([100, 100, 100, 100], 150), [[100, 100], [100, 100]])

    def test_a_scene_longer_than_a_chunk_is_a_chunk_of_its_own(self):
        self.assertEqual(self.sizes([50, 900, 50, 50], 250), [[50, 900], [50, 50]])
        self.assertEqual(self.sizes([900, 900], 250), [[900], [900]])

    def test_a_small_rest_joins_the_chunk_before_it(self):
        self.assertEqual(self.sizes([100, 100, 30], 200), [[100, 100, 30]])          # 30 is under a quarter of a chunk
        self.assertEqual(self.sizes([100, 100, 60], 200), [[100, 100], [60]])

    def test_a_film_that_is_shorter_than_a_chunk_is_one_chunk_and_nothing_is_one_chunk_too(self):
        self.assertEqual(self.sizes([100, 100], 1000), [[100, 100]])
        self.assertEqual(self.sizes([], 1000), [])

    def test_no_chunk_length_means_one_chunk(self):
        self.assertEqual(self.sizes([100, 100, 100], 0), [[100, 100, 100]])

    def test_the_order_is_kept_and_nothing_is_lost(self):
        lengths = [37, 211, 90, 14, 300, 77, 55, 123, 9]
        got = chunks.plan_chunks([seg(i, n) for i, n in enumerate(lengths)], 200)
        self.assertEqual([s.id for c in got for s in c], [f"s{i:03d}" for i in range(len(lengths))])


class KeyTests(ExportFixture):
    def setUp(self):
        self.tl = self.make_tl(exports=[self.entry()], loud=False)
        self.entry_ = self.tl.data["exports"][0]
        self.plan = render.make_plan(self.tl, preview_width=self.entry_["width"])

    def key(self, tl=None, plan=None, index=0, text="", fade=(0, 0), **kw):
        tl = tl or self.tl
        plan = plan or render.make_plan(tl, preview_width=self.entry_["width"])
        segs = plan.segments[index:index + 1]
        return chunks.chunk_key(chunks.key_material(tl, plan, segs, tl.data["exports"][0], text, fade, **kw))

    def test_the_same_chunk_has_the_same_key(self):
        self.assertEqual(self.key(), self.key())
        self.assertEqual(len(self.key()), 20)

    def variant(self, tl=None, entry=None, width=None, **kw):
        tl = tl or self.make_tl(exports=[self.entry()], loud=False)
        plan = render.make_plan(tl, preview_width=width or self.entry_["width"])
        e = entry or tl.data["exports"][0]
        return chunks.chunk_key(chunks.key_material(tl, plan, plan.segments[:1], e, kw.pop("text", ""),
                                                    kw.pop("fade", (0, 0)), **kw))

    def test_each_thing_that_shapes_the_pictures_changes_the_key(self):
        base = self.key()
        keys = {}
        tl = self.make_tl(exports=[self.entry()], loud=False)
        tl.data["segments"][0]["in"] += 1
        keys["in"] = self.variant(tl)
        tl = self.make_tl(exports=[self.entry()], loud=False)
        tl.data["segments"][0]["out"] -= 1
        keys["out"] = self.variant(tl)
        tl = self.make_tl(exports=[self.entry()], loud=False)
        tl.data["segments"][0]["source"] = "B"
        keys["source"] = self.variant(tl)
        tl = self.make_tl(exports=[self.entry()], loud=False)
        tl.data["exports"][0]["video"]["rate_control"]["crf"] = 20
        keys["rate"] = self.variant(tl)
        tl = self.make_tl(exports=[self.entry()], loud=False)
        keys["size"] = self.variant(tl, entry=dict(tl.data["exports"][0], width=160, height=90), width=160)
        keys["lut"] = self.variant(self.make_tl(exports=[self.entry()], loud=False, lut=self.luts / "invert.cube"))
        keys["base"] = self.variant(self.make_tl(exports=[self.entry()], loud=False, base=self.luts / "halve.cube"))
        keys["text"] = self.variant(text="abc")
        keys["fade"] = self.variant(fade=(9, 0))
        keys["ffmpeg"] = self.variant(ffmpeg_version="ffmpeg version 99")
        keys["decoder"] = self.variant(decoder="other")
        self.assertEqual(self.variant(), base)
        for what, k in keys.items():
            self.assertNotEqual(k, base, what)
        self.assertEqual(len(set(keys.values())), len(keys))

    def test_a_changed_original_file_changes_the_key(self):
        base = self.key()
        path = self.tl.data["sources"]["A"]["original_video"]
        st = os.stat(path)
        self.addCleanup(os.utime, path, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        self.assertNotEqual(self.key(), base)

    def test_a_change_in_another_chunk_does_not_change_this_key(self):
        a = self.key(index=0)
        tl = self.make_tl(exports=[self.entry()], loud=False)
        tl.data["segments"][2]["in"] += 3
        self.assertEqual(self.key(tl, index=0), a)
        self.assertNotEqual(self.key(tl, index=2), self.key(index=2))

    def test_the_sound_and_the_name_of_the_export_are_not_in_the_key(self):
        a = self.key()
        tl = self.make_tl(exports=[self.entry()], loud=True)
        tl.data["exports"][0]["audio"]["bitrate_kbps"] = 128
        self.assertEqual(self.key(tl), a)

    def test_the_key_names_its_chunks_scenes_and_frames(self):
        m = chunks.key_material(self.tl, self.plan, self.plan.segments[1:2], self.entry_, "", (0, 0))
        self.assertEqual([s["id"] for s in m["segments"]], ["s002"])
        self.assertEqual(m["segments"][0]["in"], 5)
        self.assertEqual(m["format"], chunks.CHUNK_FORMAT)


class FolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = os.path.join(self.tmp.name, "chunks", "film")
        os.makedirs(self.folder)

    def finish(self, i, key, frames=60, size=1000, ext="mp4"):
        path = chunks.chunk_path(self.folder, i, ext)
        Path(path).write_bytes(b"x" * size)
        chunks.write_record(self.folder, i, {"key": key, "frames": frames, "bytes": size})
        return path

    def test_the_names(self):
        self.assertEqual(os.path.basename(chunks.chunk_path(self.folder, 7, "mp4")), "chunk-0007.mp4")
        self.assertEqual(os.path.basename(chunks.chunk_path(self.folder, 7, "mp4", partial=True)), "chunk-0007.mp4.partial")
        self.assertTrue(chunks.folder("/w", "film", None).endswith(os.path.join("chunks", "film")))
        self.assertTrue(chunks.folder("/w", "film", "ja").endswith(os.path.join("chunks", "film.ja")))
        self.assertEqual(chunks.folder("/w", "film", "en"), chunks.folder("/w", "film", None))

    def test_a_finished_chunk_with_its_key_is_reusable(self):
        self.finish(1, "k1")
        self.assertTrue(chunks.reusable(self.folder, 1, "k1", 60, "mp4"))

    def test_a_chunk_is_not_reusable_with_another_key_frames_size_or_without_a_file_or_record(self):
        self.finish(1, "k1")
        self.assertFalse(chunks.reusable(self.folder, 1, "k2", 60, "mp4"))
        self.assertFalse(chunks.reusable(self.folder, 1, "k1", 61, "mp4"))
        os.truncate(chunks.chunk_path(self.folder, 1, "mp4"), 10)                # the file is not the one recorded
        self.assertFalse(chunks.reusable(self.folder, 1, "k1", 60, "mp4"))
        self.assertFalse(chunks.reusable(self.folder, 2, "k1", 60, "mp4"))
        Path(chunks.chunk_path(self.folder, 3, "mp4")).write_bytes(b"x" * 5)       # a file without a record
        self.assertFalse(chunks.reusable(self.folder, 3, "k1", 60, "mp4"))

    def test_a_broken_record_is_not_reusable(self):
        self.finish(1, "k1")
        Path(chunks.record_path(self.folder, 1)).write_text("{not json")
        self.assertFalse(chunks.reusable(self.folder, 1, "k1", 60, "mp4"))

    def test_prune_removes_partial_files_stale_chunks_and_chunks_that_are_no_longer_wanted(self):
        self.finish(1, "k1")
        self.finish(2, "old")
        self.finish(5, "k5")
        Path(chunks.chunk_path(self.folder, 3, "mp4", partial=True)).write_bytes(b"half")
        removed = chunks.prune(self.folder, {1: "k1", 2: "k2", 3: "k3"}, "mp4")
        names = sorted(os.listdir(self.folder))
        self.assertEqual(names, ["chunk-0001.json", "chunk-0001.mp4"])
        self.assertEqual(len(removed), 5)                  # chunk 2 and its record, chunk 5 and its record, the partial

    def test_prune_keeps_the_audio_file_and_its_record(self):
        Path(os.path.join(self.folder, "audio.m4a")).write_bytes(b"a")
        Path(os.path.join(self.folder, "audio.json")).write_text("{}")
        chunks.prune(self.folder, {}, "mp4")
        self.assertEqual(sorted(os.listdir(self.folder)), ["audio.json", "audio.m4a"])

    def test_the_bytes_of_the_finished_chunks(self):
        self.finish(1, "k1", size=1000)
        self.finish(2, "k2", size=500)
        self.assertEqual(chunks.existing_bytes(self.folder, {1: "k1", 2: "other"}, "mp4"), 1000)
        self.assertEqual(chunks.existing_bytes(os.path.join(self.tmp.name, "nowhere"), {1: "k1"}, "mp4"), 0)

    def test_the_concat_list_names_the_chunks_in_order_and_escapes_a_quote(self):
        lst = os.path.join(self.tmp.name, "list.txt")
        chunks.write_concat_list(["/a/c1.mp4", "/a/it's/c2.mp4"], lst)
        self.assertEqual(Path(lst).read_text(), "file '/a/c1.mp4'\nfile '/a/it'\\''s/c2.mp4'\n")


class SpaceTests(unittest.TestCase):
    def test_a_chunked_render_needs_about_two_point_one_times_the_file(self):
        self.assertEqual(chunks.CHUNK_SPACE_FACTOR, 2.1)
        self.assertEqual(chunks.space_needed(1000, 0, 50, chunked=True), 2100 + 50)

    def test_finished_chunks_are_part_of_it_already(self):
        self.assertEqual(chunks.space_needed(1000, 600, 50, chunked=True), 1500 + 50)
        self.assertEqual(chunks.space_needed(1000, 2000, 50, chunked=True), int(1000 * 1.1) + 50)   # never below the old margin

    def test_one_process_keeps_its_old_margin(self):
        self.assertEqual(chunks.space_needed(1000, 0, 50, chunked=False), int(1000 * export.SPACE_MARGIN) + 50)


if __name__ == "__main__":
    unittest.main()
