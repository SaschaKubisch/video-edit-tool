"""
Local web server for the Ambient Video Automation app.
Provides API endpoints that the frontend calls, and serves the UI.
"""

import copy
import json
import os
import re
import sys
import threading
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

import config
from project import Project, SceneMatch

# Global state
_project = Project()
_project_lock = threading.Lock()
_status = {"step": "idle", "message": "Ready", "progress": 0}
_status_lock = threading.Lock()

# Only one background job (match, sync, resolve, assemble, preview) may run at a time
_job_running = False
_job_lock = threading.Lock()

# The film (SPECS 12): the timeline file, the work folder and the preview settings can be
# set by the command line or by tests. Without them the timeline file is timeline.json
# next to the project file and the work folder is the project's work_dir.
_timeline_path = None
_work_dir = None
_preview_settings = None
_wav_seconds = None                    # replaces ffprobe where the length of a WAV is read (tests)
_decoder = None                        # the Decoder of the final render (tests); else BrawSourceDecoder
_lut_dir = None                        # the folder of the creative LUTs (tests); else VET_LUT_DIR
_timeline_lock = threading.Lock()      # one edit of the timeline file at a time

PREVIEW_NAME = "preview"


def configure(**kw):
    """Set what the film endpoints use: timeline_path, work_dir, project, preview_settings,
    wav_seconds, decoder, lut_dir. Only the names given are changed; None or "" means "not set"."""
    global _timeline_path, _work_dir, _preview_settings, _project, _wav_seconds, _decoder, _lut_dir
    unknown = set(kw) - {"timeline_path", "work_dir", "project", "preview_settings",
                         "wav_seconds", "decoder", "lut_dir"}
    if unknown:
        raise TypeError(f"unknown settings: {', '.join(sorted(unknown))}")
    if "timeline_path" in kw:
        _timeline_path = kw["timeline_path"] or None
    if "work_dir" in kw:
        _work_dir = kw["work_dir"] or None
    if "preview_settings" in kw:
        _preview_settings = kw["preview_settings"]
    if "wav_seconds" in kw:
        _wav_seconds = kw["wav_seconds"]
    if "decoder" in kw:
        _decoder = kw["decoder"]
    if "lut_dir" in kw:
        _lut_dir = kw["lut_dir"] or None
    if "project" in kw:
        with _project_lock:
            _project = kw["project"]


def reset_state():
    """Free the job slot and set the status to idle (tests)."""
    global _job_running
    with _job_lock:
        _job_running = False
    _set_status("idle", "Ready", 0)


def timeline_file() -> str:
    if _timeline_path:
        return _timeline_path
    return str(Path(config.PROJECT_FILE).resolve().parent / config.TIMELINE_FILE)


def work_dir() -> str:
    if _work_dir:
        return _work_dir
    with _project_lock:
        return _project.work_dir or ""


def _set_status(step: str, message: str, progress: int = 0):
    global _status
    with _status_lock:
        _status = {"step": step, "message": message, "progress": progress}


def _get_status() -> dict:
    with _status_lock:
        return dict(_status)


def _try_start_job() -> bool:
    """Claim the job slot. Returns False if a job is already running."""
    global _job_running
    with _job_lock:
        if _job_running:
            return False
        _job_running = True
        return True


def _end_job():
    global _job_running
    with _job_lock:
        _job_running = False


def _thumb_dir() -> Path:
    """Thumbnails live next to the project file."""
    return Path(config.PROJECT_FILE).resolve().parent / "thumbnails"


def _load_project():
    """Load the saved project from disk, if there is one (resume)."""
    global _project
    if not Path(config.PROJECT_FILE).exists():
        return
    try:
        loaded = Project.load()
        with _project_lock:
            _project = loaded
        print(f"  Resumed project from {config.PROJECT_FILE}")
    except Exception as e:
        print(f"  Could not load {config.PROJECT_FILE}, starting empty: {e}")
        traceback.print_exc()


def _json_response(handler, data, status=200):
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.end_headers()
    handler.wfile.write(json.dumps(data).encode())


def _read_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    body = handler.rfile.read(length).decode()
    return json.loads(body) if body else {}


class AppHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the app."""

    def log_message(self, format, *args):
        # Suppress default logging to keep terminal clean
        pass

    def _check_request(self, is_post: bool) -> bool:
        """Reject requests not addressed to this local server (DNS rebinding / CSRF)."""
        port = self.server.server_address[1]
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        if self.headers.get("Host", "") not in hosts:
            self._refuse("Forbidden host", is_post)
            return False
        if is_post:
            origin = self.headers.get("Origin")
            if origin is not None and origin not in {f"http://{h}" for h in hosts}:
                self._refuse("Forbidden origin", is_post)
                return False
        return True

    def _refuse(self, message: str, is_post: bool):
        """Answer 403. The request body is read first: a server that closes the socket
        with unread data makes the client see a reset instead of the answer."""
        if is_post:
            try:
                left = min(int(self.headers.get("Content-Length", 0)), 1 << 20)
                while left > 0:
                    chunk = self.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    left -= len(chunk)
            except (ValueError, OSError):
                pass
        _json_response(self, {"error": message}, 403)

    def do_GET(self):
        if not self._check_request(False):
            return
        try:
            self._route_get()
        except Exception as e:
            traceback.print_exc()
            _json_response(self, {"error": str(e)}, 500)

    def _route_get(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_ui()
        elif path == "/api/status":
            _json_response(self, _get_status())
        elif path == "/api/project":
            with _project_lock:
                _json_response(self, self._project_to_dict())
        elif path == "/api/browse":
            params = parse_qs(parsed.query)
            self._handle_browse(params)
        elif path.startswith("/api/thumbnail/"):
            self._serve_thumbnail(path)
        elif path == "/api/timeline":
            self._handle_get_timeline()
        elif path.startswith("/api/preview/"):
            self._serve_preview_file(unquote(path[len("/api/preview/"):]))
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._check_request(True):
            return
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/match":
                self._handle_match()
            elif path == "/api/select":
                self._handle_select()
            elif path == "/api/sync":
                self._handle_sync()
            elif path == "/api/resolve":
                self._handle_resolve()
            elif path == "/api/timeline/edit":
                self._handle_timeline_edit()
            elif path == "/api/assemble":
                self._handle_assemble()
            elif path == "/api/preview":
                self._handle_preview()
            elif path == "/api/look":
                self._handle_look()
            elif path == "/api/loudness":
                self._handle_loudness()
            elif path == "/api/render":
                self._handle_render()
            else:
                self.send_error(404)
        except json.JSONDecodeError:
            _json_response(self, {"error": "Invalid JSON body"}, 400)
        except Exception as e:
            traceback.print_exc()
            _json_response(self, {"error": str(e)}, 500)

    # ── Directory browser ──

    def _handle_browse(self, params):
        """List directories for the file browser."""
        path = params.get("path", [os.path.expanduser("~")])[0]
        p = Path(path)

        if not p.exists():
            _json_response(self, {"error": f"Path not found: {path}"}, 404)
            return

        entries = []
        try:
            # Parent directory
            if p.parent != p:
                entries.append({
                    "name": "..",
                    "path": str(p.parent),
                    "is_dir": True,
                })

            for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if item.name.startswith("."):
                    continue
                entries.append({
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else None,
                })
        except PermissionError:
            _json_response(self, {"error": f"Permission denied: {path}"}, 403)
            return

        _json_response(self, {
            "current": str(p),
            "entries": entries,
        })

    # ── Match ──

    def _handle_match(self):
        body = _read_body(self)
        video_dir = body.get("video_dir", "")
        audio_dir = body.get("audio_dir", "")

        if not video_dir or not audio_dir:
            _json_response(self, {"error": "video_dir and audio_dir are required"}, 400)
            return
        for d in (video_dir, audio_dir):
            if not Path(d).is_dir():
                _json_response(self, {"error": f"Folder not found: {d}"}, 400)
                return

        if not _try_start_job():
            _json_response(self, {"error": "Another job is already running"}, 409)
            return

        _set_status("matching", "Matching audio fingerprints...", 10)

        def do_match():
            global _project
            try:
                from matcher import match_files
                from thumbnails import extract_thumbnail

                matches = match_files(video_dir, audio_dir)

                # Build the new project locally so the lock is not held during ffmpeg
                new_project = Project(
                    video_dir=video_dir,
                    audio_dir=audio_dir,
                    matches=matches,
                )

                thumb_dir = _thumb_dir()
                thumb_dir.mkdir(parents=True, exist_ok=True)
                total = len(new_project.matches)
                for i, m in enumerate(new_project.matches):
                    if not isinstance(m, SceneMatch):
                        m = new_project.matches[i] = SceneMatch(**m)
                    _set_status("thumbnails",
                                f"Extracting thumbnails ({i + 1}/{total})...",
                                60 + int(39 * i / max(total, 1)))
                    thumb_path = thumb_dir / f"thumb_{m.index:03d}.{config.THUMBNAIL_FORMAT}"
                    try:
                        extract_thumbnail(m.video_path, str(thumb_path))
                        m.thumbnail_path = str(thumb_path)
                    except Exception as e:  # noqa: BLE001 - e.g. .braw has no decoder
                        print(f"  [{m.index}] Thumbnail failed: {e}")

                with _project_lock:
                    _project = new_project
                    _project.save()

                _set_status("matched", f"Found {len(matches)} matched pairs.", 100)

            except Exception as e:
                traceback.print_exc()
                _set_status("error", f"Matching failed: {e}", 0)
            finally:
                _end_job()

        thread = threading.Thread(target=do_match, daemon=True)
        thread.start()

        _json_response(self, {"status": "started"})

    # ── Select ──

    def _handle_select(self):
        body = _read_body(self)

        selection = body.get("selection", [])
        trims = body.get("selection_trims", {})

        with _project_lock:
            _project.selection = selection
            _project.selection_trims = trims
            _project.save()

        _json_response(self, {"status": "saved", "count": len(selection)})

    # ── Sync ──

    def _handle_sync(self):
        body = _read_body(self)

        if not _try_start_job():
            _json_response(self, {"error": "Another job is already running"}, 409)
            return

        _set_status("syncing", "Copying and renaming audio files...", 10)

        def do_sync():
            try:
                from syncer import sync_all_scenes

                # Work on a copy so the in-memory project stays the single source of truth
                with _project_lock:
                    proj_copy = copy.deepcopy(_project)

                if not proj_copy.selection:
                    _set_status("error", "No scenes selected.", 0)
                    return

                # Default output to video directory
                output_dir = body.get("output_dir") or proj_copy.video_dir
                outputs = sync_all_scenes(proj_copy, output_dir)

                with _project_lock:
                    _project.output_dir = output_dir
                    _project.save()

                _set_status("synced",
                            f"Copied {len(outputs)} audio files to video folder.", 100)

            except Exception as e:
                traceback.print_exc()
                _set_status("error", f"Sync failed: {e}", 0)
            finally:
                _end_job()

        thread = threading.Thread(target=do_sync, daemon=True)
        thread.start()

        _json_response(self, {"status": "started"})

    # ── Resolve ──

    def _handle_resolve(self):
        body = _read_body(self)

        if not _try_start_job():
            _json_response(self, {"error": "Another job is already running"}, 409)
            return

        _set_status("resolve", "Creating DaVinci Resolve project...", 10)

        def do_resolve():
            try:
                from resolve_integration import create_resolve_project

                with _project_lock:
                    proj_copy = copy.deepcopy(_project)

                result = create_resolve_project(
                    project=proj_copy,
                    project_name=(body.get("project_name") or body.get("name")
                                  or "Ambient Video Project"),
                    timeline_name=(body.get("timeline_name") or body.get("timeline")
                                   or "Main Timeline"),
                )

                _set_status("resolve_done",
                            f"Resolve project created with {result['clips_imported']} clips.", 100)

            except Exception as e:
                traceback.print_exc()
                _set_status("error", f"Resolve failed: {e}", 0)
            finally:
                _end_job()

        thread = threading.Thread(target=do_resolve, daemon=True)
        thread.start()

        _json_response(self, {"status": "started"})

    # ── The film: timeline, edits, preview ──

    def _load_timeline(self):
        """(Timeline, hash) of the timeline file, or None after answering 404 or 422."""
        import timeline
        path = timeline_file()
        if not os.path.isfile(path):
            _json_response(self, {"error": f"No timeline file at {path}. Assemble the film "
                                  "first (the Assemble button, or python main.py assemble)."}, 404)
            return None
        try:
            return timeline.Timeline.load(path), timeline.file_hash(path)
        except (timeline.TimelineError, OSError) as e:
            _json_response(self, {"error": f"{path} is not a timeline file: {e}"}, 422)
            return None

    def _handle_get_timeline(self):
        import edits
        with _timeline_lock:
            loaded = self._load_timeline()
        if loaded is None:
            return
        tl, h = loaded
        findings = tl.validate(file_checks=False, wav_seconds=_wav_reader())
        _json_response(self, _film_answer(tl, h, findings))

    def _handle_timeline_edit(self):
        import edits
        import store
        import timeline
        body = _read_body(self)
        ops = body.get("operations") if isinstance(body, dict) else None
        sent_hash = body.get("hash") if isinstance(body, dict) else None
        if not isinstance(body, dict) or not isinstance(sent_hash, str) or not sent_hash:
            _json_response(self, {"error": "The body must be an object with 'hash' (the hash "
                                  "the page started from) and 'operations'."}, 400)
            return
        with _timeline_lock:
            loaded = self._load_timeline()
            if loaded is None:
                return
            tl, h = loaded
            if h != sent_hash:
                _json_response(self, {"error": "The timeline file changed since the page read "
                                      "it. Reload.", "hash": h}, 409)
                return
            try:
                new_data = edits.apply_operations(tl.data, ops)
            except edits.OperationError as e:
                _json_response(self, {"error": str(e)}, 400)
                return
            new = timeline.Timeline(new_data, tl.duplicates)
            findings = new.validate(file_checks=False, wav_seconds=_wav_reader())
            if timeline.has_errors(findings):
                _json_response(self, {"error": "The change breaks a rule of the timeline file; "
                                      "nothing was saved.",
                                      "findings": [f.as_dict() for f in findings]}, 422)
                return
            try:
                new_hash = store.save(new, timeline_file(), expected_hash=sent_hash)
            except store.StaleHash as e:
                _json_response(self, {"error": str(e), "hash": e.actual}, 409)
                return
        _json_response(self, _film_answer(new, new_hash, findings))

    def _serve_preview_file(self, name):
        """A file of <work>/previews, with HTTP range requests (SPECS 12). Any path that
        leaves that folder, also by a symbolic link, is a 404."""
        work = work_dir()
        base = os.path.realpath(os.path.join(work, "previews")) if work else None
        if not base or not name or "\0" in name or os.path.isabs(name):
            self.send_error(404)
            return
        target = os.path.realpath(os.path.join(base, name))
        if not target.startswith(base + os.sep) or not os.path.isfile(target):
            self.send_error(404)
            return
        size = os.path.getsize(target)
        start, end, status = 0, size - 1, 200
        header = (self.headers.get("Range") or "").strip()
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
        if m and (m.group(1) or m.group(2)):
            if not m.group(1):                      # the last n bytes
                n = int(m.group(2))
                start, end = max(0, size - n), size - 1
                if n == 0:
                    start = size
            else:
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            if start >= size or start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        mime = {".mp4": "video/mp4", ".json": "application/json", ".vtt": "text/vtt",
                ".jpg": "image/jpeg", ".png": "image/png"}.get(Path(target).suffix.lower(),
                                                                "application/octet-stream")
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-cache")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(target, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    block = f.read(min(262144, left))
                    if not block:
                        break
                    self.wfile.write(block)
                    left -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass            # the browser stopped reading (it seeks, or the tab closed)

    def _handle_preview(self):
        import render
        import timeline
        _read_body(self)
        work = work_dir()
        with _timeline_lock:
            loaded = self._load_timeline()
        if loaded is None:
            return
        if not work:
            _json_response(self, {"error": "No work folder. Start the app with --work DIR, or "
                                  "set work_dir in the project file."}, 400)
            return
        tl, h = loaded
        findings = tl.validate(file_checks=False)
        if timeline.has_errors(findings):
            _json_response(self, {"error": "The timeline file is not valid; no preview was "
                                  "started.", "findings": [f.as_dict() for f in findings]}, 422)
            return
        if not _try_start_job():
            _json_response(self, {"error": "Another job is already running"}, 409)
            return
        _set_status("previewing", "Rendering the film preview...", 3)

        def do_preview():
            try:
                settings = _preview_settings or render.PreviewSettings()
                originals = sorted({str(Path(s[k]).parent) for s in tl.data["sources"].values()
                                    for k in ("original_video", "audio")})

                def progress(i, n):
                    _set_status("previewing", f"Rendering segment {i} of {n}...",
                                min(95, 5 + int(85 * i / max(n, 1))))

                result = render.render_preview(tl, work, name=PREVIEW_NAME, settings=settings,
                                               timeline_hash=h, protected=originals,
                                               log=lambda s: None, progress=progress)
                _set_status("previewed",
                            f"Film preview ready: {result.frames} frames, "
                            f"{len(result.rendered)} segments rendered, {len(result.cached)} reused.",
                            100)
            except Exception as e:
                traceback.print_exc()
                _set_status("error", f"Preview failed: {e}", 0)
            finally:
                _end_job()

        threading.Thread(target=do_preview, daemon=True).start()
        _json_response(self, {"status": "started"})

    def _change_timeline(self, change):
        """Read the hash the page started from and the timeline, apply `change(tl) -> new data`
        under the lock, validate, save with a snapshot, and answer with the film as it is now.
        Answers 400, 404, 409 and 422 as the page's edits do (SPECS 4.4)."""
        import store
        import timeline
        body = _read_body(self)
        sent_hash = body.get("hash") if isinstance(body, dict) else None
        if not isinstance(body, dict) or not isinstance(sent_hash, str) or not sent_hash:
            _json_response(self, {"error": "The body must be an object with 'hash' (the hash the "
                                  "page started from)."}, 400)
            return
        with _timeline_lock:
            loaded = self._load_timeline()
            if loaded is None:
                return
            tl, h = loaded
            if h != sent_hash:
                _json_response(self, {"error": "The timeline file changed since the page read it. "
                                      "Reload.", "hash": h}, 409)
                return
            try:
                new_data = change(tl, body)
            except ValueError as e:
                _json_response(self, {"error": str(e)}, 400)
                return
            new = timeline.Timeline(new_data, tl.duplicates)
            findings = new.validate(file_checks=False, wav_seconds=_wav_reader())
            if timeline.has_errors(findings):
                _json_response(self, {"error": "The change breaks a rule of the timeline file; "
                                      "nothing was saved.",
                                      "findings": [f.as_dict() for f in findings]}, 422)
                return
            try:
                new_hash = store.save(new, timeline_file(), expected_hash=sent_hash)
            except store.StaleHash as e:
                _json_response(self, {"error": str(e), "hash": e.actual}, 409)
                return
        _json_response(self, _film_answer(new, new_hash, findings))

    def _handle_look(self):
        """The creative LUT of the film (SPECS 8.2): {hash, lut: id of a LUT in the LUT folder, or
        null for none}. The page names an id; the server finds the file inside the folder."""
        import look

        def change(tl, body):
            if "lut" not in body or not (body["lut"] is None or isinstance(body["lut"], str)):
                raise ValueError("'lut' must be the id of a LUT, or null for none.")
            if body["lut"] is None:
                return look.with_creative_lut(tl.data, None)
            work = work_dir()
            if not work:
                raise ValueError("No work folder. Start the app with --work DIR, or set work_dir in "
                                 "the project file.")
            try:
                path = look.find_lut(_lut_folder(), body["lut"])
                entry = look.install(path, work, protected=_originals_of(tl))
            except look.LutError as e:
                raise ValueError(str(e))
            except Exception as e:
                raise ValueError(str(e))
            return look.with_creative_lut(tl.data, entry)

        self._change_timeline(change)

    def _handle_loudness(self):
        """The loudness of the film (decision D9): {hash, mode, target_lufs?, true_peak_dbtp?,
        max_gain_db?}. The mode `off`, `segment_then_film` or `film_only`."""
        import edits

        def change(tl, body):
            nums = {}
            for key in ("target_lufs", "true_peak_dbtp", "max_gain_db"):
                if key in body and body[key] is not None:
                    if not isinstance(body[key], (int, float)) or isinstance(body[key], bool):
                        raise ValueError(f"'{key}' must be a number.")
                    nums[key] = body[key]
            try:
                return edits.set_loudness(tl.data, body.get("mode"), **nums)
            except edits.OperationError as e:
                raise ValueError(str(e))

        self._change_timeline(change)

    def _handle_render(self):
        """Start the final render of an export (SPECS 8.5). {export: name} renders an entry of the
        timeline file; {preset, name?, width?, height?} adds an entry first (an entry of that name
        is reused); {enable: true} switches a switched-off export on (the ProRes master). The job
        shares the slot with the other jobs; the page cannot name a path."""
        import export
        import store
        import timeline
        body = _read_body(self)
        body = body if isinstance(body, dict) else {}
        work = work_dir()
        if not work:
            _json_response(self, {"error": "No work folder. Start the app with --work DIR, or "
                                  "set work_dir in the project file."}, 400)
            return
        with _timeline_lock:
            loaded = self._load_timeline()
            if loaded is None:
                return
            tl, h = loaded
            name = body.get("export")
            data = tl.data
            try:
                if body.get("preset") is not None:
                    entry = export.preset_entry(
                        body["preset"], os.path.join(work, "renders"), data["film"]["frame"],
                        name=body.get("name"), width=body.get("width"), height=body.get("height"))
                    name = entry["name"]
                    if not any(e.get("name") == name for e in data.get("exports") or []):
                        data = export.with_export(data, entry)
                if not isinstance(name, str):
                    raise export.ExportError("Send 'export' (the name of an export) or 'preset'.")
                found = export.find_export(data, name)
                if body.get("enable") is True and not found.get("enabled", True):
                    data = export.with_export_enabled(data, name, True)
            except export.ExportError as e:
                _json_response(self, {"error": str(e)}, 400)
                return
            if data is not tl.data:
                new = timeline.Timeline(data, tl.duplicates)
                findings = new.validate(file_checks=False)
                if timeline.has_errors(findings):
                    _json_response(self, {"error": "The export breaks a rule of the timeline file; "
                                          "nothing was saved.",
                                          "findings": [f.as_dict() for f in findings]}, 422)
                    return
                h = store.save(new, timeline_file(), expected_hash=h)
                tl = new
            findings = tl.validate(file_checks=False)
            if timeline.has_errors(findings):
                _json_response(self, {"error": "The timeline file is not valid; no render was "
                                      "started.", "findings": [f.as_dict() for f in findings]}, 422)
                return
            if not export.find_export(tl.data, name).get("enabled", True):
                _json_response(self, {"error": f"The export {name!r} is switched off. Tick it to "
                                      "switch it on (send enable: true)."}, 422)
                return
        if not _try_start_job():
            _json_response(self, {"error": "Another job is already running"}, 409)
            return
        _set_status("rendering", f"Starting the render of {name}...", 2)

        def do_render():
            try:
                decoder = _decoder
                if decoder is None:
                    from decoder import BrawSourceDecoder
                    decoder = BrawSourceDecoder()
                decoder.check()
                state = {"progress": 3}

                def log(line):
                    _set_status("rendering", line, state["progress"])

                def progress(done, total, fps):
                    state["progress"] = min(97, 5 + int(92 * done / max(total, 1)))
                    rate = fps / float(tl.fps)
                    left = (total - done) / fps if fps else 0
                    _set_status("rendering",
                                f"Rendering {name}: {100 * done / max(total, 1):.0f} percent, "
                                f"{fps:.1f} fps ({rate:.2f}x real time), about {left / 60:.0f} min to go",
                                state["progress"])

                result = export.render_export(tl, name, work, decoder, timeline_hash=h,
                                              protected=_originals_of(tl), log=log, progress=progress)
                if result.ok:
                    out = result.output_loudness or {}
                    shown = "" if not out else f"; {out.get('lufs')} LUFS, true peak {out.get('true_peak_dbtp')} dBTP"
                    _set_status("rendered", f"Export ready: {result.path}{shown}", 100)
                else:
                    _set_status("error", "The file failed its checks: " + "; ".join(result.problems), 0)
            except Exception as e:
                traceback.print_exc()
                _set_status("error", f"Render failed: {e}", 0)
            finally:
                _end_job()

        threading.Thread(target=do_render, daemon=True).start()
        _json_response(self, {"status": "started", "export": name})

    def _handle_assemble(self):
        body = _read_body(self)
        body = body if isinstance(body, dict) else {}
        with _project_lock:
            proj = copy.deepcopy(_project)
        work = work_dir()
        if not proj.matches:
            _json_response(self, {"error": "The project has no matched clips. Match the files "
                                  "first."}, 400)
            return
        if not work:
            _json_response(self, {"error": "No work folder. Start the app with --work DIR, or "
                                  "set work_dir in the project file."}, 400)
            return
        path = timeline_file()
        replace = bool(body.get("replace"))
        if os.path.exists(path) and not replace:
            _json_response(self, {"error": "A timeline file exists. Send replace: true to "
                                  "assemble again; the old file is kept as a snapshot."}, 409)
            return
        limit = body.get("limit")
        limit = limit if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0 else None
        only = body.get("only")
        only = [s for s in only if isinstance(s, str)] if isinstance(only, list) else None
        if not _try_start_job():
            _json_response(self, {"error": "Another job is already running"}, 409)
            return
        _set_status("assembling", "Assembling the film...", 10)

        def do_assemble():
            try:
                import assemble
                result = assemble.assemble_project(proj, work, path, replace=replace, limit=limit,
                                                   only=only, log=lambda s: None)
                film = result.report["film"]
                _set_status("assembled",
                            f"Assembled {film['segments']} segments from {film['clips_used']} "
                            f"clips, {film['seconds']:.0f} s; {len(film['left_out'])} clips left out.",
                            100)
            except Exception as e:
                traceback.print_exc()
                _set_status("error", f"Assembly failed: {e}", 0)
            finally:
                _end_job()

        threading.Thread(target=do_assemble, daemon=True).start()
        _json_response(self, {"status": "started"})

    # ── Thumbnails ──

    def _serve_thumbnail(self, path):
        """Serve a thumbnail image file."""
        # path is /api/thumbnail/<index>; the ?v= cache-busting query is already stripped by urlparse
        try:
            index = int(path.split("/")[-1])
        except ValueError:
            self.send_error(400)
            return

        with _project_lock:
            match = _project.get_match(index)

        if not match or not match.thumbnail_path:
            self.send_error(404)
            return

        thumb_path = Path(match.thumbnail_path)
        if not thumb_path.exists():
            self.send_error(404)
            return

        self.send_response(200)
        ext = thumb_path.suffix.lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(thumb_path.read_bytes())

    # ── UI ──

    def _serve_ui(self):
        """Serve the main HTML UI."""
        ui_path = Path(__file__).parent / "ui.html"
        if not ui_path.exists():
            self.send_error(500, "ui.html not found")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(ui_path.read_bytes())

    # ── Helpers ──

    def _project_to_dict(self):
        """Convert project state to JSON-serializable dict."""
        from dataclasses import asdict
        matches = []
        for m in _project.matches:
            m_obj = m if isinstance(m, SceneMatch) else SceneMatch(**m)
            d = asdict(m_obj)
            d["video_file"] = Path(m_obj.video_path).name
            d["audio_file"] = Path(m_obj.audio_path).name
            d["has_thumbnail"] = False
            d["thumb_version"] = 0
            if m_obj.thumbnail_path:
                try:
                    # mtime changes on every re-match, so the UI can bust its cache
                    d["thumb_version"] = Path(m_obj.thumbnail_path).stat().st_mtime_ns
                    d["has_thumbnail"] = True
                except OSError:
                    pass
            matches.append(d)

        return {
            "video_dir": _project.video_dir,
            "audio_dir": _project.audio_dir,
            "output_dir": _project.output_dir,
            "matches": matches,
            "selection": _project.selection,
            "selection_trims": _project.selection_trims,
        }


def _review_flags() -> dict:
    """Segment id -> reasons to look at it, from the assembly report in the work folder."""
    work = work_dir()
    if not work:
        return {}
    path = os.path.join(work, "reports", "assembly-report.json")
    try:
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError):
        return {}
    flags = {}
    for clip in report.get("clips", []):
        if clip.get("review") and clip.get("review_reasons"):
            for seg_id in clip.get("segments", []):
                flags[seg_id] = clip["review_reasons"]
    return flags


def _wav_reader():
    """How the length of a WAV is read for rule V23: ffprobe, or what a test set."""
    import timeline
    return _wav_seconds or timeline.wav_length_seconds


def _lut_folder():
    import look
    return _lut_dir or look.lut_dir()


def _originals_of(tl) -> list:
    return sorted({str(Path(s[k]).parent) for s in tl.data["sources"].values()
                   for k in ("original_video", "audio")})


def _look_state(tl) -> dict:
    """The creative LUT of the film and the LUTs the page may choose (SPECS 8.2)."""
    import look
    folder = _lut_folder()
    lut = tl.data["look"].get("lut")
    choices = [{k: r.get(k) for k in ("id", "name", "title", "credit", "problem")}
               for r in look.list_luts(folder)]
    current = None
    if lut:
        current = {"name": lut.get("name") or os.path.basename(lut["path"]),
                   "credit": look.plain_credit(lut["credit"]) if lut.get("credit") else None,
                   "id": next((c["id"] for c in choices if c["name"] == lut.get("name")), None)}
    return {"lut": current, "luts": choices, "folder": folder or "",
            "base": tl.data["look"]["base"].get("mode")}


def _exports_state(tl) -> list:
    """The exports of the timeline file with an estimate of the file and the last render."""
    import export
    work = work_dir()
    out = []
    for e in tl.data.get("exports") or []:
        last = export.latest_render(work, e["name"]) if work else None
        row = {"name": e["name"], "enabled": e.get("enabled", True), "width": e["width"],
               "height": e["height"], "codec": e["video"]["codec"], "output": e["output"],
               "estimate_bytes": export.estimate_bytes(e, tl.film_length_frames(), tl.fps),
               "last": None}
        if last:
            row["last"] = {k: last.get(k) for k in ("ok", "finished_at", "speed", "bytes", "mbps",
                                                    "warnings", "problems", "credit_file", "frames")}
            row["last"]["output_loudness"] = (last.get("loudness") or {}).get("output_file")
        out.append(row)
    return out


def _loudness_state(tl, preview: dict) -> dict:
    import render
    return {"settings": render.loudness_settings(tl), "preview": preview.get("loudness")}


def _free_bytes() -> int:
    import shutil
    work = work_dir()
    try:
        return shutil.disk_usage(work if work and os.path.isdir(work) else os.path.expanduser("~")).free
    except OSError:
        return 0


def _film_answer(tl, h: str, findings) -> dict:
    """What the page needs to show the film: the summary, the findings, the state of the preview,
    the creative LUT and the choices, the loudness, and the exports."""
    import edits
    preview = _preview_state(h, tl)
    return {"path": timeline_file(), "name": os.path.basename(timeline_file()), "hash": h,
            "summary": edits.summary(tl, h, _review_flags()),
            "findings": [f.as_dict() for f in findings],
            "preview": preview, "look": _look_state(tl), "loudness": _loudness_state(tl, preview),
            "exports": _exports_state(tl), "free_bytes": _free_bytes()}


def _preview_state(current_hash: str, tl=None) -> dict:
    """Whether a film preview exists and was made from the timeline file as it is now.
    With `tl`, also what the next preview has to do (`pending`, SPECS 7.6): the segments
    whose picture is not in the cache and the segments whose sound is not the sound of
    the last preview. A changed sync offset changes the sound only."""
    state = {"exists": False, "up_to_date": False, "file": None, "frames": None}
    work = work_dir()
    if not work:
        return state
    if tl is not None:
        import render
        try:
            state["pending"] = render.pending_work(tl, work, _preview_settings, PREVIEW_NAME)
        except Exception:               # a broken file must not stop the page from loading
            traceback.print_exc()
    folder = os.path.join(work, "previews")
    meta_path = os.path.join(folder, f"{PREVIEW_NAME}.json")
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return state
    if not os.path.isfile(os.path.join(folder, str(meta.get("file", "")))):
        return state
    state.update(exists=True, file=meta["file"], frames=meta.get("frames"),
                 seconds=meta.get("seconds"), rendered_at=meta.get("rendered_at"),
                 loudness=meta.get("loudness"),
                 up_to_date=meta.get("timeline_hash") == current_hash and not meta.get("range"))
    return state


def make_server(port: int = 0, host: str = "127.0.0.1"):
    """A server bound to `host`:`port` (port 0 picks a free one). Not started."""
    httpd = ThreadingHTTPServer((host, port), AppHandler)
    httpd.daemon_threads = True
    return httpd


def run_server(port: int = None, open_browser: bool = True, timeline_path: str = None,
               work_dir_path: str = None):
    """Start the app server."""
    port = port or config.SELECTOR_PORT
    configure(timeline_path=timeline_path, work_dir=work_dir_path)
    _load_project()
    server = make_server(port)
    print(f"\n  Ambient Video Automation")
    print(f"  Running at: http://localhost:{port}")
    print(f"  Timeline file: {timeline_file()}")
    print(f"  Press Ctrl+C to stop.\n")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()
