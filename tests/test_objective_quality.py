import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "objective_quality.py"
SPEC = importlib.util.spec_from_file_location("objective_quality_under_test", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
objective_is_near_copy = MODULE.objective_is_near_copy
semantic_reinterpretation = MODULE.semantic_reinterpretation


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
