"""
Local web server for the Ambient Video Automation app.
Provides API endpoints that the frontend calls, and serves the UI.
"""

import json
import os
import sys
import threading
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import config
from project import Project, SceneMatch

# Global state
_project = Project()
_project_lock = threading.Lock()
_status = {"step": "idle", "message": "Ready", "progress": 0}


def _set_status(step: str, message: str, progress: int = 0):
    global _status
    _status = {"step": step, "message": message, "progress": progress}


def _json_response(handler, data, status=200):
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
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

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            self._serve_ui()
        elif path == "/api/status":
            _json_response(self, _status)
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
            elif path == "/api/rename":
                self._handle_rename()
            else:
                self.send_error(404)
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
        global _project
        body = _read_body(self)
        video_dir = body.get("video_dir", "")
        audio_dir = body.get("audio_dir", "")

        if not video_dir or not audio_dir:
            _json_response(self, {"error": "video_dir and audio_dir are required"}, 400)
            return

        _set_status("matching", "Matching audio fingerprints...", 10)

        def do_match():
            global _project
            try:
                from matcher import match_files
                from thumbnails import extract_all_thumbnails

                matches = match_files(video_dir, audio_dir)

                with _project_lock:
                    _project = Project(
                        video_dir=video_dir,
                        audio_dir=audio_dir,
                        matches=matches,
                    )

                _set_status("thumbnails", "Extracting thumbnails...", 60)

                thumb_dir = Path(video_dir).parent / "thumbnails"
                with _project_lock:
                    _project = extract_all_thumbnails(_project, str(thumb_dir))
                    _project.save()

                _set_status("matched", f"Found {len(matches)} matched pairs.", 100)

            except Exception as e:
                traceback.print_exc()
                _set_status("error", f"Matching failed: {e}", 0)

        thread = threading.Thread(target=do_match, daemon=True)
        thread.start()

        _json_response(self, {"status": "started"})

    # ── Select ──

    def _handle_select(self):
        global _project
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
        global _project
        body = _read_body(self)

        _set_status("syncing", "Copying and renaming audio files...", 10)

        def do_sync():
            global _project
            try:
                from syncer import sync_all_scenes

                with _project_lock:
                    proj_copy = Project.load()

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

        thread = threading.Thread(target=do_sync, daemon=True)
        thread.start()

        _json_response(self, {"status": "started"})

    # ── Resolve ──

    def _handle_resolve(self):
        body = _read_body(self)

        _set_status("resolve", "Creating DaVinci Resolve project...", 10)

        def do_resolve():
            try:
                from resolve_integration import create_resolve_project

                with _project_lock:
                    proj_copy = Project.load()

                result = create_resolve_project(
                    project=proj_copy,
                    project_name=body.get("name", "Ambient Video Project"),
                    timeline_name=body.get("timeline", "Main Timeline"),
                )

                _set_status("resolve_done",
                            f"Resolve project created with {result['clips_imported']} clips.", 100)

            except Exception as e:
                traceback.print_exc()
                _set_status("error", f"Resolve failed: {e}", 0)

        thread = threading.Thread(target=do_resolve, daemon=True)
        thread.start()

        _json_response(self, {"status": "started"})

    # ── Rename ──

    def _handle_rename(self):
        global _project
        body = _read_body(self)
        dry_run = body.get("dry_run", False)

        from renamer import rename_matches

        with _project_lock:
            ops = rename_matches(_project, dry_run=dry_run)
            if not dry_run:
                _project.save()

        _json_response(self, {"operations": ops, "dry_run": dry_run})

    # ── Thumbnails ──

    def _serve_thumbnail(self, path):
        """Serve a thumbnail image file."""
        # path is /api/thumbnail/<index>
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
        self.send_header("Cache-Control", "max-age=3600")
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
            d["has_thumbnail"] = bool(m_obj.thumbnail_path and Path(m_obj.thumbnail_path).exists())
            matches.append(d)

        return {
            "video_dir": _project.video_dir,
            "audio_dir": _project.audio_dir,
            "output_dir": _project.output_dir,
            "matches": matches,
            "selection": _project.selection,
            "selection_trims": _project.selection_trims,
        }


def run_server(port: int = None):
    """Start the app server."""
    port = port or config.SELECTOR_PORT
    server = HTTPServer(("127.0.0.1", port), AppHandler)
    print(f"\n  Ambient Video Automation")
    print(f"  Running at: http://localhost:{port}")
    print(f"  Press Ctrl+C to stop.\n")

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
