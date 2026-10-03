"""
Local web server for the Ambient Video Automation app.
Provides API endpoints that the frontend calls, and serves the UI.
"""

import copy
import json
import os
import sys
import threading
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import config
from project import Project, SceneMatch

# Global state
_project = Project()
_project_lock = threading.Lock()
_status = {"step": "idle", "message": "Ready", "progress": 0}
_status_lock = threading.Lock()

# Only one background job (match, sync, resolve) may run at a time
_job_running = False
_job_lock = threading.Lock()


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
            _json_response(self, {"error": "Forbidden host"}, 403)
            return False
        if is_post:
            origin = self.headers.get("Origin")
            if origin is not None and origin not in {f"http://{h}" for h in hosts}:
                _json_response(self, {"error": "Forbidden origin"}, 403)
                return False
        return True

    def do_GET(self):
        if not self._check_request(False):
            return
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


def run_server(port: int = None, open_browser: bool = True):
    """Start the app server."""
    port = port or config.SELECTOR_PORT
    _load_project()
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    server.daemon_threads = True
    print(f"\n  Ambient Video Automation")
    print(f"  Running at: http://localhost:{port}")
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
