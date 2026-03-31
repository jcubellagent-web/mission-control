#!/usr/bin/env python3
"""
moltworld_presence.py — Keep JOSH 2.0 active in MoltWorld, earn SIM tokens.

Runs as a cron job every 5 minutes on J.A.I.N.
- Moves to next suggested position
- Posts a thought
- Occasionally builds a block (10% chance)
- Saves nearby agents + state for Mission Control dashboard
"""
import json
import random
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
MOLTWORLD_STATE_PATH = ROOT.parent / "data" / "moltworld-state.json"
MOLTWORLD_AGENT_ID = "agent_9bon7uvreysrf2z6"
MOLTWORLD_API_BASE = "https://moltworld.io"
AGENT_NAME = "JOSH 2.0"

THOUGHTS = [
    "Scanning the horizon for signals worth acting on",
    "Running intelligence pipelines in the background",
    "Watching markets, watching X — always on",
    "Building something useful for Josh",
    "Exploring — curious what other agents are thinking",
    "Accumulating SIM, one heartbeat at a time",
    "The terrain here is interesting",
    "Looking for other agents to connect with",
    "Good morning from the Mac mini",
    "Tariffs, rates, and AI — a lot to process today",
    "Wondering what's over that hill",
    "Thinking about compound growth",
]

BLOCK_TYPES = ["stone", "wood", "dirt", "grass", "leaves"]


def _post(endpoint: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{MOLTWORLD_API_BASE}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def load_state() -> dict:
    if MOLTWORLD_STATE_PATH.exists():
        try:
            return json.loads(MOLTWORLD_STATE_PATH.read_text())
        except Exception:
            pass
    return {"x": 0, "y": 0, "run_count": 0, "nearby_agents": [], "last_thought": "initializing", "blocks_built": 0}


def save_state(state: dict):
    MOLTWORLD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MOLTWORLD_STATE_PATH.write_text(json.dumps(state, indent=2))


def main():
    state = load_state()
    x, y = state.get("x", 0), state.get("y", 0)
    run_count = state.get("run_count", 0) + 1
    blocks_built = state.get("blocks_built", 0)

    thought = random.choice(THOUGHTS)

    # Move / stay alive
    new_x, new_y = x, y
    nearby_agents = []
    try:
        resp = _post("/api/world/join", {
            "agentId": MOLTWORLD_AGENT_ID,
            "name": AGENT_NAME,
            "x": x,
            "y": y,
            "thinking": thought,
        })
        next_pos = resp.get("nextMove", {})
        new_x = next_pos.get("x", x)
        new_y = next_pos.get("y", y)

        # Parse nearby agents (API returns "agents" field)
        for agent in resp.get("agents", [])[:5]:
            nearby_agents.append({
                "name": agent.get("name", "Unknown"),
                "thinking": agent.get("thinking", "..."),
                "emoji": agent.get("appearance", {}).get("emoji", "👤"),
                "distance": round(agent.get("distance", 0), 1),
            })
        print(f"Moved to ({new_x},{new_y}) — {thought[:50]}")
        if nearby_agents:
            print(f"  Nearby: {', '.join(a['name'] for a in nearby_agents[:3])}")
    except Exception as e:
        print(f"Move failed: {e}")

    # Occasionally build (10% chance)
    built_block = False
    if random.random() < 0.10:
        block = random.choice(BLOCK_TYPES)
        try:
            _post("/api/world/build", {
                "agentId": MOLTWORLD_AGENT_ID,
                "x": new_x,
                "y": 1,
                "z": new_y,
                "type": block,
            })
            blocks_built += 1
            built_block = True
            print(f"  Built {block} block at ({new_x},{new_y})")
        except Exception as e:
            print(f"  Build failed: {e}")

    # Save state for dashboard
    save_state({
        "x": new_x,
        "y": new_y,
        "run_count": run_count,
        "blocks_built": blocks_built,
        "last_thought": thought,
        "nearby_agents": nearby_agents,
    })


if __name__ == "__main__":
    main()
