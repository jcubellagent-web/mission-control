#!/usr/bin/env python3
"""Guarded Robinhood free/cheap-mint executor using the JAIMES burner.

Standing scope: one NFT per contract when a free or <=$0.10 mint has strict
traction/collector evidence and is approaching sellout. Total cost is capped.
"""
import json, subprocess, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from eth_account import Account
from eth_utils import keccak, to_checksum_address

RPC='https://rpc.mainnet.chain.robinhood.com';CHAIN_ID=4663
WALLET='0xa8Fed22B1DF934370B7E9E0F611a3A894Fc257d8'
CAND=Path('/Users/jc_agent/reports/rh_nft_free_mint_candidate.json')
LEDGER=Path('/Users/jc_agent/reports/rh_nft_free_mint_ledger.jsonl')
LATEST=Path('/Users/jc_agent/reports/rh_nft_free_mint_executor_latest.json')
POSITIONS=Path('/Users/jc_agent/reports/rh_nft_strategy_positions.json')
MAX_GAS_ETH=0.0003
MAX_MINT_PRICE_USD=0.10
MAX_TOTAL_COST_USD=0.15
NOARG={('0x'+keccak(text=s)[:4].hex()) for s in ['mint()','publicMint()','freeMint()','claim()']}
ONEARG={('0x'+keccak(text=s)[:4].hex()) for s in ['mint(uint256)','publicMint(uint256)','freeMint(uint256)','claim(uint256)']}
TRANSFER_TOPIC='0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'

def now():return datetime.now(timezone.utc).isoformat()
def rpc(method,params):
 b=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode();req=urllib.request.Request(RPC,data=b,headers={'Content-Type':'application/json','User-Agent':'Mozilla/5.0','Origin':'https://docs.robinhood.com/','Referer':'https://docs.robinhood.com/'})
 with urllib.request.urlopen(req,timeout=30) as r:d=json.loads(r.read())
 if d.get('error'):raise RuntimeError(str(d['error']))
 return d['result']
def emit(x):
 LATEST.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
 with LEDGER.open('a') as f:f.write(json.dumps(x,sort_keys=True)+'\n')
def signer():
 key=subprocess.check_output(['security','find-generic-password','-a','jaimes','-s','jaimes-rh-burner-private-key','-w'],text=True).strip();a=Account.from_key(key)
 if a.address.lower()!=WALLET.lower():raise RuntimeError('NFT signer address mismatch')
 return a
def already(contract):
 if not LEDGER.exists():return False
 return any(json.loads(x).get('contract','').lower()==contract.lower() and json.loads(x).get('type') in {'EXECUTED_FREE_MINT','EXECUTED_NFT_MINT'} for x in LEDGER.read_text().splitlines() if x.strip())
def record_position(receipt,r,total_usd,h):
 ids=[]
 for log in (receipt or {}).get('logs') or []:
  topics=log.get('topics') or []
  if len(topics)==4 and str(topics[0]).lower()==TRANSFER_TOPIC and str(log.get('address') or '').lower()==str(r.get('contract') or '').lower():
   try:ids.append(str(int(str(topics[3]),16)))
   except Exception:pass
 try:state=json.loads(POSITIONS.read_text())
 except Exception:state={'positions':[]}
 rows=state.get('positions') or []
 for token_id in ids:
  rows.append({'contract':str(r.get('contract') or '').lower(),'token_id':token_id,'collection':r.get('collection'),'slug':r.get('slug'),'status':'OPEN','opened_at':now(),'basis_usd':total_usd/max(len(ids),1),'mint_tx':h,'horizon':'QUICK_0_TO_48_HOURS','medium_extension_rule':'real secondary demand only','absolute_max_hold_days':14})
 POSITIONS.write_text(json.dumps({'updated_at':now(),'positions':rows},indent=2)+'\n')
 return ids
def main():
 try:x=json.loads(CAND.read_text());r=x.get('candidate') or {}
 except Exception:x={};r={}
 base={'at':now(),'contract':r.get('contract'),'collection':r.get('collection')}
 if x.get('status')!='READY' or not r.get('auto_mint_ready'):
  emit({**base,'type':'NO_FREE_MINT_CANDIDATE','allowed':False});return 0
 contract=str(r['contract']).lower();data=str(r.get('safe_mint_calldata') or '')
 if already(contract):emit({**base,'type':'FREE_MINT_ALREADY_DONE','allowed':False});return 0
 sel=data[:10].lower();safe=(len(data)==10 and sel in NOARG) or (len(data)==74 and sel in ONEARG and int(data[10:],16)==1)
 mint_value=int(r.get('mint_value_wei') or 0);eth_usd=float(x.get('eth_usd') or 0);mint_usd=mint_value/1e18*eth_usd
 checks={'price_qualified':bool(r.get('price_qualified')) and mint_usd<=MAX_MINT_PRICE_USD,'mint_velocity':int(r.get('mints_15m') or 0)>=12,'collector_breadth':int(r.get('unique_collectors') or 0)>=8,'smart_collectors':int(r.get('smart_collectors') or 0)>=2,'sellout_proximity':bool(r.get('going_to_mint_out')),'wash_risk':float(r.get('wash_risk') or 1)<=.25,'legitimacy':float(r.get('legitimacy_score') or 0)>=75,'safe_public_selector':safe}
 if not all(checks.values()):emit({**base,'type':'FREE_MINT_BLOCKED','allowed':False,'checks':checks});return 0
 a=signer();tx={'from':WALLET,'to':to_checksum_address(contract),'value':hex(mint_value),'data':data}
 gas=int(rpc('eth_estimateGas',[tx]),16);gp=int(rpc('eth_gasPrice',[]),16);gas_cost=gas*gp/1e18
 total_usd=(gas_cost+mint_value/1e18)*eth_usd
 if gas_cost>MAX_GAS_ETH or total_usd>MAX_TOTAL_COST_USD:emit({**base,'type':'FREE_MINT_BLOCKED','allowed':False,'checks':checks,'reason':'cost_cap','mint_usd':mint_usd,'gas_eth':gas_cost,'total_usd':total_usd});return 0
 nonce=int(rpc('eth_getTransactionCount',[WALLET,'pending']),16);bal=int(rpc('eth_getBalance',[WALLET,'latest']),16)
 if bal<mint_value+gas*gp*2:emit({**base,'type':'FREE_MINT_BLOCKED','allowed':False,'reason':'mint_balance','gas_eth':gas_cost});return 0
 signed=a.sign_transaction({'chainId':CHAIN_ID,'nonce':nonce,'to':to_checksum_address(contract),'value':mint_value,'data':data,'gas':int(gas*1.2),'gasPrice':gp})
 h=rpc('eth_sendRawTransaction',['0x'+signed.raw_transaction.hex()]);receipt=None
 for _ in range(45):
  receipt=rpc('eth_getTransactionReceipt',[h])
  if receipt:break
  time.sleep(2)
 ok=bool(receipt and int(receipt.get('status','0x0'),16)==1)
 token_ids=record_position(receipt,r,total_usd,h) if ok else []
 out={**base,'type':'EXECUTED_NFT_MINT' if ok else 'NFT_MINT_FAILED','allowed':True,'tx_hash':h,'mint_price_usd':mint_usd,'total_usd':total_usd,'gas_eth':gas_cost,'checks':checks,'receipt_status':int(receipt.get('status','0x0'),16) if receipt else None,'token_ids':token_ids,'holding_horizon':'QUICK_0_TO_48_HOURS','medium_extension_rule':'extend only on real secondary demand','absolute_max_hold_days':14};emit(out)
 if ok:print(f"🖼 NFT MINT EXECUTED | {r.get('collection')} | mint ${mint_usd:.3f} | total ${total_usd:.3f} | {h}")
 return 0
if __name__=='__main__':raise SystemExit(main())
