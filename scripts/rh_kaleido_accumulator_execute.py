#!/usr/bin/env python3
"""Execute one pre-authorized KALEIDO pullback tranche from a fresh candidate."""
import json,sys,time
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,'/Users/jc_agent/.hermes/scripts')
from eth_abi import encode
from eth_utils import to_checksum_address
import rh_autonomous_executor as buy
import rh_autonomous_exit_executor as tx
K=to_checksum_address('0x6689ab375aaaa9aed78aaaba2e8edcc547478cf1');W=to_checksum_address(buy.WETH)
CAND=Path('/Users/jc_agent/reports/rh_kaleido_accumulator_candidate.json');OUT=Path('/Users/jc_agent/reports/rh_kaleido_accumulator_execution_latest.json');STATE=Path('/Users/jc_agent/.hermes/cron/state/rh_kaleido_accumulator.json')
def load(p,d):
 try:return json.loads(p.read_text())
 except Exception:return d
def wait(h):
 for _ in range(90):
  r=tx.rpc('eth_getTransactionReceipt',[h])
  if r:
   if int(r.get('status','0x0'),16)!=1:raise RuntimeError('reverted '+h)
   return r
  time.sleep(2)
 raise TimeoutError(h)
def main():
 c=load(CAND,{});at=datetime.fromisoformat(str(c.get('as_of','')).replace('Z','+00:00'));age=(datetime.now(timezone.utc)-at).total_seconds()
 if c.get('status')!='READY' or age>180:raise RuntimeError('candidate stale or not ready')
 amount=int(c['amount_in_wei']);expected=buy.quote(W,K,amount);minimum=expected*94//100
 if expected<int(c['expected_out_wei'])*97//100:raise RuntimeError('quote deteriorated >3%')
 before=buy.erc20_balance(K);txs={};a=tx.approve_if_needed(W,amount,True)
 if a and a.get('tx_hash'):txs['approve_weth']=a['tx_hash'];wait(a['tx_hash'])
 params=(W,K,buy.FEE,to_checksum_address(buy.WALLET),amount,minimum,0);data=buy.SEL_SWAP+encode(['(address,address,uint24,address,uint256,uint256,uint160)'],[params]).hex();txs['buy']=tx.sign_send(buy.ROUTER,data);rec=wait(txs['buy']);after=buy.erc20_balance(K)
 if after<=before:raise RuntimeError('KALEIDO balance did not increase')
 s=load(STATE,{});s['spent_eth']=float(s.get('spent_eth') or 0)+amount/1e18;s['pending']=False;s['last_execution_at']=datetime.now(timezone.utc).isoformat();s['balance_kaleido']=after/1e18;STATE.write_text(json.dumps(s,indent=2))
 out={'type':'KALEIDO_TRANCHE_EXECUTED','as_of':datetime.now(timezone.utc).isoformat(),'spent_weth':amount/1e18,'acquired_kaleido':(after-before)/1e18,'balance_kaleido':after/1e18,'share_expected':after>=10**18,'block':int(rec['blockNumber'],16),'tx_hashes':txs};OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
