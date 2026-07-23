import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "objective_quality.py"
SPEC = importlib.util.spec_from_file_location("objective_quality_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
objective_is_near_copy = MODULE.objective_is_near_copy
semantic_reinterpretation = MODULE.semantic_reinterpretation
current_request_text = MODULE.current_request_text
request_context_text = MODULE.request_context_text


def test_exact_request_after_courtesy_is_rejected() -> None:
    assert objective_is_near_copy(
        "Please inspect JOSHeX after the update",
        "Inspect JOSHeX after the update",
    )


def test_long_shared_prompt_span_is_rejected() -> None:
    assert objective_is_near_copy(
        "Please make sure the Control Tower objective reflects the work being done in real time",
        "Make sure the Control Tower objective reflects the work being done",
    )


def test_semantic_interpretation_is_accepted() -> None:
    assert not objective_is_near_copy(
        "The objective is just a copy of my message; interpret it in your own words first.",
        "Make agent task objectives reflect interpreted intent",
    )


def test_common_actions_become_success_oriented_objectives() -> None:
    assert semantic_reinterpretation("@JAIMES verify receipt validation") == (
        "Confirm receipt validation meets the intended requirements"
    )
    assert semantic_reinterpretation("Please fix a multi-step Inbox task") == (
        "Resolve a multi-step Inbox task and restore expected behavior"
    )


def test_ambiguous_request_is_left_for_main_agent_interpretation() -> None:
    assert semantic_reinterpretation("Please handle this thoughtfully") == ""


def test_bare_connectivity_canaries_get_one_shared_semantic_objective() -> None:
    expected = "Confirm the Telegram agent is responsive and completes a simple request"
    for prompt in ("test", "testing", "testing testing", "[J|redacted] testing"):
        assert objective_is_near_copy(prompt, "testing")
        assert semantic_reinterpretation(prompt) == expected


def test_topic17_control_tower_canary_gets_an_outcome_objective() -> None:
    prompt = """POST-FIX CANARY: topic17-human-20260723-01
Run a host-native verification for this exact JAIMES Ops topic.
Use host-native and Control Tower evidence. Verify the Telegram gateway,
one live card, one structured final, delivery receipts, and Delivered state.
"""
    assert semantic_reinterpretation(prompt) == (
        "Verify Topic 17 delivery and Control Tower agreement"
    )


def test_output_contract_cannot_override_model_routing_objective() -> None:
    prompt = (
        "Assess whether our model routing is resilient and whether private work and "
        "execution are routed appropriately. Make no changes.\n"
        "Return three findings, the verified model and authentication route actually "
        "used, any fallback that occurred, and a final conclusion of functioning or "
        "needs attention."
    )
    request = current_request_text(prompt)
    assert request == (
        "Assess whether our model routing is resilient and whether private work and "
        "execution are routed appropriately read-only"
    )
    assert "authentication" not in request.lower()
    assert "fallback" not in request.lower()
    assert "needs attention" not in request.lower()
    objective = semantic_reinterpretation(prompt)
    assert objective == "Assess model-routing resilience and private-execution boundaries"
    assert not objective_is_near_copy(prompt, objective)


def test_output_instruction_variants_are_not_actionable_objectives() -> None:
    core = "Review model routing and private execution boundaries."
    for instruction in (
        "Respond with findings, model, route, and status.",
        "Output format: findings, fallback, conclusion, and approval.",
        "Include the authentication route, issues, and next steps.",
    ):
        request = current_request_text(f"{core}\n{instruction}")
        assert request == core.rstrip(".")


def test_multiple_actionable_clauses_are_preserved_in_source_order() -> None:
    request = current_request_text(
        "Review Inbox ownership. Fix JAIMES Ops routing. "
        "Return three findings, model, route, and status."
    )
    assert request == "Review Inbox ownership; Fix JAIMES Ops routing"
    assert "Return" not in request


def test_common_no_change_variants_preserve_the_read_only_constraint() -> None:
    for constraint in (
        "Do not make any changes.",
        "Do not apply any changes.",
        "Don't make any changes.",
        "No changes.",
    ):
        assert current_request_text(f"Review Inbox ownership. {constraint}") == (
            "Review Inbox ownership read-only"
        )


def test_shared_context_removes_rendered_card_and_output_contract_rows() -> None:
    prompt = """Please fix the current task mapping.

🎯 Objective
Stale objective copied from an old card
🤖 Model: codex/gpt-5.6-luna
Return findings, route, issues, and approval needed.
"""
    assert request_context_text(prompt) == "Please fix the current task mapping."
    assert current_request_text(prompt) == "Please fix the current task mapping"
