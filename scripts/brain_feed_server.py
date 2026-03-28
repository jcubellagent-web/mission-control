#!/usr/bin/env python3
"""
Brain Feed Local Server — serves brain-feed.json + remote control endpoints.
Port 8765.

Endpoints:
  GET  /brain-feed.json     — serve latest brain feed data
  POST /refresh             — force refresh Safari (reloads Mission Control)
  POST /nightmode/on        — enable night mode on desktop
  POST /nightmode/off       — disable night mode on desktop
  GET  /nightmode/state     — current night mode state
"""
import http.server, json, os, subprocess, sys, threading
from pathlib import Path

PORT = 8765
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_FILE = DATA_DIR / "remote-control-state.json"

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
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        SERVED_FILES = {
            "/brain-feed.json": "brain-feed.json",
            "/jain-brain-feed.json": "jain-brain-feed.json",
            "/dashboard-data.json": "dashboard-data.json",
            "/agent-comms.json": "agent-comms.json",
        }
        if path in SERVED_FILES:
            fname = SERVED_FILES[path]
            bf_path = DATA_DIR / fname
            try:
                data = bf_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self._cors()
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == "/nightmode/state":
            self._json(get_state())

        elif path == "/ping":
            self._json({"ok": True, "port": PORT})

        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/refresh":
            # Force reload Safari on the desktop
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
            # Signal the dashboard via a flag file
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

if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Brain Feed + Remote Control server on http://localhost:{PORT}", flush=True)
    server.serve_forever()
