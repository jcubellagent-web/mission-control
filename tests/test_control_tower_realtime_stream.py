from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_client_uses_named_local_sse_event_with_polling_fallback() -> None:
    source = (ROOT / "v2-react" / "src" / "data.ts").read_text(encoding="utf-8")
    main = (ROOT / "v2-react" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert 'new EventSource("/events/mission-control")' in source
    assert 'addEventListener("mission-control"' in source
    assert 'addEventListener("open"' in source
    assert 'addEventListener("error"' in source
    assert "events.close()" in source
    assert "LIVE_REFRESH_MS" in main
    assert "Stream live" in main
    assert "10s fallback" in main


def test_server_stream_watches_rendered_snapshot_and_support_sidecars() -> None:
    source = (ROOT / "vite.config.ts").read_text(encoding="utf-8")

    for filename in (
        "control-tower-live.json",
        "agent-task-queue.json",
        "handoff-queue.json",
        "agent-context-registry.json",
        "memory-operations.json",
        "model-provider-budgets.json",
    ):
        assert f'"{filename}"' in source
    assert '"X-Accel-Buffering": "no"' in source
    assert 'res.write("retry: 2000\\n\\n")' in source
