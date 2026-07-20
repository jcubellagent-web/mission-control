from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_natural_language_model_requests_cover_every_supported_family() -> None:
    coordinator = load_module("inbox_coordinator", ROOT / "scripts" / "inbox_coordinator.py")
    cases = {
        "Use GPT-5.6 Luna sub-agent for this.": "luna",
        "Run this with GPT-5.6 Terra.": "terra",
        "Launch a sub-agent using GPT-5.6 Sol.": "sol",
        "Use GPT-5.5 for compatibility.": "gpt-5.5",
        "Use GPT-5.4 Mini for this bounded check.": "gpt-5.4-mini",
        "Use Antigravity Gemini 3.5 Flash sub-agent.": "gemini",
        "Use Antigravity Gemini 3.1 Pro sub-agent.": "gemini-pro",
        "Spawn a sub-agent using GLM 5.2.": "glm",
        "Use Ollama sub-agent for a local draft.": "ollama",
        "Use Grok sub-agent for X-native context.": "grok",
    }
    for prompt, expected in cases.items():
        assert coordinator.detect_explicit_route(prompt) == expected


def test_topic_registry_matches_live_control_center_names() -> None:
    config = json.loads((ROOT / "config" / "telegram-intake-lanes.json").read_text())
    topics = config["groups"]["-1003589561528"]["topics"]
    assert {topic_id: row["label"] for topic_id, row in topics.items()} == {
        "1": "Inbox",
        "17": "JAIMES Ops",
        "18": "JOSH 2.0",
        "19": "Sorare",
        "20": "Crypto Alerts",
        "21": "Approvals",
        "22": "Mission Control",
        "56": "News",
    }


def test_openclaw_fresh_lane_uses_real_main_agent_and_provider_prefix() -> None:
    model_lane = load_module("model_lane", ROOT / "scripts" / "model_lane.py")

    class Args:
        transport = "openclaw"
        objective = "Verify route"
        task_type = "summary"
        requested_provider = "ollama"
        requested_model = "qwen2.5:7b"
        privacy = "dashboard-safe"
        prompt = "Return one word."

    route = {
        "agent": "jaimes",
        "modelRoute": {
            "provider": "ollama",
            "model": "qwen2.5:7b",
            "auth": "Local Ollama runtime",
            "reason": "explicit request",
        },
    }
    command = model_lane.command_for(Args(), route)
    assert command[command.index("--agent") + 1] == "main"
    assert command[command.index("--model") + 1] == "ollama/qwen2.5:7b"


def test_remote_specialists_use_current_host_interfaces() -> None:
    coordinator = load_module("inbox_coordinator_executor", ROOT / "scripts" / "inbox_coordinator.py")
    assert coordinator.ROUTES["gemini"]["executor"] == "remote-antigravity"
    assert coordinator.ROUTES["gemini"]["model"] == "agy-gemini-3.5-flash"
    assert coordinator.ROUTES["gemini-pro"]["model"] == "agy-gemini-3.1-pro"
    assert '"--provider", "antigravity"' in coordinator.ANTIGRAVITY_EXECUTOR_CODE
    assert "refusing silent GPT fallback" in coordinator.ANTIGRAVITY_EXECUTOR_CODE
    command, host = coordinator.executor_command(coordinator.ROUTES["grok"], 60)
    joined = " ".join(command)
    assert host == "jaimes"
    assert "grok" in joined
    assert "op_agent_env.sh" not in joined
    assert coordinator.ROUTES["grok"]["executor"] == "remote-grok-cli"
    assert coordinator.ROUTES["grok"]["model"] == "grok-4.5"
    assert '"--model", str(cfg.get("model") or "grok-4.5")' in coordinator.GROK_EXECUTOR_CODE


def test_glm_policy_is_cloud_specific_and_distinct_from_other_models() -> None:
    coordinator = load_module("inbox_coordinator_glm_policy", ROOT / "scripts" / "inbox_coordinator.py")
    agent_route = load_module("agent_route_glm_policy", ROOT / "scripts" / "agent_route.py")

    assert coordinator.ROUTES["glm"]["model"] == "glm-5.2:cloud"
    assert coordinator.ROUTES["glm"]["role"] == "sanitized large-context technical reasoning"
    assert coordinator.ROUTES["glm"]["host"] == "jaimes"
    assert coordinator.ROUTES["glm"]["executor"] == "remote-ollama"
    glm_command, glm_host = coordinator.executor_command(coordinator.ROUTES["glm"], 60)
    assert glm_host == "jaimes"
    assert "OLLAMA_EXECUTOR_CODE" not in " ".join(glm_command)
    assert "Ollama Cloud authentication failed" in coordinator.OLLAMA_EXECUTOR_CODE
    assert agent_route.provider_auth_label("ollama", "glm-5.2:cloud") == "Ollama Cloud"
    assert "structured-code-review" in agent_route.GLM_FIRST_TASK_TYPES
    assert "summary" in agent_route.GEMINI_FIRST_TASK_TYPES
    assert "repo-patch" in agent_route.CODEX_ONLY_TASK_TYPES
    assert "x-search" in agent_route.XAI_FIRST_TASK_TYPES

    josh_args = SimpleNamespace(task_type="structured-code-review", requester="josh2", privacy="dashboard-safe")
    jaimes_args = SimpleNamespace(task_type="structured-code-review", requester="jaimes", privacy="dashboard-safe")
    assert agent_route.hard_owner_for(josh_args) == "josh"
    assert agent_route.hard_owner_for(jaimes_args) == "jaimes"
