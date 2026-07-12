#!/usr/bin/env python3
import json,sys,time
from pathlib import Path
sys.path.insert(0,'/Users/jc_agent/.hermes/scripts')
import rh_autonomous_exit_executor as x
TOKEN='0x020bfc650a365f8bb26819deaabf3e21291018b4';OUT=Path('/Users/jc_agent/reports/rh_cashcat_rotation_latest.json')
def wait(h):
 for _ in range(90):
  r=x.rpc('eth_getTransactionReceipt',[h])
  if r:
   if int(r.get('status','0x0'),16)!=1:raise RuntimeError('tx reverted '+h)
   return r
  time.sleep(2)
 raise TimeoutError(h)
def main():
 before=x.balance(TOKEN);amount=before*3//4;quote=x.quote(TOKEN,x.WETH,amount);txs={}
 a=x.approve_if_needed(TOKEN,amount,True)
 if a and a.get('tx_hash'):txs['approve']=a['tx_hash'];wait(a['tx_hash'])
 quote=x.quote(TOKEN,x.WETH,amount);minout=quote*94//100;data=x.sell_data(TOKEN,amount,minout);txs['swap']=x.sign_send(x.ROUTER,data);rec=wait(txs['swap'])
 after=x.balance(TOKEN);weth=x.balance(x.WETH)
 out={'type':'CASHCAT_ROTATION_EXECUTED','sold_units':(before-after)/1e18,'retained_units':after/1e18,'quoted_weth':quote/1e18,'min_weth':minout/1e18,'weth_balance':weth/1e18,'block':int(rec['blockNumber'],16),'tx_hashes':txs}
 OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
