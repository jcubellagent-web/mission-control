#!/usr/bin/env python3
"""jaimes_fast_bf_remote.py — write JAIMES brain feed DIRECTLY to JOSH 2.0's
local mission-control and patch the JAIMES slice of dashboard-data.json in place,
so the kiosk SSE watcher fires and the Live Work Board updates in ~150ms.

This is the REAL-TIME path. It does NOT run the full 6s dashboard regen and does
NOT git-push. It only mutates the two files the kiosk reads + the SSE watches:
  data/jaimes-brain-feed.json   (top-level + steps)
  data/dashboard-data.json      (agentBrainFeeds.jaimes + jaimesBrainFeed mirror)

Run ON JOSH 2.0 (it is scp'd + invoked over SSH by the J.A.I.N wrapper).
Args: <objective> <state active|idle|...> <tool> <model> <auth> <detail>
"""
import json
import sys
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BF = DATA / "jaimes-brain-feed.json"
DD = DATA / "dashboard-data.json"

def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def main():
    objective = sys.argv[1] if len(sys.argv) > 1 else "Working..."
    state = sys.argv[2] if len(sys.argv) > 2 else "active"
    tool = sys.argv[3] if len(sys.argv) > 3 else "exec"
    model = sys.argv[4] if len(sys.argv) > 4 else "unknown"
    auth = sys.argv[5] if len(sys.argv) > 5 else "api"
    detail = sys.argv[6] if len(sys.argv) > 6 else objective
    now = now_iso()
    active = state.lower() not in ("idle", "done", "complete", "ready", "ok")

    # 1) brain feed sidecar
    try:
        bf = json.loads(BF.read_text()) if BF.exists() else {}
        if not isinstance(bf, dict):
            bf = {}
    except Exception:
        bf = {}
    bf.update({
        "active": active,
        "objective": objective,
        "detail": detail,
        "status": state,
        "updatedAt": now,
        "updated_at": now,
        "currentTool": tool,
        "model": model,
        "auth": auth,
        "modelAuth": f"{model} ({auth})",
        "agent": "JAIMES",
        "checkedAt": now,
        "liveAgentPush": now,
        "liveAgentPushAt": now,
    })
    if active:
        bf["messageReceived"] = now
    steps = bf.get("steps") if isinstance(bf.get("steps"), list) else []
    if steps and isinstance(steps[-1], dict) and steps[-1].get("status") == "active":
        steps[-1]["status"] = "done"
    if active:
        steps.append({"label": objective[:60], "status": "active", "tool": tool, "time": now})
    else:
        for s in steps:
            if isinstance(s, dict):
                s["status"] = "done"
    bf["steps"] = steps[-10:]
    BF.write_text(json.dumps(bf, indent=2) + "\n")

    # 2) surgically patch dashboard-data.json JAIMES slice (no full regen)
    patched = False
    if DD.exists():
        try:
            dd = json.loads(DD.read_text())
            slice_obj = {
                "agent": "jaimes",
                "label": "JAIMES",
                "active": active,
                "status": state,
                "objective": objective,
                "detail": detail,
                "updatedAt": now,
                "updated_at": now,
                "currentTool": tool,
                "tool": tool,
                "model": model,
                "auth": auth,
                "modelAuth": f"{model} ({auth})",
                "steps": bf["steps"],
            }
            abf = dd.setdefault("agentBrainFeeds", {})
            existing = abf.get("jaimes", {}) if isinstance(abf.get("jaimes"), dict) else {}
            existing.update(slice_obj)
            abf["jaimes"] = existing
            # top-level mirror the loader also reads
            jtop = dd.get("jaimesBrainFeed", {}) if isinstance(dd.get("jaimesBrainFeed"), dict) else {}
            jtop.update(slice_obj)
            dd["jaimesBrainFeed"] = jtop
            dd["generatedAt"] = now
            DD.write_text(json.dumps(dd, indent=2))
            patched = True
        except Exception as exc:
            print(f"[warn] dashboard-data patch skipped: {exc}", file=sys.stderr)

    print(json.dumps({"ok": True, "active": active, "dashboardPatched": patched, "updatedAt": now}))

if __name__ == "__main__":
    main()
