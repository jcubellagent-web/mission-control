from __future__ import annotations

from scripts import interaction_route_guard as guard


CONFIG = {
    "personalMacFallback": {
        "personalHost": "joshex",
        "defaultVisibleHost": "josh2",
        "backgroundHost": "jaimes",
        "requireExplicitAcknowledgement": True,
        "allowedReasons": ["personal-account", "oauth", "local-file"],
    }
}


def evaluate(**overrides):
    values = {
        "target_host": "joshex",
        "surface": "computer-use",
        "reason": "",
        "private_context": False,
        "acknowledged": False,
        "config": CONFIG,
    }
    values.update(overrides)
    return guard.evaluate(**values)


def test_personal_mac_visible_work_reroutes_by_default() -> None:
    result = evaluate()
    assert result["ok"] is False
    assert result["decision"] == "reroute"
    assert result["targetHost"] == "josh2"
    assert result["alert"] is True


def test_private_exception_requires_explicit_acknowledgement() -> None:
    result = evaluate(reason="oauth", private_context=True)
    assert result["ok"] is False
    assert result["decision"] == "acknowledgement-required"
    assert result["targetHost"] == "joshex"


def test_acknowledged_canonical_exception_allows_personal_mac() -> None:
    result = evaluate(reason="personal-account", private_context=True, acknowledged=True)
    assert result["ok"] is True
    assert result["decision"] == "allow-exception"
    assert result["alert"] is True


def test_dedicated_host_is_allowed_without_personal_alert() -> None:
    result = evaluate(target_host="josh2", surface="browser-visual")
    assert result == {
        "ok": True,
        "decision": "allow",
        "targetHost": "josh2",
        "surface": "browser-visual",
        "personalDevice": False,
        "alert": False,
    }
