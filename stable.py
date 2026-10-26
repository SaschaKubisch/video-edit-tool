"""
Finding the steady scenes of a clip (SPECS 7.4, decision D7, "every steady scene").

The owner films static. A steady scene is a stretch in which the camera does not
move. Everything between two steady scenes is movement and is dropped. A scene
shorter than STABLE_MIN_RUN_SEC is dropped too.

Method. The preview copy is read at about 5 frames per second, grey, 480 pixels
wide. Each sampled frame becomes the gradient magnitude of the picture, cut into
a grid of blocks (4 x 3). For two sampled frames, every block is compared with the
same block of the other frame by phase correlation (OpenCV), which gives the shift
of that block and the height of the correlation peak. Then one camera motion is
fitted to the blocks: a similarity transform (shift, zoom, roll). The fit takes
the largest group of blocks that agree and ignores the others, so a person who
walks through the picture, and whose blocks move on their own, is not taken for
camera movement. The motion of a step is the largest displacement of a frame
corner under the fitted transform, in pixels of the 480 pixel wide picture.

A step between two samples is movement when that displacement is at least
STABLE_MOVE_PX. The shift against the first sample of the run is measured too
(STABLE_DRIFT_PX), to catch a slow creep. When no block can be measured and the
picture changed a lot, the step is a cut and counts as movement.

A scene ends where the movement begins. A short shake that ends where it began
(shorter than STABLE_MERGE_GAP_SEC, back within STABLE_DRIFT_PX of the start of the
run) does not split the scene; a bump that moves the framing does. Each scene loses
STABLE_GUARD_SEC at both ends.
"""

import math
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np

import config
import fsutil
import safety
import tools


# ── Settings ────────────────────────────────────────────────────

@dataclass
class StableSettings:
    sample_fps: float = config.STABLE_SAMPLE_FPS
    width_px: int = config.STABLE_WIDTH_PX
    grid: tuple = config.STABLE_GRID
    move_px: float = config.STABLE_MOVE_PX
    drift_px: float = config.STABLE_DRIFT_PX
    min_peak: float = config.STABLE_MIN_PEAK
    min_inliers: int = config.STABLE_MIN_INLIERS
    inlier_px: float = config.STABLE_INLIER_PX
    cut_diff: float = config.STABLE_CUT_DIFF
    min_run_sec: float = config.STABLE_MIN_RUN_SEC
    merge_gap_sec: float = config.STABLE_MERGE_GAP_SEC
    guard_sec: float = config.STABLE_GUARD_SEC

    def as_dict(self) -> dict:
        return {"sample_fps": self.sample_fps, "width_px": self.width_px,
                "grid": list(self.grid), "move_px": self.move_px, "drift_px": self.drift_px,
                "min_peak": self.min_peak, "min_inliers": self.min_inliers,
                "inlier_px": self.inlier_px, "cut_diff": self.cut_diff,
                "min_run_sec": self.min_run_sec, "merge_gap_sec": self.merge_gap_sec,
                "guard_sec": self.guard_sec}


def step_frames(fps_num: int, fps_den: int, settings: StableSettings) -> int:
    """Frames between two samples: the frame rate over the sample rate, at least 1."""
    return max(1, round(fps_num / fps_den / settings.sample_fps))


# ── One sampled frame, prepared for comparison ──────────────────

class Prepared:
    """The blocks of one frame (gradient magnitude, normalised) and a tiny copy of
    the picture for the cut test."""
    __slots__ = ("blocks", "tiny")

    def __init__(self, blocks, tiny):
        self.blocks = blocks
        self.tiny = tiny


class Geometry:
    """Block layout for a frame size: block size, block centres relative to the
    frame centre, and the Hann window for phase correlation."""

    def __init__(self, width: int, height: int, grid):
        cols, rows = grid
        self.width, self.height = width, height
        self.cols, self.rows = cols, rows
        self.bw, self.bh = width // cols, height // rows
        self.window = cv2.createHanningWindow((self.bw, self.bh), cv2.CV_32F)
        self.centres = np.array(
            [[(c + 0.5) * self.bw - width / 2.0, (r + 0.5) * self.bh - height / 2.0]
             for r in range(rows) for c in range(cols)], dtype=np.float64)
        self.corners = np.array([[sx * width / 2.0, sy * height / 2.0]
                                 for sx in (-1, 1) for sy in (-1, 1)], dtype=np.float64)


def prepare(gray: np.ndarray, geo: Geometry) -> Prepared:
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.0)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    blocks = []
    for r in range(geo.rows):
        for c in range(geo.cols):
            b = mag[r * geo.bh:(r + 1) * geo.bh, c * geo.bw:(c + 1) * geo.bw]
            mean, std = cv2.meanStdDev(b)
            s = float(std[0][0])
            # The Hann window is applied here, once. cv2.phaseCorrelate would multiply its
            # input by the window in place, and a block is compared several times.
            blocks.append(None if s < 1e-4 else
                          (((b - float(mean[0][0])) / s) * geo.window).astype(np.float32))
    tiny = cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)
    return Prepared(blocks, tiny)


# ── Fitting one camera motion to the blocks ─────────────────────

@dataclass
class Fit:
    """The motion between two frames."""
    kind: str                   # "ok" (measured), "cut" (changed, not measurable), "unmeasurable"
    motion_px: float = 0.0      # largest displacement of a frame corner
    valid: int = 0              # blocks with a usable correlation peak
    inliers: int = 0            # blocks that agree with the fitted motion
    params: tuple = ()          # (tx, ty, a, b): d = t + [[a, -b], [b, a]] p


def _apply(params, pts):
    tx, ty, a, b = params
    return np.column_stack([tx + a * pts[:, 0] - b * pts[:, 1],
                            ty + b * pts[:, 0] + a * pts[:, 1]])


def _corner_motion(params, corners) -> float:
    return float(np.max(np.hypot(*_apply(params, corners).T)))


def _least_squares(pts, ds, with_zoom):
    n = len(pts)
    if with_zoom:
        A = np.zeros((2 * n, 4))
        A[0::2] = np.column_stack([np.ones(n), np.zeros(n), pts[:, 0], -pts[:, 1]])
        A[1::2] = np.column_stack([np.zeros(n), np.ones(n), pts[:, 1], pts[:, 0]])
        sol, *_ = np.linalg.lstsq(A, ds.reshape(-1), rcond=None)
        return tuple(float(v) for v in sol)
    t = ds.mean(axis=0)
    return (float(t[0]), float(t[1]), 0.0, 0.0)


def _hypotheses(pts, ds):
    """Candidate camera motions (rows of tx, ty, a, b): the shift of each single
    block, and the similarity transform through each pair of blocks."""
    n = len(pts)
    single = np.column_stack([ds[:, 0], ds[:, 1], np.zeros(n), np.zeros(n)])
    i, j = np.triu_indices(n, 1)
    if len(i) == 0:
        return single
    P = len(i)
    A = np.zeros((P, 4, 4))
    rhs = np.zeros((P, 4))
    for row, k in ((0, i), (2, j)):
        x, y = pts[k, 0], pts[k, 1]
        A[:, row, 0] = 1
        A[:, row, 2] = x
        A[:, row, 3] = -y
        A[:, row + 1, 1] = 1
        A[:, row + 1, 2] = y
        A[:, row + 1, 3] = x
        rhs[:, row] = ds[k, 0]
        rhs[:, row + 1] = ds[k, 1]
    try:
        pair = np.linalg.solve(A, rhs[..., None])[..., 0]
    except np.linalg.LinAlgError:
        pair = np.zeros((0, 4))
    return np.vstack([single, pair])


def fit_motion(prev: Prepared, cur: Prepared, geo: Geometry, st: StableSettings) -> Fit:
    pts, ds = [], []
    for k in range(len(prev.blocks)):
        a, b = prev.blocks[k], cur.blocks[k]
        if a is None or b is None:
            continue
        (dx, dy), peak = cv2.phaseCorrelate(a, b)
        if peak >= st.min_peak:
            pts.append(geo.centres[k])
            ds.append((dx, dy))
    valid = len(pts)
    if valid < st.min_inliers:
        return _unmeasured(prev, cur, st, valid)
    pts, ds = np.array(pts), np.array(ds)

    # Fast path: every block agrees on one shift (the camera is still, or it pans).
    med = np.median(ds, axis=0)
    if np.all(np.hypot(*(ds - med).T) < st.inlier_px):
        inl = np.ones(valid, bool)
    else:
        H = _hypotheses(pts, ds)
        px = H[:, 0:1] + H[:, 2:3] * pts[None, :, 0] - H[:, 3:4] * pts[None, :, 1]
        py = H[:, 1:2] + H[:, 3:4] * pts[None, :, 0] + H[:, 2:3] * pts[None, :, 1]
        resid = np.hypot(ds[None, :, 0] - px, ds[None, :, 1] - py)
        inliers = resid < st.inlier_px
        counts = inliers.sum(axis=1)
        cx, cy = geo.corners[:, 0], geo.corners[:, 1]
        qx = H[:, 0:1] + H[:, 2:3] * cx[None, :] - H[:, 3:4] * cy[None, :]
        qy = H[:, 1:2] + H[:, 3:4] * cx[None, :] + H[:, 2:3] * cy[None, :]
        motion = np.hypot(qx, qy).max(axis=1)
        best = int(np.lexsort((motion, -counts))[0])      # most blocks, then the least motion
        inl = inliers[best]
    n_in = int(inl.sum())
    if n_in < st.min_inliers:
        return _unmeasured(prev, cur, st, valid)
    params = _least_squares(pts[inl], ds[inl], with_zoom=n_in >= 3)
    return Fit("ok", _corner_motion(params, geo.corners), valid, n_in, params)


def _unmeasured(prev, cur, st, valid) -> Fit:
    change = float(np.mean(np.abs(prev.tiny - cur.tiny))) / 255.0
    return Fit("cut" if change >= st.cut_diff else "unmeasurable", 0.0, valid, 0)


# ── The result ──────────────────────────────────────────────────

@dataclass
class Run:
    """One steady scene. `in_frame` is the first frame used, `out_frame` the first not used."""
    in_frame: int
    out_frame: int
    score: float
    low_confidence: bool = False

    def as_dict(self, fps_num=None, fps_den=None) -> dict:
        d = {"in": self.in_frame, "out": self.out_frame, "score": round(self.score, 3),
             "low_confidence": self.low_confidence}
        if fps_num:
            d["seconds"] = round((self.out_frame - self.in_frame) * fps_den / fps_num, 3)
        return d


@dataclass
class SceneResult:
    fps_num: int
    fps_den: int
    total_frames: int
    settings: StableSettings
    runs: list = field(default_factory=list)        # Run, the steady scenes of at least min_run_sec
    dropped: list = field(default_factory=list)     # dicts: steady stretches that were too short
    moving: list = field(default_factory=list)      # (first frame, last frame) of movement between runs
    sample_frames: list = field(default_factory=list)
    step_px: list = field(default_factory=list)     # motion into each sample (first is 0)
    drift_px: list = field(default_factory=list)    # motion against the run's first sample
    step_kind: list = field(default_factory=list)

    def seconds(self, frames: int) -> float:
        return frames * self.fps_den / self.fps_num

    @property
    def kept_seconds(self) -> float:
        return sum(self.seconds(r.out_frame - r.in_frame) for r in self.runs)

    @property
    def moving_seconds(self) -> float:
        return sum(self.seconds(b - a) for a, b in self.moving)

    def as_dict(self) -> dict:
        total = self.seconds(self.total_frames)
        return {
            "fps": {"num": self.fps_num, "den": self.fps_den},
            "frames": self.total_frames,
            "seconds": round(total, 3),
            "settings": self.settings.as_dict(),
            "runs": [r.as_dict(self.fps_num, self.fps_den) for r in self.runs],
            "dropped": self.dropped,
            "moving": [{"in": a, "out": b} for a, b in self.moving],
            "kept_seconds": round(self.kept_seconds, 3),
            "dropped_seconds": round(max(0.0, total - self.kept_seconds), 3),
            "moving_seconds": round(self.moving_seconds, 3),
            "samples": {"frames": self.sample_frames,
                        "step_px": [round(v, 2) for v in self.step_px],
                        "drift_px": [round(v, 2) for v in self.drift_px],
                        "kind": self.step_kind},
        }


# ── The detector ────────────────────────────────────────────────

DRIFT_EVERY = 3     # the shift against the run's first sample is measured every 3rd sample

def detect(samples, fps_num: int, fps_den: int, total_frames: int,
           settings: StableSettings = None) -> SceneResult:
    """Find the steady scenes in `samples`, an iterable of (frame number, grey
    picture) in time order, all of one size. Returns a SceneResult."""
    st = settings or StableSettings()
    res = SceneResult(fps_num, fps_den, total_frames, st)
    sec = lambda frames: frames * fps_den / fps_num

    geo = None
    prev = anchor = None
    frames_of = []              # frame numbers of the samples
    steps = []                  # Fit of the step into sample i (None for i = 0)
    raw_runs = []               # (first sample, last sample) of each steady stretch
    raw_moving = []             # (last steady sample before, first steady sample after)
    anchor_i = 0
    in_move, move_start = False, 0

    for i, (frame_no, gray) in enumerate(samples):
        if geo is None:
            geo = Geometry(gray.shape[1], gray.shape[0], st.grid)
        cur = prepare(gray, geo)
        frames_of.append(frame_no)
        if prev is None:
            steps.append(None)
            res.step_px.append(0.0)
            res.drift_px.append(0.0)
            res.step_kind.append("start")
            prev = anchor = cur
            continue

        step = fit_motion(prev, cur, geo, st)
        steps.append(step)
        moving = step.kind == "cut" or (step.kind == "ok" and step.motion_px >= st.move_px)
        # The shift against the first sample of the run catches a slow creep, and
        # tells at the end of a movement whether the camera came back.
        drift = None
        if not moving:
            if prev is anchor:
                drift = step
            elif in_move or (i - anchor_i) % DRIFT_EVERY == 0:
                drift = fit_motion(anchor, cur, geo, st)
        res.step_px.append(step.motion_px)
        res.drift_px.append(drift.motion_px if drift is not None and drift.kind == "ok" else 0.0)
        res.step_kind.append("moving" if moving and step.kind == "ok" else
                             "cut" if step.kind == "cut" else step.kind)

        if moving:
            if not in_move:
                in_move, move_start = True, i - 1
        elif in_move:
            # The movement ended: sample i - 1 is the first steady sample after it.
            in_move = False
            stretch = sec(frames_of[i - 1] - frames_of[move_start])
            # back where it began: measured and close, or too flat to tell (not a cut)
            returned = (drift.kind == "ok" and drift.motion_px < st.drift_px) \
                or drift.kind == "unmeasurable"
            if stretch < st.merge_gap_sec and returned and move_start > anchor_i:
                pass            # a short shake that ended where it began: one scene
            else:
                raw_runs.append((anchor_i, move_start))
                raw_moving.append((frames_of[move_start], frames_of[i - 1]))
                anchor_i, anchor = i - 1, prev
        elif drift is not None and drift.kind == "ok" and drift.motion_px >= st.drift_px:
            raw_runs.append((anchor_i, i - 1))      # a slow creep added up
            anchor_i, anchor = i, cur
        prev = cur

    if frames_of:
        if in_move:
            raw_runs.append((anchor_i, move_start))
            raw_moving.append((frames_of[move_start], total_frames))
        else:
            raw_runs.append((anchor_i, len(frames_of) - 1))
    res.sample_frames = list(frames_of)

    guard = int(math.ceil(st.guard_sec * fps_num / fps_den - 1e-9))
    min_frames = st.min_run_sec * fps_num / fps_den
    for a, b in raw_runs:
        raw_len = frames_of[b] - frames_of[a]
        if raw_len <= 0:
            continue
        first = frames_of[a] + guard
        last = min(frames_of[b] + 1, total_frames) - guard
        inside = [s for s in steps[a + 1:b + 1] if s is not None]
        measured = [s.motion_px for s in inside if s.kind == "ok"]
        rms = float(np.sqrt(np.mean(np.square(measured)))) if measured else 0.0
        score = 1.0 / (1.0 + rms / st.move_px)
        low = any(s.kind == "unmeasurable" for s in inside) or not measured
        if last - first >= min_frames - 1e-9:
            res.runs.append(Run(first, last, score, low))
        elif sec(raw_len) >= 0.5:
            res.dropped.append({"in": max(first, frames_of[a]), "out": max(last, first),
                                "seconds": round(max(0.0, sec(last - first)), 3),
                                "reason": f"shorter than {st.min_run_sec:g} s"})
    res.moving = raw_moving
    return res


# ── Reading the preview copy ────────────────────────────────────

def probe_size(path: str) -> tuple:
    import json
    cmd = [tools.ffprobe_path(), "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "json", str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {r.stderr.strip()}")
    s = json.loads(r.stdout)["streams"][0]
    return int(s["width"]), int(s["height"])


def sample_size(size, width_px: int) -> tuple:
    w, h = size
    return width_px, max(2, int(round(h * width_px / w / 2.0)) * 2)


def read_samples(path: str, size, step: int, width_px: int):
    """Yield (frame number, grey picture) for every `step`-th frame of the video,
    scaled to `width_px` wide. A pipe from ffmpeg; the picture is decoded once."""
    ow, oh = sample_size(size, width_px)
    vf = f"select='not(mod(n,{step}))',scale={ow}:{oh}:flags=area,format=gray"
    cmd = [tools.ffmpeg_path(), "-v", "error", "-nostdin", "-i", str(path), "-an", "-sn",
           "-vf", vf, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    err = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
    n = 0
    try:
        size_bytes = ow * oh
        while True:
            buf = proc.stdout.read(size_bytes)
            if len(buf) < size_bytes:
                break
            yield n * step, np.frombuffer(buf, np.uint8).reshape(oh, ow)
            n += 1
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        proc.wait()
        if proc.returncode not in (0, -9, None) and n == 0:
            err.seek(0)
            message = err.read().decode("utf-8", "replace").strip()
            err.close()
            raise RuntimeError(f"ffmpeg could not read {path}: {message}")
        err.close()


def analyse_video(path: str, fps_num: int, fps_den: int, frames: int,
                  settings: StableSettings = None, size=None) -> SceneResult:
    """The steady scenes of one preview copy."""
    st = settings or StableSettings()
    size = size or probe_size(path)
    step = step_frames(fps_num, fps_den, st)
    return detect(read_samples(path, size, step, st.width_px), fps_num, fps_den, frames, st)


# ── Over a project ──────────────────────────────────────────────

def scenes_path(work_dir: str, stem: str) -> str:
    return os.path.join(work_dir, "analysis", stem, "scenes.json")


def load_scenes(work_dir: str, stem: str):
    """The saved scenes.json of a clip as a dict, or None."""
    import json
    p = scenes_path(work_dir, stem)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def analyse_project(project, work_dir: str, limit: int = None, only=None,
                    settings: StableSettings = None, report_name: str = "scenes-report",
                    log=print, reader=None) -> list:
    """Find the steady scenes in the preview copy of every matched clip (or the
    first `limit`, or those named in `only`) and write
    work_dir/analysis/<stem>/scenes.json for each, and work_dir/reports/<report_name>.json
    and .txt for all. Only copies whose status in conversion.json is `done` are read;
    the other clips get a row with the result "no_copy". Returns the report rows.

    `reader(path, fps_num, fps_den, frames, settings, size)` returns a SceneResult;
    the default reads the preview copy with ffmpeg."""
    from convert import ConversionStore
    reader = reader or analyse_video
    st = settings or StableSettings()
    safety.assert_not_inside(work_dir, safety.originals_folders(project))
    store = ConversionStore.load(os.path.join(work_dir, config.CONVERSION_FILE))
    matches = list(project.matches)
    if only:
        wanted = set(only)
        matches = [m for m in matches if Path((m if isinstance(m, dict) else m.__dict__)["video_path"]).stem in wanted]
    if limit:
        matches = matches[:limit]
    rows = []
    for i, m in enumerate(matches, start=1):
        d = m if isinstance(m, dict) else m.__dict__
        stem = Path(d["video_path"]).stem
        entry = store.clips.get(stem)
        if not entry or entry.get("status") != "done" or not os.path.isfile(entry.get("preview_copy", "")):
            rows.append({"clip": stem, "result": "no_copy", "runs": 0, "kept_s": 0.0,
                         "seconds": 0.0, "moving_s": 0.0, "low_confidence": False,
                         "elapsed_s": 0.0, "reasons": ["no finished preview copy"]})
            log(f"[{i}/{len(matches)}] {stem}  no finished preview copy, skipped")
            continue
        fps = entry["fps"]
        copy_size = entry.get("copy_size")
        size = (copy_size["width"], copy_size["height"]) if copy_size else None
        started = time.time()
        res = reader(entry["preview_copy"], fps["num"], fps["den"], entry["frames"], st, size)
        elapsed = time.time() - started
        data = {"clip": stem, "preview_copy": entry["preview_copy"], **res.as_dict(),
                "elapsed_s": round(elapsed, 2)}
        fsutil.write_json_atomic(scenes_path(work_dir, stem), data)
        seconds = res.seconds(res.total_frames)
        low = any(r.low_confidence for r in res.runs)
        row = {"clip": stem, "result": "scenes" if res.runs else "no_scene",
               "runs": len(res.runs), "seconds": round(seconds, 2),
               "kept_s": round(res.kept_seconds, 2), "moving_s": round(res.moving_seconds, 2),
               "dropped_short": len(res.dropped), "low_confidence": low,
               "elapsed_s": round(elapsed, 1),
               "reasons": ([] if res.runs else ["no steady scene of "
                                                f"{st.min_run_sec:g} s or longer"])}
        rows.append(row)
        log(f"[{i}/{len(matches)}] {stem}  {len(res.runs)} scene(s), kept {res.kept_seconds:.1f} of "
            f"{seconds:.1f} s{'  LOW CONFIDENCE' if low else ''}  ({elapsed:.1f} s)")
    reports = os.path.join(work_dir, "reports")
    fsutil.write_json_atomic(os.path.join(reports, f"{report_name}.json"),
                             {"settings": st.as_dict(), "clips": rows})
    fsutil.write_text_atomic(os.path.join(reports, f"{report_name}.txt"), format_report(rows))
    return rows


def format_report(rows: list) -> str:
    head = f"{'clip':22} {'result':9} {'scenes':>6} {'length':>8} {'kept':>8} {'moving':>8} {'short':>5}  note"
    lines = [head, "-" * len(head)]
    for r in rows:
        note = "; ".join(r["reasons"])
        if r.get("low_confidence"):
            note = (note + "; " if note else "") + "low confidence (some steps could not be measured)"
        lines.append(f"{r['clip']:22} {r['result']:9} {r['runs']:>6} {r['seconds']:>7.1f}s "
                     f"{r['kept_s']:>7.1f}s {r['moving_s']:>7.1f}s {r.get('dropped_short', 0):>5}  {note}")
    n = len(rows)
    with_scene = sum(1 for r in rows if r["result"] == "scenes")
    lines.append("")
    lines.append(f"{n} clips: {with_scene} with steady scenes, "
                 f"{sum(1 for r in rows if r['result'] == 'no_scene')} without, "
                 f"{sum(1 for r in rows if r['result'] == 'no_copy')} without a finished preview copy.")
    return "\n".join(lines) + "\n"
