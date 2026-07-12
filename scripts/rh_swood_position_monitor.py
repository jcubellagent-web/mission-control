#!/usr/bin/env python3
"""Read-only SWOOD medium-term position monitor; alerts only on state changes."""
from __future__ import annotations
import json, sys, urllib.request
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,'/Users/jc_agent/.hermes/scripts')
from eth_abi import encode, decode
from eth_utils import keccak, to_checksum_address
import rh_autonomous_executor as core

ROUTER='0x89e5DB8B5aA49aA85AC63f691524311AEB649eba';WETH=core.WETH;VIRTUAL='0xc6911796042b15d7Fa4F6CDe69e245DdCd3d9c31';SWOOD='0xB1cB27F78B7335df8C3d8ebF0881A15BeD6BeB60'
POSITIONS=Path('/Users/jc_agent/reports/rh_manual_strategy_positions.json');OUT=Path('/Users/jc_agent/reports/rh_swood_monitor_latest.json');STATE=Path('/Users/jc_agent/.hermes/cron/state/rh_swood_monitor.json')
SEL_QUOTE='0x'+keccak(text='getAmountsOut(uint256,address[])')[:4].hex()

def load(p,d):
 try:return json.loads(p.read_text())
 except Exception:return d

def quote(amount,path):
 data=SEL_QUOTE+encode(['uint256','address[]'],[amount,[to_checksum_address(x) for x in path]]).hex();raw=core.selector_call(ROUTER,data);return list(decode(['uint256[]'],bytes.fromhex(raw[2:]))[0])
def market():
 u='https://api.dexscreener.com/latest/dex/tokens/'+SWOOD;req=urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0'});pairs=json.loads(urllib.request.urlopen(req,timeout=30).read()).get('pairs') or [];p=max([x for x in pairs if str((x.get('pairAddress') or '')).lower()=='0xabc83c3f04c3dec51ce32f8aa83be281e1b27dad'],key=lambda x:float((x.get('liquidity') or {}).get('usd') or 0));return {'price_usd':float(p.get('priceUsd') or 0),'mcap_usd':float(p.get('marketCap') or 0),'liquidity_usd':float((p.get('liquidity') or {}).get('usd') or 0),'price_change':p.get('priceChange') or {},'volume':p.get('volume') or {}}
def main():
 pos=next((p for p in load(POSITIONS,{}).get('positions',[]) if p.get('symbol')=='SWOOD' and p.get('status')=='OPEN'),None)
 if not pos:return 0
 units=int(float(pos['token_units'])*1e18);value=quote(units,[SWOOD,VIRTUAL,WETH])[-1]/1e18;entry=float(pos['entry_eth']);pnl=(value/entry-1)*100;opened=datetime.fromisoformat(pos['opened_at'].replace('Z','+00:00'));age_h=(datetime.now(timezone.utc)-opened).total_seconds()/3600;m=market()
 action='HOLD_MEDIUM';trigger=None
 if pnl<=-28:action='EXIT_HARD_INVALIDATION';trigger='pnl<=-28%'
 elif pnl<=-18 and float((m['price_change'] or {}).get('h1') or 0)<-8:action='EXIT_THESIS_FAILURE';trigger='pnl<=-18% and 1h breakdown'
 elif pnl>=150:action='TRIM_25_TP3';trigger='pnl>=150%'
 elif pnl>=80:action='TRIM_25_TP2';trigger='pnl>=80%'
 elif pnl>=35:action='TRIM_25_TP1';trigger='pnl>=35%'
 elif age_h>=168 and pnl<15:action='EXIT_MEDIUM_TIME_LIMIT';trigger='age>=7d without +15%'
 elif age_h>=168:action='PROMOTE_LONG_REVIEW';trigger='7d review; never exceed 14d'
 out={'as_of':datetime.now(timezone.utc).isoformat(),'symbol':'SWOOD','horizon':'MEDIUM_3_TO_7_DAYS','age_hours':age_h,'entry_eth':entry,'exit_quote_eth':value,'pnl_pct':pnl,'action':action,'trigger':trigger,'market':m};OUT.write_text(json.dumps(out,indent=2,sort_keys=True))
 st=load(STATE,{});key=action
 if action!='HOLD_MEDIUM' and st.get('last_alert')!=key:
  print(f"SWOOD POSITION ACTION | {action} | P/L {pnl:+.1f}% | value {value:.6f} ETH | {trigger}")
  st['last_alert']=key;st['at']=out['as_of'];STATE.parent.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps(st,indent=2))
 return 0
if __name__=='__main__':raise SystemExit(main())
