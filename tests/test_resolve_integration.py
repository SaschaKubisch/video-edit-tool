import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from project import Project, SceneMatch
from resolve_integration import build_timeline_plan, apply_plan


def scene(index=0, vd=10.0, ad=10.0, offset=0.0, ts=0.0, te=0.0, **kw):
    return SceneMatch(index=index, video_path=kw.get("vp", f"/v/{index}.MOV"),
                      audio_path=kw.get("ap", f"/a/{index}.WAV"),
                      video_duration=vd, audio_duration=ad, offset=offset,
                      confidence=1.0, trim_start=ts, trim_end=te)


class PlanTests(unittest.TestCase):
    def test_back_to_back_no_trims(self):
        plan = build_timeline_plan([scene(0, 10, 10), scene(1, 5, 5)], 24)
        self.assertEqual(plan[0]["video"], {"startFrame": 0, "endFrame": 240,
                         "recordFrame": 0, "trackIndex": 1, "mediaType": 1})
        self.assertEqual(plan[0]["audio"], {"startFrame": 0, "endFrame": 240,
                         "recordFrame": 0, "trackIndex": 2, "mediaType": 2})
        self.assertEqual(plan[1]["video"]["recordFrame"], 240)
        self.assertEqual(plan[1]["video"]["endFrame"], 120)
        self.assertEqual(plan[1]["audio"]["recordFrame"], 240)

    def test_trims(self):
        plan = build_timeline_plan([scene(0, 10, 10, ts=1.0, te=2.0),
                                    scene(1, 5, 5)], 24)
        v, a = plan[0]["video"], plan[0]["audio"]
        self.assertEqual((v["startFrame"], v["endFrame"]), (24, 192))
        self.assertEqual((a["startFrame"], a["endFrame"]), (24, 192))
        self.assertEqual(plan[1]["video"]["recordFrame"], 168)

    def test_positive_offset_advances_audio(self):
        plan = build_timeline_plan([scene(0, 10, 12, offset=1.0)], 24)
        a = plan[0]["audio"]
        self.assertEqual((a["startFrame"], a["endFrame"], a["recordFrame"]),
                         (24, 264, 0))

    def test_negative_offset_delays_audio(self):
        plan = build_timeline_plan([scene(0, 10, 10, offset=-0.5)], 24)
        a = plan[0]["audio"]
        self.assertEqual(a["recordFrame"], 12)
        self.assertEqual(a["startFrame"], 0)
        self.assertEqual(a["endFrame"], 228)
        for clip in (plan[0]["video"], a):
            self.assertGreaterEqual(clip["startFrame"], 0)
            self.assertGreaterEqual(clip["recordFrame"], 0)

    def test_audio_shorter_is_clamped(self):
        plan = build_timeline_plan([scene(0, 10, 8)], 24)
        a = plan[0]["audio"]
        self.assertEqual(a["endFrame"], 192)
        self.assertLessEqual(a["endFrame"], 8 * 24)
        self.assertEqual(plan[0]["video"]["endFrame"], 240)


class ProjectTests(unittest.TestCase):
    def make(self):
        p = Project(video_dir="/v", audio_dir="/a",
                    matches=[scene(0), scene(1)], selection=[1, 0],
                    selection_trims={"1": {"start": 2.0, "end": 1.0}})
        return p

    def test_roundtrip_and_no_mutation(self):
        p = self.make()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "project.json")
            p.save(path)
            self.assertEqual(os.listdir(d), ["project.json"])
            q = Project.load(path)
        self.assertEqual(q.selection_trims, {"1": {"start": 2.0, "end": 1.0}})
        first = q.get_ordered_scenes()
        self.assertEqual((first[0].index, first[0].trim_start, first[0].trim_end),
                         (1, 2.0, 1.0))
        q.selection_trims = {1: {"start": 5.0}}
        second = q.get_ordered_scenes()
        self.assertEqual(second[0].trim_start, 5.0)
        self.assertEqual(q.get_match(1).trim_start, 0.0)

    def test_unknown_keys_ignored(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            m = {"index": 0, "video_path": "v", "audio_path": "a",
                 "video_duration": 1, "audio_duration": 1, "offset": 0,
                 "confidence": 1, "future_field": 3}
            with open(path, "w") as f:
                json.dump({"matches": [m]}, f)
            self.assertEqual(Project.load(path).matches[0].index, 0)


class FakeItem:
    def __init__(self, path):
        self.path = path

    def GetClipProperty(self, key):
        return self.path if key == "File Path" else ""


class FakeTimeline:
    def __init__(self, name):
        self.name = name
        self.audio_tracks = 1

    def GetName(self):
        return self.name

    def GetTrackCount(self, kind):
        return self.audio_tracks

    def AddTrack(self, kind, sub):
        self.audio_tracks += 1
        return True

    def GetSetting(self, key):
        return "24"

    def GetStartFrame(self):
        return 86400


class FakePool:
    def __init__(self):
        self.appended = None
        self.timelines = []

    def ImportMedia(self, paths):
        return [FakeItem(p) for p in paths]

    def GetRootFolder(self):
        return self

    def GetClipList(self):
        return []

    def CreateEmptyTimeline(self, name):
        tl = FakeTimeline(name)
        self.timelines.append(tl)
        return tl

    def AppendToTimeline(self, infos):
        self.appended = infos
        return [object()] * len(infos)


class FakeProject:
    def __init__(self, existing):
        self.pool = FakePool()
        self.pool.timelines = [FakeTimeline(n) for n in existing]

    def GetMediaPool(self):
        return self.pool

    def GetTimelineCount(self):
        return len(self.pool.timelines)

    def GetTimelineByIndex(self, i):
        return self.pool.timelines[i - 1]

    def SetCurrentTimeline(self, tl):
        pass

    def GetSetting(self, key):
        return "24"

    def SetSetting(self, key, val):
        return True


class FakeResolve:
    def __init__(self, existing=()):
        self.proj = FakeProject(existing)

    def GetProjectManager(self):
        return self

    def LoadProject(self, name):
        return self.proj


class ApplyPlanTests(unittest.TestCase):
    def test_append_and_suffix(self):
        scenes = [scene(0, 10, 10, offset=1.0, vp="/v/A.MOV", ap="/a/A.WAV")]
        resolve = FakeResolve(existing=["Main Timeline", "Main Timeline 2"])
        res = apply_plan(resolve, "P", "Main Timeline", scenes,
                         media_paths=[("/v/A.MOV", "/a/A.WAV")])
        self.assertEqual(res["timeline_name"], "Main Timeline 3")
        infos = resolve.proj.pool.appended
        self.assertEqual(len(infos), 2)
        v, a = infos
        self.assertEqual(v["mediaPoolItem"].path, "/v/A.MOV")
        self.assertEqual((v["startFrame"], v["endFrame"], v["recordFrame"],
                          v["trackIndex"], v["mediaType"]),
                         (0, 240, 86400, 1, 1))
        self.assertEqual((a["startFrame"], a["endFrame"], a["recordFrame"],
                          a["trackIndex"], a["mediaType"]),
                         (24, 240, 86400, 2, 2))
        self.assertEqual(resolve.proj.pool.timelines[-1].audio_tracks, 2)


if __name__ == "__main__":
    unittest.main()
