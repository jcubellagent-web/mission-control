import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "telegram_channel_registry",
    ROOT / "scripts" / "telegram_channel_registry.py",
)
assert SPEC and SPEC.loader
REGISTRY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REGISTRY)


def test_known_topics_preserve_agent_ownership() -> None:
    group = "-1003589561528"
    assert REGISTRY.topic_owner(group, "1") == "josh2"
    assert REGISTRY.topic_owner(group, "17") == "jaimes"
    assert REGISTRY.topic_owner(group, "56") == "jaimes"


def test_new_authorized_topic_defaults_to_josh2() -> None:
    assert REGISTRY.topic_owner("-1003589561528", "999") == "josh2"
    assert REGISTRY.owner_accepts("josh2", "-1003589561528", "999")
    assert not REGISTRY.owner_accepts("jaimes", "-1003589561528", "999")


def test_registry_exposes_all_owned_topics() -> None:
    group = "-1003589561528"
    assert {"1", "18", "21", "22"} <= REGISTRY.topics_for_owner("josh2", group)
    assert {"17", "19", "20", "56"} <= REGISTRY.topics_for_owner("jaimes", group)
