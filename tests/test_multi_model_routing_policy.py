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


def allowance_args() -> SimpleNamespace:
    return SimpleNamespace(codex_allowance="auto")


def write_usage(path: Path, remaining: float) -> None:
    path.write_text(json.dumps({
        "codexbarLimits": {
            "codex": {
                "usageWindows": [
                    {"label": "Weekly", "remainingPercent": remaining},
                    {"label": "Codex Spark Weekly", "remainingPercent": 100},
                ]
            }
        }
    }))


def test_exact_codexbar_allowance_drives_conservation(tmp_path, monkeypatch) -> None:
    route = load_module("agent_route_allowance", ROOT / "scripts" / "agent_route.py")
    usage = tmp_path / "usage.json"
    budgets = tmp_path / "budgets.json"
    budgets.write_text(json.dumps({"policy": {"codexAllowanceMode": "auto"}}))
    monkeypatch.setattr(route, "MODEL_USAGE_PATH", usage)
    monkeypatch.setattr(route, "BUDGETS_PATH", budgets)
    monkeypatch.delenv("CODEX_ALLOWANCE_MODE", raising=False)

    write_usage(usage, 13)
    assert route.codex_allowance_mode(allowance_args()) == "conserve"
    write_usage(usage, 0)
    assert route.codex_allowance_mode(allowance_args()) == "exhausted"
    write_usage(usage, 80)
    assert route.codex_allowance_mode(allowance_args()) == "normal"


def test_verified_grok_is_auto_enabled_unless_explicitly_disabled(monkeypatch) -> None:
    route = load_module("agent_route_xai", ROOT / "scripts" / "agent_route.py")
    monkeypatch.setattr(route, "provider_budget", lambda provider: {"authStatus": "available-verified"})
    monkeypatch.setattr(route, "provider_budget_guard", lambda provider: (True, "budget available"))
    monkeypatch.setattr(route, "remote_specialist_available", lambda provider, model="": True)
    monkeypatch.delenv("XAI_ENABLED", raising=False)
    monkeypatch.delenv("XAI_VERIFIED", raising=False)
    assert route.xai_verified_available()[0] is True

    monkeypatch.setenv("XAI_ENABLED", "0")
    available, reason = route.xai_verified_available()
    assert available is False
    assert "explicitly disabled" in reason


def test_antigravity_model_ids_are_executable_not_human_labels() -> None:
    route = load_module("agent_route_gemini", ROOT / "scripts" / "agent_route.py")
    assert route.gemini_model("fast") == "gemini-3.6-flash-medium"
    assert route.gemini_model("review") == "gemini-3.6-flash-high"
    assert route.gemini_model("deep") == "gemini-3.1-pro-high"
    assert route.gemini_model("longContext") == "gemini-3.1-pro-high"


def model_args(*, transport: str = "auto") -> SimpleNamespace:
    return SimpleNamespace(
        transport=transport,
        task_type="summary",
        title="Safe title",
        objective="Safe objective",
        prompt="SENSITIVE_SENTINEL_SHOULD_NOT_APPEAR_IN_PREVIEW",
        privacy="dashboard-safe",
        requester="joshex",
        capability=[],
        requested_provider="",
        requested_model="",
        requested_reason="",
        codex_allowance="auto",
    )


def test_codex_app_specialists_forward_to_jaimes_with_redacted_preview(monkeypatch) -> None:
    lane = load_module("model_lane_remote", ROOT / "scripts" / "model_lane.py")
    monkeypatch.setattr(lane.Path, "home", classmethod(lambda cls: Path("/Users/josh2.0")))
    args = model_args()
    route = {"agent": "joshex", "modelRoute": {
        "provider": "gemini", "model": "gemini-3.6-flash-medium", "reason": "conserve",
        "codexAllowanceMode": "conserve",
    }}
    command = lane.command_for(args, route)
    assert command[:2] == ["ssh", "jaimes"]
    assert "--transport hermes" in command[2]
    assert "--codex-allowance conserve" in command[2]
    preview = lane.command_preview(command)
    assert "prompt redacted" in preview
    assert "SENSITIVE_SENTINEL" not in preview


def test_direct_specialist_commands_cannot_silently_use_gpt(monkeypatch) -> None:
    lane = load_module("model_lane_direct", ROOT / "scripts" / "model_lane.py")
    monkeypatch.setattr(lane.Path, "home", classmethod(lambda cls: Path("/Users/jc_agent")))

    gemini = lane.command_for(model_args(transport="hermes"), {
        "agent": "jaimes", "modelRoute": {"provider": "gemini", "model": "gemini-3.6-flash-medium"},
    })
    assert gemini[1].endswith("scripts/antigravity_pass.py")
    assert "SENSITIVE_SENTINEL" not in lane.command_preview(gemini)

    grok = lane.command_for(model_args(transport="hermes"), {
        "agent": "jaimes", "modelRoute": {"provider": "xai", "model": "grok-4.5"},
    })
    assert grok[0] == "grok"
    assert "--permission-mode" in grok
    assert "SENSITIVE_SENTINEL" not in lane.command_preview(grok)

    glm = lane.command_for(model_args(transport="hermes"), {
        "agent": "jaimes", "modelRoute": {"provider": "ollama", "model": "glm-5.2:cloud"},
    })
    assert glm[1].endswith("scripts/ollama_cloud_pass.py")
    assert "SENSITIVE_SENTINEL" not in lane.command_preview(glm)


def test_antigravity_pass_uses_local_proxy_and_verifies_model(monkeypatch) -> None:
    helper = load_module("antigravity_pass", ROOT / "scripts" / "antigravity_pass.py")
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "model": "gemini-3.6-flash-medium",
                "choices": [{"message": {"content": "GEMINI_OK"}}],
            }).encode()

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["payload"] = json.loads(request.data)
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(helper.urllib.request, "urlopen", fake_urlopen)
    output = helper.run("gemini-3.6-flash-medium", "safe prompt", 30)
    assert output == "GEMINI_OK"
    assert seen["url"] == "http://127.0.0.1:11435/v1/chat/completions"
    assert seen["payload"]["model"] == "gemini-3.6-flash-medium"


def test_shared_skill_requires_real_verified_dispatch() -> None:
    skill = " ".join((ROOT / "agent-skills" / "multi-model-routing" / "SKILL.md").read_text().lower().split())
    assert "never infer success" in skill
    assert "executed route" in skill
    assert "gemini-3.6-flash-medium" in skill
    assert "glm-5.2:cloud" in skill
    assert "grok-4.5" in skill
