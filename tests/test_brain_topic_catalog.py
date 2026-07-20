import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = (HERE.parent / "scripts" / "brain_topic_catalog.py") if (HERE.parent / "scripts" / "brain_topic_catalog.py").exists() else (HERE / "brain_topic_catalog.py")


def run(root, *args):
    p = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root), *args], text=True, capture_output=True)
    return p.returncode, json.loads(p.stdout)


def test_ingest_search_dedupe_and_copy(tmp_path):
    media = tmp_path / "note.txt"
    media.write_text("alpha durable sample")
    root = tmp_path / "brain"
    argv = ["ingest", "--chat-id", "-100", "--thread-id", "57", "--message-id", "9", "--description", "Project alpha decision", "--tags", "project,alpha", "--media", str(media)]
    code, first = run(root, *argv)
    assert code == 0 and first["status"] == "stored"
    assert Path(first["attachments"][0]["path"]).read_text() == "alpha durable sample"
    code, second = run(root, *argv)
    assert code == 0 and second == {"ok": True, "status": "duplicate", "item_id": first["item_id"]}
    code, found = run(root, "search", "alpha")
    assert code == 0 and found["count"] == 1
    assert found["items"][0]["id"] == first["item_id"]


def test_text_only_and_promotion_fail_closed(tmp_path):
    root = tmp_path / "brain"
    code, item = run(root, "ingest", "--message-id", "10", "--description", "Remember my concise formatting preference")
    assert code == 0 and item["category"] == "text"
    code, blocked = run(root, "promote", item["item_id"])
    assert code == 1 and blocked["error"] == "explicit-memory-confirmation-required"
    code, status = run(root, "status")
    assert code == 0 and status["items"] == 1 and status["attachments"] == 0
