#!/usr/bin/env python3
"""Poll Hermes state.db and archive posts from the configured Brain topic."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
CONFIG = HOME / "brain" / "config.json"
STATE = HOME / "brain" / "watcher_state.json"
DB = HOME / ".hermes" / "state.db"
CATALOG = HOME / ".hermes" / "scripts" / "brain_topic_catalog.py"
ATTACHMENT_RE = re.compile(r"\[(?:image|audio|voice|video|document|file)[^\]]*saved at:\s*([^\]]+)\]", re.I)
SENDER_RE = re.compile(r"^\[J\|(\d+)\]\s*", re.M)


def load(path: Path, fallback):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return fallback


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(tmp, path)


def parse_content(content: str) -> tuple[str, list[str], str]:
    sender = ""
    match = SENDER_RE.search(content or "")
    if match:
        sender = match.group(1)
    media = [m.strip() for m in ATTACHMENT_RE.findall(content or "")]
    text = SENDER_RE.sub("", content or "", count=1)
    text = ATTACHMENT_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, media, sender


def memory_type(text: str) -> str:
    lower = text.lower()
    if "prefer" in lower or "preference" in lower or re.search(r"\b(?:likes?|dislikes?|wants?)\b", lower):
        return "preference"
    if "decision" in lower or lower.startswith("decided"):
        return "decision"
    if "procedure" in lower or "steps" in lower or "workflow" in lower:
        return "procedure"
    if "lesson" in lower:
        return "lesson"
    return "fact"


def run_once(config_path: Path = CONFIG, state_path: Path = STATE, db_path: Path = DB, catalog_path: Path = CATALOG) -> dict:
    cfg = load(config_path, {})
    chat_id = str(cfg.get("chat_id") or "")
    thread_id = str(cfg.get("thread_id") or "")
    allowed = {str(x) for x in cfg.get("allowed_sender_ids", ["6218150306"])}
    if not chat_id or not thread_id:
        return {"ok": True, "status": "unconfigured", "processed": 0}
    state = load(state_path, {"last_db_message_id": 0})
    last_id = int(state.get("last_db_message_id") or 0)
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT m.id,m.content,m.platform_message_id,s.chat_id,s.thread_id
        FROM messages m JOIN sessions s ON s.id=m.session_id
        WHERE m.id>? AND m.role='user' AND s.chat_id=? AND s.thread_id=?
        ORDER BY m.id
        """,
        (last_id, chat_id, thread_id),
    ).fetchall()
    processed = 0
    promoted = 0
    for row in rows:
        text, media, sender = parse_content(str(row["content"] or ""))
        state["last_db_message_id"] = int(row["id"])
        if sender and sender not in allowed:
            continue
        message_id = str(row["platform_message_id"] or row["id"])
        catalog_root = str(cfg.get("catalog_root") or (Path.home() / "brain"))
        cmd = [sys.executable, str(catalog_path), "--root", catalog_root, "ingest", "--chat-id", chat_id, "--thread-id", thread_id, "--message-id", message_id, "--description", text]
        for path in media:
            cmd += ["--media", path]
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "catalog ingest failed")
        result = json.loads(proc.stdout)
        processed += int(result.get("status") == "stored")
        explicit = re.match(r"^(?:remember|memory)\s*:\s*(.+)$", text, re.I | re.S)
        if explicit and result.get("item_id"):
            value = explicit.group(1).strip()
            promote = [sys.executable, str(catalog_path), "--root", catalog_root, "promote", result["item_id"], "--confirm-explicit", "--agent", "jaimes", "--memory-type", memory_type(value), "--subject", value.splitlines()[0][:120], "--value", value]
            p2 = subprocess.run(promote, text=True, capture_output=True, timeout=120)
            if p2.returncode == 0:
                promoted += 1
        save(state_path, state)
    if rows and not state_path.exists():
        save(state_path, state)
    return {"ok": True, "status": "ready", "processed": processed, "promoted": promoted, "cursor": state.get("last_db_message_id", last_id)}


def main() -> int:
    try:
        result = run_once()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__, "detail": str(exc)[:500]}))
        return 1
    if result.get("processed") or result.get("promoted"):
        print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
