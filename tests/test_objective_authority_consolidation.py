from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def parsed(name: str) -> ast.Module:
    return ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))


def function_names(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def objective_quality_imports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "objective_quality":
            names.update(alias.name for alias in node.names)
    return names


def test_request_selection_has_one_shared_implementation() -> None:
    shared = parsed("objective_quality.py")
    josh = parsed("josh_telegram_fast_ack.py")
    jaimes = parsed("jaimes_telegram_fast_ack.py")

    assert {"current_request_text", "request_context_text"} <= function_names(shared)
    assert "current_request_text" not in function_names(josh)
    assert "current_request_text" not in function_names(jaimes)
    assert "current_request_text" in objective_quality_imports(josh)
    assert {"current_request_text", "request_context_text"} <= objective_quality_imports(jaimes)


def test_agent_summarizers_do_not_redeclare_card_or_output_contract_filters() -> None:
    for name in ("josh_telegram_fast_ack.py", "jaimes_telegram_fast_ack.py"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "embedded_card_row = re.compile" not in source
        assert "output_instruction = re.compile" not in source
