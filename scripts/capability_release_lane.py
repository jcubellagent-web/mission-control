#!/usr/bin/env python3
"""Score and prepare high-value OpenCLAW/Hermes releases without promotion."""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


SOURCE_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("MISSION_CONTROL_RUNTIME_ROOT") or SOURCE_ROOT).expanduser().resolve()
DEFAULT_CONFIG = SOURCE_ROOT / "config" / "capability-release-lane.json"
DEFAULT_WATCH = ROOT / "data" / "capability-watch.json"
ACTIVE_STATUSES = {"fast-track", "test", "routine", "candidate-prepared", "blocked-prerequisite", "prepare-failed"}


def now_dt() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or now_dt()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def resolve_runtime_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def resolve_source_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else SOURCE_ROOT / path


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def append_events(path: Path, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    addition = "".join(json.dumps(event, sort_keys=True) + "\n" for event in events)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(existing + addition, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def history_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def clean_text(value: Any, limit: int = 7000) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", str(value or ""))
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = " ".join(text.split())
    return text[:limit]


def normalize_version(value: Any) -> str:
    return str(value or "").strip().removeprefix("v")


def hermes_installed_tag(version_text: str) -> str:
    match = re.search(r"Hermes Agent v[^\s]+\s+\((\d{4})\.(\d{1,2})\.(\d{1,2})\)", version_text or "")
    if not match:
        return ""
    return ".".join(str(int(part)) for part in match.groups())


def release_rows(watch: dict[str, Any]) -> list[dict[str, Any]]:
    sources = watch.get("sources") if isinstance(watch.get("sources"), dict) else {}
    npm = (sources.get("openclawNpm") or {}).get("distTags") or {}
    openclaw = sources.get("openclawUpdate") or {}
    hermes = sources.get("hermesUpdate") or {}
    specs = [
        {
            "product": "openclaw", "channel": "stable",
            "installed": str(openclaw.get("currentVersion") or ""),
            "target": str(npm.get("latest") or openclaw.get("latestVersion") or ""),
            "release": sources.get("openclawLatestRelease") or {},
        },
        {
            "product": "openclaw", "channel": "preview",
            "installed": str(openclaw.get("currentVersion") or ""),
            "target": str(npm.get("beta") or ""),
            "release": sources.get("openclawPreviewRelease") or {},
        },
        {
            "product": "hermes", "channel": "stable",
            "installed": hermes_installed_tag(str(hermes.get("version") or "")),
            "target": normalize_version((sources.get("hermesLatestRelease") or {}).get("tag")),
            "release": sources.get("hermesLatestRelease") or {},
        },
    ]
    return [row for row in specs if row["target"]]


def score_release(text: str, config: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    lowered = clean_text(text).lower()
    matches: list[dict[str, Any]] = []
    score = 0
    for signal in config.get("signals") or []:
        patterns = [str(pattern).lower() for pattern in signal.get("patterns") or []]
        matched = sorted({pattern for pattern in patterns if pattern and pattern in lowered})
        if matched:
            weight = int(signal.get("weight") or 0)
            score += weight
            matches.append({"id": signal.get("id"), "weight": weight, "matches": matched[:6]})
    return score, matches


def classify(score: int, thresholds: dict[str, Any]) -> str:
    if score >= int(thresholds.get("fastTrack") or 7):
        return "fast-track"
    if score >= int(thresholds.get("test") or 4):
        return "test"
    return "routine"


def due_at(track: str, config: dict[str, Any], start: dt.datetime) -> str:
    slas = config.get("slas") or {}
    hours = {
        "fast-track": int(slas.get("fastTrackCandidateHours") or 12),
        "test": int(slas.get("testCandidateHours") or 24),
        "routine": int(slas.get("routineReviewHours") or 72),
    }.get(track, int(slas.get("routineReviewHours") or 72))
    return iso(start + dt.timedelta(hours=hours))


def exact_release_match(row: dict[str, Any]) -> bool:
    release = row.get("release") if isinstance(row.get("release"), dict) else {}
    return bool(release.get("ok") and normalize_version(release.get("tag")) == normalize_version(row.get("target")))


def run(command: list[str], timeout: int = 900) -> dict[str, Any]:
    try:
        proc = subprocess.run(command, cwd=SOURCE_ROOT, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": clean_text(exc, 500)}
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    payload = None
    try:
        payload = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        pass
    return {"ok": proc.returncode == 0, "code": proc.returncode, "detail": clean_text(output, 800), "json": payload}


def unmet_requirements(product: str, notes: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    product_config = (config.get("products") or {}).get(product) or {}
    lowered = clean_text(notes).lower()
    failures: list[dict[str, Any]] = []
    for requirement in product_config.get("requirements") or []:
        patterns = [str(value).lower() for value in requirement.get("whenPatterns") or []]
        if patterns and not any(pattern in lowered for pattern in patterns):
            continue
        executable = str(requirement.get("executable") or "")
        allowed = {str(value) for value in config.get("allowedPrerequisiteExecutables") or []}
        if not executable or executable not in allowed or not shutil.which(executable):
            failures.append({"id": requirement.get("id"), "minimumMajor": int(requirement.get("minimumMajor") or 0), "observedMajor": None})
            continue
        result = run([executable, "--version"], timeout=30)
        major_match = re.search(r"(\d+)", str(result.get("detail") or ""))
        observed = int(major_match.group(1)) if major_match else None
        minimum = int(requirement.get("minimumMajor") or 0)
        if not result.get("ok") or observed is None or observed < minimum:
            failures.append({"id": requirement.get("id"), "minimumMajor": minimum, "observedMajor": observed})
    return failures


def should_prepare(assessment: dict[str, Any], config: dict[str, Any]) -> bool:
    if not config.get("enabled") or not config.get("automaticCandidatePreparation"):
        return False
    if assessment["status"] not in {"fast-track", "test"}:
        return False
    channels = config.get("channels") or {}
    if assessment["channel"] == "preview":
        return bool(channels.get("previewAutoPrepare")) and assessment["status"] == "fast-track"
    return bool(channels.get("stableAutoPrepare"))


def already_prepared(rows: list[dict[str, Any]], product: str, channel: str, release: str) -> bool:
    for row in reversed(rows):
        if not (
            row.get("event") == "candidate-prepared"
            and row.get("product") == product
            and row.get("channel") == channel
            and row.get("release") == release
        ):
            continue
        manifest_path = Path(str(row.get("manifest") or ""))
        manifest = read_json(manifest_path, {}) if manifest_path.is_file() else {}
        return bool(manifest) and Path(str(manifest.get("sandbox") or "")).is_dir()
    return False


def acquire_lock(lock: Any, wait_seconds: float = 30.0) -> bool:
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)


def prepare_candidate(assessment: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    product_config = (config.get("products") or {}).get(assessment["product"]) or {}
    pipeline = resolve_source_path(str(product_config.get("pipeline") or ""))
    if not pipeline.is_file():
        return {"ok": False, "detail": "candidate pipeline is unavailable"}
    target = assessment["release"]
    if assessment["product"] == "hermes":
        target = f"v{normalize_version(target)}"
    evidence_dir = resolve_runtime_path(str(product_config.get("stableEvidenceDir") or f"data/{assessment['product']}-update-evidence"))
    result = run([sys.executable, str(pipeline), "prepare", "--target", target, "--evidence-dir", str(evidence_dir)])
    payload = result.get("json") if isinstance(result.get("json"), dict) else {}
    return {"ok": bool(result.get("ok")) and bool(payload.get("manifest")), "manifest": payload.get("manifest"), "detail": result.get("detail")}


def assess(watch: dict[str, Any], config: dict[str, Any], previous: dict[str, Any], history: list[dict[str, Any]], prepare: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started = now_dt()
    events: list[dict[str, Any]] = []
    previous_rows = {
        (str(row.get("product")), str(row.get("channel"))): row
        for row in previous.get("assessments") or [] if isinstance(row, dict)
    }
    assessments: list[dict[str, Any]] = []
    for row in release_rows(watch):
        release = row.get("release") if isinstance(row.get("release"), dict) else {}
        release_id = normalize_version(row["target"])
        notes = clean_text(f"{release.get('name') or ''} {release.get('notes') or ''}")
        score, signals = score_release(notes, config)
        track = "adopted" if row["channel"] == "stable" and normalize_version(row["installed"]) == release_id else classify(score, config.get("thresholds") or {})
        assessment = {
            "product": row["product"], "channel": row["channel"], "release": release_id,
            "installed": normalize_version(row["installed"]), "status": track, "score": score,
            "signals": signals, "publishedAt": release.get("publishedAt"), "url": release.get("url"),
            "candidateDueAt": None if track == "adopted" else due_at(track, config, started), "productionPromotion": "manual-only",
        }
        if not exact_release_match(row):
            assessment["status"] = "metadata-mismatch"
            assessment["blockers"] = ["exact-release-metadata"]
        blockers = unmet_requirements(row["product"], notes, config) if assessment["status"] != "adopted" else []
        if blockers:
            assessment["status"] = "blocked-prerequisite"
            assessment["prerequisites"] = blockers
        previous_row = previous_rows.get((row["product"], row["channel"]))
        if previous_row and previous_row.get("release") != release_id and previous_row.get("status") in ACTIVE_STATUSES:
            events.append({
                "event": "candidate-superseded", "time": iso(), "product": row["product"], "channel": row["channel"],
                "release": previous_row.get("release"), "supersededBy": release_id,
            })
        same_release = already_prepared(history + events, row["product"], row["channel"], release_id)
        if same_release:
            assessment["status"] = "candidate-prepared"
            assessment["idempotent"] = True
        elif prepare and not blockers and should_prepare(assessment, config) and assessment["status"] != "metadata-mismatch":
            prepared = prepare_candidate(assessment, config)
            assessment["status"] = "candidate-prepared" if prepared.get("ok") else "prepare-failed"
            assessment["manifest"] = prepared.get("manifest")
            if not prepared.get("ok"):
                assessment["failure"] = prepared.get("detail")
            events.append({
                "event": assessment["status"], "time": iso(), "product": row["product"], "channel": row["channel"],
                "release": release_id, "manifest": prepared.get("manifest"),
            })
        if not previous_row or any(previous_row.get(key) != assessment.get(key) for key in ("release", "status", "score")):
            events.append({
                "event": "release-assessed", "time": iso(), "product": row["product"], "channel": row["channel"],
                "release": release_id, "status": assessment["status"], "score": score,
            })
        assessments.append(assessment)
    fast = sum(row.get("status") == "fast-track" for row in assessments)
    prepared_count = sum(row.get("status") == "candidate-prepared" for row in assessments)
    blocked = sum(str(row.get("status") or "").startswith(("blocked", "metadata", "prepare-failed")) for row in assessments)
    status = "attention" if blocked else "watch" if fast or prepared_count else "ok"
    state = {
        "version": 1, "updatedAt": iso(), "status": status,
        "summary": f"{fast} fast-track, {prepared_count} prepared, {blocked} blocked; production promotion remains manual.",
        "slas": config.get("slas") or {}, "assessments": assessments,
        "automaticCandidatePreparation": bool(config.get("automaticCandidatePreparation")),
        "automaticProductionPromotion": False, "privacy": "dashboard-safe metadata only",
    }
    return state, events


def update_watch(path: Path, state: dict[str, Any]) -> None:
    watch = read_json(path, {})
    if not isinstance(watch, dict):
        return
    watch["fastLane"] = state
    base = str(watch.get("summary") or "Capability Watch refreshed.").split(" Fast lane:", 1)[0]
    watch["summary"] = f"{base} Fast lane: {state['summary']}"
    atomic_write(path, watch)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--watch", type=Path, default=DEFAULT_WATCH)
    parser.add_argument("--no-prepare", action="store_true")
    args = parser.parse_args()
    config = read_json(args.config, {})
    watch = read_json(args.watch, {})
    if not isinstance(config, dict) or not isinstance(watch, dict):
        raise SystemExit("Capability lane config or watch payload is unavailable")
    state_path = resolve_runtime_path(str(config.get("statePath") or "data/capability-release-lane.json"))
    history_path = resolve_runtime_path(str(config.get("historyPath") or "data/capability-release-history.jsonl"))
    lock_path = resolve_runtime_path(str(config.get("lockPath") or "data/capability-release-lane.lock"))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        if not acquire_lock(lock):
            print(json.dumps({
                "status": "busy", "updatedAt": iso(),
                "summary": "Capability release lane is already running; retained the last complete state.",
                "automaticProductionPromotion": False,
            }, indent=2))
            return 0
        previous = read_json(state_path, {})
        history = history_rows(history_path)
        state, events = assess(watch, config, previous if isinstance(previous, dict) else {}, history, not args.no_prepare)
        append_events(history_path, events)
        atomic_write(state_path, state)
        update_watch(args.watch, state)
        print(json.dumps(state, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
