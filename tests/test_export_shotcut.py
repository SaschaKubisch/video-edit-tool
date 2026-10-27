"""Check 3.7: the Shotcut project (SPECS 8.6). The XML is read back as XML: the profile, one
video entry and one sound entry per enabled segment with the in and out frames, the crossfade
and fade filters, the creative LUT filter (and none for the base LUT), the shifted sound files.
Where melt (the engine inside Shotcut) is installed, it reads the project and renders it, and
its picture and its sound are compared with the tool's own."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

import export_shotcut
import loudness
import render
import timeline
import words
from tests import media
from tests.test_export import ExportFixture, W, H, FPS, RATE

MELT = export_shotcut.melt_path()
HAVE_MELT = os.path.isfile(MELT)


def props(e):
    return {p.get("name"): (p.text or "") for p in e.findall("property")}


class ShotcutFixture(ExportFixture):
    """The film of ExportFixture with real preview copies (half size) and a 96 kHz WAV."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.copies = cls.root / "copies"
        cls.copies.mkdir()
        for name, h in (("A", H), ("B", 136)):
            media.write_ramp_mp4(cls.copies / f"{name}.mp4", 100, W // 2, h // 2)
            cls.sources[name]["preview_copy"] = str(cls.copies / f"{name}.mp4")
        media.sine_wav(cls.originals / "B.wav", 12, 96000, 660, 0.3)         # 96 kHz
        cls.originals_before = None

    def make_project(self, tl, work=None, **kw):
        work = work or (self.root / f"work-{self.id().split('.')[-1]}")
        out = work / "film.mlt"
        r = export_shotcut.write_project(tl, str(work), str(out), protected=[str(self.originals)],
                                         log=lambda s: None, **kw)
        return r, ET.parse(out).getroot(), work

    def video_entries(self, root):
        pl = [p for p in root.findall("playlist") if props(p).get("shotcut:video") == "1"][0]
        return [(e.get("producer"), int(e.get("in")), int(e.get("out"))) for e in pl.findall("entry")]

    def chains(self, root):
        return {c.get("id"): props(c) for c in root.findall("chain")}


class ProjectContentTests(ShotcutFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tl = cls.make_tl(lut=cls.luts / "identity.cube", base=cls.luts / "halve.cube")
        cls.result, cls.root_el, cls.work_dir = cls.make_project(cls, cls.tl, cls.root / "work-content")

    def test_the_xml_is_well_formed_and_melt_reads_it(self):
        self.assertEqual(self.root_el.tag, "mlt")
        if HAVE_MELT:
            info = export_shotcut.check_with_melt(self.result.path, frames=30)
            self.assertEqual(info["frames_rendered"], 30)

    def test_the_profile_is_the_film_at_the_scale_of_the_preview_copies(self):
        p = self.root_el.find("profile")
        self.assertEqual((p.get("width"), p.get("height")), ("160", "90"))
        self.assertEqual((p.get("frame_rate_num"), p.get("frame_rate_den")), ("30000", "1001"))
        self.assertEqual(self.result.profile, (160, 90))

    def test_one_video_entry_per_enabled_segment_with_the_frames_of_the_segment(self):
        entries = self.video_entries(self.root_el)
        chains = self.chains(self.root_el)
        got = [(chains[c]["vet:segment"], chains[c]["vet:source"], a, b) for c, a, b in entries]
        # MLT's out is the last frame used: out - 1.
        self.assertEqual(got, [("s001", "A", 10, 69), ("s002", "B", 5, 64), ("s003", "A", 40, 99)])
        for c, a, b in entries:
            self.assertTrue(chains[c]["resource"].endswith(".mp4"))
            self.assertIn("copies", chains[c]["resource"])

    def test_a_short_has_no_shotcut_project(self):
        """Step 6: the project has no code for `fill` and `crop`, so a short would show the wide pictures letterboxed in
        a vertical profile. It is refused, with the reason, before anything is written."""
        tl = self.make_tl()
        tl.data["film"]["fit"] = "fill"
        work = self.root / "work-short-refused"
        with self.assertRaises(export_shotcut.ShotcutError) as ctx:
            export_shotcut.write_project(tl, str(work), str(work / "film.mlt"), log=lambda s: None)
        self.assertIn("short", str(ctx.exception))
        self.assertFalse((work / "film.mlt").exists())
        self.assertFalse((work / "audio-synced").exists())

    def test_a_disabled_segment_is_not_in_the_project(self):
        tl = self.make_tl()
        tl.data["segments"][1]["enabled"] = False
        r, root, work = self.make_project(tl, self.root / "work-disabled")
        ids = [self.chains(root)[c]["vet:segment"] for c, a, b in self.video_entries(root)]
        self.assertEqual(ids, ["s001", "s003"])

    def test_the_sound_has_one_entry_per_segment_on_two_tracks_in_turn(self):
        audio = [p for p in self.root_el.findall("playlist") if props(p).get("shotcut:audio") == "1"]
        self.assertEqual([props(p)["shotcut:name"] for p in audio], ["A1", "A2"])
        chains = self.chains(self.root_el)
        names = lambda pl: [chains[e.get("producer")]["vet:audio_of"] for e in pl.findall("entry")]
        self.assertEqual(names(audio[0]), ["s001", "s003"])
        self.assertEqual(names(audio[1]), ["s002"])
        for pl in audio:
            for e in pl.findall("entry"):
                self.assertIn("audio-synced", chains[e.get("producer")]["resource"])

    def test_a_sound_entry_reaches_half_a_join_beyond_the_picture_at_each_join(self):
        # join 0.25 s: half of it is 0.125 s, which is 4 frames at 29.97 fps
        audio = [p for p in self.root_el.findall("playlist") if props(p).get("shotcut:audio") == "1"]
        a1 = [(int(e.get("in")), int(e.get("out"))) for e in audio[0].findall("entry")]
        a2 = [(int(e.get("in")), int(e.get("out"))) for e in audio[1].findall("entry")]
        self.assertEqual(a1[0], (10, 69 + 4))        # s001: nothing before, 4 frames after
        self.assertEqual(a2[0], (5 - 4, 64 + 4))     # s002: 4 before, 4 after
        self.assertEqual(a1[1], (40 - 4, 99))        # s003: 4 before, nothing after

    def test_the_audio_tracks_have_blanks_so_that_entries_start_where_the_film_does(self):
        audio = [p for p in self.root_el.findall("playlist") if props(p).get("shotcut:audio") == "1"]
        # A2 holds s002, which starts 60 frames into the film minus 4 frames of handle.
        first = [c for c in audio[1] if c.tag in ("blank", "entry")][0]
        self.assertEqual((first.tag, first.get("length")), ("blank", "56"))
        # A1: s001 from 0, then s003 from 120 - 4: a blank of 116 - 78 frames.
        kinds = [(c.tag, c.get("length")) for c in audio[0] if c.tag == "blank"]
        self.assertEqual(kinds, [("blank", str(116 - (69 + 4 - 10 + 1)))])

    def test_the_tracks_are_background_video_and_two_sound_tracks(self):
        tractor = self.root_el.find("tractor")
        tracks = [(t.get("producer"), t.get("hide")) for t in tractor.findall("track")]
        self.assertEqual(tracks, [("background", None), ("playlist0", "audio"),
                                  ("playlist1", "video"), ("playlist2", "video")])
        self.assertEqual(props(tractor)["shotcut"], "1")

    def test_the_creative_lut_is_a_filter_on_the_video_track_and_the_base_lut_is_not_in_the_project(self):
        tractor = self.root_el.find("tractor")
        luts = [f for f in tractor.findall("filter") if props(f).get("mlt_service") == "avfilter.lut3d"]
        self.assertEqual(len(luts), 1)
        self.assertEqual(props(luts[0])["av.file"], self.tl.data["look"]["lut"]["path"])
        self.assertEqual(props(luts[0])["av.interp"], "tetrahedral")
        self.assertEqual(luts[0].get("track"), "1")
        text = Path(self.result.path).read_text()
        self.assertNotIn(self.tl.data["look"]["base"]["path"], text)

    def test_no_lut_filter_without_a_creative_lut(self):
        r, root, work = self.make_project(self.make_tl(), self.root / "work-nolut")
        self.assertEqual([f for f in root.find("tractor").findall("filter")
                          if props(f).get("mlt_service") == "avfilter.lut3d"], [])

    def test_the_fades_and_one_crossfade_pair_per_join(self):
        filters = self.root_el.find("tractor").findall("filter")
        services = [props(f)["mlt_service"] for f in filters]
        self.assertEqual(services.count("brightness"), 2)             # fade in, fade out
        # film fades of the sound (2) and a falling and a rising filter at both joins (4)
        self.assertEqual(services.count("volume"), 2 + 4)

    def test_the_volume_of_each_segment_is_in_its_sound_chain(self):
        chains = self.chains(self.root_el)
        by_seg = {}
        for c in self.root_el.findall("chain"):
            p = props(c)
            if "vet:audio_of" in p:
                vol = [f for f in c.findall("filter") if props(f)["mlt_service"] == "volume"]
                by_seg[p["vet:audio_of"]] = float(props(vol[0])["level"])
        self.assertEqual(sorted(by_seg), ["s001", "s002", "s003"])
        for sid, db in by_seg.items():
            self.assertAlmostEqual(db, self.result.gains_db[sid], places=2)

    def test_every_filter_and_transition_of_the_writer_is_marked_as_its_own(self):
        for f in self.root_el.iter("filter"):
            self.assertEqual(props(f).get("vet:own"), "1")
        for t in self.root_el.iter("transition"):
            self.assertEqual(props(t).get("vet:own"), "1")

    def test_the_project_is_written_into_the_work_folder_and_not_into_the_originals(self):
        self.assertTrue(str(self.result.path).startswith(str(self.root / "work-content")))
        self.assertTrue((self.root / "work-content" / "audio-synced").is_dir())
        names = sorted(p.name for p in self.originals.iterdir())
        self.assertFalse([n for n in names if n.endswith(".mlt") or "synced" in n], names)


class SyncedSoundTests(ShotcutFixture):
    def test_the_synced_file_is_as_long_as_the_picture_and_keeps_the_rate_of_the_wav(self):
        tl = self.make_tl(offsets={"B": 0.5})
        r, root, work = self.make_project(tl)
        for sid, rate in (("A", 48000), ("B", 96000)):
            info = media.probe(r.audio_files[sid])["streams"][0]
            self.assertEqual(int(info["sample_rate"]), rate)
            self.assertEqual(info["codec_name"], "pcm_s16le")
            frames = 100
            want = round(frames * 1001 / 30000 * rate)
            self.assertEqual(int(info["duration_ts"]), want)

    def test_a_click_lies_where_the_original_click_plus_the_offset_puts_it(self):
        # Offset 0.5 s: a click 5.0 s into the WAV belongs to picture time 4.5 s. Offset -0.5 s: 5.0 -> 5.5 s.
        for offset, expect in ((0.5, 4.5), (-0.5, 5.5)):
            wav = self.originals / f"click{offset}.wav"
            x = np.zeros((12 * RATE, 2), np.float32)
            x[5 * RATE, :] = 0.9
            media.write_wav(wav, x, RATE)
            tl = self.make_tl(offsets={"A": offset})
            tl.data["sources"]["A"]["audio"] = str(wav)
            tl.data["sources"]["A"]["frames"] = 400
            media.write_ramp_mp4(self.copies / "A400.mp4", 400, W // 2, H // 2)
            tl.data["sources"]["A"]["preview_copy"] = str(self.copies / "A400.mp4")
            tl.data["segments"] = [{"id": "s001", "source": "A", "in": 0, "out": 300}]
            self.clips[str(self.originals / "A.braw")].frames = 400
            try:
                info = export_shotcut.sync_audio(tl, "A", str(self.root / f"synced-{offset}"))
            finally:
                self.clips[str(self.originals / "A.braw")].frames = 100
            a = media.read_audio_f32(info["path"])
            at = int(np.argmax(np.abs(a[:, 0]) > 0.5))
            self.assertAlmostEqual(at / RATE, expect, delta=0.0005, msg=f"offset {offset}")
            self.assertEqual(len(a), round(400 * 1001 / 30000 * RATE))

    def test_a_file_that_is_right_is_kept_and_a_changed_offset_makes_it_again(self):
        tl = self.make_tl()
        d = str(self.root / "synced-keep")
        first = export_shotcut.sync_audio(tl, "A", d)
        self.assertFalse(first["kept"])
        self.assertTrue(export_shotcut.sync_audio(tl, "A", d)["kept"])
        tl.data["sources"]["A"]["audio_offset_hand_s"] = 0.3
        again = export_shotcut.sync_audio(tl, "A", d)
        self.assertFalse(again["kept"])
        self.assertEqual(again["shift_samples"], round(0.3 * 48000))

    def test_the_wav_original_is_not_changed(self):
        before = hashlib.sha256((self.originals / "B.wav").read_bytes()).hexdigest()
        self.make_project(self.make_tl(offsets={"B": 0.25}))
        self.assertEqual(hashlib.sha256((self.originals / "B.wav").read_bytes()).hexdigest(), before)


class RefusalTests(ShotcutFixture):
    def test_a_missing_preview_copy_stops_the_export_and_names_the_clip(self):
        tl = self.make_tl()
        tl.data["sources"]["A"]["preview_copy"] = str(self.copies / "gone.mp4")
        with self.assertRaisesRegex(export_shotcut.ShotcutError, "clip A"):
            self.make_project(tl)

    def test_a_project_inside_the_originals_is_refused(self):
        import safety
        with self.assertRaises(safety.OriginalsProtectedError):
            export_shotcut.write_project(self.make_tl(), str(self.originals / "w"),
                                         str(self.originals / "film.mlt"), protected=[str(self.originals)],
                                         log=lambda s: None)

    def test_an_invalid_timeline_is_refused(self):
        tl = self.make_tl()
        tl.data["segments"][0]["out"] = 5000
        with self.assertRaisesRegex(export_shotcut.ShotcutError, "V9"):
            self.make_project(tl)

    def test_melt_refuses_a_broken_project(self):
        if not HAVE_MELT:
            self.skipTest("melt is not installed")
        r, root, work = self.make_project(self.make_tl())
        text = Path(r.path).read_text().replace("copies", "nowhere")
        broken = work / "broken.mlt"
        broken.write_text(text)
        with self.assertRaises(export_shotcut.ShotcutError):
            export_shotcut.check_with_melt(str(broken), frames=10)


CUES = [
    {"id": "c001", "anchor": "s001", "start_s": 0.5, "end_s": 1.5, "text": "First scene.", "origin": "manual"},
    {"id": "c002", "anchor": "s002", "start_s": 0.5, "end_s": 1.5, "text": "Second scene\nwith bars.", "origin": "manual"},
    {"id": "c003", "anchor": "s003", "start_s": 0.5, "end_s": 1.5, "text": "Third scene.", "origin": "manual"},
]


class SubtitleTrackTests(ShotcutFixture):
    """Step 6: the film's cues as a subtitle track of the project (SPECS 8.6). Shotcut 24 and later keep a subtitle
    track as a `subtitle_feed` filter on the tractor: `feed` is the name of the track, `lang` its three-letter code and
    `text` the whole SRT document. The Subtitle filter (`subtitle`, with the same `feed`) draws it."""

    def tl_with_cues(self, cues=CUES):
        tl = self.make_tl(loud=False)
        tl.data["subtitles"] = {"language": "en", "cues": [dict(c) for c in cues]}
        return tl

    def project(self, tl=None, name=None, **kw):
        name = name or self.id().split(".")[-1]
        work = self.root / f"work-{name}"
        return self.make_project(tl or self.tl_with_cues(), work, **kw)

    def feeds(self, root):
        tractor = root.find("tractor")
        return [f for f in tractor.findall("filter") if props(f).get("mlt_service") == "subtitle_feed"]

    def burns(self, root):
        tractor = root.find("tractor")
        return [f for f in tractor.findall("filter") if props(f).get("mlt_service") == "subtitle"]

    def test_the_default_is_one_english_track_on_the_tractor_with_the_srt_of_the_film(self):
        r, root, work = self.project()
        feeds = self.feeds(root)
        self.assertEqual(len(feeds), 1)
        p = props(feeds[0])
        self.assertEqual((p["feed"], p["lang"]), ("English", "eng"))
        self.assertEqual(p["shotcut:hidden"], "1")
        self.assertEqual(p["vet:own"], "1")
        self.assertIsNone(feeds[0].get("track"))                         # the tractor's own filter, not a track's
        parsed = words.parse_srt(p["text"])
        self.assertEqual([c["text"] for c in parsed], ["First scene.", "Second scene\nwith bars.", "Third scene."])
        # film times: the scenes are 60 frames long
        self.assertAlmostEqual(parsed[0]["start_s"], 0.5, delta=0.001)
        self.assertAlmostEqual(parsed[1]["start_s"], 60 * 1001 / 30000 + 0.5, delta=0.04)
        self.assertAlmostEqual(parsed[2]["end_s"], 120 * 1001 / 30000 + 1.5, delta=0.04)
        self.assertEqual(r.subtitle_tracks, ["English"])

    def test_the_subtitle_filter_that_draws_the_first_track_comes_after_the_feed(self):
        r, root, work = self.project()
        burns = self.burns(root)
        self.assertEqual(len(burns), 1)
        p = props(burns[0])
        self.assertEqual((p["feed"], p["shotcut:filter"], p["vet:own"]), ("English", "subtitles", "1"))
        self.assertEqual((p["halign"], p["valign"]), ("center", "bottom"))
        tractor = root.find("tractor")
        filters = [f for f in tractor.findall("filter") if f.get("track") is None]
        self.assertEqual([props(f)["mlt_service"] for f in filters], ["subtitle_feed", "subtitle"])

    def test_a_translation_is_a_track_of_its_own_and_only_the_first_is_drawn(self):
        from tests.test_export_lang import JA, put_translations
        name = self.id().split(".")[-1]
        work = self.root / f"work-{name}"
        work.mkdir(exist_ok=True)
        put_translations(work, "ja", JA)
        r, root, work = self.make_project(self.tl_with_cues(), work, subtitle_langs=("en", "ja"))
        feeds = self.feeds(root)
        self.assertEqual([(props(f)["feed"], props(f)["lang"]) for f in feeds], [("English", "eng"), ("Japanese", "jpn")])
        ja = words.parse_srt(props(feeds[1])["text"])
        self.assertEqual([c["text"].replace("\n", "") for c in ja], list(JA.values()))
        self.assertEqual(len(self.burns(root)), 1)
        self.assertEqual(props(self.burns(root)[0])["feed"], "English")

    def test_a_language_without_translations_stops_the_export_and_names_the_command(self):
        with self.assertRaises(export_shotcut.ShotcutError) as cm:
            self.project(subtitle_langs=("en", "th"))
        self.assertIn("subtitles translate", str(cm.exception))
        self.assertFalse((self.root / f"work-{self.id().split('.')[-1]}" / "film.mlt").exists())

    def test_no_track_without_cues_or_when_none_is_asked_for(self):
        r, root, work = self.project(self.make_tl(loud=False), name="nocues")
        self.assertEqual((self.feeds(root), self.burns(root)), ([], []))
        self.assertEqual(r.subtitle_tracks, [])
        r, root, work = self.project(subtitle_langs=(), name="none")
        self.assertEqual((self.feeds(root), self.burns(root)), ([], []))

    def test_the_cues_of_a_scene_that_is_switched_off_are_not_in_the_track(self):
        tl = self.tl_with_cues()
        tl.data["segments"][1]["enabled"] = False
        r, root, work = self.project(tl)
        parsed = words.parse_srt(props(self.feeds(root)[0])["text"])
        self.assertEqual([c["text"] for c in parsed], ["First scene.", "Third scene."])
        # the third scene now starts at frame 60
        self.assertAlmostEqual(parsed[1]["start_s"], 60 * 1001 / 30000 + 0.5, delta=0.04)

    def test_the_subtitle_text_is_xml_escaped_and_comes_back_whole(self):
        tl = self.tl_with_cues([dict(CUES[0], text="Salt & pepper <fresh> \"now\".")])
        r, root, work = self.project(tl)
        parsed = words.parse_srt(props(self.feeds(root)[0])["text"])
        self.assertEqual(parsed[0]["text"], "Salt & pepper <fresh> \"now\".")

    @unittest.skipUnless(HAVE_MELT, "melt (Shotcut) is not installed")
    def test_melt_reads_the_track_and_keeps_it_when_it_writes_the_project_again(self):
        r, root, work = self.project()
        info = export_shotcut.check_with_melt(r.path, frames=30)
        self.assertEqual(info["frames_rendered"], 30)
        text = Path(r.path).read_text().replace(' producer="main_bin"', "")
        probe = work / "again.mlt"
        again = work / "again-out.mlt"
        probe.write_text(text)
        done = subprocess.run([MELT, str(probe), "in=0", "out=0", "-consumer", f"xml:{again}"],
                              capture_output=True, text=True, env=export_shotcut.melt_env())
        self.assertEqual(done.returncode, 0, done.stderr)
        back = ET.parse(again).getroot()
        # melt writes the loaded project as one producer that stands for the tractor, with the tractor's filters on it
        feeds = [f for f in back.iter("filter") if props(f).get("mlt_service") == "subtitle_feed"]
        self.assertEqual(len(feeds), 1)
        self.assertEqual(props(feeds[0])["feed"], "English")
        self.assertEqual(words.parse_srt(props(feeds[0])["text"])[0]["text"], "First scene.")

    @unittest.skipUnless(HAVE_MELT, "melt (Shotcut) is not installed")
    def test_melt_draws_the_cue_on_its_frames_and_on_no_other(self):
        name = self.id().split(".")[-1]
        with_text, root, work = self.project(name=name + "-with")
        plain, root2, work2 = self.project(subtitle_langs=(), name=name + "-without")
        frames = {}
        for label, res in (("with", with_text), ("without", plain)):
            text = Path(res.path).read_text().replace(' producer="main_bin"', "")
            mlt = self.root / f"melt-{name}-{label}.mlt"
            mlt.write_text(text)
            out = self.root / f"melt-{name}-{label}.mp4"
            subprocess.run([MELT, str(mlt), "-consumer", f"avformat:{out}", "an=1", "vcodec=libx264", "crf=8",
                            "preset=veryfast", "pix_fmt=yuv420p", "real_time=-1"], capture_output=True,
                           env=export_shotcut.melt_env(), check=True)
            frames[label] = media.read_yuv_frames(out, 160, 90)
        diff = lambda f: float(np.abs(frames["with"][f][0].astype(int) - frames["without"][f][0].astype(int)).sum())
        self.assertGreater(diff(25), 200)            # 0.83 s: inside the first cue (0.5 s to 1.5 s)
        self.assertLess(diff(5), 5)                  # before it
        self.assertLess(diff(50), 5)                 # after it, before the next


TINT_CUBE = "LUT_3D_SIZE 2\n" + "\n".join(
    f"{r} {g} {0.2 + 0.8 * b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)) + "\n"     # black becomes (0, 0, 51)


@unittest.skipUnless(HAVE_MELT, "melt (Shotcut) is not installed")
class LetterboxLutTests(ShotcutFixture):
    """Step 6, SPECS 8.6 (measured on the first shoot with the slay Film cube, which lifts black to (0, 4, 14)): the
    creative LUT is a filter on the video track, and MLT adds the black bars of a wide preview copy before it. Does the
    LUT tint the bars? One frame of the project is rendered by melt (no window) and the pixels of the bars are read. The
    cube here lifts black to (0, 0, 51), so a tinted bar would be clear in the numbers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        (cls.luts / "tint.cube").write_text(TINT_CUBE)

    def frame(self, tl, film_frame, name):
        work = self.root / f"work-{name}"
        r, root, work = self.make_project(tl, work)
        text = Path(r.path).read_text().replace(' producer="main_bin"', "").replace("avformat-novalidate", "avformat")
        mlt = work / "frame.mlt"
        mlt.write_text(text)
        png = work / "frame.png"
        if png.exists():
            png.unlink()
        done = subprocess.run([MELT, str(mlt), f"in={film_frame}", f"out={film_frame}", "-consumer", f"avformat:{png}",
                               "vcodec=png", "pix_fmt=rgb24", "real_time=-1"], capture_output=True, text=True,
                              env=export_shotcut.melt_env())
        self.assertEqual(done.returncode, 0, done.stderr[-400:])
        from PIL import Image
        return np.asarray(Image.open(png).convert("RGB")).astype(int)

    def test_the_bars_of_a_wide_clip_stay_black_when_the_creative_lut_lifts_black(self):
        tl = self.make_tl(lut=self.luts / "tint.cube", loud=False)
        a = self.frame(tl, 90, "tint-wide")                  # film frame 90 is in the scene of the wide clip B
        self.assertEqual(a.shape, (90, 160, 3))
        bar = np.concatenate([a[:10].reshape(-1, 3), a[-10:].reshape(-1, 3)])
        self.assertEqual(bar.max(), 0, "the LUT tinted the bars of the letterbox")
        picture = a[12:78].reshape(-1, 3)
        self.assertGreaterEqual(picture[:, 2].min(), 50)     # the picture has the lifted blue: the LUT acts

    def test_the_same_cube_on_a_scene_without_bars_tints_everything(self):
        tl = self.make_tl(lut=self.luts / "tint.cube", loud=False)
        a = self.frame(tl, 30, "tint-full")                  # film frame 30 is in a scene of the full clip A
        self.assertGreaterEqual(a[:, :, 2].min(), 50)

    def test_without_a_creative_lut_the_bars_are_black_too(self):
        a = self.frame(self.make_tl(loud=False), 90, "tint-none")
        self.assertEqual(np.concatenate([a[:10].reshape(-1, 3), a[-10:].reshape(-1, 3)]).max(), 0)


@unittest.skipUnless(HAVE_MELT, "melt (Shotcut) is not installed")
class MeltRenderTests(ShotcutFixture):
    """The project is rendered by melt and compared with the tool's own film."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Noise sound, so that a shift of one sample or a wrong level is seen.
        cls.noise = {}
        for name, seed in (("A", 1), ("B", 2)):
            x = (np.random.default_rng(seed).standard_normal((12 * RATE, 2)) * 0.1).astype(np.float32)
            media.write_wav(cls.originals / f"{name}n.wav", x, RATE)
            cls.sources[name]["audio"] = str(cls.originals / f"{name}n.wav")
        cls.tl = cls.make_tl(loud=False, fade_in=0.3, fade_out=0.3, join=0.25)
        cls.result, cls.root_el, cls.work_dir = cls.make_project(cls, cls.tl, cls.root / "work-melt")
        text = Path(cls.result.path).read_text().replace(' producer="main_bin"', "")
        cls.check = cls.work_dir / "check.mlt"
        cls.check.write_text(text)
        cls.out_wav = cls.work_dir / "out.wav"
        cls.out_mp4 = cls.work_dir / "out.mp4"
        env = export_shotcut.melt_env()
        subprocess.run([MELT, str(cls.check), "-consumer", f"avformat:{cls.out_wav}", "vn=1", "acodec=pcm_f32le",
                        "ar=48000", "ac=2", "real_time=-1"], capture_output=True, env=env, check=True)
        subprocess.run([MELT, str(cls.check), "-consumer", f"avformat:{cls.out_mp4}", "an=1", "vcodec=libx264",
                        "crf=8", "preset=veryfast", "pix_fmt=yuv420p", "real_time=-1"],
                       capture_output=True, env=env, check=True)

    def test_the_sound_is_the_tools_own_to_the_sample_in_time_and_close_in_level(self):
        plan = render.make_plan(self.tl, preview_width=None)
        mine = render.build_mix(self.tl, plan, lambda p: render.load_wav(p, RATE)).samples
        theirs = render.load_wav(str(self.out_wav), RATE)
        self.assertEqual(len(theirs), len(mine))
        for s in plan.segments:
            a, b = s.film_start_sample, s.film_start_sample + s.sample_length
            x, y = theirs[a + 3000:b - 3000, 0], mine[a + 3000:b - 3000, 0]
            corr = {lag: float(np.dot(x, np.roll(y, lag)) / (np.linalg.norm(x) * np.linalg.norm(y)))
                    for lag in range(-6, 7)}
            lag = max(corr, key=corr.get)
            # MLT places sound on frames (1601.6 samples at 29.97 fps), the tool on samples: the
            # two differ by at most one sample.
            self.assertLessEqual(abs(lag), 1, f"{s.id}: the sound is {lag} samples off ({corr})")
            self.assertGreater(corr[lag], 0.93, s.id)

    def test_the_picture_has_the_frames_of_the_segments_the_fades_and_the_bars(self):
        w, h = 160, 90
        frames = media.read_yuv_frames(self.out_mp4, w, h)
        self.assertEqual(len(frames), 180)
        expect = [("A", i) for i in range(10, 70)] + [("B", i) for i in range(5, 65)] + \
                 [("A", i) for i in range(40, 100)]
        for f in (12, 30, 59, 61, 90, 119, 121, 150):
            src, i = expect[f]
            y = frames[f][0]
            rows = y if src == "A" else y[11:79]
            self.assertAlmostEqual(float(rows.mean()), media.limited_luma(media.frame_value(i)), delta=5,
                                   msg=f"film frame {f}: {src} {i}")
        self.assertAlmostEqual(float(frames[0][0].mean()), 16.0, delta=1.5)           # fade in
        self.assertAlmostEqual(float(frames[-1][0].mean()), 16.0, delta=1.5)          # fade out
        y = frames[90][0]                                                            # a frame of B
        self.assertAlmostEqual(float(y[:10].mean()), 16.0, delta=1.5)               # the bars
        self.assertAlmostEqual(float(y[-10:].mean()), 16.0, delta=1.5)


if __name__ == "__main__":
    unittest.main()
