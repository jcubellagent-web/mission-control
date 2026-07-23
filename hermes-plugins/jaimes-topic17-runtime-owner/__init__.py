"""Guard JAIMES Ops against model-created Telegram message surfaces.

The gateway and fast-ack watcher own the reaction, managed live card, and
final delivery for JAIMES Ops.  This plugin prevents model tool calls from
creating a second, untracked Telegram surface while leaving ordinary research,
file, and execution work alone.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional


_MANAGED_PLATFORM = "telegram"
_RUNTIME_OWNER = "jaimes"
_MANAGED_LANE_LABEL = "JAIMES Ops"
_MAX_INSPECTION_CHARS = 128 * 1024

_SILENT_BOT_REASON = "telegram-bot-origin"
_SILENT_NON_OWNER_REASON = "telegram-non-owner"
_SILENT_REGISTRY_REASON = "telegram-ownership-unavailable"


class GatewayLifecycleAbort(BaseException):
    """Abort a managed turn before delivery when its terminal intent is absent."""

_BLOCK_MESSAGE = (
    "JAIMES Ops Telegram and Control Tower lifecycle updates are managed by "
    "the gateway. This direct surface command was blocked so it cannot create "
    "a duplicate or frozen message. Continue the substantive work normally; "
    "the runtime will update the managed live card and deliver the final reply."
)

_OUTBOUND_BOT_API_METHOD_RE = re.compile(
    r"\b(?:"
    r"sendmessage|editmessagetext|editmessagecaption|editmessagereplymarkup|"
    r"deletemessage|copymessage|forwardmessage|sendphoto|senddocument|"
    r"sendvideo|sendanimation|sendaudio|sendvoice|sendmediagroup|"
    r"sendsticker|setmessagereaction|pinchatmessage|unpinchatmessage"
    r")\b",
    re.IGNORECASE,
)
_BOT_API_HOST_RE = re.compile(r"\bapi\.telegram\.org\b", re.IGNORECASE)
_BOT_CREDENTIAL_NAME_RE = re.compile(
    r"\b(?:telegram_bot_token|gateway_telegram_token)\b", re.IGNORECASE
)
_OUTBOUND_TRANSPORT_RE = re.compile(
    r"\b(?:curl|wget|urllib(?:\.request)?|urlopen|requests?|httpx|aiohttp|"
    r"fetch|invoke-webrequest)\b|\.(?:post|request)\s*\(",
    re.IGNORECASE,
)
_TELEGRAM_SDK_SURFACE_RE = re.compile(
    r"\b(?:bot\.)?(?:send_message|edit_message_text|edit_message_caption|"
    r"edit_message_reply_markup|delete_message|set_message_reaction)\s*\(",
    re.IGNORECASE,
)
_TELEGRAM_SDK_MARKER_RE = re.compile(
    r"\b(?:telegram(?:\.ext)?|telebot|telegram_bot_token|"
    r"gateway_telegram_token)\b",
    re.IGNORECASE,
)
_HERMES_TELEGRAM_SEND_RE = re.compile(
    r"\bhermes\s+send\b[^\n;&|]{0,300}\b(?:--to\s+)?telegram\b",
    re.IGNORECASE,
)
_CARD_HELPER_ACTION_RE = re.compile(
    r"\bjaimes_(?:live|work)_card\.py\b[^\n;&|]{0,240}"
    r"\b(?:start|update|done|fail|pause|send|create)\b",
    re.IGNORECASE,
)
_RUNTIME_MODULE_IMPORT_RE = re.compile(
    r"\b(?:"
    r"import\s+(?:jaimes_work_card|jaimes_live_card|"
    r"jaimes_telegram_fast_ack)\b|"
    r"from\s+(?:jaimes_work_card|jaimes_live_card|"
    r"jaimes_telegram_fast_ack)\s+import\b"
    r")",
    re.IGNORECASE,
)
_RUNTIME_SURFACE_HELPER_RE = re.compile(
    r"\b(?:api_call|send_card|edit_card|send_initial_ack|edit_message|"
    r"send_ack)\s*\(",
    re.IGNORECASE,
)
_BRAIN_FEED_HELPER_RE = re.compile(
    r"(?:"
    r"\b(?:bash|zsh|sh)\s+[^\n;&|]{0,200}\bjaimes_bf_push\.sh\b|"
    r"\bpython(?:3(?:\.\d+)?)?\s+[^\n;&|]{0,200}\bagent_publish\.py\b|"
    r"(?:^|[\s;&|])(?:\./|/|~/)[^\s;&|]*"
    r"(?:jaimes_bf_push\.sh|agent_publish\.py)\b"
    r")",
    re.IGNORECASE,
)


def _registry_candidates() -> tuple[Path, ...]:
    """Locate canonical registry code without embedding any topic identifiers."""
    roots: list[Path] = []
    for name in ("MISSION_CONTROL_ROOT", "CONTROL_TOWER_ROOT"):
        value = os.environ.get(name, "").strip()
        if value:
            roots.append(Path(value).expanduser())

    plugin_path = Path(__file__).resolve()
    if len(plugin_path.parents) > 2:
        roots.append(plugin_path.parents[2])
    roots.append(Path.home() / ".openclaw" / "workspace" / "mission-control")

    candidates: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        candidate = root / "scripts" / "telegram_channel_registry.py"
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            candidates.append(candidate)
    return tuple(candidates)


@lru_cache(maxsize=1)
def _load_registry_module() -> Any | None:
    """Load canonical ownership helpers; callers deny Telegram on failure."""
    for path in _registry_candidates():
        try:
            if not path.is_file():
                continue
            spec = importlib.util.spec_from_file_location(
                "_jaimes_telegram_channel_registry",
                path,
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if all(
                callable(getattr(module, name, None))
                for name in (
                    "owner_accepts_source",
                    "telegram_source_is_bot",
                    "topic_matches",
                    "topic_owner",
                )
            ):
                return module
        except Exception:
            continue
    return None


def _platform_name(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


def _on_pre_gateway_dispatch(
    event: Any = None,
    **_: Any,
) -> Optional[dict[str, str]]:
    """Silently reject Telegram bot traffic and messages owned elsewhere."""
    source = getattr(event, "source", None)
    if _platform_name(getattr(source, "platform", "")) != _MANAGED_PLATFORM:
        return None

    if bool(getattr(source, "is_bot", False)):
        return {"action": "skip", "reason": _SILENT_BOT_REASON}

    registry = _load_registry_module()
    if registry is None:
        return {"action": "skip", "reason": _SILENT_REGISTRY_REASON}
    try:
        if registry.telegram_source_is_bot(source):
            return {"action": "skip", "reason": _SILENT_BOT_REASON}
        if not registry.owner_accepts_source(
            _RUNTIME_OWNER,
            source,
            text=getattr(event, "text", ""),
        ):
            return {"action": "skip", "reason": _SILENT_NON_OWNER_REASON}
    except Exception:
        return {"action": "skip", "reason": _SILENT_REGISTRY_REASON}
    return None


def _session_value(name: str) -> str:
    """Read task-local gateway context without process-global leakage."""
    try:
        from gateway.session_context import get_session_env

        return str(get_session_env(name, "") or "")
    except Exception:
        # A missing session context means this is not a gateway Topic 17 turn.
        return ""


def _is_managed_topic() -> bool:
    if (
        _session_value("HERMES_SESSION_PLATFORM").strip().lower()
        != _MANAGED_PLATFORM
    ):
        return False
    registry = _load_registry_module()
    if registry is None:
        return False
    chat_id = _session_value("HERMES_SESSION_CHAT_ID").strip()
    thread_id = _session_value("HERMES_SESSION_THREAD_ID").strip()
    try:
        if registry.topic_matches(
            chat_id,
            thread_id,
            owner=_RUNTIME_OWNER,
            label=_MANAGED_LANE_LABEL,
        ):
            return True
        # Preserve the existing managed-lane tool scope. Ingress ownership is
        # independently fail-closed by ``_on_pre_gateway_dispatch``.
        return False
    except Exception:
        return False


def _active_managed_card(session_id: str) -> Optional[dict[str, Any]]:
    """Recover ownership when Hermes finalizes outside gateway ContextVars.

    Hermes can finish the model turn on a worker thread after the gateway's
    task-local session context has been cleared. The durable fast-ack card is
    already bound to the exact Hermes session and canonical topic, so it is a
    safer fallback than letting an owned final bypass terminal preparation.
    """
    if not str(session_id or "").strip():
        return None
    registry = _load_registry_module()
    if registry is None:
        return None
    try:
        state = json.loads(
            (Path.home() / ".openclaw" / "telegram" / "jaimes_fast_ack_state.json").read_text()
        )
    except Exception:
        return None
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for run_id, card in (state.get("active_cards") or {}).items():
        if not isinstance(card, dict):
            continue
        if str(card.get("session_id") or "") != str(session_id):
            continue
        if str(card.get("status") or "").lower() not in {
            "active",
            "awaiting-final-gate",
            "closing-before-final",
        }:
            continue
        chat_id = str(card.get("telegram_chat_id") or "")
        thread_id = str(card.get("telegram_thread_id") or "")
        try:
            accepts = getattr(registry, "owner_accepts", None)
            if callable(accepts):
                owned = bool(accepts(_RUNTIME_OWNER, chat_id, thread_id, direct=not bool(thread_id)))
            else:
                owned = str(registry.topic_owner(chat_id, thread_id) or "") == _RUNTIME_OWNER
        except Exception:
            owned = False
        if owned:
            candidates.append((
                str(card.get("started_at") or card.get("task_started_at") or ""),
                str(run_id),
                dict(card),
            ))
    if not candidates:
        return None
    #JAIMES: terminal preparation must bind to the newest exact card in a
    # long-lived Telegram session; returning the first dict entry can attach a
    # final to an older still-active turn after media batching or compaction.
    _, run_id, card = max(candidates, key=lambda item: (item[0], item[1]))
    card["_runtime_run_id"] = run_id
    return card


def _is_owned_telegram_session(session_id: str = "") -> bool:
    if _session_value("HERMES_SESSION_PLATFORM").strip().lower() != _MANAGED_PLATFORM:
        return _active_managed_card(session_id) is not None
    registry = _load_registry_module()
    if registry is None:
        return False
    chat_id = _session_value("HERMES_SESSION_CHAT_ID").strip()
    thread_id = _session_value("HERMES_SESSION_THREAD_ID").strip()
    try:
        accepts = getattr(registry, "owner_accepts", None)
        if callable(accepts):
            return bool(accepts(_RUNTIME_OWNER, chat_id, thread_id, direct=not bool(thread_id)))
        return str(registry.topic_owner(chat_id, thread_id) or "") == _RUNTIME_OWNER
    except Exception:
        return False


def _mission_control_root() -> Path:
    for name in ("MISSION_CONTROL_ROOT", "CONTROL_TOWER_ROOT"):
        value = os.environ.get(name, "").strip()
        if value:
            return Path(value).expanduser()
    plugin_path = Path(__file__).resolve()
    if len(plugin_path.parents) > 2:
        candidate = plugin_path.parents[2]
        if (candidate / "scripts" / "jaimes_telegram_fast_ack.py").is_file():
            return candidate
    return Path.home() / ".openclaw" / "workspace" / "mission-control"


def _writer_rollout_required(session_id: str) -> bool:
    root = _mission_control_root()
    try:
        rollout = json.loads((root / "config" / "telegram-lifecycle-rollout.json").read_text())
        if rollout.get("globalKillSwitch"):
            return True
        if str(rollout.get("masterState") or "off") in {"jaimes", "all"}:
            return True
    except Exception:
        return True
    try:
        state = json.loads(
            (Path.home() / ".openclaw" / "telegram" / "jaimes_fast_ack_state.json").read_text()
        )
        for card in (state.get("active_cards") or {}).values():
            if (
                isinstance(card, dict)
                and card.get("status") == "active"
                and str(card.get("session_id") or "") == str(session_id or "")
                and card.get("lifecycle_writer_enabled") is True
            ):
                return True
    except Exception:
        pass
    return False


def _on_transform_llm_output(
    response_text: str = "",
    session_id: str = "",
    model: str = "",
    platform: str = "",
    **_: Any,
) -> Optional[str]:
    """Commit the terminal outbox before Hermes performs its native send."""
    if str(platform or "").strip().lower() != _MANAGED_PLATFORM:
        return None
    recovered_card = _active_managed_card(session_id)
    if not _is_owned_telegram_session(session_id):
        return None
    root = _mission_control_root()
    script = root / "scripts" / "jaimes_telegram_fast_ack.py"
    required = _writer_rollout_required(session_id)
    payload = {
        "response_text": str(response_text or ""),
        "session_id": str(session_id or ""),
        "model": str(model or ""),
        "card_run_id": str((recovered_card or {}).get("_runtime_run_id") or ""),
        "inbound_message_id": (
            str((recovered_card or {}).get("inbound_message_id") or "")
            or _session_value("HERMES_SESSION_MESSAGE_ID").strip()
        ),
    }
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--prepare-terminal-json-stdin"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
            cwd=root,
        )
        if result.returncode != 0:
            raise RuntimeError("terminal-preparation-command-failed")
        receipt = json.loads(result.stdout)
        if not isinstance(receipt, dict) or receipt.get("ok") is not True:
            raise RuntimeError("terminal-preparation-receipt-invalid")
        if receipt.get("managed") is not True:
            if required:
                raise RuntimeError("required-terminal-lifecycle-not-managed")
            return None
        transformed = str(receipt.get("text") or "")
        if not transformed:
            raise RuntimeError("terminal-preparation-empty-final")
        return transformed
    except Exception as exc:
        if required:
            # Hermes deliberately catches ordinary Exception from plugins and
            # would otherwise deliver the untracked model text. BaseException
            # crosses that boundary and fails the managed turn closed.
            raise GatewayLifecycleAbort("managed Telegram terminal preparation failed") from exc
        return None

def _payload_text(value: Any) -> str:
    """Flatten bounded tool arguments for narrow execution-pattern checks."""
    parts: list[str] = []
    remaining = _MAX_INSPECTION_CHARS

    def collect(item: Any, depth: int = 0) -> None:
        nonlocal remaining
        if remaining <= 0 or depth > 8:
            return
        if isinstance(item, str):
            chunk = item[:remaining]
            parts.append(chunk)
            remaining -= len(chunk)
            return
        if isinstance(item, Mapping):
            for key, child in item.items():
                collect(key, depth + 1)
                collect(child, depth + 1)
                if remaining <= 0:
                    break
            return
        if isinstance(item, (list, tuple, set, frozenset)):
            for child in item:
                collect(child, depth + 1)
                if remaining <= 0:
                    break

    collect(value)
    return "\n".join(parts)


def _send_message_targets_telegram(args: Any) -> bool:
    """Return whether a model send_message call would touch Telegram."""
    if not isinstance(args, Mapping):
        return True
    action = str(args.get("action", "send") or "send").strip().lower()
    if action == "list":
        return False

    target = str(args.get("target", "") or "").strip().lower()
    if not target:
        # In a Telegram gateway turn an omitted target is ambiguous and can
        # resolve back to the active Telegram destination. Fail closed.
        return True
    return target.split(":", 1)[0].strip() == "telegram"


def _is_raw_telegram_surface(payload: str) -> bool:
    if not payload:
        return False

    if _HERMES_TELEGRAM_SEND_RE.search(payload):
        return True

    outbound_method = _OUTBOUND_BOT_API_METHOD_RE.search(payload)
    bot_destination = (
        _BOT_API_HOST_RE.search(payload) or _BOT_CREDENTIAL_NAME_RE.search(payload)
    )
    if outbound_method and bot_destination and _OUTBOUND_TRANSPORT_RE.search(payload):
        return True

    if (
        _TELEGRAM_SDK_SURFACE_RE.search(payload)
        and _TELEGRAM_SDK_MARKER_RE.search(payload)
    ):
        return True

    if (
        _RUNTIME_MODULE_IMPORT_RE.search(payload)
        and _RUNTIME_SURFACE_HELPER_RE.search(payload)
    ):
        return True

    if _BRAIN_FEED_HELPER_RE.search(payload):
        return True

    return bool(_CARD_HELPER_ACTION_RE.search(payload))


def _on_pre_tool_call(
    tool_name: str = "",
    args: Any = None,
    **_: Any,
) -> Optional[dict[str, str]]:
    """Block only model-owned Telegram surfaces in JAIMES Ops Topic 17."""
    if not _is_managed_topic():
        return None

    normalized_tool = str(tool_name or "").strip().lower()
    if normalized_tool == "send_message" and _send_message_targets_telegram(args):
        return {"action": "block", "message": _BLOCK_MESSAGE}

    if normalized_tool in {"terminal", "execute_code"}:
        if _is_raw_telegram_surface(_payload_text(args)):
            return {"action": "block", "message": _BLOCK_MESSAGE}

    return None


def register(ctx: Any) -> None:
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("transform_llm_output", _on_transform_llm_output)
