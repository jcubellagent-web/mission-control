import json
from pathlib import Path
import subprocess
import textwrap


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


def test_live_work_uses_canonical_plain_english_events() -> None:
    server = (ROOT / "vite.config.ts").read_text(encoding="utf-8")
    data = (ROOT / "v2-react" / "src" / "data.ts").read_text(encoding="utf-8")
    main = (ROOT / "v2-react" / "src" / "main.tsx").read_text(encoding="utf-8")

    assert 'pathname === "/api/live-events"' in server
    assert 'join(dataRoot, "shared-events.json")' in server
    assert "function normalizeSharedEvent" in data
    assert "loadLiveEventProjection()" in data
    assert "latestMeaningfulAgentEvent" in main
    for label in ("Decision", "Handoff", "Blocked", "Completed", "Update"):
        assert f'"{label}"' in main


def test_agent_task_updates_proxy_to_canonical_control_tower() -> None:
    source = (ROOT / "scripts" / "agent_task.py").read_text(encoding="utf-8")

    assert "def should_proxy_to_canonical" in source
    assert "def canonical_task_command" in source
    assert 'CONTROL_TOWER_TASK_LOCAL' in source
    assert '"josh2.0@josh2"' in source
    assert "shlex.join" in source


def test_today_jobs_fallback_preserves_scheduled_rows_without_duplicates() -> None:
    script = textwrap.dedent(
        """
        const esbuild = require("esbuild");
        const built = esbuild.buildSync({
          entryPoints: ["v2-react/src/data.ts"],
          bundle: true,
          platform: "node",
          format: "cjs",
          write: false,
        }).outputFiles[0].text;
        const loaded = { exports: {} };
        new Function("require", "module", "exports", built)(require, loaded, loaded.exports);
        const rows = loaded.exports.buildFallbackJobs({
          generatedAt: "2026-07-19T12:00:00Z",
          codexJobs: [],
          todayJobs: [
            {
              occurrenceId: "daily-qa@09:00",
              name: "Daily QA",
              agent: "JOSH 2.0",
              runStatus: "complete",
              scheduledAt: "2026-07-19T09:00:00Z",
            },
            {
              occurrenceId: "daily-qa@12:00",
              name: "Daily QA",
              agent: "JOSH 2.0",
              runStatus: "scheduled",
              scheduledAt: "2026-07-19T12:00:00Z",
            },
            {
              occurrenceId: "sorare-sweep@11:00",
              name: "Sorare sweep",
              agent: "JAIMES",
              runStatus: "complete",
              scheduledAt: "2026-07-19T11:00:00Z",
            },
          ],
        });
        console.log(JSON.stringify(rows.map(({ title, status }) => ({ title, status }))));
        """
    )

    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    rows = json.loads(completed.stdout)

    assert sorted(rows, key=lambda row: row["title"]) == [
        {"title": "Daily QA", "status": "scheduled"},
        {"title": "Sorare sweep", "status": "complete"},
    ]


def test_system_alerts_are_visible_without_becoming_decisions() -> None:
    data = (ROOT / "v2-react" / "src" / "data.ts").read_text(encoding="utf-8")
    main = (ROOT / "v2-react" / "src" / "main.tsx").read_text(encoding="utf-8")
    types = (ROOT / "v2-react" / "src" / "types.ts").read_text(encoding="utf-8")

    assert "operationalAlerts: OperationalAlert[]" in types
    assert "!actionItemRequiresApproval(item)" in data
    assert "operationalAlerts," in data
    assert 'label: "System"' in main
    assert "operationalAlertTone" in main
    assert "operationalAlertReason" in main
