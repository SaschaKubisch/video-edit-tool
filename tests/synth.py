"""Synthetic video for the tests of the steady-scene finder, the assembly and the
film preview. Nothing is stored in the repository: the clips are made inside the
tests with numpy and OpenCV, and written to H.264 MP4 with ffmpeg.

A camera is a window on a large textured canvas. The window moves (a pan), zooms,
shakes or stays still. A "person" is a separate textured patch that moves over the
static canvas, the way people move in a static shot.
"""

import subprocess
from fractions import Fraction

import cv2
import numpy as np

import tools

FPS = Fraction(30000, 1001)


def texture(w, h, seed):
    """Smooth random texture with detail at three scales, values 20..235."""
    rng = np.random.default_rng(seed)
    t = np.zeros((h, w), np.float32)
    for sigma in (1.5, 5.0, 14.0):
        n = cv2.GaussianBlur(rng.standard_normal((h, w)).astype(np.float32), (0, 0), sigma)
        t += n / max(1e-6, float(n.std()))
    lo, hi = np.percentile(t, [1, 99])
    return np.clip((t - lo) / (hi - lo), 0, 1) * 215 + 20


class Camera:
    """A window of size (w, h) on a canvas. `pose(t)` gives (x, y, zoom): the
    window's top-left corner on the canvas in pixels and its scale (1 = none)."""

    def __init__(self, w=480, h=270, seed=1, margin=420, noise=0.0, flicker=0.0):
        self.w, self.h = w, h
        self.margin = margin
        self.canvas = texture(w + 2 * margin, h + 2 * margin, seed)
        self.noise = noise
        self.flicker = flicker
        self.people = []        # (patch, x(t), y(t), mask)
        self._rng = np.random.default_rng(seed + 100)

    def add_person(self, w, h, x_of_t, y_of_t, seed=9):
        patch = texture(w, h, seed) * 0.5 + 110
        mask = np.zeros((h, w), np.float32)
        cv2.ellipse(mask, (w // 2, h // 2), (w // 2 - 1, h // 2 - 1), 0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), 2.0)
        self.people.append((patch, x_of_t, y_of_t, mask))

    def frame(self, t, pose):
        x, y, zoom = pose
        # window top-left at (x + margin, y + margin), scaled about the window centre
        cx, cy = self.w / 2, self.h / 2
        # output pixel (u, v) takes canvas pixel (x0 + cx + (u - cx)/zoom, y0 + cy + (v - cy)/zoom)
        x0, y0 = x + self.margin, y + self.margin
        m = np.float32([[1 / zoom, 0, x0 + cx - cx / zoom],
                        [0, 1 / zoom, y0 + cy - cy / zoom]])
        out = cv2.warpAffine(self.canvas, m, (self.w, self.h),
                             flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                             borderMode=cv2.BORDER_REFLECT)
        for patch, px_of_t, py_of_t, mask in self.people:
            px, py = int(round(px_of_t(t))), int(round(py_of_t(t)))
            ph, pw = patch.shape
            x1, y1 = max(0, px), max(0, py)
            x2, y2 = min(self.w, px + pw), min(self.h, py + ph)
            if x2 <= x1 or y2 <= y1:
                continue
            sub = (slice(y1 - py, y2 - py), slice(x1 - px, x2 - px))
            a = mask[sub]
            out[y1:y2, x1:x2] = out[y1:y2, x1:x2] * (1 - a) + patch[sub] * a
        if self.flicker:
            out = out * (1 + self.flicker * np.sin(2 * np.pi * 0.7 * t))
        if self.noise:
            out = out + self._rng.standard_normal(out.shape) * self.noise
        return np.clip(out, 0, 255).astype(np.uint8)


def frames_at(camera, times, pose_of_t):
    return [camera.frame(t, pose_of_t(t)) for t in times]


def sample_times(seconds, step_frames=6, fps=FPS):
    """Times of the frames the analysis samples: every step_frames-th frame."""
    n = int(seconds * fps)
    return [(i, float(i / fps)) for i in range(0, n, step_frames)]


def write_mp4(path, camera, seconds, pose_of_t, fps=FPS, size=None):
    """Render the camera to an H.264 MP4 (4:2:0, key frame every 30 frames)."""
    w, h = camera.w, camera.h
    n = int(seconds * fps)
    cmd = [tools.ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
           "-r", f"{fps.numerator}/{fps.denominator}", "-i", "-",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p",
           "-g", "30", "-bf", "0", "-movflags", "+faststart", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n):
            t = float(i / fps)
            p.stdin.write(camera.frame(t, pose_of_t(t)).tobytes())
        p.stdin.close()
    finally:
        p.wait()
    if p.returncode != 0:
        raise RuntimeError("ffmpeg failed to write the synthetic video")
    return n


def still(x=0.0, y=0.0):
    return lambda t: (x, y, 1.0)
