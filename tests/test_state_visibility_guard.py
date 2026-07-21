from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_guard_module():
    path = ROOT / "scripts" / "state_visibility_guard.py"
    spec = importlib.util.spec_from_file_location("state_visibility_guard_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_feed(path: Path, *, active: bool, status: str, updated_at: datetime) -> None:
    path.write_text(json.dumps({
        "active": active,
        "status": status,
        "updatedAt": updated_at.isoformat().replace("+00:00", "Z"),
    }), encoding="utf-8")


def test_inactive_feed_is_valid_durable_state(tmp_path) -> None:
    module = load_guard_module()
    old = datetime.now(timezone.utc) - timedelta(days=2)
    feed = tmp_path / "feed.json"
    write_feed(feed, active=False, status="info", updated_at=old)

    assert module.active_feed_age_minutes(feed, datetime.now(timezone.utc)) is None


def test_stale_active_feed_is_detected(tmp_path) -> None:
    module = load_guard_module()
    old = datetime.now(timezone.utc) - timedelta(minutes=25)
    feed = tmp_path / "feed.json"
    write_feed(feed, active=True, status="working", updated_at=old)

    age = module.active_feed_age_minutes(feed, datetime.now(timezone.utc))
    assert age is not None and age >= 20


def test_invalid_active_feed_shape_fails_closed(tmp_path) -> None:
    module = load_guard_module()
    feed = tmp_path / "feed.json"
    feed.write_text("not-json", encoding="utf-8")

    assert module.active_feed_age_minutes(feed, datetime.now(timezone.utc)) == float("inf")
