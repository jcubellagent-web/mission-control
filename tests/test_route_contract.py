from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def telemetry_row(index: int) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "routeDecisionId": f"{index:020x}",
        "requestSignature": f"{index:016x}",
        "owner": "josh",
        "provider": "codex",
        "model": "gpt-5.6-luna",
        "reason": "bounded Inbox coordination",
        "outcome": "routed",
        "routingDurationMs": 4,
    }


class RouteContractTest(unittest.TestCase):
    def test_telegram_slo_uses_the_canonical_final_sections(self) -> None:
        slo = json.loads((ROOT / "config" / "ecosystem-qa-slo.json").read_text(encoding="utf-8"))
        self.assertEqual(
            slo["telegramInbox"]["requiredFinalSections"],
            ["Complete", "What was done", "Issues", "Appropriate next steps", "Approval needed"],
        )

    def test_router_telemetry_hashes_request_without_raw_prompt_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            telemetry = Path(tmp) / "routes.jsonl"
            env = os.environ.copy()
            env["AGENT_ROUTE_TELEMETRY_PATH"] = str(telemetry)
            marker = "PRIVATE-RAW-MARKER-MUST-NOT-APPEAR"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "agent_route.py"),
                    "--task-type",
                    "inbox",
                    "--title",
                    marker,
                    "--objective",
                    marker,
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            raw = telemetry.read_text(encoding="utf-8")
            self.assertNotIn(marker, raw)
            row = json.loads(raw)
            self.assertFalse({"title", "objective", "message", "prompt", "rawPrompt"} & set(row))
            self.assertEqual(row["provider"], "codex")
            self.assertEqual(row["model"], "gpt-5.6-luna")
            self.assertTrue(row["reason"])

    def test_fixture_suite_is_100_percent(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "route_contract_benchmark.py"), "--no-write"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertGreaterEqual(payload["fixtureCount"], 24)
        self.assertEqual(payload["passRatePct"], 100.0)
        self.assertEqual(payload["routeMetadataCoveragePct"], {"provider": 100.0, "model": 100.0, "reason": 100.0})

    def run_audit(self, rows: list[dict[str, object]]) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "routes.jsonl"
            output = Path(tmp) / "audit.json"
            source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "ROUTE_QA_SOURCE": str(source),
                    "ROUTE_QA_OUTPUT": str(output),
                    "ROUTE_QA_MIN_WINDOW": "24",
                    "ROUTE_QA_WINDOW": "100",
                }
            )
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "route_quality_audit.py")],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
        return proc, payload

    def test_sparse_telemetry_is_attention_not_green(self) -> None:
        proc, payload = self.run_audit([telemetry_row(index) for index in range(3)])
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(payload["status"], "attention")
        self.assertTrue(payload["sparseTelemetry"])

    def test_complete_safe_window_is_green(self) -> None:
        proc, payload = self.run_audit([telemetry_row(index) for index in range(24)])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["requiredFieldCoveragePct"]["model"], 100.0)

    def test_raw_prompt_key_fails_privacy_contract(self) -> None:
        rows = [telemetry_row(index) for index in range(24)]
        rows[-1]["rawPrompt"] = "must never be stored"
        proc, payload = self.run_audit(rows)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(payload["unsafeRawContentRows"], 1)


if __name__ == "__main__":
    unittest.main()
