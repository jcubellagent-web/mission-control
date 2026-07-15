import argparse
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("josh_telegram_fast_ack", ROOT / "josh_telegram_fast_ack.py")
watcher = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = watcher
SPEC.loader.exec_module(watcher)


def test_send_ack_uses_prompt_reaction_without_message_id_and_does_not_fail_claim():
    event = {"session_id": "session", "ts": "2026-07-15T03:42:21Z", "run_id": "before-dispatch:1", "message_id": "", "prompt": "private request"}
    with patch.object(watcher, "fast_ack_enabled", return_value=True), patch.object(watcher, "send_chat_action"), patch.object(watcher, "send_message_draft"), patch.object(watcher, "send_prompt_reaction", return_value=False) as prompt_reaction, patch.object(watcher, "publish_josh"):
        result = watcher.send_ack(event, model=watcher.DEFAULT_MODEL, dry_run=False, meta={"telegram_chat_id": "-100", "telegram_thread_id": "1"})
    prompt_reaction.assert_called_once_with("private request", meta={"telegram_chat_id": "-100", "telegram_thread_id": "1"})
    assert result["ok"] is True
    assert result["reaction_ok"] is False


def test_claim_inbox_queues_when_ack_explicitly_reports_failure():
    args = argparse.Namespace(run_id="before-dispatch:1", message_id="", chat_id="-100", thread_id="1", session_key="session", dry_run=False)
    submitted = {"ok": True, "stdout": '{"job":{"jobId":"job-1"},"route":{"routeId":"route-1"},"deduplicated":false}'}
    with patch("sys.stdin.read", return_value="private request"), patch.object(watcher, "send_ack", return_value={"ok": False, "key": "card-1", "objective": "Request", "model": "model", "route": "route", "last_card_update_at": "now"}), patch.object(watcher, "run_cmd", return_value=submitted) as run_cmd, patch.object(watcher, "load_json", return_value={}), patch.object(watcher, "save_json"), patch.object(watcher, "publish_josh"):
        result = watcher.claim_inbox(args)
    assert result["status"] == "queued"
    assert any("submit" in call.args[0] for call in run_cmd.call_args_list)
