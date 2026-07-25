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
    assert result["ok"] is True
    assert result["decision"] == "allow"
    assert result["targetHost"] == "josh2"
    assert result["personalDevice"] is False
    assert result["verificationRequired"] is True
    assert result["maxAttempts"] == 3


def test_jaimes_visible_work_promotes_to_josh2() -> None:
    result = evaluate(target_host="jaimes", surface="computer-use")
    assert result["ok"] is True
    assert result["decision"] == "promote"
    assert result["fromHost"] == "jaimes"
    assert result["targetHost"] == "josh2"


def test_jaimes_headless_dom_work_stays_on_jaimes() -> None:
    result = evaluate(target_host="jaimes", surface="browser-dom")
    assert result["decision"] == "allow"
    assert result["targetHost"] == "jaimes"


def test_retry_budget_is_hard_capped_at_three() -> None:
    config = {**CONFIG, "sessionEngine": {"enabled": True, "maxAttempts": 5}}
    result = evaluate(target_host="jaimes", surface="browser-dom", config=config)
    assert result["maxAttempts"] == 3
