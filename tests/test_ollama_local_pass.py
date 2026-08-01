from __future__ import annotations

import json

import pytest

from scripts import ollama_local_pass


class Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_local_pass_verifies_exact_model_and_returns_metrics(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_local_pass.urllib.request,
        "urlopen",
        lambda _request, timeout: Response({
            "model": "qwen2.5:7b",
            "response": "LOCAL_OK",
            "prompt_eval_count": 5,
            "eval_count": 2,
        }),
    )
    output, metrics = ollama_local_pass.run_with_metrics("ollama/qwen2.5:7b", "safe", 10)
    assert output == "LOCAL_OK"
    assert metrics["inputTokens"] == 5
    assert metrics["outputTokens"] == 2


def test_local_pass_rejects_cloud_models_without_calling_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        ollama_local_pass.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("cloud model must not reach local pass"),
    )
    with pytest.raises(RuntimeError, match="non-cloud"):
        ollama_local_pass.run_with_metrics("glm-5.2:cloud", "safe", 10)
