from __future__ import annotations

import time

from scripts import interaction_promotion_broker as broker


def test_empty_broker_poll_is_healthy() -> None:
    result = broker.process_once(lambda _arguments: {"ok": True, "requests": []})
    assert result == {
        "ok": True,
        "checked": 0,
        "leased": 0,
        "released": 0,
        "expired": 0,
        "deferred": 0,
        "errors": 0,
    }


def test_promotion_lease_is_returned_only_through_private_completion(monkeypatch) -> None:
    responses = []
    monkeypatch.setattr(
        broker.control_tower_foreground,
        "begin_lease",
        lambda **_kwargs: {"leaseId": "private-lease", "expiresAt": "2099-01-01T00:00:00Z"},
    )
    monkeypatch.setattr(broker.control_tower_foreground, "publish_display_lease", lambda _payload: None)
    monkeypatch.setattr(broker, "private_complete", lambda request_id, response: responses.append((request_id, response)))
    status = broker.promote_request(
        {
            "requestId": "ipr-test",
            "kind": "promote",
            "owner": "jaimes",
            "purpose": "browser",
            "expiresEpoch": int(time.time()) + 60,
            "expired": False,
        }
    )
    assert status == "leased"
    assert responses == [
        (
            "ipr-test",
            {"status": "leased", "leaseId": "private-lease", "expiresAt": "2099-01-01T00:00:00Z"},
        )
    ]
