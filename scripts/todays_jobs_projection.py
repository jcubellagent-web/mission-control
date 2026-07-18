#!/usr/bin/env python3
"""Source-discovered schedule inventory and Today's Jobs materialization.

The Control Tower generator owns I/O.  This module intentionally keeps the
schedule parsing and occurrence projection pure so add/delete/disable behavior
can be tested without reaching a live host.
"""
from __future__ import annotations

import datetime as dt
import ast
import hashlib
import json
import plistlib
import re
import shlex
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.parsers.expat import ExpatError
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_TOKEN_TO_INDEX = {
    "MO": 0,
    "MON": 0,
    "TU": 1,
    "TUE": 1,
    "WE": 2,
    "WED": 2,
    "TH": 3,
    "THU": 3,
    "FR": 4,
    "FRI": 4,
    "SA": 5,
    "SAT": 5,
    "SU": 6,
    "SUN": 6,
}

METADATA_FIELDS = {
    "name",
    "description",
    "category",
    "agent",
    "logPath",
    "multiRun",
}


def _text(value: Any, limit: int = 180) -> str:
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _slug(value: Any) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return value[:48] or "job"


def _stable_id(source: str, owner: str, native_key: str) -> str:
    digest = hashlib.sha256(f"{source}\0{owner}\0{native_key}".encode("utf-8")).hexdigest()[:14]
    return f"{_slug(source)}:{_slug(owner)}:{digest}"


def _source_label(source: str, owner: str = "") -> str:
    return {
        "codex_automation": "Codex Automation",
        "launchd": "LaunchAgent",
        "hermes": "Hermes",
        "ecosystem_qa_scheduler": "Canonical QA Scheduler",
        "cron": "J.A.I.N Cron" if owner == "jain" else "Josh Local Cron",
    }.get(source, source.replace("_", " ").title())


def _override_matches(
    override: Mapping[str, Any],
    *,
    source: str,
    owner: str,
    match_text: str,
) -> bool:
    expected = str(override.get("source") or "cron")
    if expected != source:
        return False
    if source == "cron" and bool(override.get("jain")) != (owner == "jain"):
        return False
    pattern = str(
        override.get("automationId")
        or override.get("hermesName")
        or override.get("pattern")
        or ""
    ).strip()
    return bool(pattern and pattern.lower() in match_text.lower())


def apply_metadata_override(
    definition: dict[str, Any], overrides: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Apply presentation metadata without allowing it to create inventory."""
    match_text = str(definition.pop("_matchText", ""))
    source = str(definition.get("source") or "")
    owner = str(definition.get("ownerKey") or "")
    for override in overrides:
        if not _override_matches(override, source=source, owner=owner, match_text=match_text):
            continue
        for field in METADATA_FIELDS:
            value = override.get(field)
            if value is not None and value != "":
                definition[field] = value
        # Keep native schedules authoritative.  A legacy label is only useful
        # when launchctl/Hermes exposes presence but not a machine schedule.
        if not definition.get("schedule") and override.get("schedule"):
            definition["schedule"] = str(override["schedule"])
            definition["scheduleSpec"] = human_schedule_spec(str(override["schedule"]))
        break
    definition.setdefault("description", "")
    definition.setdefault("category", "Other")
    definition.setdefault("agent", "J.A.I.N" if owner == "jain" else "JOSH 2.0")
    return definition


def _command_name(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    candidates = [token for token in tokens if re.search(r"\.(?:py|sh|js|ts)$", token, re.I)]
    token = candidates[0] if candidates else next(
        (item for item in tokens if not item.startswith("-") and "=" not in item),
        "scheduled job",
    )
    stem = Path(token).name
    stem = re.sub(r"\.(?:py|sh|js|ts)$", "", stem, flags=re.I)
    return " ".join(word.capitalize() for word in re.split(r"[_-]+", stem) if word) or "Scheduled Job"


def _command_pattern(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    token = next((item for item in tokens if re.search(r"\.(?:py|sh|js|ts)$", item, re.I)), None)
    if token is None:
        token = next((item for item in tokens if "=" not in item and not item.startswith("-")), "scheduled-job")
    return Path(token).name


def _cron_label(expression: str) -> str:
    macros = {
        "@hourly": "Hourly",
        "@daily": "Daily 12:00 AM ET",
        "@midnight": "Daily 12:00 AM ET",
        "@weekly": "Sun 12:00 AM ET",
        "@reboot": "On boot",
    }
    if expression in macros:
        return macros[expression]
    parts = expression.split()
    if len(parts) != 5:
        return expression
    minute, hour, dom, month, dow = parts
    if minute.startswith("*/") and hour == "*" and dom == month == dow == "*":
        return f"Every {minute[2:]} min"
    if minute == "0" and hour == "*" and dom == month == dow == "*":
        return "Hourly"
    if minute.isdigit() and hour.isdigit() and dom == month == dow == "*":
        when = dt.datetime(2000, 1, 1, int(hour), int(minute)).strftime("%-I:%M %p")
        return f"Daily {when} ET"
    return f"Cron {expression} ET"


def parse_crontab_definitions(
    listing: str,
    *,
    owner: str,
    agent: str,
    overrides: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    seen_native: dict[str, int] = {}
    for raw in str(listing or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", line):
            continue
        if line.startswith("@"):
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            expression, command = parts
        else:
            parts = line.split(None, 5)
            if len(parts) != 6:
                continue
            expression, command = " ".join(parts[:5]), parts[5]
        native_key = command.strip()
        duplicate = seen_native.get(native_key, 0)
        seen_native[native_key] = duplicate + 1
        stable_key = native_key if not duplicate else f"{native_key}\0{expression}\0{duplicate}"
        definition = {
            "definitionId": _stable_id("cron", owner, stable_key),
            "name": _command_name(command),
            "schedule": _cron_label(expression),
            "scheduleSpec": {"kind": "cron", "expression": expression, "timezone": str(ET)},
            "source": "cron",
            "sourceLabel": _source_label("cron", owner),
            "ownerKey": owner,
            "agent": agent,
            "enabled": True,
            "present": True,
            "status": "ok",
            "pattern": _command_pattern(command),
            "_matchText": f"{expression} {command}",
        }
        definitions.append(apply_metadata_override(definition, overrides))
    return definitions


def _rrule_label(rule: str) -> str:
    values = _parse_key_value_rule(rule)
    freq = values.get("FREQ", "").title()
    hour = (values.get("BYHOUR") or "0").split(",")[0]
    minute = (values.get("BYMINUTE") or "0").split(",")[0]
    try:
        when = dt.datetime(2000, 1, 1, int(hour), int(minute)).strftime("%-I:%M %p")
    except ValueError:
        when = "scheduled"
    days = values.get("BYDAY")
    if days:
        readable = "/".join(DAY_NAMES[DAY_TOKEN_TO_INDEX[token[-2:].upper()]] for token in days.split(",") if token[-2:].upper() in DAY_TOKEN_TO_INDEX)
        return f"{readable} {when} ET"
    interval = values.get("INTERVAL")
    if freq == "Daily" and interval and interval != "1":
        return f"Every {interval} days at {when} ET"
    return f"{freq or 'Recurring'} {when} ET"


def discover_codex_automations(
    directory: Path,
    *,
    status_payload: Mapping[str, Any] | None = None,
    overrides: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    statuses = (status_payload or {}).get("automations", {})
    statuses = statuses if isinstance(statuses, Mapping) else {}
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.9 on the dedicated hosts
        tomllib = None  # type: ignore[assignment]
    paths = sorted(directory.glob("*/automation.toml")) if directory.exists() else []
    for path in paths:
        try:
            if tomllib is not None:
                with path.open("rb") as handle:
                    payload = tomllib.load(handle)
            else:
                # Codex automation files keep the identity/schedule fields at
                # top level.  Read only those flat scalar values; prompts and
                # nested target payloads never cross the dashboard boundary.
                payload = {}
                wanted = {"id", "name", "status", "rrule", "created_at"}
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$", line)
                    if not match or match.group(1) not in wanted:
                        continue
                    key, raw = match.groups()
                    try:
                        payload[key] = ast.literal_eval(raw)
                    except (SyntaxError, ValueError):
                        payload[key] = raw.strip().strip('"').strip("'")
        except (OSError, ValueError):
            continue
        automation_id = str(payload.get("id") or path.parent.name)
        rrule = str(payload.get("rrule") or "").strip()
        if not rrule:
            continue
        status = str(payload.get("status") or "PAUSED").upper()
        state = statuses.get(automation_id) if isinstance(statuses.get(automation_id), Mapping) else {}
        active = bool(state.get("active", status == "ACTIVE"))
        definition = {
            "definitionId": _stable_id("codex_automation", "joshex", automation_id),
            "name": _text(payload.get("name") or automation_id, 90),
            "schedule": _rrule_label(rrule),
            "scheduleSpec": {
                "kind": "rrule",
                "rrule": rrule,
                "timezone": str(ET),
                "anchorMs": payload.get("created_at"),
            },
            "source": "codex_automation",
            "sourceLabel": _source_label("codex_automation"),
            "ownerKey": "joshex",
            "agent": "JOSHeX",
            "enabled": active,
            "present": True,
            "status": "ok" if active else "paused",
            "automationId": automation_id,
            "pattern": automation_id,
            "lastRun": state.get("lastRun"),
            "_matchText": f"{automation_id} {payload.get('name', '')}",
        }
        definitions.append(apply_metadata_override(definition, overrides))

    # Dedicated-host generation consumes a dashboard-safe JOSHeX status
    # sidecar when the private Mac's automation directory is intentionally not
    # mounted.  The sidecar must carry its native rrule/schedule; a metadata
    # override alone is never enough to invent an automation.
    known_ids = {row.get("automationId") for row in definitions}
    for automation_id, raw_state in sorted(statuses.items(), key=lambda item: str(item[0])):
        if automation_id in known_ids or not isinstance(raw_state, Mapping) or not raw_state.get("present", True):
            continue
        rrule = str(raw_state.get("rrule") or "").strip()
        schedule = str(raw_state.get("schedule") or "").strip()
        if rrule:
            spec = {"kind": "rrule", "rrule": rrule, "timezone": str(ET), "anchorMs": raw_state.get("createdAt")}
            schedule = schedule or _rrule_label(rrule)
        elif schedule:
            spec = human_schedule_spec(schedule)
        else:
            continue
        active = bool(raw_state.get("active", True))
        definition = {
            "definitionId": _stable_id("codex_automation", "joshex", str(automation_id)),
            "name": _text(raw_state.get("name") or str(automation_id).replace("-", " ").title(), 90),
            "schedule": schedule,
            "scheduleSpec": spec,
            "source": "codex_automation",
            "sourceLabel": _source_label("codex_automation"),
            "ownerKey": "joshex",
            "agent": "JOSHeX",
            "enabled": active,
            "present": True,
            "status": "ok" if active else "paused",
            "automationId": str(automation_id),
            "pattern": str(automation_id),
            "lastRun": raw_state.get("lastRun"),
            "_matchText": f"{automation_id} {raw_state.get('name', '')}",
        }
        definitions.append(apply_metadata_override(definition, overrides))
    return definitions


def _launchd_schedule(payload: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    intervals = payload.get("StartCalendarInterval")
    if isinstance(intervals, Mapping):
        intervals = [dict(intervals)]
    if isinstance(intervals, list) and intervals:
        clean = [dict(item) for item in intervals if isinstance(item, Mapping)]
        labels = []
        for item in clean[:8]:
            hour, minute = int(item.get("Hour", 0)), int(item.get("Minute", 0))
            when = dt.datetime(2000, 1, 1, hour, minute).strftime("%-I:%M %p")
            labels.append(when)
        label = "/".join(labels) + " ET"
        return {"kind": "launchd_calendar", "intervals": clean, "timezone": str(ET)}, label
    seconds = payload.get("StartInterval")
    if isinstance(seconds, (int, float)) and seconds > 0:
        seconds = int(seconds)
        label = f"Every {seconds // 60} min" if seconds % 60 == 0 else f"Every {seconds} sec"
        return {"kind": "interval", "seconds": seconds, "timezone": str(ET)}, label
    return {"kind": "daemon", "timezone": str(ET)}, "Continuous / on boot"


def _launchd_supported(label: str, command: str, overrides: Sequence[Mapping[str, Any]]) -> bool:
    text = f"{label} {command}".lower()
    if label.startswith(("com.josh20.", "com.jaimes.", "com.jain.")):
        return True
    if any(token in text for token in ("/.openclaw/", "mission-control", "control-tower")):
        return True
    return any(_override_matches(row, source="launchd", owner="josh2", match_text=text) for row in overrides)


def discover_launchd_definitions(
    plist_paths: Iterable[Path],
    *,
    active_labels: Iterable[str] = (),
    overrides: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    active = set(active_labels)
    definitions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(set(plist_paths), key=str):
        try:
            with path.open("rb") as handle:
                payload = plistlib.load(handle)
        except (OSError, plistlib.InvalidFileException, ValueError, ExpatError):
            continue
        label = str(payload.get("Label") or path.stem)
        args = payload.get("ProgramArguments") if isinstance(payload.get("ProgramArguments"), list) else []
        command = " ".join(str(item) for item in args) or str(payload.get("Program") or "")
        if label in seen or not _launchd_supported(label, command, overrides):
            continue
        seen.add(label)
        spec, schedule = _launchd_schedule(payload)
        enabled = not bool(payload.get("Disabled", False))
        definition = {
            "definitionId": _stable_id("launchd", "josh2", label),
            "name": " ".join(word.capitalize() for word in re.split(r"[.-]+", label.split(".")[-1]) if word),
            "schedule": schedule,
            "scheduleSpec": spec,
            "source": "launchd",
            "sourceLabel": _source_label("launchd"),
            "ownerKey": "josh2",
            "agent": "JOSH 2.0",
            "enabled": enabled,
            "present": True,
            "active": label in active,
            "status": "ok" if enabled else "paused",
            "pattern": label,
            "_matchText": f"{label} {command}",
        }
        definitions.append(apply_metadata_override(definition, overrides))

    # `launchctl list` is still a native inventory.  Keep ecosystem-owned
    # labels even when the generator cannot read the installed plist path.
    for label in sorted(active - seen):
        if not _launchd_supported(label, "", overrides):
            continue
        definition = {
            "definitionId": _stable_id("launchd", "josh2", label),
            "name": " ".join(word.capitalize() for word in re.split(r"[.-]+", label.split(".")[-1]) if word),
            "schedule": "",
            "scheduleSpec": {"kind": "daemon", "timezone": str(ET)},
            "source": "launchd",
            "sourceLabel": _source_label("launchd"),
            "ownerKey": "josh2",
            "agent": "JOSH 2.0",
            "enabled": True,
            "present": True,
            "active": True,
            "status": "ok",
            "pattern": label,
            "_matchText": label,
        }
        definitions.append(apply_metadata_override(definition, overrides))
    return definitions


def _hermes_schedule(job: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    # #JAIMES: Hermes native {kind, expr/minutes} schedules are authoritative;
    # never fabricate midnight occurrences from schedule-less inventory.
    raw: Any = job.get("cron") or job.get("cron_expression") or job.get("expression") or job.get("schedule")
    if isinstance(raw, Mapping):
        kind = str(raw.get("kind") or "").lower()
        if kind == "cron":
            expression = str(raw.get("expr") or raw.get("cron") or raw.get("expression") or raw.get("value") or "").strip()
            if expression:
                return {"kind": "cron", "expression": expression, "timezone": str(ET)}, _cron_label(expression)
        if kind == "interval":
            seconds = int(raw.get("seconds") or 0)
            if not seconds:
                seconds = int(raw.get("minutes") or 0) * 60
            if not seconds:
                seconds = int(raw.get("hours") or 0) * 3600
            if seconds > 0:
                display = str(raw.get("display") or "").strip()
                if not display:
                    display = f"Every {seconds // 60} min" if seconds % 60 == 0 else f"Every {seconds} sec"
                return {"kind": "interval", "seconds": seconds, "timezone": str(ET)}, display
        raw = raw.get("expr") or raw.get("cron") or raw.get("expression") or raw.get("value") or raw.get("display")
    if isinstance(raw, str) and raw.strip():
        raw = raw.strip()
        if raw.startswith("@") or len(raw.split()) == 5:
            return {"kind": "cron", "expression": raw, "timezone": str(ET)}, _cron_label(raw)
        return human_schedule_spec(raw), raw
    return {"kind": "unknown", "timezone": str(ET)}, ""


def discover_hermes_definitions(
    jobs: Iterable[Mapping[str, Any]],
    *,
    overrides: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for job in jobs:
        name = str(job.get("name") or job.get("id") or "").strip()
        if not name:
            continue
        native_id = str(job.get("id") or name)
        spec, schedule = _hermes_schedule(job)
        enabled = bool(job.get("enabled", True))
        definition = {
            "definitionId": _stable_id("hermes", "jaimes", native_id),
            "name": _text(name, 90),
            "schedule": schedule,
            "scheduleSpec": spec,
            "source": "hermes",
            "sourceLabel": _source_label("hermes"),
            "ownerKey": "jaimes",
            "agent": "JAIMES",
            "enabled": enabled,
            "present": True,
            "status": "ok" if enabled else "paused",
            "hermesName": name,
            "pattern": native_id,
            "lastRun": job.get("last_run_at") or job.get("lastRun"),
            "nextRun": job.get("next_run_at") or job.get("nextRun"),
            "rawRunStatus": job.get("last_status") or job.get("status"),
            "lastError": (
                f"Latest Hermes run reported {str(job.get('last_status')).lower()}."
                if job.get("last_status") not in {None, "", "ok", "success", "completed"}
                else None
            ),
            "errors": 1 if job.get("last_status") not in {None, "", "ok", "success", "completed"} else 0,
            "durationMs": job.get("duration_ms") or job.get("durationMs"),
            "_matchText": f"{native_id} {name} {job.get('command', '')}",
        }
        definitions.append(apply_metadata_override(definition, overrides))
    return definitions


def qa_schedule_label(schedule: Mapping[str, Any]) -> str:
    interval = int(schedule.get("intervalMinutes") or 0)
    if interval:
        return f"Every {interval} min"
    minutes = [int(value) for value in schedule.get("minutes", [])]
    hours = [int(value) for value in schedule.get("hours", [])]
    weekdays = [int(value) for value in schedule.get("weekdays", [])]
    minute_text = ",".join(f"{value:02d}" for value in minutes) or "00"
    hour_text = ",".join(f"{value:02d}:{minute_text} ET" for value in hours) if hours else f":{minute_text} each hour ET"
    if weekdays:
        hour_text = ",".join(DAY_NAMES[value] for value in weekdays) + " " + hour_text
    return hour_text


def discover_qa_definitions(
    config: Mapping[str, Any], state: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    runs = (state or {}).get("jobs", {})
    runs = runs if isinstance(runs, Mapping) else {}
    definitions: list[dict[str, Any]] = []
    owner_labels = {"josh2": "JOSH 2.0", "jaimes": "JAIMES", "jain": "J.A.I.N", "joshex": "JOSHeX"}
    for job in config.get("jobs", []):
        if not isinstance(job, Mapping) or not job.get("id"):
            continue
        job_id = str(job["id"])
        owner = str(job.get("owner") or "josh2")
        schedule = job.get("schedule") if isinstance(job.get("schedule"), Mapping) else {}
        weekdays = [int(value) for value in schedule.get("weekdays", [])]
        run = runs.get(job_id) if isinstance(runs.get(job_id), Mapping) else {}
        state_name = str(run.get("status") or "scheduled")
        failure_streak = int(run.get("failureStreak") or 0)
        failed = state_name in {"failed", "timeout"} or (state_name.startswith("skipped_") and failure_streak > 0)
        definitions.append({
            "definitionId": _stable_id("ecosystem_qa_scheduler", owner, job_id),
            "name": _text(job.get("team") or job_id.replace("-", " ").title(), 90),
            "schedule": qa_schedule_label(schedule),
            "scheduleSpec": {"kind": "qa", **dict(schedule), "timezone": str(ET)},
            "description": job_id.replace("-", " ").capitalize(),
            "category": "QA & Product Support",
            "source": "ecosystem_qa_scheduler",
            "sourceLabel": _source_label("ecosystem_qa_scheduler"),
            "ownerKey": owner,
            "agent": owner_labels.get(owner, "JOSH 2.0"),
            "enabled": bool(job.get("enabled", True)),
            "present": True,
            "todayRelevant": not weekdays or dt.datetime.now(ET).weekday() in weekdays,
            "status": "error" if failed else "ok",
            "runStatus": "missed" if failed else ("done" if state_name == "ok" else "upcoming"),
            "rawRunStatus": state_name,
            "errors": 1 if failed else 0,
            "lastError": "Latest QA run needs attention." if failed else None,
            "lastRun": run.get("completedAt") or run.get("startedAt"),
            "durationMs": run.get("durationMs"),
            "failureStreak": failure_streak,
        })
    return definitions


def human_schedule_spec(label: str) -> dict[str, Any]:
    text = str(label or "").strip()
    lowered = text.lower()
    match = re.search(r"every\s+(\d+)\s*(?:min|minute)", lowered)
    if match:
        return {"kind": "interval", "seconds": int(match.group(1)) * 60, "timezone": str(ET)}
    if lowered.startswith("hourly"):
        return {"kind": "interval", "seconds": 3600, "timezone": str(ET)}
    match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", lowered)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2) or 0)
        if match.group(3) == "pm" and hour != 12:
            hour += 12
        elif match.group(3) == "am" and hour == 12:
            hour = 0
        weekdays = [idx for token, idx in DAY_TOKEN_TO_INDEX.items() if len(token) == 3 and re.search(rf"\b{token.lower()}\b", lowered)]
        if "weekday" in lowered:
            weekdays = list(range(5))
        elif "weekend" in lowered:
            weekdays = [5, 6]
        return {"kind": "human_time", "hour": hour, "minute": minute, "weekdays": sorted(set(weekdays)), "timezone": str(ET)}
    if "boot" in lowered or "continuous" in lowered or "keepalive" in lowered:
        return {"kind": "daemon", "timezone": str(ET)}
    return {"kind": "unknown", "timezone": str(ET)}


def default_launchd_plist_paths(root: Path, home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    directories = [root / "launchd", home / "Library" / "LaunchAgents"]
    paths: list[Path] = []
    for directory in directories:
        try:
            paths.extend(directory.glob("*.plist"))
        except OSError:
            continue
    return paths


def _parse_key_value_rule(rule: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in str(rule or "").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.strip().upper()] = value.strip().upper()
    return values


def _cron_field_values(field: str, minimum: int, maximum: int, *, sunday: bool = False) -> set[int]:
    values: set[int] = set()
    for raw_part in field.upper().split(","):
        part, _, step_raw = raw_part.partition("/")
        step = max(1, int(step_raw)) if step_raw.isdigit() else 1
        named = {**{name: index + 1 for index, name in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"))},
                 **{"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}}

        def number(token: str) -> int:
            return named.get(token, int(token) if token.lstrip("-").isdigit() else minimum)

        if part == "*":
            start, end = minimum, maximum
        elif "-" in part:
            left, right = part.split("-", 1)
            start, end = number(left), number(right)
        else:
            start = end = number(part)
        values.update(range(max(minimum, start), min(maximum, end) + 1, step))
    if sunday and 7 in values:
        values.add(0)
    return values


def _cron_times(expression: str, day: dt.date) -> list[dt.datetime]:
    macros = {
        "@hourly": "0 * * * *",
        "@daily": "0 0 * * *",
        "@midnight": "0 0 * * *",
        "@weekly": "0 0 * * 0",
    }
    if expression == "@reboot":
        return [dt.datetime.combine(day, dt.time(0), ET)]
    expression = macros.get(expression, expression)
    parts = expression.split()
    if len(parts) != 5:
        return []
    minute, hour, dom, month, dow = parts
    try:
        if day.month not in _cron_field_values(month, 1, 12):
            return []
        dom_match = day.day in _cron_field_values(dom, 1, 31)
        cron_dow = (day.weekday() + 1) % 7
        dow_match = cron_dow in _cron_field_values(dow, 0, 7, sunday=True)
        if dom != "*" and dow != "*":
            calendar_match = dom_match or dow_match
        else:
            calendar_match = dom_match and dow_match
        if not calendar_match:
            return []
        return [
            dt.datetime.combine(day, dt.time(hour_value, minute_value), ET)
            for hour_value in sorted(_cron_field_values(hour, 0, 23))
            for minute_value in sorted(_cron_field_values(minute, 0, 59))
        ]
    except (TypeError, ValueError):
        return []


def _rrule_times(spec: Mapping[str, Any], day: dt.date) -> list[dt.datetime]:
    values = _parse_key_value_rule(str(spec.get("rrule") or ""))
    freq = values.get("FREQ")
    byday = [DAY_TOKEN_TO_INDEX[token[-2:]] for token in values.get("BYDAY", "").split(",") if token[-2:] in DAY_TOKEN_TO_INDEX]
    if freq == "WEEKLY" and byday and day.weekday() not in byday:
        return []
    if freq == "DAILY" and byday and day.weekday() not in byday:
        return []
    interval = max(1, int(values.get("INTERVAL") or 1))
    anchor_ms = spec.get("anchorMs")
    if interval > 1 and isinstance(anchor_ms, (int, float)):
        anchor = dt.datetime.fromtimestamp(anchor_ms / 1000, tz=dt.timezone.utc).astimezone(ET).date()
        unit = 7 if freq == "WEEKLY" else 1
        if ((day - anchor).days // unit) % interval:
            return []
    hours = [int(value) for value in (values.get("BYHOUR") or "0").split(",")]
    minutes = [int(value) for value in (values.get("BYMINUTE") or "0").split(",")]
    return [dt.datetime.combine(day, dt.time(hour, minute), ET) for hour in hours for minute in minutes]


def occurrence_times(definition: Mapping[str, Any], day: dt.date) -> list[dt.datetime]:
    spec = definition.get("scheduleSpec") if isinstance(definition.get("scheduleSpec"), Mapping) else {}
    kind = str(spec.get("kind") or "unknown")
    if kind == "cron":
        return _cron_times(str(spec.get("expression") or ""), day)
    if kind == "rrule":
        return _rrule_times(spec, day)
    if kind == "interval":
        seconds = int(spec.get("seconds") or 0)
        if seconds <= 0:
            return []
        midnight = dt.datetime.combine(day, dt.time(0), ET)
        anchor = _parse_timestamp(definition.get("nextRun")) or _parse_timestamp(definition.get("lastRun"))
        if anchor:
            anchor_seconds = int((anchor - midnight).total_seconds()) % seconds
            start = midnight + dt.timedelta(seconds=anchor_seconds)
        else:
            start = midnight
        return [
            candidate
            for offset in range(0, 86400, seconds)
            if (candidate := start + dt.timedelta(seconds=offset)).date() == day
        ]
    if kind == "launchd_calendar":
        rows = []
        for interval in spec.get("intervals", []):
            if not isinstance(interval, Mapping):
                continue
            weekday = interval.get("Weekday")
            if weekday is not None:
                launchd_weekday = (day.weekday() + 1) % 7
                if launchd_weekday != int(weekday) % 7:
                    continue
            if interval.get("Day") is not None and int(interval["Day"]) != day.day:
                continue
            if interval.get("Month") is not None and int(interval["Month"]) != day.month:
                continue
            rows.append(dt.datetime.combine(day, dt.time(int(interval.get("Hour", 0)), int(interval.get("Minute", 0))), ET))
        return sorted(set(rows))
    if kind == "qa":
        weekdays = [int(value) for value in spec.get("weekdays", [])]
        if weekdays and day.weekday() not in weekdays:
            return []
        interval = int(spec.get("intervalMinutes") or 0)
        if interval:
            offset = int(spec.get("offset") or 0) % interval
            start = dt.datetime.combine(day, dt.time(0), ET) + dt.timedelta(minutes=offset)
            return [start + dt.timedelta(minutes=minute) for minute in range(0, 1440, interval)]
        minutes = [int(value) for value in spec.get("minutes", [])] or [0]
        hours = [int(value) for value in spec.get("hours", [])] or list(range(24))
        return [dt.datetime.combine(day, dt.time(hour, minute), ET) for hour in hours for minute in minutes]
    if kind == "human_time":
        weekdays = [int(value) for value in spec.get("weekdays", [])]
        if weekdays and day.weekday() not in weekdays:
            return []
        return [dt.datetime.combine(day, dt.time(int(spec.get("hour", 0)), int(spec.get("minute", 0))), ET)]
    # Continuous services and schedule-less inventory are operational health,
    # not daily job occurrences.  Unknown definitions must never be invented
    # as midnight work; wait until their source publishes a native schedule.
    if kind in {"daemon", "unknown"}:
        return []
    return []


def _parse_timestamp(value: Any) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ET)
        return parsed.astimezone(ET)
    except (TypeError, ValueError):
        return None


def _duration_label(duration_ms: Any) -> str | None:
    try:
        seconds = max(0, round(float(duration_ms) / 1000))
    except (TypeError, ValueError):
        return None
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds}s" if seconds else f"{minutes}m"


def _base_outcome(
    definition: Mapping[str, Any], *, day: dt.date
) -> tuple[str | None, str]:
    row_status = str(definition.get("status") or "").lower()
    run_status = str(definition.get("runStatus") or "").lower()
    raw_run_status = str(definition.get("rawRunStatus") or "").lower()
    last_run = _parse_timestamp(definition.get("lastRun"))
    evidence_today = bool(last_run and last_run.date() == day)
    current_failure = bool(
        (int(definition.get("errors") or 0) > 0 or row_status in {"error", "failed", "broken"})
        and evidence_today
    )
    scheduled_failure = (
        run_status == "missed" and (evidence_today or last_run is None)
    ) or (
        run_status in {"failed", "error", "timeout"} and evidence_today
    )
    raw_failure = raw_run_status in {"failed", "error", "timeout", "missed"} and evidence_today
    if current_failure or scheduled_failure or raw_failure:
        return "broken", run_status or "failed"
    if not bool(definition.get("enabled", True)) or row_status in {"paused", "disabled", "skipped"} or run_status in {"paused", "cancelled"}:
        return "skipped", run_status or "skipped"
    if (run_status.startswith("skipped") or raw_run_status.startswith("skipped")) and evidence_today:
        return "skipped", run_status or raw_run_status
    if run_status in {"running", "working"}:
        return "pending", "running"
    if run_status == "active":
        return "pending", "active"
    return None, run_status or "scheduled"


def _multi_run_evidence(definition: Mapping[str, Any], scheduled: dt.datetime) -> bool | None:
    payload = definition.get("multiRun") if isinstance(definition.get("multiRun"), Mapping) else {}
    rows = payload.get("runs") if isinstance(payload.get("runs"), list) else []
    for row in rows:
        if not isinstance(row, Mapping) or not row.get("time"):
            continue
        try:
            parsed = dt.datetime.strptime(str(row["time"]), "%I:%M %p")
        except ValueError:
            continue
        if parsed.hour == scheduled.hour and parsed.minute == scheduled.minute:
            return bool(row.get("done"))
    return None


def _occurrence_outcome(
    definition: Mapping[str, Any],
    scheduled: dt.datetime,
    *,
    now: dt.datetime,
    occurrence_count: int,
) -> tuple[str, str]:
    fixed, run_status = _base_outcome(definition, day=scheduled.date())
    if fixed:
        return fixed, run_status
    multi_done = _multi_run_evidence(definition, scheduled)
    if multi_done is True:
        return "complete", "done"
    if scheduled > now:
        return "pending", "scheduled"
    last_run = _parse_timestamp(definition.get("lastRun"))
    if last_run and last_run.date() == scheduled.date():
        interval_seconds = int((definition.get("scheduleSpec") or {}).get("seconds") or 0)
        tolerance = (
            min(dt.timedelta(minutes=15), dt.timedelta(seconds=max(1, interval_seconds // 2)))
            if interval_seconds
            else dt.timedelta(minutes=15)
        )
        if abs(last_run - scheduled) <= tolerance:
            return "complete", "done"
    return "pending", run_status


def materialize_today_jobs(
    definitions: Sequence[Mapping[str, Any]],
    *,
    now: dt.datetime | None = None,
    rollup_threshold: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now_et = (now or dt.datetime.now(ET)).astimezone(ET)
    day = now_et.date()
    rows: list[dict[str, Any]] = []
    rolled_count = 0

    for definition in definitions:
        if definition.get("todayRelevant") is False:
            continue
        times = occurrence_times(definition, day)
        if not times:
            continue
        rolled = len(times) > rollup_threshold
        if rolled:
            rolled_count += 1
            fixed, run_status = _base_outcome(definition, day=day)
            last_run = _parse_timestamp(definition.get("lastRun"))
            completed = 1 if last_run and last_run.date() == day else 0
            if fixed == "pending" and run_status == "active" and definition.get("present"):
                outcome, run_status, completed = "complete", "coverage-current", len(times)
            elif fixed:
                outcome = fixed
            elif completed:
                outcome, run_status = "complete", "done"
            else:
                outcome = "pending"
            scheduled_rows = [(times[0], outcome, run_status or "scheduled")]
        else:
            completed = 0
            scheduled_rows = []
            for scheduled in times:
                outcome, run_status = _occurrence_outcome(
                    definition, scheduled, now=now_et, occurrence_count=len(times)
                )
                completed += outcome == "complete"
                scheduled_rows.append((scheduled, outcome, run_status))

        for scheduled, outcome, run_status in scheduled_rows:
            definition_id = str(definition.get("definitionId") or _stable_id(
                str(definition.get("source") or "job"),
                str(definition.get("agent") or "owner"),
                str(definition.get("name") or "job"),
            ))
            suffix = f"{day.isoformat()}:rollup" if rolled else scheduled.strftime("%Y-%m-%dT%H%M")
            raw_last_run = definition.get("lastRun")
            last_run_dt = _parse_timestamp(raw_last_run)
            current_last_run = raw_last_run if last_run_dt and last_run_dt.date() == day else None
            evidence_status = str(definition.get("rawRunStatus") or definition.get("runStatus") or outcome) if current_last_run else outcome
            row = {
                "occurrenceId": f"{definition_id}@{suffix}",
                "definitionId": definition_id,
                "name": _text(definition.get("name") or "Scheduled job", 90),
                "owner": _text(definition.get("agent") or "JOSH 2.0", 32),
                "agent": _text(definition.get("agent") or "JOSH 2.0", 32),
                "source": str(definition.get("source") or "scheduler"),
                "sourceLabel": _text(definition.get("sourceLabel") or _source_label(str(definition.get("source") or "scheduler")), 48),
                "category": _text(definition.get("category") or "Other", 48),
                "description": _text(definition.get("description") or "", 180),
                "scheduledAt": scheduled.isoformat(timespec="seconds"),
                "scheduledTime": "Coverage" if rolled else scheduled.strftime("%-I:%M %p"),
                "schedule": _text(definition.get("schedule") or "Recurring", 80),
                "outcome": outcome,
                "status": outcome,
                "runStatus": run_status,
                "lastRun": current_last_run,
                "previousRun": raw_last_run if raw_last_run and not current_last_run else None,
                "durationMs": definition.get("durationMs"),
                "duration": _duration_label(definition.get("durationMs")),
                "evidence": {
                    "source": str(definition.get("source") or "scheduler"),
                    "status": evidence_status,
                    "at": current_last_run,
                    "summary": _text(definition.get("lastError") or "", 140) or None if current_last_run else None,
                },
                "rolledUp": rolled,
                "expectedRuns": len(times),
                "completedRuns": int(completed),
            }
            rows.append(row)

    rows.sort(key=lambda row: (row["scheduledAt"], row["name"].lower(), row["occurrenceId"]))
    now_index = next((index for index, row in enumerate(rows) if _parse_timestamp(row["scheduledAt"]) and _parse_timestamp(row["scheduledAt"]) > now_et), len(rows))
    counts = {key: sum(row["outcome"] == key for row in rows) for key in ("complete", "skipped", "broken", "pending")}
    meta = {
        "version": 1,
        "timezone": str(ET),
        "date": day.isoformat(),
        "generatedAt": now_et.isoformat(timespec="seconds"),
        "now": now_et.isoformat(timespec="seconds"),
        "nowIndex": now_index,
        "nextOccurrenceId": rows[now_index]["occurrenceId"] if now_index < len(rows) else None,
        "counts": counts,
        "definitionCount": len({str(row["definitionId"]) for row in rows}),
        "occurrenceCount": len(rows),
        "rolledUpDefinitionCount": rolled_count,
    }
    return rows, meta


def json_safe_definitions(definitions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Drop internal match helpers and normalize Path-like values."""
    return json.loads(json.dumps([dict(row) for row in definitions], default=str))
