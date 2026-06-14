#!/usr/bin/env python3
"""Update Control Tower's Solana P2E scorecard in agentic-crypto-wallet.json."""
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.home() / ".openclaw" / "workspace" / "mission-control"
DATA = ROOT / "data" / "agentic-crypto-wallet.json"

TOKENS = [
    {"symbol":"KINS","mint":"Tqj8yFmagrg7oorpQkVGYR52r96RFTamvWfth9bpump","score":74,"verdict":"Promising but risky","posture":"Core P2E hold","held":True,"action":"Added","risk":"Anon team; LP <50%"},
    {"symbol":"Islands","mint":"yoA2CoHk6HRNtFuTP1kVt5xkcvG7mr5raQ5zuNxpump","score":72,"verdict":"Promising but risky","posture":"Core P2E hold","held":True,"action":"Added","risk":"Anon team; LP <50%"},
    {"symbol":"xWorld","mint":"B7a9wpdcdSt44Y9iHqrMYo9yuntqtEV25irTS5YRpump","score":55,"verdict":"Watchlist / tactical","posture":"Runner only","held":True,"action":"Trimmed 50%","risk":"CA ambiguity; tokenized-stock claim"},
    {"symbol":"Pixelands","mint":"9p7msBtHCWoWCnQM2hJtPpCMjurTaAMorZ6k26F9pump","score":43,"verdict":"High-risk watchlist","posture":"Monitor only","held":False,"action":"Avoided","risk":"Brand-new social/team unknown"},
    {"symbol":"GRINDFUN","mint":"B3SUGXHxn4aEfHzT6nLpzrRKTUmCjA4AsDeHUgCEpump","score":18,"verdict":"Avoid","posture":"No-touch","held":False,"action":"Avoided","risk":"Creator rug history; private-key flow"},
]

def main():
    data = json.loads(DATA.read_text()) if DATA.exists() else {}
    held = sum(1 for t in TOKENS if t["held"])
    avoid = sum(1 for t in TOKENS if "Avoid" in t["verdict"])
    data["p2eResearch"] = {
        "updatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": "watch",
        "headline": f"{held} held · {avoid} avoid",
        "detail": "Kintara/Islands overweight; xWorld runner",
        "score": round(sum(t["score"] for t in TOKENS) / len(TOKENS), 1),
        "monitorJobId": "5bf4eb4aec41",
        "tokens": TOKENS,
        "alerts": [],
        "workflow": {
            "skill": "solana-token-research",
            "script": "solana_p2e_token_monitor.py",
            "cadence": "30m",
            "checks": ["RugCheck risk changes", "liquidity drops", "drawdowns", "CA/product red flags"],
        },
    }
    DATA.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(json.dumps(data["p2eResearch"], indent=2))

if __name__ == "__main__":
    main()
