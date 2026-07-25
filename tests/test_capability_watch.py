from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "capability_watch.py"
SPEC = importlib.util.spec_from_file_location("capability_watch", MODULE_PATH)
assert SPEC and SPEC.loader
watch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch)


def test_release_summary_excludes_release_body() -> None:
    result = watch.release_summary({"ok": True, "status": "ok", "data": {"tag_name": "v1", "name": "Release", "published_at": "2026-07-25T00:00:00Z", "html_url": "https://example.test/release", "body": "unbounded release notes"}})
    assert result["tag"] == "v1"
    assert "body" not in result
    assert "unbounded" not in str(result)
