#!/usr/bin/env python3
"""
Brain Feed Local Server — serves brain-feed.json + full dashboard + remote control.
Port 8765.

Endpoints:
  GET  /                    — serve legacy static dashboard (index.html)
  GET  /index.html          — same legacy static dashboard
  GET  /data/*.json         — serve data files with no-cache headers
  GET  /assets/*            — serve static assets (CSS, JS, images)
  GET  /v2/*                — serve legacy static data-layer proof files
  GET  /brain-feed.json     — serve latest brain feed data (legacy compat)
  GET  /dashboard-data.json — serve dashboard data (legacy compat)
  GET  /jain-brain-feed.json— serve J.A.I.N brain feed (legacy compat)
  GET  /agent-comms.json    — serve agent comms (legacy compat)
  POST /refresh             — force refresh Safari (reloads Control Tower)
  POST /nightmode/on        — enable night mode on desktop
  POST /nightmode/off       — disable night mode on desktop
  GET  /nightmode/state     — current night mode state
  GET  /eightsleep/status   — fetch live Eight Sleep device state
  PUT  /eightsleep/left     — set left side temp (?level=-54)
  PUT  /eightsleep/right    — set right side temp (?level=-54)
  PUT  /eightsleep/both     — set both sides temp (?level=-54)
  POST /eightsleep/off      — turn off (set both sides to 0)
"""
import http.server, json, mimetypes, os, subprocess, sys, threading, urllib.error, urllib.request
from pathlib import Path

# ── Eight Sleep proxy config ───────────────────────────────────────────────────
_8S_AUTH_URL = "https://auth-api.8slp.net/v1/tokens"
_8S_CLIENT_API = "https://client-api.8slp.net/v1"
_8S_SECRET_PATH = Path(
    os.environ.get("EIGHT_SLEEP_SECRET_FILE", Path.home() / ".secrets" / "eightsleep.json")
)
_8s_config_cache = None
_8s_token_cache: dict = {}  # {"token": str, "expires": float}


def _8s_config() -> dict:
    """Load Eight Sleep credentials from a host-local secret file or env."""
    global _8s_config_cache
    if _8s_config_cache is not None:
        return _8s_config_cache

    cfg = {}
    if _8S_SECRET_PATH.exists():
        cfg = json.loads(_8S_SECRET_PATH.read_text())

    env_map = {
        "email": "EIGHT_SLEEP_EMAIL",
        "password": "EIGHT_SLEEP_PASSWORD",
        "client_id": "EIGHT_SLEEP_CLIENT_ID",
        "client_secret": "EIGHT_SLEEP_CLIENT_SECRET",
        "device_id": "EIGHT_SLEEP_DEVICE_ID",
        "user_id": "EIGHT_SLEEP_USER_ID",
    }
    for key, env_name in env_map.items():
        if os.environ.get(env_name):
            cfg[key] = os.environ[env_name]

    required = ("email", "password", "client_id", "client_secret", "device_id")
    missing = [key for key in required if not cfg.get(key)]
    if missing:
        raise RuntimeError(
            "Eight Sleep secret config missing required key(s): " + ", ".join(missing)
        )

    _8s_config_cache = cfg
    return cfg


def _8s_authenticate() -> str:
    """Get (or return cached) Eight Sleep access token."""
    import time
    now = time.time()
    if _8s_token_cache.get("token") and _8s_token_cache.get("expires", 0) > now + 60:
        return _8s_token_cache["token"]
    cfg = _8s_config()
    body = json.dumps({
        "grant_type": "password",
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "username": cfg["email"],
        "password": cfg["password"],
    }).encode()
    req = urllib.request.Request(_8S_AUTH_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        resp = json.load(r)
    token = resp.get("session", {}).get("token") or resp.get("access_token") or resp.get("token")
    if not token:
        for v in resp.values():
            if isinstance(v, dict) and "token" in v:
                token = v["token"]
                break
    if not token:
        raise ValueError("No token in Eight Sleep auth response")
    _8s_token_cache["token"] = token
    _8s_token_cache["expires"] = now + 3600  # assume 1h TTL
    return token


def _8s_get(path: str) -> dict:
    token = _8s_authenticate()
    url = f"{_8S_CLIENT_API}{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def _8s_put(path: str, body: dict) -> dict:
    token = _8s_authenticate()
    url = f"{_8S_CLIENT_API}{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

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

        # Legacy static dashboard root. Current Control Tower is the React kiosk
        # served by Vite on Josh 2.0 at http://127.0.0.1:5174/.
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

        # /v2/* — serve the legacy static data-layer proof surface.
        if path == "/v2":
            return self._serve_file(ROOT_DIR / "v2" / "index.html", no_cache=True)
        if path.startswith("/v2/"):
            rel = path[len("/v2/"):] or "index.html"
            if ".." not in rel and rel in {"index.html", "styles.css", "app.js", "config.example.js"}:
                return self._serve_file(ROOT_DIR / "v2" / rel, no_cache=True)

        # /nightmode/state
        if path == "/nightmode/state":
            return self._json(get_state())

        # /ping
        if path == "/ping":
            return self._json({"ok": True, "port": PORT})

        # /eightsleep/status — proxy live device state
        if path == "/eightsleep/status":
            try:
                device = _8s_get(f"/devices/{_8s_config()['device_id']}")
                result = device.get("result", device)
                return self._json({"ok": True, "device": result})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)

        self._json({"error": "not found"}, 404)

    def do_PUT(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        try:
            level = int(params.get("level", [None])[0])
        except (TypeError, ValueError):
            return self._json({"ok": False, "error": "?level=<int> required"}, 400)
        level = max(-100, min(100, level))
        try:
            if path == "/eightsleep/left":
                _8s_put(f"/devices/{_8s_config()['device_id']}", {"leftHeatingLevel": level})
                return self._json({"ok": True, "side": "left", "level": level})
            elif path == "/eightsleep/right":
                _8s_put(f"/devices/{_8s_config()['device_id']}", {"rightHeatingLevel": level})
                return self._json({"ok": True, "side": "right", "level": level})
            elif path == "/eightsleep/both":
                _8s_put(f"/devices/{_8s_config()['device_id']}", {"leftHeatingLevel": level, "rightHeatingLevel": level})
                return self._json({"ok": True, "side": "both", "level": level})
            else:
                return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 500)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/refresh":
            # Soft refresh — JS forceUpdate()
            self._json({"ok": True, "action": "refresh"})

        elif path == "/hard-refresh":
            # Hard reload — Safari location.reload(true) on the Control Tower tab
            run_applescript(
                'tell application "Safari"\n'
                '    repeat with w in windows\n'
                '        repeat with t in tabs of w\n'
                '            set u to URL of t\n'
                '            if u contains "mission-control" or u contains "localhost:8765" or u contains "jcubellagent-web.github.io" then\n'
                '                do JavaScript "location.reload(true)" in t\n'
                '            end if\n'
                '        end repeat\n'
                '    end repeat\n'
                'end tell'
            )
            self._json({"ok": True, "action": "hard-refresh"})

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

        elif path == "/eightsleep/off":
            try:
                _8s_put(f"/devices/{_8s_config()['device_id']}", {"leftHeatingLevel": 0, "rightHeatingLevel": 0})
                self._json({"ok": True, "action": "off"})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)

        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        pass  # suppress access logs

def _poll_jain_x_progress():
    """Background thread: pull J.A.I.N x-progress.json via SSH every 60s for tight post feedback."""
    import time
    dest = DATA_DIR / "x-progress.json"
    while True:
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=3", "-o", "BatchMode=yes",
                 "-o", "StrictHostKeyChecking=no",
                 "jc_agent@100.121.89.84",
                 "cat /Users/jc_agent/.openclaw/workspace/mission-control/data/x-progress.json"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0 and result.stdout:
                dest.write_bytes(result.stdout)
        except Exception:
            pass
        time.sleep(60)


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
                 "cat /Users/jc_agent/.openclaw/workspace/mission-control/data/jain-brain-feed.json"],
                capture_output=True, timeout=5
            )
            if result.returncode == 0 and result.stdout:
                dest.write_bytes(result.stdout)
        except Exception:
            pass
        time.sleep(30)



def supabase_command_polling_enabled() -> bool:
    return os.environ.get("MISSION_CONTROL_SUPABASE_COMMANDS", "").lower() in {"1", "true", "yes", "on"}


def _poll_supabase_commands():
    """Poll Supabase agent_comms for phone commands (refresh, nightmode).
    Server-side polling — works even when Safari is backgrounded.
    """
    import time
    SUPABASE_URL = "https://cdzaeptrggczynijegls.supabase.co"
    SUPABASE_KEY = "sb_publishable_S6K05dWzCylIOjEOM1TcEQ_FUG1DAJ6"
    seen_ids = set()
    ready = False

    while True:
        try:
            url = SUPABASE_URL + "/rest/v1/agent_comms?agent=eq.phone&status=eq.pending&order=id.desc&limit=10"
            req = urllib.request.Request(url, headers={
                "apikey": SUPABASE_KEY,
                "Authorization": "Bearer " + SUPABASE_KEY,
            })
            with urllib.request.urlopen(req, timeout=8) as r:
                rows = json.load(r)

            if not ready:
                for row in rows:
                    seen_ids.add(row["id"])
                ready = True
                time.sleep(3)
                continue

            for row in rows:
                rid = row["id"]
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                cmd = row.get("tool") or ""

                if cmd in ("refresh", "hard-refresh"):
                    # hard-refresh: full page reload; refresh: forceUpdate() data refresh
                    js = "location.reload(true)" if cmd == "hard-refresh" else "typeof forceUpdate==='function' && forceUpdate()"
                    run_applescript(
                        'tell application "Safari"\n'
                        f'    set jsCmd to "{js}"\n'
                        '    repeat with w in windows\n'
                        '        repeat with t in tabs of w\n'
                        '            set u to URL of t\n'
                        '            if u contains "mission-control" or u contains "localhost:8765" or u contains "jcubellagent-web.github.io" then\n'
                        '                do JavaScript jsCmd in t\n'
                        '            end if\n'
                        '        end repeat\n'
                        '    end repeat\n'
                        'end tell'
                    )
                    print(f"[remote] {cmd} via Supabase id=" + str(rid), flush=True)

                elif cmd == "nightmode":
                    state = get_state()
                    state["nightMode"] = True
                    set_state(state)
                    (DATA_DIR / "night-mode.flag").write_text("on")
                    run_applescript(
                        'tell application "Safari"\n'
                        '    set frontDoc to document 1\n'
                        '    do JavaScript "typeof enterNightMode===\'function\' && enterNightMode()" in frontDoc\n'
                        'end tell'
                    )
                    print("[remote] nightmode ON via Supabase id=" + str(rid), flush=True)

                elif cmd == "nightmode-off":
                    state = get_state()
                    state["nightMode"] = False
                    set_state(state)
                    flag = DATA_DIR / "night-mode.flag"
                    if flag.exists():
                        flag.unlink()
                    run_applescript(
                        'tell application "Safari"\n'
                        '    set frontDoc to document 1\n'
                        '    do JavaScript "typeof exitNightMode===\'function\' && exitNightMode()" in frontDoc\n'
                        'end tell'
                    )
                    print("[remote] nightmode OFF via Supabase id=" + str(rid), flush=True)

                # Mark done
                try:
                    patch_url = SUPABASE_URL + "/rest/v1/agent_comms?id=eq." + str(rid)
                    patch_req = urllib.request.Request(
                        patch_url,
                        data=json.dumps({"status": "done"}).encode(),
                        headers={
                            "apikey": SUPABASE_KEY,
                            "Authorization": "Bearer " + SUPABASE_KEY,
                            "Content-Type": "application/json",
                            "Prefer": "return=minimal",
                        },
                        method="PATCH"
                    )
                    urllib.request.urlopen(patch_req, timeout=5)
                except Exception:
                    pass

        except Exception:
            pass
        time.sleep(3)


if __name__ == "__main__":
    # Start JAIN brain feed poller in background (real-time, independent of dashboard cron)
    t = threading.Thread(target=_poll_jain_brain_feed, daemon=True)
    t.start()
    print("J.A.I.N brain feed poller started (30s interval)", flush=True)
    t2 = threading.Thread(target=_poll_jain_x_progress, daemon=True)
    t2.start()
    print("J.A.I.N x-progress poller started (60s interval)", flush=True)

    if supabase_command_polling_enabled():
        t3 = threading.Thread(target=_poll_supabase_commands, daemon=True)
        t3.start()
        print("Supabase remote command poller started (3s interval)", flush=True)
    else:
        print("Supabase remote command poller disabled; local-first Brain Feed is active", flush=True)

    class BrainFeedThreadingServer(http.server.ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = BrainFeedThreadingServer(("0.0.0.0", PORT), Handler)
    print(f"Brain Feed + Dashboard server on http://localhost:{PORT}", flush=True)
    server.serve_forever()
