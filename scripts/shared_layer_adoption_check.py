#!/usr/bin/env python3
"""Check whether a node can comply with the shared agent operating layer."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT = DATA_DIR / "shared-layer-adoption.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except Exception as exc:
        return 126, str(exc)


def check_local(label: str, agent: str, workspace: Path) -> dict[str, Any]:
    mission = workspace / "mission-control"
    helper = mission / "scripts" / "agent_publish.py"
    wrapper = mission / "scripts" / "agent_job_wrap.sh"
    top_helper = workspace / "scripts" / "agent_publish.py"
    top_wrapper = workspace / "scripts" / "agent_job_wrap.sh"
    docs = [
        mission / "docs" / "shared-agent-operating-layer-phase1.md",
        mission / "docs" / "shared-agent-operating-layer-phase2.md",
        mission / "docs" / "shared-agent-operating-layer-handoff.md",
    ]
    schemas = [
        mission / "schemas" / "shared-agent-event.schema.json",
        mission / "schemas" / "decision-record.schema.json",
        mission / "schemas" / "handoff-record.schema.json",
    ]
    code, crontab = run(["crontab", "-l"])
    cron_lines = [line for line in crontab.splitlines() if line.strip() and not line.lstrip().startswith("#")] if code == 0 else []
    wrapped = [line for line in cron_lines if "agent_job_wrap.sh" in line or "agent_publish.py" in line]
    result = {
        "label": label,
        "agent": agent,
        "workspace": str(workspace),
        "checkedAt": utc_now(),
        "helperReady": helper.exists(),
        "wrapperReady": wrapper.exists(),
        "topLevelHelperReady": top_helper.exists(),
        "topLevelWrapperReady": top_wrapper.exists(),
        "docsReady": all(path.exists() for path in docs),
        "schemasReady": all(path.exists() for path in schemas),
        "crontabAvailable": code == 0,
        "totalCronLines": len(cron_lines),
        "wrappedCronLines": len(wrapped),
        "sampleWrappedCronLines": wrapped[:5],
        "needsWrapping": [line for line in cron_lines if "agent_job_wrap.sh" not in line and "agent_publish.py" not in line][:8],
    }
    required = [
        result["helperReady"],
        result["wrapperReady"],
        result["docsReady"],
        result["schemasReady"],
    ]
    result["status"] = "ready" if all(required) else "needs_install"
    if result["status"] == "ready" and result["totalCronLines"] and not result["wrappedCronLines"]:
        result["status"] = "ready_unwrapped"
    return result


def write_report(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "nodesChecked": len(nodes),
        "helpersReady": sum(1 for n in nodes if n.get("helperReady")),
        "wrappersReady": sum(1 for n in nodes if n.get("wrapperReady")),
        "wrappedCronLines": sum(int(n.get("wrappedCronLines") or 0) for n in nodes),
        "totalCronLines": sum(int(n.get("totalCronLines") or 0) for n in nodes),
        "overall": "ready" if nodes and all(n.get("status") in {"ready", "ready_unwrapped"} for n in nodes) else "attention",
    }
    report = {"generatedAt": utc_now(), "nodes": nodes, "summary": summary}
    OUT.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="local")
    parser.add_argument("--agent", default="joshex")
    parser.add_argument("--workspace", type=Path, default=ROOT.parent)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    node = check_local(args.label, args.agent, args.workspace.expanduser())
    report = write_report([node])
    print(json.dumps(report if args.json else node, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
