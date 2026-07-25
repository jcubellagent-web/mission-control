from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def allowance_args() -> SimpleNamespace:
    return SimpleNamespace(codex_allowance="auto")


def write_usage(
    path: Path,
    remaining: float,
    will_last_to_reset: bool | None = None,
    reset_credits: int = 0,
) -> None:
    limits = {
        "codexResetCredits": {"availableCount": reset_credits},
        "usageWindows": [
            {"label": "Weekly", "remainingPercent": remaining},
            {"label": "Codex Spark Weekly", "remainingPercent": 100},
        ]
    }
    if will_last_to_reset is not None:
        limits["pace"] = {"secondary": {"willLastToReset": will_last_to_reset}}
    path.write_text(json.dumps({
        "codexbarLimits": {
            "codex": limits
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


def test_available_reset_credit_defers_codex_conservation(tmp_path, monkeypatch) -> None:
    route = load_module("agent_route_reset_credit", ROOT / "scripts" / "agent_route.py")
    usage = tmp_path / "usage.json"
    budgets = tmp_path / "budgets.json"
    budgets.write_text(json.dumps({"policy": {"codexAllowanceMode": "auto"}}))
    monkeypatch.setattr(route, "MODEL_USAGE_PATH", usage)
    monkeypatch.setattr(route, "BUDGETS_PATH", budgets)
    monkeypatch.delenv("CODEX_ALLOWANCE_MODE", raising=False)

    write_usage(usage, 13, will_last_to_reset=False, reset_credits=1)
    assert route.codex_allowance_mode(allowance_args()) == "normal"
    write_usage(usage, 29, will_last_to_reset=False, reset_credits=1)
    assert route.codex_allowance_mode(allowance_args()) == "normal"
    write_usage(usage, 29, will_last_to_reset=False, reset_credits=0)
    assert route.codex_allowance_mode(allowance_args()) == "conserve"
    write_usage(usage, 0, will_last_to_reset=True, reset_credits=1)
    assert route.codex_allowance_mode(allowance_args()) == "exhausted"


def test_explicit_allowance_overrides_reset_credit_telemetry(tmp_path, monkeypatch) -> None:
    route = load_module("agent_route_reset_credit_override", ROOT / "scripts" / "agent_route.py")
    usage = tmp_path / "usage.json"
    monkeypatch.setattr(route, "MODEL_USAGE_PATH", usage)
    write_usage(usage, 13, will_last_to_reset=False, reset_credits=1)

    assert route.codex_allowance_mode(SimpleNamespace(codex_allowance="conserve")) == "conserve"
    monkeypatch.setenv("CODEX_ALLOWANCE_MODE", "exhausted")
    assert route.codex_allowance_mode(allowance_args()) == "exhausted"


def test_codexbar_exhaustion_pace_conserves_before_static_threshold(tmp_path, monkeypatch) -> None:
    route = load_module("agent_route_pace", ROOT / "scripts" / "agent_route.py")
    usage = tmp_path / "usage.json"
    budgets = tmp_path / "budgets.json"
    budgets.write_text(json.dumps({"policy": {"codexAllowanceMode": "auto"}}))
    monkeypatch.setattr(route, "MODEL_USAGE_PATH", usage)
    monkeypatch.setattr(route, "BUDGETS_PATH", budgets)
    monkeypatch.delenv("CODEX_ALLOWANCE_MODE", raising=False)

    write_usage(usage, 29, will_last_to_reset=False)
    assert route.codex_allowance_mode(allowance_args()) == "conserve"
    write_usage(usage, 29, will_last_to_reset=True)
    assert route.codex_allowance_mode(allowance_args()) == "normal"
    write_usage(usage, 29)
    assert route.codex_allowance_mode(allowance_args()) == "normal"
    write_usage(usage, 20, will_last_to_reset=True)
    assert route.codex_allowance_mode(allowance_args()) == "conserve"
    write_usage(usage, 0, will_last_to_reset=True)
    assert route.codex_allowance_mode(allowance_args()) == "exhausted"


def test_exact_weekly_window_derives_conservation_when_pace_is_missing(tmp_path, monkeypatch) -> None:
    route = load_module("agent_route_derived_pace", ROOT / "scripts" / "agent_route.py")
    usage = tmp_path / "usage.json"
    budgets = tmp_path / "budgets.json"
    budgets.write_text(json.dumps({"policy": {"codexAllowanceMode": "auto"}}))
    monkeypatch.setattr(route, "MODEL_USAGE_PATH", usage)
    monkeypatch.setattr(route, "BUDGETS_PATH", budgets)
    monkeypatch.delenv("CODEX_ALLOWANCE_MODE", raising=False)
    now = route.dt.datetime.now(route.dt.timezone.utc)
    reset = (now + route.dt.timedelta(days=4)).isoformat().replace("+00:00", "Z")

    def write_window(used: float) -> None:
        usage.write_text(json.dumps({"codexbarLimits": {"codex": {"usageWindows": [{
            "label": "Weekly",
            "usedPercent": used,
            "remainingPercent": 100 - used,
            "windowMinutes": 10080,
            "resetsAt": reset,
        }]}}}))

    write_window(72)
    assert route.codex_allowance_mode(allowance_args()) == "conserve"
    write_window(30)
    assert route.codex_allowance_mode(allowance_args()) == "normal"


def test_codexbar_pace_projection_is_preserved(monkeypatch) -> None:
    update = load_module("update_mission_control_pace", ROOT / "scripts" / "update_mission_control.py")

    class Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps([{
            "source": "oauth",
            "pace": {"secondary": {
                "expectedUsedPercent": 43,
                "deltaPercent": 28,
                "stage": "farAhead",
                "willLastToReset": False,
                "etaSeconds": 105108,
                "privateUnexpectedField": "drop-me",
            }},
            "usage": {
                "updatedAt": "2026-07-24T16:52:40Z",
                "loginMethod": "pro",
                "identity": {"providerID": "codex"},
                "secondary": {"usedPercent": 71, "windowMinutes": 10080},
            },
        }])

    monkeypatch.setattr(update.subprocess, "run", lambda *_args, **_kwargs: Result())
    limits = update.fetch_codexbar_limits("codex")

    assert limits["pace"]["secondary"]["willLastToReset"] is False
    assert limits["pace"]["secondary"]["deltaPercent"] == 28
    assert "privateUnexpectedField" not in limits["pace"]["secondary"]


def test_verified_grok_is_auto_enabled_unless_explicitly_disabled(monkeypatch) -> None:
    route = load_module("agent_route_xai", ROOT / "scripts" / "agent_route.py")
    monkeypatch.setattr(route, "provider_budget", lambda provider: {"authStatus": "available-verified"})
    monkeypatch.setattr(route, "provider_budget_guard", lambda provider: (True, "budget available"))
    monkeypatch.setattr(route, "remote_specialist_available", lambda provider, model="": True)
    monkeypatch.setattr(route, "xai_live_allowance_status", lambda: (True, "98% remaining"))
    monkeypatch.delenv("XAI_ENABLED", raising=False)
    monkeypatch.delenv("XAI_VERIFIED", raising=False)
    assert route.xai_verified_available()[0] is True

    monkeypatch.setenv("XAI_ENABLED", "0")
    available, reason = route.xai_verified_available()
    assert available is False
    assert "explicitly disabled" in reason


def test_live_grok_allowance_is_read_from_codexbar(tmp_path, monkeypatch) -> None:
    route = load_module("agent_route_xai_allowance", ROOT / "scripts" / "agent_route.py")
    usage = tmp_path / "modelUsage.json"
    usage.write_text(json.dumps({
        "codexbarLimits": {"xai": {
            "available": True,
            "status": "ready",
            "codexbarUpdatedAt": "2026-07-21T23:20:00Z",
            "usageWindows": [{"label": "Session", "remainingPercent": 98}],
        }}
    }))
    monkeypatch.setattr(route, "MODEL_USAGE_PATH", usage)

    healthy, reason = route.xai_live_allowance_status(
        route.dt.datetime(2026, 7, 21, 23, 25, tzinfo=route.dt.timezone.utc)
    )
    assert healthy is True
    assert "98% remaining" in reason

    payload = json.loads(usage.read_text())
    payload["codexbarLimits"]["xai"]["usageWindows"][0]["remainingPercent"] = 0
    usage.write_text(json.dumps(payload))
    healthy, reason = route.xai_live_allowance_status(
        route.dt.datetime(2026, 7, 21, 23, 25, tzinfo=route.dt.timezone.utc)
    )
    assert healthy is False
    assert "exhausted" in reason


def test_x_search_falls_back_to_authenticated_ui_when_grok_is_unavailable(monkeypatch) -> None:
    route = load_module("agent_route_x_fallback", ROOT / "scripts" / "agent_route.py")
    args = model_args()
    args.task_type = "x-search"
    args.priority = "normal"
    monkeypatch.setattr(route, "xai_verified_available", lambda: (False, "SuperGrok allowance is exhausted"))
    monkeypatch.setattr(route, "codex_allowance_mode", lambda _args: "normal")

    selected = route.choose_model_route(args, "joshex", False)

    assert selected["provider"] == "codex"
    assert selected["fallbackPath"] == "authenticated-x-ui"
    assert selected["fallbackLadder"] == [
        "authenticated-x-ui", "forwarded-x-links", "public-web-primary-sources"
    ]
    assert "session canary" in " ".join(selected["guardrails"]).lower()

    args.requested_provider = "xai"
    args.requested_model = "grok-4.5"
    explicit = route.choose_model_route(args, "joshex", False)
    assert explicit["provider"] == "codex"
    assert explicit["explicitRequest"] is True
    assert explicit["fallbackPath"] == "authenticated-x-ui"
    assert explicit["fallbackFrom"] == "xai"


def test_antigravity_model_ids_are_executable_not_human_labels() -> None:
    route = load_module("agent_route_gemini", ROOT / "scripts" / "agent_route.py")
    assert route.gemini_model("fast") == "gemini-3.6-flash-medium"
    assert route.gemini_model("review") == "gemini-3.6-flash-high"
    assert route.gemini_model("deep") == "gemini-3.1-pro-high"
    assert route.gemini_model("longContext") == "gemini-3.1-pro-high"
    assert route.normalize_requested_model("gemini", "agy-gemini-3.6-flash-medium") == "gemini-3.6-flash-medium"
    assert route.normalize_requested_model("gemini", "antigravity/gemini-3.1-pro-high") == "gemini-3.1-pro-high"
    assert route.normalize_requested_model("gemini", "google-gemini-cli/gemini-3.6-flash-high") == "gemini-3.6-flash-high"


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
        controller_work_id="controller-work",
        controller_run_id="controller-run",
        lane_id="test-model-lane",
        lane_visibility="required",
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
    assert "--lane-visibility parent-owned" in command[2]
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


@pytest.mark.parametrize("task_type", ["architecture", "technical-analysis"])
def test_technical_analysis_uses_glm_with_explicit_runtime_fallbacks(monkeypatch, task_type) -> None:
    route = load_module("agent_route_glm_architecture", ROOT / "scripts" / "agent_route.py")
    args = model_args()
    args.task_type = task_type
    args.priority = "normal"
    args.complexity = "auto"
    args.blast_radius = "auto"
    monkeypatch.setattr(route, "codex_allowance_mode", lambda _args: "normal")
    monkeypatch.setattr(route, "explicit_route_unavailable", lambda provider, model="": "")
    monkeypatch.setattr(route, "ollama_live_allowance_status", lambda: (True, "99% remaining"))

    selected = route.choose_model_route(args, "jaimes", False)

    assert selected["provider"] == "ollama"
    assert selected["model"] == "glm-5.2:cloud"
    assert selected["fallbackLadder"] == [
        "ollama/glm-5.2:cloud", "gemini/gemini-3.1-pro-high", "codex/gpt-5.6-terra"
    ]
    assert [row["provider"] for row in selected["fallbackRoutes"]] == ["gemini", "codex"]
    assert selected["spendClass"] == "quota-favored-cloud-specialist"
    assert "99% remaining" in selected["reason"]


def test_dashboard_safe_sorare_analytics_and_reports_use_specialists(monkeypatch) -> None:
    route = load_module("agent_route_sorare_specialists", ROOT / "scripts" / "agent_route.py")
    args = model_args()
    args.requester = "jaimes"
    args.priority = "normal"
    args.complexity = "auto"
    args.blast_radius = "auto"
    monkeypatch.setattr(route, "codex_allowance_mode", lambda _args: "normal")
    monkeypatch.setattr(route, "explicit_route_unavailable", lambda provider, model="": "")
    monkeypatch.setattr(route, "ollama_live_allowance_status", lambda: (True, "98.8% remaining"))

    args.task_type = "sorare-report"
    report = route.choose_model_route(args, "jaimes", False)
    assert report["provider"] == "gemini"
    assert report["model"] == "gemini-3.6-flash-medium"

    args.task_type = "sorare-analytics"
    analytics = route.choose_model_route(args, "jaimes", False)
    assert analytics["provider"] == "ollama"
    assert analytics["model"] == "glm-5.2:cloud"


def test_sorare_mutations_and_private_analytics_remain_codex_owned(monkeypatch) -> None:
    route = load_module("agent_route_sorare_execution", ROOT / "scripts" / "agent_route.py")
    args = model_args()
    args.requester = "jaimes"
    args.priority = "normal"
    args.complexity = "auto"
    args.blast_radius = "auto"
    monkeypatch.setattr(route, "codex_allowance_mode", lambda _args: "normal")
    monkeypatch.setattr(route, "ollama_live_allowance_status", lambda: (True, "98.8% remaining"))

    args.task_type = "sorare-lineup"
    assert route.choose_model_route(args, "jaimes", False)["provider"] == "codex"

    args.task_type = "sorare-analytics"
    args.privacy = "agent-private"
    assert route.choose_model_route(args, "jaimes", False)["provider"] == "codex"


def test_sorare_codex_and_gemini_routes_skip_unneeded_provider_probes(monkeypatch) -> None:
    route = load_module("agent_route_sorare_probe_scope", ROOT / "scripts" / "agent_route.py")
    args = model_args()
    args.requester = "jaimes"
    args.priority = "normal"
    args.complexity = "auto"
    args.blast_radius = "auto"
    monkeypatch.setattr(route, "codex_allowance_mode", lambda _args: "normal")

    def unexpected_probe():
        raise AssertionError("unrelated provider availability was probed")

    monkeypatch.setattr(route, "ollama_live_allowance_status", unexpected_probe)
    monkeypatch.setattr(route, "xai_verified_available", unexpected_probe)

    args.task_type = "sorare-report"
    assert route.choose_model_route(args, "jaimes", False)["provider"] == "gemini"

    args.task_type = "sorare-lineup"
    assert route.choose_model_route(args, "jaimes", False)["provider"] == "codex"


def test_ollama_quota_is_a_fresh_soft_signal_and_exhaustion_falls_back(tmp_path, monkeypatch) -> None:
    route = load_module("agent_route_glm_quota", ROOT / "scripts" / "agent_route.py")
    usage = tmp_path / "modelUsage.json"
    usage.write_text(json.dumps({"codexbarLimits": {"ollama": {
        "available": True,
        "status": "ready",
        "quotaTelemetryStatus": "fresh",
        "codexbarUpdatedAt": "2026-07-24T03:55:00Z",
        "usageWindows": [
            {"label": "Session", "remainingPercent": 100},
            {"label": "Weekly", "remainingPercent": 99.3},
        ],
    }}}))
    monkeypatch.setattr(route, "MODEL_USAGE_PATH", usage)
    healthy, reason = route.ollama_live_allowance_status(
        route.dt.datetime(2026, 7, 24, 4, 0, tzinfo=route.dt.timezone.utc)
    )
    assert healthy is True
    assert "99.3% remaining" in reason

    args = model_args()
    args.task_type = "technical-analysis"
    args.priority = "normal"
    args.complexity = "auto"
    args.blast_radius = "auto"
    monkeypatch.setattr(route, "codex_allowance_mode", lambda _args: "normal")
    monkeypatch.setattr(route, "ollama_live_allowance_status", lambda: (False, "Ollama live allowance is exhausted"))
    selected = route.choose_model_route(args, "jaimes", False)
    assert selected["provider"] == "gemini"
    assert selected["role"] == "glm-allowance-fallback"
    assert selected["fallbackFrom"] == "ollama"
    assert [row["provider"] for row in selected["fallbackRoutes"]] == ["codex"]


def test_stale_local_ollama_quota_uses_canonical_allowlisted_projection(tmp_path, monkeypatch) -> None:
    route = load_module("agent_route_glm_canonical_quota", ROOT / "scripts" / "agent_route.py")
    usage = tmp_path / "modelUsage.json"
    usage.write_text(json.dumps({"codexbarLimits": {"ollama": {
        "available": True,
        "status": "ready",
        "codexbarUpdatedAt": "2026-07-24T19:30:00Z",
        "usageWindows": [{"label": "Weekly", "remainingPercent": 98.8}],
    }}}))
    monkeypatch.setattr(route, "MODEL_USAGE_PATH", usage)
    monkeypatch.setattr(route, "canonical_ollama_allowance_limits", lambda: {
        "available": True,
        "status": "ready",
        "quotaTelemetryStatus": "fresh",
        "codexbarUpdatedAt": "2026-07-24T23:05:00Z",
        "usageWindows": [
            {"label": "Session", "remainingPercent": 99.5},
            {"label": "Weekly", "remainingPercent": 98.7},
        ],
    })

    healthy, reason = route.ollama_live_allowance_status(
        route.dt.datetime(2026, 7, 24, 23, 6, tzinfo=route.dt.timezone.utc)
    )
    assert healthy is True
    assert "Control Tower" in reason
    assert "98.7% remaining" in reason

    payload = json.loads(usage.read_text())
    payload["codexbarLimits"]["ollama"].update({"available": False, "status": "error"})
    payload["codexbarLimits"]["ollama"]["codexbarUpdatedAt"] = "2026-07-24T23:05:00Z"
    usage.write_text(json.dumps(payload))
    monkeypatch.setattr(
        route,
        "canonical_ollama_allowance_limits",
        lambda: (_ for _ in ()).throw(AssertionError("explicit local failure must not use remote quota")),
    )
    healthy, reason = route.ollama_live_allowance_status(
        route.dt.datetime(2026, 7, 24, 23, 6, tzinfo=route.dt.timezone.utc)
    )
    assert healthy is False
    assert "error" in reason


def test_canonical_ollama_lookup_uses_control_tower_account(monkeypatch) -> None:
    route = load_module("agent_route_glm_canonical_host", ROOT / "scripts" / "agent_route.py")
    monkeypatch.setattr(route.Path, "home", classmethod(lambda cls: Path("/Users/jc_agent")))
    commands = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = json.dumps({
            "schemaVersion": 1,
            "provider": "ollama",
            "observedAt": "2026-07-24T23:05:00Z",
            "exportedAt": "2026-07-24T23:05:10Z",
            "windows": [{"label": "Weekly", "remainingPercent": 98.7}],
            "unexpectedIdentity": "drop-me",
        })

    def fake_run(command, **_kwargs):
        commands.append(command)
        return Result()

    monkeypatch.setattr(route.subprocess, "run", fake_run)
    limits = route.canonical_ollama_allowance_limits()

    assert commands[0][commands[0].index("ConnectTimeout=5") + 1] == "josh2.0@josh2"
    assert commands[0][-2:] == [
        "cat",
        "/Users/josh2.0/.openclaw/workspace/mission-control/data/codexbar-quota-ollama.json",
    ]
    assert limits is not None
    assert "unexpectedIdentity" not in limits


def test_surplus_ollama_quota_expands_glm_first_stop_weighting(monkeypatch) -> None:
    route = load_module("agent_route_glm_surplus", ROOT / "scripts" / "agent_route.py")
    args = model_args()
    args.task_type = "deep-review"
    args.priority = "normal"
    args.complexity = "auto"
    args.blast_radius = "auto"
    monkeypatch.setattr(route, "codex_allowance_mode", lambda _args: "normal")
    monkeypatch.setattr(route, "explicit_route_unavailable", lambda provider, model="": "")

    monkeypatch.setattr(route, "ollama_live_allowance_status", lambda: (True, "Ollama live allowance has 80% remaining"))
    selected = route.choose_model_route(args, "joshex", False)
    assert selected["provider"] == "ollama"
    assert selected["role"] == "glm-surplus-capacity-reasoning"
    assert selected["spendClass"] == "surplus-quota-favored-cloud-specialist"

    monkeypatch.setattr(route, "ollama_live_allowance_status", lambda: (True, "Ollama live allowance has 79.9% remaining"))
    assert route.choose_model_route(args, "joshex", False)["provider"] == "codex"

    monkeypatch.setattr(route, "ollama_live_allowance_status", lambda: (None, "Ollama quota telemetry is stale"))
    assert route.choose_model_route(args, "joshex", False)["provider"] == "codex"

    monkeypatch.setattr(route, "ollama_live_allowance_status", lambda: (True, "Ollama live allowance has 99% remaining"))
    args.task_type = "summary"
    assert route.choose_model_route(args, "joshex", False)["provider"] == "gemini"
    args.task_type = "repo-patch"
    assert route.choose_model_route(args, "joshex", False)["provider"] == "codex"
    args.task_type = "deep-review"
    args.privacy = "agent-private"
    assert route.choose_model_route(args, "joshex", False)["provider"] == "codex"


def test_automatic_model_lane_discloses_and_executes_declared_fallback(monkeypatch, capsys) -> None:
    lane = load_module("model_lane_runtime_fallback", ROOT / "scripts" / "model_lane.py")
    args = model_args(transport="hermes")
    attempts = []

    def fake_command(_args, route):
        model_route = route["modelRoute"]
        return [str(model_route["provider"]), str(model_route["model"])]

    def fake_execute(command, _route):
        attempts.append(command)
        return 3 if len(attempts) == 1 else 0

    monkeypatch.setattr(lane, "command_for", fake_command)
    monkeypatch.setattr(lane, "execute_verified", fake_execute)
    route = {"modelRoute": {
        "provider": "gemini",
        "model": "gemini-3.6-flash-medium",
        "fallbackRoutes": [{"provider": "ollama", "model": "glm-5.2:cloud"}],
    }}

    result = lane.execute_with_disclosed_fallbacks(args, ["gemini", "primary"], route)

    assert result == 0
    assert attempts == [["gemini", "primary"], ["ollama", "glm-5.2:cloud"]]
    assert "Fallback disclosure" in capsys.readouterr().err


def test_real_model_lane_requires_exact_controller_provenance() -> None:
    lane = load_module("model_lane_parent_contract", ROOT / "scripts" / "model_lane.py")
    args = model_args()
    args.controller_work_id = ""
    with pytest.raises(SystemExit, match="requires --controller-work-id"):
        lane.validate_lane_visibility(args)
    args.lane_visibility = "diagnostic"
    lane.validate_lane_visibility(args)


def test_model_lane_publish_is_a_verified_nested_worker() -> None:
    lane = load_module("model_lane_worker_publish", ROOT / "scripts" / "model_lane.py")
    args = model_args()
    route = {"agent": "joshex", "modelRoute": {
        "provider": "ollama", "model": "glm-5.2:cloud",
    }}
    command = lane.lane_publish_command(
        args,
        route,
        work_id="lane-work",
        run_id="lane-run",
        work_event="start",
        status="active",
        phase="working",
        detail="Separate lane is active.",
    )
    rendered = " ".join(command)
    assert "--execution-role worker" in rendered
    assert "--controller-work-id controller-work" in rendered
    assert "--controller-run-id controller-run" in rendered
    assert "--model-family ollama" in rendered
    assert "--model-id glm-5.2:cloud" in rendered
    assert "--route-verified" in command


def test_model_lane_worker_preserves_controller_owner_across_remote_host() -> None:
    lane = load_module("model_lane_cross_host_owner", ROOT / "scripts" / "model_lane.py")
    args = model_args()
    route = {"agent": "jaimes", "modelRoute": {
        "provider": "xai", "model": "grok-4.5",
    }}
    command = lane.lane_publish_command(
        args,
        route,
        work_id="lane-work",
        run_id="lane-run",
        work_event="start",
        status="active",
        phase="working",
        detail="Separate lane is active.",
    )
    assert command[command.index("--agent") + 1] == "joshex"


def test_xai_subscription_defaults_and_prefixes_use_current_cli_model() -> None:
    route = load_module("agent_route_grok_model", ROOT / "scripts" / "agent_route.py")
    assert route.PROVIDER_DEFAULT_MODELS["xai"] == "grok-4.5"
    assert route.normalize_requested_model("xai", "xai/grok-4.5") == "grok-4.5"


def test_parent_owned_child_preserves_controller_verified_grok_route() -> None:
    lane = load_module("model_lane_parent_route", ROOT / "scripts" / "model_lane.py")
    args = model_args(transport="hermes")
    args.lane_visibility = "parent-owned"
    args.requested_provider = "xai"
    args.requested_model = "grok-4.5"
    args.requested_reason = "fresh parent allowance and runtime verification"
    stale_child_route = {"agent": "jaimes", "modelRoute": {
        "provider": "codex", "model": "gpt-5.6-terra", "role": "xai-unavailable-fallback",
    }}
    preserved = lane.preserve_parent_owned_route(args, stale_child_route)
    assert preserved["modelRoute"]["provider"] == "xai"
    assert preserved["modelRoute"]["model"] == "grok-4.5"
    assert preserved["modelRoute"]["role"] == "parent-verified-specialist"


def test_remote_child_model_mismatch_is_rejected(monkeypatch, capsys) -> None:
    lane = load_module("model_lane_remote_identity", ROOT / "scripts" / "model_lane.py")

    class Result:
        returncode = 0
        stdout = "Active Model/Auth: gpt-5.6-terra (OpenAI Codex OAuth/subscription)\nOK\n"
        stderr = ""

    monkeypatch.setattr(lane.subprocess, "run", lambda *_args, **_kwargs: Result())
    result = lane.execute_verified(
        ["ssh", "jaimes", "<redacted>"],
        {"modelRoute": {"provider": "xai", "model": "grok-4.5"}},
    )
    assert result == 3
    assert "expected model grok-4.5" in capsys.readouterr().err


def test_jaimes_lane_visibility_publishes_to_canonical_control_tower(monkeypatch) -> None:
    lane = load_module("model_lane_canonical_publish", ROOT / "scripts" / "model_lane.py")
    monkeypatch.setattr(lane.Path, "home", classmethod(lambda cls: Path("/Users/jc_agent")))
    seen = {}

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **_kwargs):
        seen["command"] = command
        return Result()

    monkeypatch.setattr(lane.subprocess, "run", fake_run)
    lane.run_lane_publish(["python3", "/repo/scripts/agent_publish.py", "--agent", "jaimes"])
    assert seen["command"][:2] == ["ssh", "josh2.0@josh2"]
    assert "scripts/agent_publish.py --agent jaimes" in seen["command"][2]


def test_ollama_cloud_pass_verifies_returned_model_identity(monkeypatch) -> None:
    helper = load_module("ollama_cloud_pass_identity", ROOT / "scripts" / "ollama_cloud_pass.py")

    class Response:
        def __init__(self, model: str):
            self.model = model

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"model": self.model, "response": "GLM_OK"}).encode()

    monkeypatch.setattr(
        helper.urllib.request,
        "urlopen",
        lambda _request, timeout: Response("glm-5.2"),
    )
    assert helper.run("glm-5.2:cloud", "safe prompt", 30) == "GLM_OK"


def test_ollama_cloud_pass_rejects_unexpected_or_missing_model(monkeypatch) -> None:
    helper = load_module("ollama_cloud_pass_mismatch", ROOT / "scripts" / "ollama_cloud_pass.py")

    class Response:
        def __init__(self, payload: dict):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    monkeypatch.setattr(
        helper.urllib.request,
        "urlopen",
        lambda _request, timeout: Response({"model": "qwen2.5:7b", "response": "wrong"}),
    )
    with pytest.raises(RuntimeError, match="unexpected model"):
        helper.run("glm-5.2:cloud", "safe prompt", 30)

    monkeypatch.setattr(
        helper.urllib.request,
        "urlopen",
        lambda _request, timeout: Response({"response": "missing"}),
    )
    with pytest.raises(RuntimeError, match="omitted model identity"):
        helper.run("glm-5.2:cloud", "safe prompt", 30)


def test_lane_visibility_tracks_disclosed_fallback_model(monkeypatch) -> None:
    lane = load_module("model_lane_fallback_visibility", ROOT / "scripts" / "model_lane.py")
    args = model_args(transport="hermes")
    attempts = []
    route_changes = []

    monkeypatch.setattr(lane, "command_for", lambda _args, route: [route["modelRoute"]["model"]])

    def fake_execute(command, _route):
        attempts.append(command)
        return 3 if len(attempts) == 1 else 0

    monkeypatch.setattr(lane, "execute_verified", fake_execute)
    route = {"modelRoute": {
        "provider": "gemini",
        "model": "gemini-3.6-flash-medium",
        "fallbackRoutes": [{"provider": "ollama", "model": "glm-5.2:cloud"}],
    }}
    result = lane.execute_with_disclosed_fallbacks(
        args,
        ["primary"],
        route,
        on_route_change=route_changes.append,
    )
    assert result == 0
    assert route_changes[0]["modelRoute"]["model"] == "glm-5.2:cloud"


def test_gemini_smoke_is_explicitly_diagnostic() -> None:
    source = (ROOT / "scripts" / "gemini_agent.py").read_text()
    assert '"--lane-visibility",' in source
    assert '"diagnostic",' in source


def test_specialist_catalog_covers_reviewed_ollama_families() -> None:
    catalog = json.loads((ROOT / "config" / "model-specialist-catalog.json").read_text())
    names = " ".join(
        str(row.get("model") or "")
        for group in ("production", "candidates", "held")
        for row in catalog[group]
    )
    for family in (
        "ornith", "laguna-xs-2.1", "laguna-s-2.1", "gemma4", "qwen3.5",
        "qwen3.6", "glm-ocr", "glm-5.1", "glm-5.2", "minimax-m2.5",
        "minimax-m2.7", "nemotron-3-super",
    ):
        assert family in names


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
    output = helper.run("agy-gemini-3.6-flash-medium", "safe prompt", 30)
    assert output == "GEMINI_OK"
    assert seen["url"] == "http://127.0.0.1:11435/v1/chat/completions"
    assert seen["payload"]["model"] == "gemini-3.6-flash-medium"


def test_antigravity_status_requires_proxy_model_discovery(monkeypatch) -> None:
    helper = load_module("gemini_agent_proxy", ROOT / "scripts" / "gemini_agent.py")
    monkeypatch.setattr(helper.Path, "home", classmethod(lambda cls: Path("/Users/jc_agent")))
    monkeypatch.setattr(helper.shutil, "which", lambda _name: "/opt/homebrew/bin/agy")

    def proxy_missing(command, timeout, stdin_text=None):
        if "--version" in command:
            return 0, "1.1.5\n", ""
        if "models" in command:
            return 0, "gemini-3.6-flash-medium\ngemini-3.1-pro-high\n", ""
        return 7, "", "connection refused"

    monkeypatch.setattr(helper, "run", proxy_missing)
    unavailable = helper.cli_status()
    assert unavailable["status"] == "antigravity-proxy-unavailable"
    assert unavailable["proxy"]["available"] is False

    def proxy_ready(command, timeout, stdin_text=None):
        if "--version" in command:
            return 0, "1.1.5\n", ""
        if "models" in command:
            return 0, "gemini-3.6-flash-medium\ngemini-3.1-pro-high\n", ""
        return 0, json.dumps({"data": [
            {"id": "gemini-3.6-flash-medium"},
            {"id": "gemini-3.1-pro-high"},
        ]}), ""

    monkeypatch.setattr(helper, "run", proxy_ready)
    ready = helper.cli_status()
    assert ready["status"] == "installed"
    assert ready["proxy"]["status"] == "ready"


def test_shared_skill_requires_real_verified_dispatch() -> None:
    skill = " ".join((ROOT / "agent-skills" / "multi-model-routing" / "SKILL.md").read_text().lower().split())
    assert "never infer success" in skill
    assert "executed route" in skill
    assert "gemini-3.6-flash-medium" in skill
    assert "glm-5.2:cloud" in skill
    assert "grok-4.5" in skill
