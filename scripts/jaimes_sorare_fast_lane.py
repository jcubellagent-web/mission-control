#!/usr/bin/env python3
"""JAIMES Sorare fast lane refresh.

Read-only cache refresh for launchd. Keeps Sorare artifacts warm so Telegram and
Control Tower answers can start from fresh fixture/context/model state.
No lineup submissions, bids, roster moves, or external messages are performed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SORARE = Path.home() / "sorare_ml"
PY = SORARE / ".venv311" / "bin" / "python"
ARTIFACT_DIR = SORARE / "artifacts" / "fast_lane"
LOG_DIR = SORARE / "logs"
LOCK = Path("/tmp/jaimes_sorare_fast_lane.lock")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_step(name: str, cmd: list[str], timeout: int) -> dict:
    started = now_iso()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(SORARE),
            text=True,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, "PYTHONPATH": str(SORARE)},
        )
        return {
            "name": name,
            "started_at": started,
            "finished_at": now_iso(),
            "exit_code": proc.returncode,
            "stdout_tail": proc.stdout[-2000:],
            "stderr_tail": proc.stderr[-2000:],
            "ok": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "started_at": started,
            "finished_at": now_iso(),
            "exit_code": 124,
            "stdout_tail": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-2000:] if isinstance(exc.stderr, str) else "",
            "ok": False,
            "timeout": timeout,
        }
    except Exception as exc:
        return {
            "name": name,
            "started_at": started,
            "finished_at": now_iso(),
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": str(exc)[:1000],
            "ok": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["smoke", "full"], default="full")
    parser.add_argument("--publish", action="store_true", help="Accepted for legacy launchd compatibility; no external publish here.")
    args = parser.parse_args()

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if LOCK.exists():
        try:
            old = int(LOCK.read_text().strip() or "0")
            os.kill(old, 0)
            print(f"Sorare fast lane already running pid={old}")
            return 0
        except Exception:
            pass
    LOCK.write_text(str(os.getpid()))
    try:
        steps = [
            (
                "gw_context_refresh",
                [
                    str(PY),
                    "gw_pipeline/run_gw_pipeline.py",
                    "--artifacts-dir",
                    str(ARTIFACT_DIR / "gw_context"),
                    "--exclude-unconfirmed-sp",
                    "--exclude-native-hard-statuses",
                    "--no-warehouse-log",
                    "--no-optimize",
                ],
                360,
            ),
            (
                "model_eval_gate",
                [sys.executable, str(Path.home() / ".hermes" / "scripts" / "sorare_model_eval_gate.py")],
                60,
            ),
        ]
        if args.mode == "full":
            steps.append(
                (
                    "rp_start_scout",
                    [str(PY), "rp_start_scout.py", "--fixtures", "2", "--quiet-if-unchanged"],
                    180,
                )
            )

        results = [run_step(name, cmd, timeout) for name, cmd, timeout in steps]
        summary = {
            "generated_at": now_iso(),
            "mode": args.mode,
            "ok": all(r["ok"] for r in results),
            "steps": results,
        }
        (ARTIFACT_DIR / "latest.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"ok": summary["ok"], "steps": [{"name": r["name"], "ok": r["ok"], "exit_code": r["exit_code"]} for r in results]}, indent=2))
        return 0 if summary["ok"] else 1
    finally:
        try:
            if LOCK.read_text().strip() == str(os.getpid()):
                LOCK.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
