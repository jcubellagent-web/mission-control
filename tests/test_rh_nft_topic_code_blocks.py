#!/usr/bin/env python3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FILES=[
ROOT/'scripts/rh_nft_secondary_exit_strategy.py',
ROOT/'scripts/rh_rootwood_monitor.py',
ROOT/'scripts/robinhood_nft_opportunity_watch_eth_sol.sh',
]
for p in FILES:
 s=p.read_text();assert '```text\\n' in s or "'```text\\n'" in s,p
lane=(ROOT/'scripts/rh_nft_gui_lane.py').read_text();assert "f'<pre>{safe}</pre>'" in lane
print('NFT topic code-block contract passed')
