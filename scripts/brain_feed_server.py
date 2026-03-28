#!/usr/bin/env python3
"""
Brain Feed Local Server — serves brain-feed.json + full dashboard + remote control.
Port 8765.

Endpoints:
  GET  /                    — serve Mission Control dashboard (index.html)
  GET  /index.html          — same
  GET  /data/*.json         — serve data files with no-cache headers
  GET  /assets/*            — serve static assets (CSS, JS, images)
  GET  /brain-feed.json     — serve latest brain feed data (legacy compat)
  GET  /dashboard-data.json — serve dashboard data (legacy compat)
  GET  /jain-brain-feed.json— serve J.A.I.N brain feed (legacy compat)
  GET  /agent-comms.json    — serve agent comms (legacy compat)
  POST /refresh             — force refresh Safari (reloads Mission Control)
  POST /nightmode/on        — enable night mode on desktop
  POST /nightmode/off       — disable night mode on desktop
  GET  /nightmode/state     — current night mode state
"""
import http.server, json, mimetypes, os, subprocess, sys, threading
from pathlib import Path

PORT = 8765
ROOT_DIR = Path(__file__).resolve().parent.parent  # mission-control/
DATA_DIR = ROOT_DIR / "data"
STATE_FILE = DATA_DIR / "remote-control-state.json"

# MIME type map
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js":   "application/javascript",
    ".css":  "text/css",
    ".json": "application/json",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".svg":  "image/svg+xml",
    ".ico":  "image/x-icon",
    ".woff": "font/woff",
    ".woff2":"font/woff2",
    ".ttf":  "font/ttf",
    ".webp": "image/webp",
}

def get_mime(path):
    ext = Path(path).suffix.lower()
    return MIME_TYPES.get(ext, "application/octet-stream")

def get_state():
    try: return json.loads(STATE_FILE.read_text())
    except: return {"nightMode": False}

def set_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2))

def run_applescript(script):
    """Run AppleScript asynchronously."""
    subprocess.Popen(["osascript", "-e", script],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

class Handler(http.server.BaseHTTPRequestHandler):

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, file_path, no_cache=False):
        """Serve a static file."""
        try:
            data = Path(file_path).read_bytes()
            mime = get_mime(str(file_path))
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            if no_cache:
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            else:
                self.send_header("Cache-Control", "public, max-age=60")
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except FileNotFoundError:
            self._json({"error": "not found"}, 404)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"

        # Legacy compat: flat JSON file endpoints
        LEGACY_JSON = {
            "/brain-feed.json":       DATA_DIR / "brain-feed.json",
            "/jain-brain-feed.json":  DATA_DIR / "jain-brain-feed.json",
            "/dashboard-data.json":   DATA_DIR / "dashboard-data.json",
            "/agent-comms.json":      DATA_DIR / "agent-comms.json",
            "/jain-tasks.json":       DATA_DIR / "jain-tasks.json",
        }
        if path in LEGACY_JSON:
            return self._serve_file(LEGACY_JSON[path], no_cache=True)

        # Dashboard root
        if path in ("/", "/index.html"):
            return self._serve_file(ROOT_DIR / "index.html", no_cache=False)

        # /data/*.json — serve data files (no-cache)
        if path.startswith("/data/") and path.endswith(".json"):
            fname = path[len("/data/"):]
            if ".." not in fname:
                return self._serve_file(DATA_DIR / fname, no_cache=True)

        # /assets/* — serve static assets
        if path.startswith("/assets/"):
            rel = path[len("/assets/"):]
            if ".." not in rel:
                asset_path = ROOT_DIR / "assets" / rel
                return self._serve_file(asset_path)

        # /nightmode/state
        if path == "/nightmode/state":
            return self._json(get_state())

        # /ping
        if path == "/ping":
            return self._json({"ok": True, "port": PORT})

        self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/refresh":
            run_applescript('''
                tell application "Safari"
                    activate
                    tell application "System Events"
                        keystroke "r" using {command down}
                    end tell
                end tell
            ''')
            self._json({"ok": True, "action": "refresh"})

        elif path == "/nightmode/on":
            state = get_state()
            state["nightMode"] = True
            set_state(state)
            (DATA_DIR / "night-mode.flag").write_text("on")
            self._json({"ok": True, "nightMode": True})

        elif path == "/nightmode/off":
            state = get_state()
            state["nightMode"] = False
            set_state(state)
            flag = DATA_DIR / "night-mode.flag"
            if flag.exists(): flag.unlink()
            self._json({"ok": True, "nightMode": False})

        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        pass  # suppress access logs

def _poll_jain_brain_feed():
    """Background thread: pull J.A.I.N brain feed via SSH every 30s, completely independent of cron."""
    import time
    dest = DATA_DIR / "jain-brain-feed.json"
    while True:
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes",
                 "-o", "StrictHostKeyChecking=no",
                 "jc_agent@100.121.89.84",
                 "cat /Users/jc_agent/.openclaw/workspace/mission-control/data/brain-feed.json"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0 and result.stdout:
                dest.write_bytes(result.stdout)
        except Exception:
            pass
        time.sleep(30)


if __name__ == "__main__":
    # Start JAIN brain feed poller in background (real-time, independent of dashboard cron)
    t = threading.Thread(target=_poll_jain_brain_feed, daemon=True)
    t.start()
    print("J.A.I.N brain feed poller started (30s interval)", flush=True)

    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Brain Feed + Dashboard server on http://localhost:{PORT}", flush=True)
    server.serve_forever()
