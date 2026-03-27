#!/usr/bin/env python3
"""
Brain Feed Local Server — serves brain-feed.json over HTTP on port 8765
Provides sub-100ms updates to Mission Control when on local network,
bypassing GitHub Pages 60-120s propagation delay.

Runs as a persistent daemon via launchd or cron.
"""
import http.server, json, os, sys
from pathlib import Path
from http import HTTPStatus

PORT = 8765
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

class BrainFeedHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/brain-feed.json", "/brain-feed.json?_=" + self.path.split("=")[-1]):
            bf_path = DATA_DIR / "brain-feed.json"
            try:
                data = bf_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_error(500, str(e))
        elif self.path.startswith("/brain-feed.json"):
            # Handle cache-busting query strings
            bf_path = DATA_DIR / "brain-feed.json"
            try:
                data = bf_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(data)
            except Exception as e:
                self.send_error(500, str(e))
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass  # suppress access logs

if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), BrainFeedHandler)
    print(f"Brain Feed server running on http://localhost:{PORT}/brain-feed.json")
    sys.stdout.flush()
    server.serve_forever()
