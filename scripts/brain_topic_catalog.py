#!/usr/bin/env python3
"""Durable catalog for the J.A.I.N Control Center Brain topic.

Blobs live outside Git. Every item is indexed in SQLite/FTS5. Durable-memory
promotion is explicit and goes through the governed ecosystem registry.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path.home() / "brain"
MAX_TEXT_BYTES = 1_000_000
TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".log", ".yaml", ".yml"}


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_name(value: str) -> str:
    value = Path(value or "file").name
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return (value or "file")[:180]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def connect(root: Path) -> sqlite3.Connection:
    root.mkdir(parents=True, exist_ok=True)
    (root / "blobs").mkdir(exist_ok=True)
    con = sqlite3.connect(root / "catalog.sqlite3")
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS items (
          id TEXT PRIMARY KEY, created_at TEXT NOT NULL, chat_id TEXT,
          thread_id TEXT, message_id TEXT, title TEXT NOT NULL,
          description TEXT NOT NULL, category TEXT NOT NULL, tags TEXT NOT NULL,
          source TEXT NOT NULL, promoted_candidate_id TEXT DEFAULT '',
          UNIQUE(chat_id, thread_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS attachments (
          id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL,
          sha256 TEXT NOT NULL, original_name TEXT NOT NULL, stored_path TEXT NOT NULL,
          mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
          FOREIGN KEY(item_id) REFERENCES items(id), UNIQUE(item_id, sha256)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
          item_id UNINDEXED, title, description, tags, extracted_text
        );
        """
    )
    return con


def classify(mimes: list[str], description: str) -> str:
    groups = set()
    for mime in mimes:
        major = mime.split("/", 1)[0]
        groups.add(major if major in {"image", "audio", "video", "text"} else "document")
    if not groups:
        return "link" if re.search(r"https?://", description) else "text"
    return next(iter(groups)) if len(groups) == 1 else "mixed"


def extracted_text(path: Path) -> str:
    if path.suffix.lower() not in TEXT_EXTS or path.stat().st_size > MAX_TEXT_BYTES:
        return ""
    try:
        return path.read_text(errors="replace")[:MAX_TEXT_BYTES]
    except OSError:
        return ""


def ingest(args: argparse.Namespace) -> dict:
    root = Path(args.root).expanduser().resolve()
    con = connect(root)
    existing = con.execute(
        "SELECT id FROM items WHERE chat_id=? AND thread_id=? AND message_id=?",
        (args.chat_id, args.thread_id, args.message_id),
    ).fetchone() if args.message_id else None
    if existing:
        return {"ok": True, "status": "duplicate", "item_id": existing["id"]}

    files: list[Path] = []
    for raw in args.media or []:
        path = Path(raw).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"media file not found: {path}")
        files.append(path)

    tags = sorted({t.strip().lower() for t in (args.tags or "").split(",") if t.strip()})
    hashes = [sha256(p) for p in files]
    seed = "|".join([args.chat_id, args.thread_id, args.message_id, args.description, *hashes])
    suffix = hashlib.sha256(seed.encode()).hexdigest()[:8]
    item_id = f"brain-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{suffix}"
    mimes = [mimetypes.guess_type(p.name)[0] or "application/octet-stream" for p in files]
    category = args.category or classify(mimes, args.description)
    title = args.title.strip() if args.title else (args.description.strip().splitlines()[0][:120] if args.description.strip() else (files[0].name if files else "Brain note"))

    con.execute(
        "INSERT INTO items(id,created_at,chat_id,thread_id,message_id,title,description,category,tags,source) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (item_id, utcnow(), args.chat_id, args.thread_id, args.message_id, title, args.description.strip(), category, json.dumps(tags), args.source),
    )
    text_parts = []
    stored = []
    for path, digest, mime in zip(files, hashes, mimes):
        ext = path.suffix.lower()[:16]
        dest_dir = root / "blobs" / digest[:2]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{digest}{ext}"
        if not dest.exists():
            shutil.copy2(path, dest)
        con.execute(
            "INSERT OR IGNORE INTO attachments(item_id,sha256,original_name,stored_path,mime_type,size_bytes) VALUES(?,?,?,?,?,?)",
            (item_id, digest, safe_name(path.name), str(dest), mime, path.stat().st_size),
        )
        text_parts.append(extracted_text(path))
        stored.append({"name": safe_name(path.name), "sha256": digest, "mime": mime, "path": str(dest)})
    con.execute(
        "INSERT INTO items_fts(item_id,title,description,tags,extracted_text) VALUES(?,?,?,?,?)",
        (item_id, title, args.description.strip(), " ".join(tags), "\n".join(x for x in text_parts if x)),
    )
    con.commit()
    return {"ok": True, "status": "stored", "item_id": item_id, "category": category, "tags": tags, "attachments": stored}


def search(args: argparse.Namespace) -> dict:
    con = connect(Path(args.root).expanduser().resolve())
    query = args.query.strip()
    try:
        rows = con.execute(
            "SELECT i.* FROM items_fts f JOIN items i ON i.id=f.item_id WHERE items_fts MATCH ? ORDER BY rank LIMIT ?",
            (query, args.limit),
        ).fetchall()
    except sqlite3.OperationalError:
        like = f"%{query}%"
        rows = con.execute(
            "SELECT * FROM items WHERE title LIKE ? OR description LIKE ? OR tags LIKE ? ORDER BY created_at DESC LIMIT ?",
            (like, like, like, args.limit),
        ).fetchall()
    return {"ok": True, "count": len(rows), "items": [dict(r) for r in rows]}


def show(args: argparse.Namespace) -> dict:
    con = connect(Path(args.root).expanduser().resolve())
    item = con.execute("SELECT * FROM items WHERE id=?", (args.item_id,)).fetchone()
    if not item:
        return {"ok": False, "error": "not-found"}
    files = [dict(r) for r in con.execute("SELECT original_name,sha256,stored_path,mime_type,size_bytes FROM attachments WHERE item_id=?", (args.item_id,))]
    return {"ok": True, "item": dict(item), "attachments": files}


def status(args: argparse.Namespace) -> dict:
    root = Path(args.root).expanduser().resolve()
    con = connect(root)
    count = con.execute("SELECT count(*) FROM items").fetchone()[0]
    files = con.execute("SELECT count(*),coalesce(sum(size_bytes),0) FROM attachments").fetchone()
    return {"ok": True, "root": str(root), "items": count, "attachments": files[0], "bytes": files[1]}


def promote(args: argparse.Namespace) -> dict:
    if not args.confirm_explicit:
        return {"ok": False, "error": "explicit-memory-confirmation-required"}
    root = Path(args.root).expanduser().resolve()
    con = connect(root)
    item = con.execute("SELECT * FROM items WHERE id=?", (args.item_id,)).fetchone()
    if not item:
        return {"ok": False, "error": "not-found"}
    cli = Path.home() / "scripts" / "ecosystem_memory.py"
    command = [
        str(cli), "propose", "--agent", args.agent, "--type", args.memory_type,
        "--subject", args.subject or item["title"], "--predicate", args.predicate,
        "--value", args.value or item["description"], "--owner", args.owner,
        "--visibility", "ecosystem", "--privacy", args.privacy,
        "--source", f"brain-topic:{args.item_id}", "--evidence", args.item_id,
        "--confidence", str(args.confidence),
    ]
    proc = subprocess.run(command, text=True, capture_output=True, timeout=60)
    if proc.returncode != 0:
        return {"ok": False, "error": "registry-proposal-failed", "detail": proc.stderr.strip()[:500]}
    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError:
        response = {"output": proc.stdout.strip()[:1000]}
    candidate = str(response.get("candidateId") or response.get("id") or "")
    con.execute("UPDATE items SET promoted_candidate_id=? WHERE id=?", (candidate, args.item_id))
    con.commit()
    return {"ok": True, "status": "proposed-for-review", "item_id": args.item_id, "registry": response}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=os.environ.get("BRAIN_CATALOG_ROOT", str(DEFAULT_ROOT)))
    sub = p.add_subparsers(dest="command", required=True)
    x = sub.add_parser("ingest")
    x.add_argument("--chat-id", default="")
    x.add_argument("--thread-id", default="")
    x.add_argument("--message-id", default="")
    x.add_argument("--description", default="")
    x.add_argument("--title", default="")
    x.add_argument("--tags", default="")
    x.add_argument("--category", choices=["text", "link", "image", "audio", "video", "document", "mixed"], default="")
    x.add_argument("--source", default="telegram-brain-topic")
    x.add_argument("--media", action="append", default=[])
    x.set_defaults(func=ingest)
    x = sub.add_parser("search"); x.add_argument("query"); x.add_argument("--limit", type=int, default=10); x.set_defaults(func=search)
    x = sub.add_parser("show"); x.add_argument("item_id"); x.set_defaults(func=show)
    x = sub.add_parser("status"); x.set_defaults(func=status)
    x = sub.add_parser("promote")
    x.add_argument("item_id"); x.add_argument("--confirm-explicit", action="store_true")
    x.add_argument("--agent", choices=["joshex", "josh2", "jaimes", "jain"], default="jaimes")
    x.add_argument("--memory-type", choices=["decision", "entity", "episode", "fact", "lesson", "preference", "procedure", "relationship"], default="fact")
    x.add_argument("--subject", default=""); x.add_argument("--predicate", default="brain note")
    x.add_argument("--value", default=""); x.add_argument("--owner", default="josh")
    x.add_argument("--privacy", choices=["private", "internal", "dashboard-safe"], default="private")
    x.add_argument("--confidence", type=float, default=0.98); x.set_defaults(func=promote)
    return p


def main() -> int:
    args = parser().parse_args()
    result = args.func(args)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
