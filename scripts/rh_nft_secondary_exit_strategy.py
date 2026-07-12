#!/usr/bin/env python3
"""Build secondary-market exit plans for NFTs minted by the guarded executor.

Read-only: emits only when a listing/bid action changes. OpenSea order signing and
submission remain a separate authenticated execution lane.
"""
import json,sys,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,'/Users/jc_agent/.hermes/scripts')
import rh_nft_analytics as analytics
POSITIONS=Path('/Users/jc_agent/reports/rh_nft_strategy_positions.json');INTEL=Path('/Users/jc_agent/reports/rh_nft_intelligence_latest.json');OUT=Path('/Users/jc_agent/reports/rh_nft_secondary_exit_latest.json');STATE=Path('/Users/jc_agent/.hermes/cron/state/rh_nft_secondary_exit.json')

def load(p,d):
 try:return json.loads(p.read_text())
 except Exception:return d
def eth_usd():
 try:return float(json.loads(urllib.request.urlopen('https://api.coinbase.com/v2/prices/ETH-USD/spot',timeout=15).read())['data']['amount'])
 except Exception:return 0.0
def main():
 positions=[p for p in load(POSITIONS,{}).get('positions',[]) if p.get('status')=='OPEN'];intel=load(INTEL,{});price=eth_usd();rows=[]
 source=(intel.get('top_collections') or [])+(intel.get('owned_material') or [])+(intel.get('secondary_traction') or [])
 by_contract={str(r.get('contract') or '').lower():r for r in source}
 for p in positions:
  contract=str(p.get('contract') or '').lower();base=dict(by_contract.get(contract) or {});base.update({'contract':contract,'collection':p.get('collection'),'slug':p.get('slug')})
  try:a=analytics.enrich(contract,p.get('slug'),base,'0xa8fed22b1df934370b7e9e0f611a3a894fc257d8',{})
  except Exception as e:a={'market':{},'error':e.__class__.__name__}
  m=a.get('market') or {};floor=float(m.get('floor_eth') or 0);bid=float(m.get('top_bid_eth') or 0);basis_usd=float(p.get('basis_usd') or 0);basis_eth=basis_usd/price if price else 0;age_h=(datetime.now(timezone.utc)-datetime.fromisoformat(str(p['opened_at']).replace('Z','+00:00'))).total_seconds()/3600;sales=int(base.get('sales_15m') or 0);vol=float(m.get('volume_24h_eth') or 0)
  action='WAIT_FOR_SECONDARY';target=None;reason='no verified floor, bid, or demand'
  if bid and bid>=max(basis_eth*2,floor*.75 if floor else 0):action='ACCEPT_TOP_BID_PREP';target=bid;reason='verified bid clears 2x basis and demand-quality gate'
  elif floor and (sales>=3 or vol>0):action='LIST_NEAR_FLOOR_PREP';target=max(floor*.97,basis_eth*3);reason='secondary demand present; seek at least 3x basis'
  elif age_h>=168 and floor:action='LIST_TO_CLEAR_PREP';target=max(floor*.92,basis_eth*1.25);reason='7-day capital-recycling review'
  elif age_h>=336:action='EXIT_AT_BEST_VERIFIED_MARKET_PREP';target=bid or (floor*.90 if floor else None);reason='absolute 14-day hold limit'
  rows.append({'contract':contract,'token_id':p.get('token_id'),'collection':p.get('collection'),'slug':p.get('slug'),'age_hours':age_h,'basis_usd':basis_usd,'basis_eth':basis_eth,'floor_eth':floor or None,'top_bid_eth':bid or None,'sales_15m':sales,'volume_24h_eth':vol,'action':action,'target_eth':target,'reason':reason,'execution_ready':False,'execution_blocker':'OpenSea authenticated Seaport order-signing/submission lane not configured' if action!='WAIT_FOR_SECONDARY' else None})
 result={'as_of':datetime.now(timezone.utc).isoformat(),'positions':rows,'open_positions':len(rows),'actionable':sum(r['action']!='WAIT_FOR_SECONDARY' for r in rows)};OUT.write_text(json.dumps(result,indent=2,sort_keys=True))
 st=load(STATE,{});alerts=[]
 for r in rows:
  key=f"{r['contract']}:{r['token_id']}";sig=f"{r['action']}:{r.get('target_eth')}"
  if r['action']!='WAIT_FOR_SECONDARY' and st.get(key)!=sig:
   alerts.append(f"NFT SECONDARY EXIT PREP | {r['collection']} #{r['token_id']} | {r['action']} | target {r.get('target_eth') or 0:.5f} ETH | {r['reason']}");st[key]=sig
 STATE.parent.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps(st,indent=2))
 if alerts:print('\n'.join(alerts))
 return 0
if __name__=='__main__':raise SystemExit(main())
