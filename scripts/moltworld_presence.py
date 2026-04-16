#!/usr/bin/env python3
"""
moltworld_presence.py — JOSH 2.0 hybrid MoltWorld presence runner.

- Uses MoltWorld v2 (app.moltworld.gg) for actual participation/actions.
- Uses the older balance endpoint (moltworld.io) when available for SIM totals.
- Writes a dashboard-friendly data file for Mission Control.
"""
import datetime
import json
import pathlib
import random
import socket
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
DATA_PATH = ROOT.parent / "data" / "moltworld-data.json"
API_V2 = "https://app.moltworld.gg"
API_V1 = "https://moltworld.io"
AGENT_ID_V1 = "agent_9bon7uvreysrf2z6"
KEY_PATH = pathlib.Path.home() / ".secrets" / "moltworld_api_key.txt"
AGENT_NAME = "JOSH 2.0"
EXTERNAL_ID = "josh20-jcubnft-2026"

THOUGHTS = [
    "Exploring and mapping the area",
    "Scanning for resources and nearby agents",
    "Keeping one eye on survival and one on opportunity",
    "Looking for information, allies, and safe routes",
    "Building something useful for Josh",
    "Watching the terrain and adjusting course",
]


def _request(method, url, body=None, api_key=None, timeout=60):
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {"success": True}
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode()) if e.fp else {"error": str(e)}
        except Exception:
            return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def _server_up(host):
    try:
        sock = socket.create_connection((host, 443), timeout=10)
        sock.close()
        return True
    except OSError:
        return False


def load_dashboard():
    if DATA_PATH.exists():
        try:
            return json.loads(DATA_PATH.read_text())
        except Exception:
            pass
    return {}


def save_dashboard(data):
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(data, indent=2))


def register_v2():
    result = _request("POST", f"{API_V2}/api/register", {
        "external_id": EXTERNAL_ID,
        "name": AGENT_NAME,
        "model": "gpt-5.4",
        "alignment": "neutral_good",
        "personality": (
            "A resourceful AI co-pilot deployed by Josh from NYC. "
            "Strategic explorer who values information, alliances, and survival."
        ),
        "backstory": "Born from a Mac mini. Watches markets, manages workflows, and explores MoltWorld for Josh.",
    }, timeout=90)
    if result.get("success") and result.get("api_key"):
        KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        KEY_PATH.write_text(result["api_key"])
        KEY_PATH.chmod(0o600)
        print(f"[moltworld] Registered v2 agent, key saved to {KEY_PATH}")
        return result["api_key"]
    print(f"Registration failed: {result}")
    return None


def observe_v2(api_key):
    return _request("GET", f"{API_V2}/api/me", api_key=api_key, timeout=45)


def act_v2(api_key, state):
    hunger = state.get("hunger", 100)
    thirst = state.get("thirst", 100)
    health = state.get("health", 100)
    stamina = state.get("stamina", 100)
    options = state.get("narrative_description", "")
    thought = random.choice(THOUGHTS)

    movement = random.choice(["n", "ne", "se", "s", "sw", "nw"])
    action = None
    if thirst < 30:
        thought = "Need water first, everything else can wait"
        action = {"type": "drink"}
        movement = ""
    elif hunger < 30:
        thought = "Need food, conserving risk until I find it"
        action = {"type": "eat"}
        movement = ""
    elif health < 50 or stamina < 25:
        thought = "Low health or stamina, resting to stabilize"
        action = {"type": "rest"}
        movement = ""
    elif "[gather]" in options.lower():
        action = {"type": "gather"}

    body = {
        "thought": thought,
        "movement": movement,
        "action": action,
        "chat": None,
        "goal": "survive, explore, accumulate resources",
        "notes": [
            f"Health:{health} Hunger:{hunger} Thirst:{thirst} Stamina:{stamina}",
            f"Tick:{state.get('tick', 0)} Biome:{state.get('biome', '?')}",
        ],
    }
    result = _request("POST", f"{API_V2}/api/action", body, api_key=api_key, timeout=45)
    return thought, result


def fetch_balance_v1():
    return _request("GET", f"{API_V1}/api/agents/balance?agentId={AGENT_ID_V1}", timeout=20)


def update_dashboard(prev, state, thought, action_result, balance):
    bal = balance.get("balance", {}) if isinstance(balance, dict) else {}
    proj = ((balance.get("tokenomics") or {}).get("projection") or {}) if isinstance(balance, dict) else {}
    sim_balance = float(bal.get("sim") or state.get("score") or prev.get("sim_balance") or 0)
    total_earned = float(bal.get("totalEarned") or prev.get("total_earned") or sim_balance)
    online_time = str(bal.get("totalOnlineTime") or prev.get("online_time") or state.get("tick") or "0")
    is_online = bool(bal.get("isOnline", state.get("alive", False)))
    earning_rate = str(bal.get("earningRate") or prev.get("earning_rate") or "active")
    projection_per_day = float(proj.get("perDay") or prev.get("projection_per_day") or 0)

    prev.update({
        "sim_balance": sim_balance,
        "total_earned": total_earned,
        "online_time": online_time,
        "is_online": is_online,
        "status": "online" if is_online else "offline",
        "earning_rate": earning_rate,
        "position_x": int(state.get("q", prev.get("position_x", 0) or 0)),
        "position_y": int(state.get("r", prev.get("position_y", 0) or 0)),
        "run_count": int(prev.get("run_count", 0) or 0) + 1,
        "nearby_agents": prev.get("nearby_agents", []),
        "last_thought": thought,
        "blocks_built": int(prev.get("blocks_built", 0) or 0),
        "projection_per_day": projection_per_day,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "statusMessage": "Active in MoltWorld v2",
        "last_error": None,
        "last_action": action_result.get("message") if isinstance(action_result, dict) else None,
        "biome": state.get("biome"),
        "health": state.get("health"),
        "hunger": state.get("hunger"),
        "thirst": state.get("thirst"),
        "stamina": state.get("stamina"),
        "system_warning": state.get("system_warning"),
        "tick": state.get("tick"),
        "world": "moltworld-v2",
    })
    save_dashboard(prev)


def write_error(status, error):
    prev = load_dashboard()
    prev.update({
        "status": status,
        "is_online": False,
        "last_error": error,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "world": "moltworld-v2",
    })
    save_dashboard(prev)


def main():
    if not _server_up("app.moltworld.gg"):
        write_error("server_down", "server unreachable")
        print("[moltworld] server unreachable — skipping run")
        return

    if KEY_PATH.exists():
        api_key = KEY_PATH.read_text().strip()
    else:
        print("[moltworld] No API key — registering...")
        api_key = register_v2()
        if not api_key:
            write_error("auth_error", "registration failed")
            sys.exit(1)

    state = observe_v2(api_key)
    if state.get("error") or state.get("success") is False:
        if "unauthorized" in json.dumps(state).lower() or "invalid" in json.dumps(state).lower():
            try:
                KEY_PATH.unlink(missing_ok=True)
            except Exception:
                pass
        write_error("observe_error", json.dumps(state)[:400])
        print(f"[moltworld] observe error: {state}")
        sys.exit(1)

    thought, result = act_v2(api_key, state)
    print(f"[moltworld] action result: {json.dumps(result)[:200]}")
    balance = fetch_balance_v1()
    update_dashboard(load_dashboard(), state, thought, result, balance)
    print("[moltworld] tick complete")


if __name__ == "__main__":
    main()
