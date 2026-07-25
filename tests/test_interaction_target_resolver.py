from __future__ import annotations

from scripts import interaction_target_resolver as resolver


CONFIG = {
    "sessionEngine": {
        "targetResolution": {
            "minimumConfidence": 0.62,
            "sourceOrder": ["browser-dom", "accessibility", "vision", "coordinates"],
        }
    }
}


def test_resolver_prefers_semantic_dom_candidate() -> None:
    result = resolver.resolve_target(
        {"role": "button", "name": "Continue"},
        [
            {"id": "vision-1", "source": "vision", "role": "button", "name": "Continue", "bounds": [1, 2, 30, 20]},
            {"id": "dom-1", "source": "browser-dom", "role": "button", "name": "Continue", "bounds": [5, 6, 40, 22]},
        ],
        CONFIG,
    )
    assert result["ok"] is True
    assert result["candidateId"] == "dom-1"
    assert result["source"] == "browser-dom"


def test_resolver_rejects_disabled_and_low_confidence_candidates() -> None:
    result = resolver.resolve_target(
        {"role": "button", "name": "Continue"},
        [
            {"id": "disabled", "source": "accessibility", "role": "button", "name": "Continue", "enabled": False, "bounds": [1, 2, 30, 20]},
            {"id": "wrong", "source": "vision", "role": "button", "name": "Cancel", "bounds": [1, 2, 30, 20]},
        ],
        CONFIG,
    )
    assert result["ok"] is False
    assert result["reason"] == "semantic-target-not-confident"


def test_resolver_requires_a_semantic_target() -> None:
    result = resolver.resolve_target({}, [], CONFIG)
    assert result == {"ok": False, "reason": "empty-semantic-target", "candidateCount": 0}


def test_resolver_enforces_source_order_before_driver_confidence() -> None:
    result = resolver.resolve_target(
        {"role": "button", "name": "Continue"},
        [
            {"id": "dom-low", "source": "browser-dom", "role": "button", "name": "Continue", "confidence": 0, "bounds": [0, 0, 10, 10]},
            {"id": "vision-high", "source": "vision", "role": "button", "name": "Continue", "confidence": 1, "bounds": [0, 0, 10, 10]},
        ],
        CONFIG,
    )
    assert result["ok"] is True
    assert result["candidateId"] == "dom-low"


def test_resolver_rejects_equal_semantic_matches_as_ambiguous() -> None:
    result = resolver.resolve_target(
        {"role": "button", "name": "Continue"},
        [
            {"id": "first", "source": "browser-dom", "role": "button", "name": "Continue", "bounds": [0, 0, 10, 10]},
            {"id": "second", "source": "browser-dom", "role": "button", "name": "Continue", "bounds": [20, 0, 10, 10]},
        ],
        CONFIG,
    )
    assert result["ok"] is False
    assert result["reason"] == "semantic-target-ambiguous"


def test_resolver_rejects_candidate_without_stable_id() -> None:
    result = resolver.resolve_target(
        {"role": "button", "name": "Continue"},
        [{"source": "browser-dom", "role": "button", "name": "Continue", "bounds": [0, 0, 10, 10]}],
        CONFIG,
    )
    assert result["ok"] is False
