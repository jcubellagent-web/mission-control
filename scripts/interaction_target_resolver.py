#!/usr/bin/env python3
"""Resolve a semantic UI target across DOM, accessibility, vision, and coordinates.

Inputs and results stay host-local. The resolver never writes target names,
candidate text, selectors, accessibility trees, screenshots, or bounds into
shared telemetry.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "interaction-routing.json"
ALLOWED_SOURCES = {"browser-dom", "accessibility", "vision", "coordinates"}


def load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def normalize(value: Any) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))[:256]


def valid_bounds(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        bounds = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in bounds):
        return None
    if bounds[2] <= 0 or bounds[3] <= 0:
        return None
    return [round(item, 2) for item in bounds]


def source_order(config: dict[str, Any]) -> list[str]:
    engine = config.get("sessionEngine") if isinstance(config.get("sessionEngine"), dict) else {}
    resolution = engine.get("targetResolution") if isinstance(engine.get("targetResolution"), dict) else {}
    values = [str(value) for value in resolution.get("sourceOrder", []) if str(value) in ALLOWED_SOURCES]
    return values or ["browser-dom", "accessibility", "vision", "coordinates"]


def minimum_confidence(config: dict[str, Any]) -> float:
    engine = config.get("sessionEngine") if isinstance(config.get("sessionEngine"), dict) else {}
    resolution = engine.get("targetResolution") if isinstance(engine.get("targetResolution"), dict) else {}
    try:
        return max(0.0, min(1.0, float(resolution.get("minimumConfidence", 0.62))))
    except (TypeError, ValueError):
        return 0.62


def ambiguity_margin(config: dict[str, Any]) -> float:
    engine = config.get("sessionEngine") if isinstance(config.get("sessionEngine"), dict) else {}
    resolution = engine.get("targetResolution") if isinstance(engine.get("targetResolution"), dict) else {}
    try:
        return max(0.0, min(0.25, float(resolution.get("ambiguityMargin", 0.03))))
    except (TypeError, ValueError):
        return 0.03


def score_candidate(target: dict[str, Any], candidate: dict[str, Any], order: list[str]) -> float:
    source = str(candidate.get("source") or "")
    if source not in order or source not in ALLOWED_SOURCES:
        return -1.0
    if candidate.get("visible") is False or candidate.get("enabled") is False:
        return -1.0
    bounds = valid_bounds(candidate.get("bounds"))
    if bounds is None:
        return -1.0

    score = max(0.0, 0.20 - 0.035 * order.index(source))
    target_role = normalize(target.get("role"))
    candidate_role = normalize(candidate.get("role"))
    if target_role:
        if target_role != candidate_role:
            return -1.0
        score += 0.28

    target_name = normalize(target.get("name"))
    candidate_name = normalize(candidate.get("name"))
    if target_name:
        if target_name == candidate_name:
            score += 0.42
        elif target_name in candidate_name or candidate_name in target_name:
            score += 0.24
        else:
            return -1.0

    requested_traits = {normalize(value) for value in target.get("traits", []) if normalize(value)}
    candidate_traits = {normalize(value) for value in candidate.get("traits", []) if normalize(value)}
    if requested_traits:
        matched = len(requested_traits & candidate_traits)
        if matched == 0:
            return -1.0
        score += 0.10 * (matched / len(requested_traits))

    try:
        driver_confidence = max(0.0, min(1.0, float(candidate.get("confidence", 1.0))))
    except (TypeError, ValueError):
        driver_confidence = 0.0
    score += 0.10 * driver_confidence
    return min(1.0, score)


def resolve_target(
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    if not normalize(target.get("role")) and not normalize(target.get("name")) and not target.get("traits"):
        return {"ok": False, "reason": "empty-semantic-target", "candidateCount": len(candidates)}
    order = source_order(config)
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            continue
        if not str(candidate.get("id") or "").strip():
            continue
        score = score_candidate(target, candidate, order)
        if score >= 0:
            scored.append((score, index, candidate))
    threshold = minimum_confidence(config)
    best_seen = max((row[0] for row in scored), default=0.0)
    for source in order:
        eligible = [row for row in scored if row[2].get("source") == source and row[0] >= threshold]
        eligible.sort(key=lambda row: (-row[0], row[1]))
        if not eligible:
            continue
        if len(eligible) > 1 and eligible[0][0] - eligible[1][0] <= ambiguity_margin(config):
            return {
                "ok": False,
                "reason": "semantic-target-ambiguous",
                "source": source,
                "candidateCount": len(candidates),
                "bestConfidence": round(eligible[0][0], 3),
            }
        score, _, candidate = eligible[0]
        return {
            "ok": True,
            "source": source,
            "candidateId": str(candidate.get("id"))[:256],
            "bounds": valid_bounds(candidate.get("bounds")),
            "confidence": round(score, 3),
            "candidateCount": len(candidates),
        }
    return {
        "ok": False,
        "reason": "semantic-target-not-confident",
        "candidateCount": len(candidates),
        "bestConfidence": round(best_seen, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-file", type=Path, required=True)
    parser.add_argument("--candidates-file", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        target = load_object(args.target_file)
        candidate_payload = json.loads(args.candidates_file.read_text(encoding="utf-8"))
        candidates = candidate_payload.get("candidates", []) if isinstance(candidate_payload, dict) else candidate_payload
        if not isinstance(candidates, list):
            raise ValueError("candidates file must contain a list or {\"candidates\": [...]} object")
        result = resolve_target(target, candidates, load_object(args.config))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        result = {"ok": False, "reason": "invalid-local-input", "errorType": type(exc).__name__}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
