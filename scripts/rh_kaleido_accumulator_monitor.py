#!/usr/bin/env python3
"""Monitor KALEIDO pullbacks and launch pre-authorized tranches via GUI Terminal."""
import json,subprocess,sys,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,'/Users/jc_agent/.hermes/scripts')
import rh_autonomous_executor as e
K='0x6689ab375aaaa9aed78aaaba2e8edcc547478cf1';CFG=Path('/Users/jc_agent/.hermes/config/rh_kaleido_accumulator.json');STATE=Path('/Users/jc_agent/.hermes/cron/state/rh_kaleido_accumulator.json');CAND=Path('/Users/jc_agent/reports/rh_kaleido_accumulator_candidate.json');OUT=Path('/Users/jc_agent/reports/rh_kaleido_accumulator_latest.json');EXEC=Path('/Users/jc_agent/reports/rh_kaleido_accumulator_execution_latest.json')
HEALTH=Path('/Users/jc_agent/.hermes/cron/state/rh_kaleido_accumulator_health.json')
TRANSIENT_MARKERS=('429','too many requests','timeout','timed out','deadline exceeded','temporarily unavailable','connection reset','urlopen error','remote end closed')
def load(p,d):
 try:return json.loads(p.read_text())
 except Exception:return d
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2,sort_keys=True))
def market():
 u='https://api.dexscreener.com/latest/dex/tokens/'+K;req=urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0'});pairs=json.loads(urllib.request.urlopen(req,timeout=30).read()).get('pairs') or [];return max(pairs,key=lambda x:float((x.get('liquidity') or {}).get('usd') or 0))
def quote_for(target_raw):
 lo,hi=1,10**15
 while e.quote(e.WETH,K,hi)<target_raw and hi<2*10**17:hi*=2
 if hi>=2*10**17:return None
 for _ in range(70):
  mid=(lo+hi)//2
  if e.quote(e.WETH,K,mid)>=target_raw:hi=mid
  else:lo=mid+1
 out=e.quote(e.WETH,K,hi);rev=e.quote(K,e.WETH,out);return hi,out,rev
def launch():
 cmd='PYTHONPATH= PYTHONHOME= /Users/jc_agent/.venvs/rh-burner/bin/python /Users/jc_agent/.hermes/scripts/rh_kaleido_accumulator_execute.py > /Users/jc_agent/reports/rh_kaleido_accumulator_execute.log 2>&1'
 script=f'tell application "Terminal" to do script "{cmd}"';subprocess.run(['osascript','-e',script],check=True,capture_output=True,text=True)
def main():
 cfg=load(CFG,{});st=load(STATE,{'peak_native':cfg['reference_peak_native'],'spent_eth':0,'pending':False});p=market();price=float(p.get('priceNative') or 0);peak=max(float(st.get('peak_native') or 0),price);st['peak_native']=peak;dd=(1-price/peak)*100 if peak else 0;liq=float((p.get('liquidity') or {}).get('usd') or 0);v1=float((p.get('volume') or {}).get('h1') or 0);v5=float((p.get('volume') or {}).get('m5') or 0);t1=(p.get('txns') or {}).get('h1') or {};trades=int(t1.get('buys') or 0)+int(t1.get('sells') or 0);buys=int(t1.get('buys') or 0);bal=e.erc20_balance(K);balance=bal/1e18
 hype=liq>=cfg['min_liquidity_usd'] and v1>=cfg['min_volume_1h_usd'] and v5>=cfg['min_volume_5m_usd'] and trades>=cfg['min_trades_1h'] and buys>=cfg['min_buys_1h']
 tranche=next((x for x in cfg['tranches'] if balance+1e-6<float(x['target_balance'])),None);status='TARGET_REACHED' if not tranche else 'WAIT_PULLBACK';checks={}
 if tranche:
  delta=max(0,float(tranche['target_balance'])-balance);q=quote_for(int(delta*1e18));remaining=float(cfg['max_total_spend_eth'])-float(st.get('spent_eth') or 0)
  if q:
   amount,out,rev=q;impact=(1-rev/amount)*100;checks={'price':price<=float(tranche['max_price_native']),'drawdown':dd>=float(tranche['min_drawdown_pct']),'hype':hype,'route':impact<=cfg['max_roundtrip_impact_pct'],'budget':amount/1e18<=remaining,'weth':amount<=e.erc20_balance(e.WETH)}
   if all(checks.values()):
    status='READY';c={'as_of':datetime.now(timezone.utc).isoformat(),'status':'READY','target_balance':tranche['target_balance'],'amount_in_wei':amount,'expected_out_wei':out,'roundtrip_impact_pct':impact,'price_native':price,'drawdown_pct':dd,'hype':{'liquidity_usd':liq,'volume_1h_usd':v1,'volume_5m_usd':v5,'trades_1h':trades,'buys_1h':buys}};save(CAND,c)
    pending_age=time.time()-float(st.get('pending_at') or 0)
    if not st.get('pending') or pending_age>600:launch();st['pending']=True;st['pending_at']=time.time();status='EXECUTION_REQUESTED'
 out={'as_of':datetime.now(timezone.utc).isoformat(),'status':status,'balance_kaleido':balance,'target_kaleido':1.0,'price_native':price,'peak_native':peak,'drawdown_pct':dd,'hype_ok':hype,'liquidity_usd':liq,'volume_1h_usd':v1,'volume_5m_usd':v5,'trades_1h':trades,'checks':checks,'spent_eth':st.get('spent_eth',0)};save(OUT,out)
 ex=load(EXEC,{});buyhash=(ex.get('tx_hashes') or {}).get('buy')
 if buyhash and st.get('last_reported_tx')!=buyhash:
  print(f"```text\nKALEIDO ACCUMULATED | +{float(ex['acquired_kaleido']):.4f} | total {float(ex['balance_kaleido']):.4f}/1.0000 | spent {float(ex['spent_weth']):.5f} WETH | {buyhash}\n```");st['last_reported_tx']=buyhash
 save(STATE,st);return 0
def guarded_main():
 try:
  rc=main()
 except Exception as exc:
  text=str(exc).lower()
  if not any(marker in text for marker in TRANSIENT_MARKERS):raise
  now=time.time();health=load(HEALTH,{});last=float(health.get('last_failure_at') or 0);count=int(health.get('consecutive_failures') or 0) if now-last<1800 else 0;count+=1;last_alert=float(health.get('last_alert_at') or 0);alert=count==3 or (count>3 and now-last_alert>=21600)
  health.update({'consecutive_failures':count,'last_failure_at':now,'last_error_type':type(exc).__name__})
  if alert:health['last_alert_at']=now
  save(HEALTH,health)
  if alert:
   print(f'KALEIDO monitor provider unavailable for {count} consecutive runs: {type(exc).__name__}: {str(exc)[:240]}',file=sys.stderr)
   return 1
  return 0
 save(HEALTH,{'consecutive_failures':0,'last_success_at':time.time()});return rc
if __name__=='__main__':raise SystemExit(guarded_main())
