#!/usr/bin/env python3
"""Keep dedicated-host Codex Remote work visible, isolated, and human-readable.

Josh 2.0 and JAIMES each have one manual workspace for phone/Desktop Remote
work. Background OpenCLAW, Hermes, cron, and ``codex exec`` jobs intentionally
remain outside that workspace and therefore outside the interactive Remote list.

The helper configures the manual workspace, creates labeled readiness threads
with one bounded read-only turn only when Remote visibility requires it, and
repairs blank or machine-like titles only inside the manual lane. It never
starts a model turn from the periodic guard.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import plistlib
import re
import socket
import sqlite3
import stat
import sys
import time
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol
from zoneinfo import ZoneInfo


TITLE_SEPARATOR = " — "
MANAGED_MARKER = "codex-remote-human-labels"
REMOTE_CONTROL_LABEL = "ai.openai.codex.remote-control"
TITLE_GUARD_LABEL = "ai.agentloops.codex-remote-title-guard"
INTERACTIVE_SOURCES = frozenset({"cli", "vscode", "appServer"})
MAX_TITLE_CHARS = 96

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
LONG_HEX_RE = re.compile(r"^(?:0x)?[0-9a-f]{16,}$", re.IGNORECASE)
MACHINE_NAME_RE = re.compile(
    r"^(?:task|thread|session|chat|run|job|conversation|codex)[\s:_#-]*[a-z0-9._:-]{6,}$",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’.-]*")
GENERIC_TITLES = {
    "chat",
    "codex",
    "conversation",
    "new chat",
    "new task",
    "remote",
    "session",
    "task",
    "thread",
    "untitled",
}
OPAQUE_FRAGMENT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[0-9a-f]{16,}|[0-9a-f]{8}-[0-9a-f-]{20,})(?![A-Za-z0-9])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AgentProfile:
    key: str
    display_name: str
    home: Path
    workspace_name: str
    project_label: str

    @property
    def title_prefix(self) -> str:
        return f"{self.display_name}{TITLE_SEPARATOR}"

    @property
    def workspace(self) -> Path:
        return self.home / "Codex Workspaces" / self.workspace_name

    @property
    def socket_path(self) -> Path:
        return self.home / ".codex" / "app-server-control" / "app-server-control.sock"

    @property
    def global_state_path(self) -> Path:
        return self.home / ".codex" / ".codex-global-state.json"

    @property
    def remote_control_plist_path(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{REMOTE_CONTROL_LABEL}.plist"

    @property
    def title_guard_plist_path(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{TITLE_GUARD_LABEL}.plist"

    @property
    def runtime_helper_path(self) -> Path:
        return self.home / ".codex" / "tools" / "codex_remote_manual_lane.py"

    @property
    def state_db_path(self) -> Path:
        return self.home / ".codex" / "state_5.sqlite"

    @property
    def operation_lock_path(self) -> Path:
        return self.home / ".codex" / "remote-manual-lane.lock"


PROFILES = {
    "josh2": AgentProfile(
        key="josh2",
        display_name="Josh 2.0",
        home=Path("/Users/josh2.0"),
        workspace_name="00 - JOSH 2.0 MANUAL SAFE",
        project_label="Josh 2.0 — Manual Remote work",
    ),
    "jaimes": AgentProfile(
        key="jaimes",
        display_name="JAIMES",
        home=Path("/Users/jc_agent"),
        workspace_name="00 - JAIMES MANUAL SAFE",
        project_label="JAIMES — Manual Remote work",
    ),
}


class RemoteProtocolError(RuntimeError):
    """Raised when the local Codex app-server returns an invalid RPC result."""


class RpcClient(Protocol):
    def request(self, method: str, params: Optional[dict[str, Any]] = None) -> Any: ...

    def wait_for_turn_completion(
        self,
        thread_id: str,
        turn_id: str,
        *,
        timeout: int = 45,
    ) -> dict[str, Any]: ...


def profile_for(agent: str, *, home: Optional[Path] = None) -> AgentProfile:
    try:
        profile = PROFILES[agent]
    except KeyError as exc:
        raise ValueError(f"unsupported agent: {agent}") from exc
    return replace(profile, home=home) if home is not None else profile


def compact_text(value: Any) -> str:
    text = str(value or "").replace("\x00", " ")
    return " ".join(text.split()).strip()


def _strip_known_prefix(value: str) -> str:
    text = value.strip()
    for profile in PROFILES.values():
        pattern = re.compile(
            rf"^{re.escape(profile.display_name)}\s*(?:—|–|-|:|\|)\s*",
            re.IGNORECASE,
        )
        text = pattern.sub("", text, count=1).strip()
    return text


def looks_machine_generated(value: Any) -> bool:
    """Return True for blank, generic, UUID-like, or ID-dominated titles."""

    text = _strip_known_prefix(compact_text(value))
    lowered = text.casefold()
    if not text or lowered in GENERIC_TITLES:
        return True
    if UUID_RE.fullmatch(text) or LONG_HEX_RE.fullmatch(text) or MACHINE_NAME_RE.fullmatch(text):
        return True
    if OPAQUE_FRAGMENT_RE.fullmatch(text):
        return True
    words = WORD_RE.findall(text)
    meaningful = [word for word in words if word.casefold() not in GENERIC_TITLES]
    if len(meaningful) < 2:
        return True
    visible = [char for char in text if not char.isspace()]
    if visible:
        identifier_chars = sum(char.isdigit() or char in "-_:/." for char in visible)
        if identifier_chars / len(visible) > 0.72:
            return True
    return False


def _truncate_at_word(value: str, limit: int = MAX_TITLE_CHARS) -> str:
    if len(value) <= limit:
        return value
    shortened = value[: limit - 1].rstrip()
    if " " in shortened:
        shortened = shortened.rsplit(" ", 1)[0]
    return f"{shortened}…"


def human_title(profile: AgentProfile, purpose: Any) -> str:
    clean = _strip_known_prefix(compact_text(purpose)).strip(" —–-|:;,.\t")
    if looks_machine_generated(clean):
        raise ValueError(
            f"use a plain-English purpose after '{profile.title_prefix}', not a generic label or ID"
        )
    return _truncate_at_word(f"{profile.title_prefix}{clean}")


def _thread_created_at(thread: dict[str, Any]) -> datetime:
    raw = thread.get("createdAt") or thread.get("updatedAt")
    try:
        value = float(raw)
        if value > 10_000_000_000:
            value /= 1000.0
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return datetime.now(timezone.utc)


def fallback_title(profile: AgentProfile, thread: dict[str, Any]) -> str:
    created = _thread_created_at(thread).astimezone(ZoneInfo("America/New_York"))
    readable_time = created.strftime("%B %d, %Y at %I:%M %p").replace(" 0", " ")
    zone = created.tzname() or "ET"
    raw_id = re.sub(r"[^A-Za-z0-9]", "", compact_text(thread.get("id")))
    reference = f" (reference {raw_id[-8:]})" if len(raw_id) >= 8 else ""
    return f"{profile.title_prefix}Untitled manual task — {readable_time} {zone}{reference}"


def repaired_title(profile: AgentProfile, thread: dict[str, Any]) -> str:
    current = compact_text(thread.get("name"))
    if current and not looks_machine_generated(current):
        try:
            return human_title(profile, current)
        except ValueError:
            pass
    return fallback_title(profile, thread)


def _source_name(value: Any) -> str:
    return value if isinstance(value, str) else ""


def thread_is_in_manual_lane(thread: dict[str, Any], profile: AgentProfile) -> bool:
    if compact_text(thread.get("cwd")) != str(profile.workspace):
        return False
    if _source_name(thread.get("source")) not in INTERACTIVE_SOURCES:
        return False
    if thread.get("archived") is True or thread.get("isArchived") is True:
        return False
    if thread.get("ephemeral") is True:
        return False
    return bool(compact_text(thread.get("id")))


def title_repairs(threads: Iterable[dict[str, Any]], profile: AgentProfile) -> list[tuple[str, str]]:
    repairs: list[tuple[str, str]] = []
    for thread in threads:
        if not thread_is_in_manual_lane(thread, profile):
            continue
        name = compact_text(thread.get("name"))
        correct_prefix = name.startswith(profile.title_prefix)
        suffix = name[len(profile.title_prefix) :] if correct_prefix else name
        if correct_prefix and not looks_machine_generated(suffix):
            continue
        repairs.append((str(thread["id"]), repaired_title(profile, thread)))
    return repairs


class AppServerClient:
    """Small synchronous client for Codex's WebSocket-over-Unix control socket."""

    def __init__(self, socket_path: Path, *, timeout: int = 15) -> None:
        self.socket_path = socket_path
        self.timeout = timeout
        self._next_id = 0
        self._connection: Any = None
        self._notifications: list[dict[str, Any]] = []

    def __enter__(self) -> "AppServerClient":
        try:
            import websocket  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RemoteProtocolError("Python package 'websocket-client' is required") from exc

        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            raw.connect(str(self.socket_path))
            self._connection = websocket.create_connection(
                "ws://localhost/",
                socket=raw,
                suppress_origin=True,
                timeout=self.timeout,
            )
        except Exception as exc:
            raw.close()
            raise RemoteProtocolError("Codex Remote connection is unavailable") from exc

        try:
            self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "agent-ecosystem-remote-bridge",
                        "title": "Human-readable Codex Remote bridge",
                        "version": "1.0",
                    },
                    "capabilities": {
                        "experimentalApi": False,
                        "requestAttestation": False,
                    },
                },
            )
            self._connection.send(json.dumps({"method": "initialized"}))
        except Exception as exc:
            self.__exit__()
            if isinstance(exc, RemoteProtocolError):
                raise
            raise RemoteProtocolError("Codex Remote initialization failed") from exc
        return self

    def __exit__(self, *_args: Any) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def request(self, method: str, params: Optional[dict[str, Any]] = None) -> Any:
        if self._connection is None:
            raise RemoteProtocolError("Codex Remote socket is not connected")
        self._next_id += 1
        request_id = self._next_id
        payload: dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            payload["params"] = params
        try:
            self._connection.send(json.dumps(payload))
        except Exception as exc:
            raise RemoteProtocolError(f"{method}: transport send failed") from exc

        while True:
            try:
                raw_message = self._connection.recv()
                if isinstance(raw_message, bytes):
                    raw_message = raw_message.decode("utf-8")
                message = json.loads(raw_message)
            except Exception as exc:
                raise RemoteProtocolError(f"{method}: invalid transport response") from exc
            if not isinstance(message, dict):
                raise RemoteProtocolError(f"{method}: invalid protocol response")
            if message.get("id") != request_id:
                if "id" not in message:
                    self._notifications.append(message)
                continue
            if "error" in message:
                error = message.get("error") or {}
                code = error.get("code")
                suffix = f" ({code})" if isinstance(code, int) else ""
                # Server-provided error text can echo request parameters such
                # as the user's title or readiness prompt. Keep local guard
                # logs useful without copying that private content into them.
                raise RemoteProtocolError(f"{method}: request failed{suffix}")
            return message.get("result")

    def wait_for_turn_completion(
        self,
        thread_id: str,
        turn_id: str,
        *,
        timeout: int = 45,
    ) -> dict[str, Any]:
        if self._connection is None:
            raise RemoteProtocolError("Codex Remote socket is not connected")
        deadline = time.monotonic() + timeout
        while True:
            message = self._notifications.pop(0) if self._notifications else None
            if message is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteProtocolError("readiness turn did not finish before the timeout")
                try:
                    self._connection.settimeout(remaining)
                    raw_message = self._connection.recv()
                    if isinstance(raw_message, bytes):
                        raw_message = raw_message.decode("utf-8")
                    message = json.loads(raw_message)
                except Exception as exc:
                    raise RemoteProtocolError("readiness turn completion was unavailable") from exc
                if not isinstance(message, dict):
                    continue

            if message.get("method") != "turn/completed":
                continue
            params = message.get("params")
            if not isinstance(params, dict) or compact_text(params.get("threadId")) != thread_id:
                continue
            turn = params.get("turn")
            if not isinstance(turn, dict) or compact_text(turn.get("id")) != turn_id:
                continue
            if compact_text(turn.get("status")) != "completed" or turn.get("error") is not None:
                raise RemoteProtocolError("the read-only readiness turn did not complete successfully")
            return turn


def list_manual_threads(client: RpcClient, profile: AgentProfile) -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    cursor: Optional[str] = None
    for _page in range(20):
        params: dict[str, Any] = {
            "limit": 100,
            "sortKey": "updated_at",
            "sortDirection": "desc",
            "sourceKinds": sorted(INTERACTIVE_SOURCES),
            "archived": False,
            "cwd": str(profile.workspace),
            "useStateDbOnly": False,
        }
        if cursor:
            params["cursor"] = cursor
        result = client.request("thread/list", params) or {}
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, list):
            raise RemoteProtocolError("thread/list returned no thread list")
        threads.extend(item for item in data if isinstance(item, dict))
        next_cursor = result.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            break
        cursor = next_cursor
    else:
        raise RemoteProtocolError("thread/list exceeded the safe pagination limit")
    return threads


def guard_manual_titles(client: RpcClient, profile: AgentProfile) -> dict[str, Any]:
    threads = list_manual_threads(client, profile)
    repairs = title_repairs(threads, profile)
    for thread_id, title in repairs:
        client.request("thread/name/set", {"threadId": thread_id, "name": title})
    return {
        "ok": True,
        "agent": profile.display_name,
        "workspace": profile.project_label,
        "checked": len(threads),
        "renamed": len(repairs),
    }


def persisted_thread_by_title(profile: AgentProfile, title: str) -> Optional[dict[str, Any]]:
    """Read the local state database without mutating Codex-owned state."""

    if not profile.state_db_path.exists():
        return None
    encoded = urllib.parse.quote(str(profile.state_db_path), safe="/")
    connection = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True, timeout=2)
    try:
        rows = connection.execute(
            """
            SELECT id, title, source, has_user_event,
                   CASE WHEN first_user_message <> '' THEN 1 ELSE 0 END,
                   tokens_used, created_at, updated_at
            FROM threads
            WHERE cwd = ? AND archived = 0
            ORDER BY updated_at DESC, created_at DESC
            """,
            (str(profile.workspace),),
        ).fetchall()
    finally:
        connection.close()
    for (
        thread_id,
        stored_title,
        source,
        has_user_event,
        has_first_message,
        tokens_used,
        created_at,
        updated_at,
    ) in rows:
        if source not in INTERACTIVE_SOURCES:
            continue
        if compact_text(stored_title).casefold() != title.casefold():
            continue
        return {
            "id": thread_id,
            "name": stored_title,
            "source": source,
            "cwd": str(profile.workspace),
            "ephemeral": False,
            "hasUserEvent": bool(has_user_event or has_first_message or tokens_used),
            "createdAt": created_at,
            "updatedAt": updated_at,
        }
    return None


def _visible_title_match(
    threads: Iterable[dict[str, Any]],
    title: str,
) -> Optional[dict[str, Any]]:
    expected = title.casefold()
    return next(
        (item for item in threads if compact_text(item.get("name")).casefold() == expected),
        None,
    )


def readiness_model_options(client: RpcClient) -> tuple[dict[str, Any], str]:
    result = client.request("model/list", {"limit": 100, "includeHidden": False}) or {}
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, list):
        return {}, "Configured Remote model — read-only readiness check"
    selected = next(
        (
            item
            for item in data
            if isinstance(item, dict) and compact_text(item.get("model")) == "gpt-5.6-luna"
        ),
        None,
    )
    if not selected:
        return {}, "Configured Remote model — read-only readiness check"
    params: dict[str, Any] = {"model": "gpt-5.6-luna"}
    supported = selected.get("supportedReasoningEfforts")
    efforts = {
        compact_text(item.get("reasoningEffort"))
        for item in supported
        if isinstance(item, dict)
    } if isinstance(supported, list) else set()
    if "low" in efforts:
        params["effort"] = "low"
    return params, "GPT-5.6 Luna — read-only readiness check"


def create_labeled_thread(
    client: RpcClient,
    profile: AgentProfile,
    title: str,
    prompt: str,
) -> dict[str, Any]:
    full_title = human_title(profile, title)
    existing = list_manual_threads(client, profile)
    existing_match = _visible_title_match(existing, full_title)
    if existing_match:
        return {
            "ok": True,
            "title": full_title,
            "workspace": profile.project_label,
            "agent": profile.display_name,
            "created": False,
            "threadId": compact_text(existing_match.get("id")),
            "modelTurnStarted": False,
        }

    persisted = persisted_thread_by_title(profile, full_title)
    created = persisted is None
    if persisted:
        thread_id = compact_text(persisted.get("id"))
        client.request(
            "thread/resume",
            {"threadId": thread_id, "cwd": str(profile.workspace)},
        )
    else:
        result = client.request(
            "thread/start",
            {
                "cwd": str(profile.workspace),
                "ephemeral": False,
                "serviceName": "agent_ecosystem_remote_bridge",
            },
        )
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = compact_text(thread.get("id")) if isinstance(thread, dict) else ""
        if not thread_id:
            raise RemoteProtocolError("thread/start returned no thread identifier")

        try:
            client.request("thread/name/set", {"threadId": thread_id, "name": full_title})
        except RemoteProtocolError:
            # A lost response can leave a successfully named thread. Retrying the
            # idempotent name operation is safer than creating another task.
            client.request("thread/name/set", {"threadId": thread_id, "name": full_title})

    read_result = client.request("thread/read", {"threadId": thread_id, "includeTurns": False}) or {}
    verified = read_result.get("thread") if isinstance(read_result, dict) else None
    if not isinstance(verified, dict):
        raise RemoteProtocolError("thread/read returned no thread")
    if compact_text(verified.get("name")) != full_title:
        raise RemoteProtocolError("the new Remote task did not retain its human-readable title")
    if compact_text(verified.get("cwd")) != str(profile.workspace):
        raise RemoteProtocolError("the new Remote task is outside the manual workspace")
    if verified.get("ephemeral") is True:
        raise RemoteProtocolError("the new Remote task is not durable")
    has_user_event = bool(persisted and persisted.get("hasUserEvent"))
    turn_started = False
    if not has_user_event:
        clean_prompt = compact_text(prompt)
        if not clean_prompt:
            raise ValueError("a short readiness prompt is required so the task appears in Remote")
        model_params, model_label = readiness_model_options(client)
        turn_result = client.request(
            "turn/start",
            {
                "threadId": thread_id,
                "clientUserMessageId": f"remote-readiness-check-{uuid.uuid4()}",
                "input": [{"type": "text", "text": clean_prompt, "text_elements": []}],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                "cwd": str(profile.workspace),
                "summary": "none",
                **model_params,
            },
        )
        turn = turn_result.get("turn") if isinstance(turn_result, dict) else None
        turn_id = compact_text(turn.get("id")) if isinstance(turn, dict) else ""
        if not turn_id:
            raise RemoteProtocolError("turn/start returned no readiness turn")
        client.wait_for_turn_completion(thread_id, turn_id)
        turn_started = True
    else:
        model_label = "Existing Remote task — no additional model turn"

    match = None
    for _attempt in range(8):
        visible = list_manual_threads(client, profile)
        match = next((item for item in visible if compact_text(item.get("id")) == thread_id), None)
        if match:
            break
        time.sleep(0.25)
    if not match:
        raise RemoteProtocolError("the new task was not found in the interactive Remote list")
    if compact_text(match.get("name")) != full_title:
        raise RemoteProtocolError("the interactive Remote list returned the wrong task title")

    return {
        "ok": True,
        "title": full_title,
        "workspace": profile.project_label,
        "agent": profile.display_name,
        "created": created,
        "threadId": thread_id,
        "model": model_label,
        "modelTurnStarted": turn_started,
    }


def _atomic_write(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        temporary.unlink(missing_ok=True)


def _mode_for(path: Path, default: int) -> int:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return default


def atomic_write_text(path: Path, value: str, *, default_mode: int = 0o644) -> None:
    _atomic_write(path, value.encode("utf-8"), mode=_mode_for(path, default_mode))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    value = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, value, default_mode=0o644)


def atomic_write_plist(path: Path, payload: dict[str, Any], *, default_mode: int = 0o600) -> None:
    _atomic_write(
        path,
        plistlib.dumps(payload, sort_keys=False),
        mode=_mode_for(path, default_mode),
    )


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    existed: bool
    payload: bytes
    mode: int


def snapshot_file(path: Path) -> FileSnapshot:
    try:
        return FileSnapshot(
            path=path,
            existed=True,
            payload=path.read_bytes(),
            mode=stat.S_IMODE(path.stat().st_mode),
        )
    except FileNotFoundError:
        return FileSnapshot(path=path, existed=False, payload=b"", mode=0o600)


def restore_snapshot(snapshot: FileSnapshot) -> None:
    if snapshot.existed:
        _atomic_write(snapshot.path, snapshot.payload, mode=snapshot.mode)
    else:
        snapshot.path.unlink(missing_ok=True)


def replace_managed_block(original: str, block: str) -> str:
    start = f"<!-- {MANAGED_MARKER}:start -->"
    end = f"<!-- {MANAGED_MARKER}:end -->"
    rendered = f"{start}\n{block.strip()}\n{end}"
    pattern = re.compile(rf"{re.escape(start)}.*?{re.escape(end)}", re.DOTALL)
    if pattern.search(original):
        return pattern.sub(rendered, original).rstrip() + "\n"
    base = original.rstrip()
    return f"{base}\n\n{rendered}\n" if base else f"{rendered}\n"


def agents_contract(profile: AgentProfile) -> str:
    return f"""
## Codex Remote task names

- Name every phone-visible task `{profile.title_prefix}<plain-English purpose>`.
- Never use a UUID, hash, session key, run ID, or generic word such as `Task` as the title.
- If an internal ID helps troubleshooting, place a shortened ID in parentheses after the readable title.
- Keep automated OpenCLAW, Hermes, cron, J.AI.N, and `codex exec` work outside this workspace.
- The periodic title guard may repair labels here, but it must never start a model turn.
"""


def readme_contract(profile: AgentProfile) -> str:
    return f"""
## What appears in Codex Remote

This project is **{profile.project_label}**. It is the host's manual phone/Desktop
lane; background agent jobs intentionally do not appear here.

Every task begins with `{profile.title_prefix}` followed by a short plain-English
purpose. Internal IDs may appear only afterward in parentheses.

The `Remote workspace ready` task is a read-only diagnostic. Start a new task for
real work so its model and permissions are chosen for that purpose.
"""


def configured_global_state(existing: dict[str, Any], profile: AgentProfile) -> dict[str, Any]:
    state = dict(existing)
    workspace = str(profile.workspace)
    state["active-workspace-roots"] = [workspace]
    state["electron-saved-workspace-roots"] = [workspace]

    labels = state.get("electron-workspace-root-labels")
    labels = dict(labels) if isinstance(labels, dict) else {}
    labels[workspace] = profile.project_label
    state["electron-workspace-root-labels"] = labels

    state["electron-workspace-project-order"] = [workspace]
    return state


def title_guard_plist(profile: AgentProfile) -> dict[str, Any]:
    log_dir = profile.home / ".codex" / "log"
    return {
        "Label": TITLE_GUARD_LABEL,
        "ProgramArguments": [
            "/opt/homebrew/bin/python3",
            str(profile.runtime_helper_path),
            "guard",
            "--agent",
            profile.key,
            "--quiet",
        ],
        "WorkingDirectory": str(profile.workspace),
        "RunAtLoad": True,
        "StartInterval": 60,
        "ProcessType": "Background",
        "ThrottleInterval": 15,
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        },
        "StandardOutPath": str(log_dir / "codex-remote-title-guard.log"),
        "StandardErrorPath": str(log_dir / "codex-remote-title-guard.error.log"),
    }


def configure_host_files(profile: AgentProfile, *, helper_path: Path) -> dict[str, Any]:
    """Configure only the explicit manual lane files; service restarts are separate."""

    #JAIMES: Remote visibility is manual-workspace-only; headless automation stays isolated.
    if not helper_path.is_file():
        raise FileNotFoundError("the Codex Remote helper source is missing")
    helper_payload = helper_path.read_bytes()
    if b"def main(" not in helper_payload:
        raise ValueError("the Codex Remote helper source is invalid")

    profile.workspace.mkdir(parents=True, exist_ok=True)
    agents_path = profile.workspace / "AGENTS.md"
    readme_path = profile.workspace / "README.md"
    agents_original = agents_path.read_text(encoding="utf-8") if agents_path.exists() else ""
    readme_original = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    agents_updated = replace_managed_block(agents_original, agents_contract(profile))
    readme_updated = replace_managed_block(readme_original, readme_contract(profile))

    try:
        remote_plist = plistlib.loads(profile.remote_control_plist_path.read_bytes())
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Codex Remote LaunchAgent is missing for {profile.display_name}") from exc
    if not isinstance(remote_plist, dict):
        raise ValueError("Codex Remote LaunchAgent is not a plist dictionary")
    remote_plist["WorkingDirectory"] = str(profile.workspace)

    try:
        state = json.loads(profile.global_state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    if not isinstance(state, dict):
        raise ValueError("Codex global state is not a JSON object")
    state_updated = configured_global_state(state, profile)
    guard_plist = title_guard_plist(profile)

    log_dir = profile.home / ".codex" / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    targets = [
        agents_path,
        readme_path,
        profile.remote_control_plist_path,
        profile.global_state_path,
        profile.runtime_helper_path,
        profile.title_guard_plist_path,
    ]
    snapshots = [snapshot_file(path) for path in targets]
    try:
        atomic_write_text(agents_path, agents_updated)
        atomic_write_text(readme_path, readme_updated)
        atomic_write_plist(profile.remote_control_plist_path, remote_plist)
        atomic_write_json(profile.global_state_path, state_updated)
        _atomic_write(profile.runtime_helper_path, helper_payload, mode=0o755)
        atomic_write_plist(profile.title_guard_plist_path, guard_plist)
    except Exception:
        for snapshot in reversed(snapshots):
            restore_snapshot(snapshot)
        raise

    return {
        "ok": True,
        "agent": profile.display_name,
        "workspace": profile.project_label,
        "taskTitleFormat": f"{profile.title_prefix}<plain-English purpose>",
        "backgroundAutomationVisible": False,
        "restartRequired": ["Codex Remote", "Codex Remote title guard"],
    }


def _print_json(payload: dict[str, Any], *, file: Any = sys.stdout) -> None:
    print(json.dumps(payload, indent=2), file=file)


@contextmanager
def host_operation_lock(profile: AgentProfile):
    profile.operation_lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(profile.operation_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure", help="configure the dedicated manual Remote lane")
    configure.add_argument("--agent", required=True, choices=sorted(PROFILES))
    configure.add_argument("--home", type=Path, help=argparse.SUPPRESS)

    create = subparsers.add_parser("create", help="create one visible, durable, clearly labeled task")
    create.add_argument("--agent", required=True, choices=sorted(PROFILES))
    create.add_argument("--title", required=True, help="plain-English task purpose; host prefix is automatic")
    create.add_argument(
        "--prompt",
        required=True,
        help="short initial instruction that makes the task visible; never echoed by this helper",
    )
    create.add_argument("--home", type=Path, help=argparse.SUPPRESS)

    guard = subparsers.add_parser("guard", help="repair unclear titles only in the manual Remote lane")
    guard.add_argument("--agent", required=True, choices=sorted(PROFILES))
    guard.add_argument("--home", type=Path, help=argparse.SUPPRESS)
    guard.add_argument("--quiet", action="store_true", help="suppress routine LaunchAgent output")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    profile = profile_for(args.agent, home=args.home)
    quiet = bool(getattr(args, "quiet", False))
    try:
        if args.home is None and Path.home().resolve() != profile.home.resolve():
            raise ValueError(
                f"{profile.display_name} configuration must run under {profile.home}"
            )
        with host_operation_lock(profile):
            if args.command == "configure":
                payload = configure_host_files(profile, helper_path=Path(__file__).resolve())
            else:
                with AppServerClient(profile.socket_path) as client:
                    if args.command == "create":
                        payload = create_labeled_thread(client, profile, args.title, args.prompt)
                    else:
                        payload = guard_manual_titles(client, profile)
    except Exception as exc:
        _print_json(
            {
                "ok": False,
                "agent": profile.display_name,
                "operation": args.command,
                "error": compact_text(exc) or type(exc).__name__,
            },
            file=sys.stderr,
        )
        return 69
    if not quiet:
        _print_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
