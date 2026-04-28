#!/usr/bin/env python3
"""Convert a Telegram voice/audio file into a structured Josh task envelope.

This is intentionally a local entrypoint: OpenClaw/Telegram can pass an inbound
voice file here, then a follow-up agent can decide whether the text becomes a
memory task, calendar hold, JAIMES ask, or Mission Control note.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parents[1]
OUTBOX = WORKSPACE / "memory" / "voice-router"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def classify(text: str) -> dict[str, Any]:
    t = text.lower()
    intents: list[str] = []
    if any(w in t for w in ["calendar", "schedule", "meeting", "appointment", "tomorrow", "today"]):
        intents.append("calendar")
    if any(w in t for w in ["jaimes", "sorare", "lineup", "model", "ml"]):
        intents.append("jaimes")
    if any(w in t for w in ["mission control", "dashboard", "tile", "card", "visual"]):
        intents.append("mission-control")
    if any(w in t for w in ["remember", "note", "todo", "task", "remind"]):
        intents.append("task")
    if not intents:
        intents.append("inbox")
    priority = "high" if any(w in t for w in ["urgent", "asap", "now", "important"]) else "normal"
    title = re.sub(r"\s+", " ", text.strip())[:96] or "Voice note"
    return {"title": title, "intents": intents, "priority": priority}


def transcribe_with_whisper(path: Path) -> str:
    candidates = [
        ["whisper", str(path), "--model", "base", "--output_format", "txt", "--output_dir", str(OUTBOX)],
        ["python3", "-m", "whisper", str(path), "--model", "base", "--output_format", "txt", "--output_dir", str(OUTBOX)],
    ]
    last_error = ""
    for cmd in candidates:
        try:
            subprocess.run(cmd, check=True, text=True, capture_output=True, timeout=180)
            txt = OUTBOX / f"{path.stem}.txt"
            if txt.exists():
                return txt.read_text().strip()
        except Exception as exc:  # noqa: BLE001 - command availability varies by host
            last_error = str(exc)
    raise RuntimeError(f"whisper transcription unavailable: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path, help="Voice/audio file path")
    parser.add_argument("--transcript", help="Use provided transcript instead of running Whisper")
    parser.add_argument("--source", default="telegram")
    parser.add_argument("--chat-id", default="")
    args = parser.parse_args()

    OUTBOX.mkdir(parents=True, exist_ok=True)
    media = args.media.expanduser().resolve()
    if not media.exists() and not args.transcript:
        raise SystemExit(f"missing media file: {media}")

    transcript = args.transcript.strip() if args.transcript else transcribe_with_whisper(media)
    routing = classify(transcript)
    envelope = {
        "createdAt": utc_now(),
        "source": args.source,
        "chatId": args.chat_id,
        "mediaPath": str(media) if media.exists() else None,
        "transcript": transcript,
        "routing": routing,
        "status": "queued",
    }
    safe_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUTBOX / f"voice-task-{safe_ts}.json"
    out.write_text(json.dumps(envelope, indent=2) + "\n")
    print(json.dumps({"ok": True, "path": str(out), "routing": routing}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1)
