from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_primary_topic_check_derives_every_owner_from_registry():
    checker = load_module(
        "telegram_primary_topics_check_consolidated",
        ROOT / "scripts" / "telegram_primary_topics_check.py",
    )
    registry = json.loads(
        (ROOT / "config" / "telegram-intake-lanes.json").read_text(encoding="utf-8")
    )
    group_id, owners = checker.canonical_topic_authority()
    assert group_id == next(iter(registry["groups"]))
    assert owners == {
        topic_id: row["owner"]
        for topic_id, row in registry["groups"][group_id]["topics"].items()
    }


def test_watchers_have_no_hardcoded_topic_owner_fallbacks():
    for name, symbol in (
        ("josh_telegram_fast_ack.py", "JOSH_CONTROL_CENTER_TOPICS"),
        ("jaimes_telegram_fast_ack.py", "JAIMES_CONTROL_CENTER_TOPICS"),
    ):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert f"{symbol}: set[str] = set()" in source
        assert f'or {symbol}' not in source
        assert "owner_accepts = lambda *_args, **_kwargs: False" in source


def test_inbox_plugin_schema_and_docs_do_not_redeclare_ownership():
    plugin = ROOT / "plugins" / "inbox-coordinator"
    schema = json.loads((plugin / "openclaw.plugin.json").read_text(encoding="utf-8"))
    properties = schema["configSchema"]["properties"]
    assert "registryPath" in properties
    assert not {"chatId", "threadId", "jaimesMentions"} & set(properties)
    readme = (plugin / "README.md").read_text(encoding="utf-8")
    assert "A single registered direct\nmention overrides the topic owner" in readme
    assert "including `@JAIN`" not in readme
