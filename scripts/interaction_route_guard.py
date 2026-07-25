#!/usr/bin/env python3
"""Fail-closed host routing guard for visible browser and desktop work.

The result is deliberately dashboard-safe: it carries only a canonical host,
surface, reason code, and routing decision—not task text or account content.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "interaction-routing.json"
VISIBLE_SURFACES = {"browser-dom", "browser-visual", "desktop-ui", "computer-use"}
JAIMES_VISIBLE_SURFACES = {"browser-visual", "desktop-ui", "computer-use"}


def load_config(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def evaluate(
    *,
    target_host: str,
    surface: str,
    reason: str,
    private_context: bool,
    acknowledged: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    policy = config.get("personalMacFallback") if isinstance(config.get("personalMacFallback"), dict) else {}
    personal_host = str(policy.get("personalHost") or "joshex")
    default_host = str(policy.get("defaultVisibleHost") or "josh2")
    background_host = str(policy.get("backgroundHost") or "jaimes")
    allowed_reasons = {str(value) for value in policy.get("allowedReasons", []) if isinstance(value, str)}
    visible = surface in VISIBLE_SURFACES
    engine = config.get("sessionEngine") if isinstance(config.get("sessionEngine"), dict) else {}
    max_attempts = max(1, min(3, int(engine.get("maxAttempts") or 3)))

    reliability = {
        "sessionRequired": engine.get("enabled") is True,
        "verificationRequired": engine.get("verificationRequired") is not False,
        "maxAttempts": max_attempts,
        "operatorControl": engine.get("enabled") is True,
        "metadataOnlyReceipts": True,
    }

    if target_host == background_host and surface in JAIMES_VISIBLE_SURFACES:
        return {
            "ok": True,
            "decision": "promote",
            "targetHost": default_host,
            "fromHost": background_host,
            "surface": surface,
            "personalDevice": False,
            "alert": False,
            "reason": "dedicated-visible-host",
            **reliability,
        }

    if target_host != personal_host or not visible:
        return {
            "ok": True,
            "decision": "allow",
            "targetHost": target_host,
            "surface": surface,
            "personalDevice": False,
            "alert": False,
            **reliability,
        }

    valid_exception = private_context and reason in allowed_reasons
    if not valid_exception:
        return {
            "ok": False,
            "decision": "reroute",
            "targetHost": default_host if surface in VISIBLE_SURFACES else background_host,
            "surface": surface,
            "personalDevice": True,
            "alert": True,
            "reason": "personal-device-not-required",
            **reliability,
        }

    if policy.get("requireExplicitAcknowledgement") is True and not acknowledged:
        return {
            "ok": False,
            "decision": "acknowledgement-required",
            "targetHost": personal_host,
            "surface": surface,
            "personalDevice": True,
            "alert": True,
            "reason": reason,
            **reliability,
        }

    return {
        "ok": True,
        "decision": "allow-exception",
        "targetHost": personal_host,
        "surface": surface,
        "personalDevice": True,
        "alert": True,
        "reason": reason,
        **reliability,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Guard personal-Mac browser and computer use.")
    parser.add_argument("--target-host", required=True, choices=("josh2", "jaimes", "joshex"))
    parser.add_argument("--surface", required=True, choices=tuple(sorted(VISIBLE_SURFACES | {"semantic-operation"})))
    parser.add_argument("--reason", default="")
    parser.add_argument("--private-context", action="store_true")
    parser.add_argument("--acknowledge-personal-device", action="store_true")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    result = evaluate(
        target_host=args.target_host,
        surface=args.surface,
        reason=args.reason,
        private_context=args.private_context,
        acknowledged=args.acknowledge_personal_device,
        config=load_config(args.config),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 3 if result.get("decision") == "acknowledgement-required" else 2


if __name__ == "__main__":
    raise SystemExit(main())
