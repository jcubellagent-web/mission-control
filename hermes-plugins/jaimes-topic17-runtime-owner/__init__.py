"""Guard JAIMES Ops against model-created Telegram message surfaces.

The gateway and fast-ack watcher own the reaction, managed live card, and
final delivery for JAIMES Ops.  This plugin prevents model tool calls from
creating a second, untracked Telegram surface while leaving ordinary research,
file, and execution work alone.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Optional


_MANAGED_PLATFORM = "telegram"
_MANAGED_CHAT_ID = "-1003589561528"
_MANAGED_THREAD_ID = "17"
_MAX_INSPECTION_CHARS = 128 * 1024

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


def _session_value(name: str) -> str:
    """Read task-local gateway context without process-global leakage."""
    try:
        from gateway.session_context import get_session_env

        return str(get_session_env(name, "") or "")
    except Exception:
        # A missing session context means this is not a gateway Topic 17 turn.
        return ""


def _is_managed_topic() -> bool:
    return (
        _session_value("HERMES_SESSION_PLATFORM").strip().lower()
        == _MANAGED_PLATFORM
        and _session_value("HERMES_SESSION_CHAT_ID").strip()
        == _MANAGED_CHAT_ID
        and _session_value("HERMES_SESSION_THREAD_ID").strip()
        == _MANAGED_THREAD_ID
    )


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
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
