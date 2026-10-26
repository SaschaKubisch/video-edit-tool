"""Check 2.6: finding the steady scenes of a clip (SPECS 7.4, decision D7).

The core (`stable.detect`) works on the sampled frames, so these tests hand it
synthetic frames made with numpy and OpenCV (tests/synth.py): a camera that is
still, pans, pans between two still parts, shakes, zooms, bumps or drifts, and
people who move in a still shot. People moving inside a static shot must not
count as camera movement."""

import unittest

import numpy as np

import config
import stable
from tests import synth

STEP = 6
FPS_NUM, FPS_DEN = 30000, 1001


def samples_of(camera, seconds, pose_of_t, cam2=None, switch_t=None):
    out = []
    for i, t in synth.sample_times(seconds, STEP):
        cam = cam2 if (cam2 is not None and t >= switch_t) else camera
        out.append((i, cam.frame(t, pose_of_t(t))))
    return out


def detect(samples, seconds, **kw):
    return stable.detect(samples, FPS_NUM, FPS_DEN, int(seconds * FPS_NUM / FPS_DEN), **kw)


def secs(frame):
    return frame * FPS_DEN / FPS_NUM


class SteadyRunTests(unittest.TestCase):
    def test_a_still_camera_gives_one_run_that_loses_only_the_guard(self):
        cam = synth.Camera(seed=1)
        r = detect(samples_of(cam, 8, synth.still()), 8)
        self.assertEqual(len(r.runs), 1)
        run = r.runs[0]
        self.assertAlmostEqual(secs(run.in_frame), config.STABLE_GUARD_SEC, delta=0.1)
        self.assertGreater(secs(run.out_frame), 8 - config.STABLE_GUARD_SEC - 0.3)
        self.assertLessEqual(run.out_frame, int(8 * FPS_NUM / FPS_DEN))
        self.assertFalse(run.low_confidence)
        self.assertEqual(r.moving, [])

    def test_a_pan_gives_no_run(self):
        cam = synth.Camera(seed=2)
        r = detect(samples_of(cam, 8, lambda t: (60 * t, 0, 1.0)), 8)
        self.assertEqual(r.runs, [])
        self.assertTrue(r.moving)

    def test_still_pan_still_gives_two_runs_next_to_the_movement(self):
        cam = synth.Camera(seed=3)

        def pose(t):
            return (0.0 if t < 4 else (80 * (t - 4) if t < 6 else 160.0), 0.0, 1.0)

        r = detect(samples_of(cam, 10, pose), 10)
        self.assertEqual(len(r.runs), 2)
        first, second = r.runs
        self.assertLessEqual(secs(first.out_frame), 4.0 - 0.1)
        self.assertGreaterEqual(secs(first.out_frame), 4.0 - 0.8)
        self.assertGreaterEqual(secs(second.in_frame), 6.0 + 0.1)
        self.assertLessEqual(secs(second.in_frame), 6.0 + 0.8)
        # the moving part lies outside every run
        for a, b in r.moving:
            for run in r.runs:
                self.assertTrue(b <= run.in_frame or a >= run.out_frame)
        self.assertTrue(any(secs(a) < 5 < secs(b) for a, b in r.moving))

    def test_a_shaking_camera_gives_no_run(self):
        cam = synth.Camera(seed=4)
        jit = np.random.default_rng(3).uniform(-3, 3, (200, 2))
        r = detect(samples_of(cam, 8, lambda t: (jit[int(round(t * 5))][0],
                                                 jit[int(round(t * 5))][1], 1.0)), 8)
        self.assertEqual(r.runs, [])

    def test_a_zoom_is_movement(self):
        cam = synth.Camera(seed=5)

        def pose(t):
            return (0.0, 0.0, 1.0 if t < 3 else (1.0 + 0.15 * (t - 3) / 2 if t < 5 else 1.15))

        r = detect(samples_of(cam, 8, pose), 8)
        self.assertEqual(len(r.runs), 2)
        self.assertTrue(any(3.0 <= secs(a) + 0.5 and secs(b) <= 5.5 for a, b in r.moving))

    def test_a_bump_that_moves_the_framing_splits_the_scene(self):
        cam = synth.Camera(seed=6)
        r = detect(samples_of(cam, 8, lambda t: (0.0 if t < 4 else 25.0, 0.0, 1.0)), 8)
        self.assertEqual(len(r.runs), 2)

    def test_a_small_wobble_that_returns_does_not_split_the_scene(self):
        cam = synth.Camera(seed=7)
        r = detect(samples_of(cam, 8, lambda t: (4.0 if 4.0 <= t < 4.2 else 0.0, 0.0, 1.0)), 8)
        self.assertEqual(len(r.runs), 1)

    def test_a_slow_drift_splits_the_scene_when_it_adds_up(self):
        cam = synth.Camera(seed=8)
        # 0.5 px per second is 0.1 px per step: no step is movement, but the
        # position moves 3 px away from the start of the run after 6 s.
        r = detect(samples_of(cam, 14, lambda t: (0.5 * t, 0.0, 1.0)), 14)
        self.assertGreaterEqual(len(r.runs), 2)
        for run in r.runs:
            self.assertLessEqual(secs(run.out_frame - run.in_frame), 6.5)

    def test_a_change_of_picture_is_a_cut_and_splits_the_scene(self):
        cam1, cam2 = synth.Camera(seed=9), synth.Camera(seed=10)
        r = detect(samples_of(cam1, 8, synth.still(), cam2=cam2, switch_t=4.0), 8)
        self.assertEqual(len(r.runs), 2)

    def test_a_static_shot_with_flicker_and_noise_is_one_run(self):
        cam = synth.Camera(seed=11, noise=4.0, flicker=0.2)
        r = detect(samples_of(cam, 8, synth.still()), 8)
        self.assertEqual(len(r.runs), 1)

    def test_a_flat_picture_cannot_be_measured_and_is_marked(self):
        flat = [(i, np.full((270, 480), 100, np.uint8)) for i in range(0, 240, STEP)]
        r = detect(flat, 8)
        self.assertEqual(len(r.runs), 1)
        self.assertTrue(r.runs[0].low_confidence)


class PeopleInAStaticShotTests(unittest.TestCase):
    """The owner films static; people who walk through the picture are not camera movement."""

    def _check(self, person_w, person_h, speed):
        cam = synth.Camera(seed=12)
        cam.add_person(person_w, person_h, lambda t: -person_w / 2 + speed * t, lambda t: 20)
        r = detect(samples_of(cam, 9, synth.still()), 9)
        self.assertEqual(r.moving, [], "the person was taken for camera movement")
        self.assertEqual(len(r.runs), 1)
        self.assertGreater(secs(r.runs[0].out_frame - r.runs[0].in_frame), 9 - 0.8)

    def test_a_person_a_quarter_of_the_picture_wide(self):
        self._check(120, 230, 45)

    def test_a_person_a_third_of_the_picture(self):
        self._check(160, 250, 40)

    def test_a_person_who_stands_and_moves_in_place(self):
        cam = synth.Camera(seed=13)
        cam.add_person(130, 220, lambda t: 170 + 25 * np.sin(2 * np.pi * 0.4 * t),
                       lambda t: 25 + 10 * np.sin(2 * np.pi * 0.6 * t))
        r = detect(samples_of(cam, 8, synth.still()), 8)
        self.assertEqual(len(r.runs), 1)
        self.assertEqual(r.moving, [])

    def test_two_people(self):
        cam = synth.Camera(seed=14)
        cam.add_person(100, 220, lambda t: -50 + 50 * t, lambda t: 30, seed=21)
        cam.add_person(110, 200, lambda t: 500 - 40 * t, lambda t: 50, seed=22)
        r = detect(samples_of(cam, 9, synth.still()), 9)
        self.assertEqual(len(r.runs), 1)
        self.assertEqual(r.moving, [])

    def test_a_camera_pan_is_still_found_with_people_in_the_picture(self):
        cam = synth.Camera(seed=15)
        cam.add_person(120, 230, lambda t: 200, lambda t: 20)

        def pose(t):
            return (0.0 if t < 4 else (80 * (t - 4) if t < 6 else 160.0), 0.0, 1.0)

        r = detect(samples_of(cam, 10, pose), 10)
        self.assertEqual(len(r.runs), 2)
        self.assertTrue(any(secs(a) < 5 < secs(b) for a, b in r.moving))


class RunRulesTests(unittest.TestCase):
    def test_a_scene_shorter_than_the_minimum_is_dropped_and_listed(self):
        cam = synth.Camera(seed=16)
        # still 0-5 s, pan 5-6 s, still 6-8 s: the second part is 2 s minus the guards
        def pose(t):
            return (0.0 if t < 5 else (100 * (t - 5) if t < 6 else 100.0), 0.0, 1.0)

        r = detect(samples_of(cam, 8, pose), 8)
        self.assertEqual(len(r.runs), 1)
        self.assertEqual(len(r.dropped), 1)
        self.assertIn("shorter", r.dropped[0]["reason"])
        self.assertLess(r.dropped[0]["seconds"], config.STABLE_MIN_RUN_SEC)

    def test_the_guard_and_the_minimum_are_settings(self):
        cam = synth.Camera(seed=17)
        samples = samples_of(cam, 8, synth.still())
        s = stable.StableSettings(guard_sec=1.0, min_run_sec=10.0)
        r = detect(samples, 8, settings=s)
        self.assertEqual(r.runs, [])
        self.assertEqual(len(r.dropped), 1)
        s = stable.StableSettings(guard_sec=1.0, min_run_sec=2.0)
        r = detect(samples, 8, settings=s)
        self.assertAlmostEqual(secs(r.runs[0].in_frame), 1.0, delta=0.1)

    def test_run_scores_rank_a_steadier_run_higher(self):
        cam = synth.Camera(seed=18)
        # run 1 is perfectly still, run 2 creeps at 0.9 px per second (below the limit per step)
        def pose(t):
            if t < 4:
                return (0.0, 0.0, 1.0)
            if t < 5.4:
                return (60 * (t - 4), 0.0, 1.0)
            return (84 + 0.9 * (t - 5.4), 0.0, 1.0)

        r = detect(samples_of(cam, 10, pose), 10)
        self.assertEqual(len(r.runs), 2)
        self.assertGreater(r.runs[0].score, r.runs[1].score)
        for run in r.runs:
            self.assertTrue(0.0 < run.score <= 1.0)

    def test_the_result_names_what_it_decided(self):
        cam = synth.Camera(seed=19)
        r = detect(samples_of(cam, 6, synth.still()), 6)
        d = r.as_dict()
        for key in ("runs", "dropped", "moving", "settings", "kept_seconds", "samples"):
            self.assertIn(key, d)
        self.assertEqual(d["runs"][0]["in"], r.runs[0].in_frame)
        self.assertEqual(d["runs"][0]["out"], r.runs[0].out_frame)


if __name__ == "__main__":
    unittest.main()
