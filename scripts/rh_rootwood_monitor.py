#!/usr/bin/env python3
"""Read-only Rootwood inventory, mint, launch, liquidity and secondary monitor."""
import json,time,urllib.request
from datetime import datetime,timezone,timedelta
from pathlib import Path
from eth_utils import keccak
RPC='https://rpc.mainnet.chain.robinhood.com';C='0x8D2301C19050bA61C1bA722EFa8a339bADD554Df';W='0xa8Fed22B1DF934370B7E9E0F611a3A894Fc257d8';EVENTS=Path('/Users/jc_agent/reports/rh_nft_activity_events.jsonl');STATE=Path('/Users/jc_agent/.hermes/cron/state/rh_rootwood_monitor.json');OUT=Path('/Users/jc_agent/reports/rh_rootwood_monitor_latest.json')
def rpc(m,p):
 req=urllib.request.Request(RPC,data=json.dumps({'jsonrpc':'2.0','id':1,'method':m,'params':p}).encode(),headers={'Content-Type':'application/json','User-Agent':'Mozilla/5.0','Origin':'https://docs.robinhood.com','Referer':'https://docs.robinhood.com/'});d=json.loads(urllib.request.urlopen(req,timeout=30).read());
 if d.get('error'):raise RuntimeError(d['error'])
 return d['result']
def sel(sig):return keccak(text=sig).hex()[:8]
def call(sig,arg='',kind='uint'):
 r=rpc('eth_call',[{'to':C,'data':'0x'+sel(sig)+arg},'latest']);
 if kind=='bool':return bool(int(r,16))
 if kind=='address':return '0x'+r[-40:]
 return int(r,16)
def load(p,d):
 try:return json.loads(p.read_text())
 except Exception:return d
def save(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,indent=2,sort_keys=True))
def secondary():
 cutoff=datetime.now(timezone.utc)-timedelta(hours=1);sales=[];mints=[]
 try:
  for line in EVENTS.read_text(errors='ignore').splitlines()[-5000:]:
   try:x=json.loads(line)
   except Exception:continue
   if str(x.get('contract','')).lower()!=C.lower():continue
   try:t=datetime.fromisoformat(str(x.get('observed_at','')).replace('Z','+00:00'))
   except Exception:continue
   if t<cutoff:continue
   (sales if x.get('type')=='SALE' else mints).append(x)
 except Exception:pass
 prices=[float(x.get('price_eth') or 0) for x in sales if float(x.get('price_eth') or 0)>0]
 return {'sales_1h':len(sales),'mint_events_1h':len(mints),'sale_volume_1h_eth':sum(prices),'lowest_sale_1h_eth':min(prices) if prices else None}
def main():
 balarg='0'*24+W[2:].lower();balance=call('balanceOf(address)',balarg);minted=call('minted()');maximum=call('maxNFTs()');price=call('mintPriceWei()')/1e18;launched=call('launched()',kind='bool');bond=call('bondToken()',kind='address');tokens_each=call('tokensPerNFT()')/1e18;sec=secondary();milestone=max(x for x in [0,25,50,75,90,95,100] if minted/maximum*100>=x)
 redeem=None;spot=None
 if launched and int(bond,16):
  amount=int(balance*tokens_each*1e18);redeem=call('quoteSell(uint256)',hex(amount)[2:].zfill(64))/1e18;spot=call('spotPriceWei()')/1e18
 out={'as_of':datetime.now(timezone.utc).isoformat(),'collection':'Rootwood','contract':C,'wallet':W,'owned':balance,'minted':minted,'max_supply':maximum,'mint_progress_pct':minted/maximum*100,'mint_price_eth':price,'launched':launched,'bond_token':bond,'root_per_nft':tokens_each,'owned_root_claim':balance*tokens_each,'redeem_all_quote_eth':redeem,'root_spot_eth':spot,'secondary':sec};save(OUT,out)
 st=load(STATE,{});alerts=[]
 if st:
  if balance!=st.get('owned'):alerts.append(f'owned {st.get("owned")} → {balance}')
  if launched and not st.get('launched'):alerts.append(f'ROOT LAUNCHED | bond {bond} | 50-NFT redemption quote {redeem:.6f} ETH' if redeem is not None else f'ROOT LAUNCHED | bond {bond}')
  if milestone>int(st.get('milestone') or 0):alerts.append(f'mint reached {milestone}% ({minted:,}/{maximum:,})')
  oldr=st.get('redeem_all_quote_eth')
  if redeem is not None and oldr and abs(redeem/oldr-1)>=.20:alerts.append(f'redemption quote moved {(redeem/oldr-1)*100:+.1f}% to {redeem:.6f} ETH')
  if sec['sales_1h'] and not int((st.get('secondary') or {}).get('sales_1h') or 0):alerts.append(f'first observed secondary sales: {sec["sales_1h"]} in 1h')
 save(STATE,{**out,'milestone':milestone})
 if alerts:print('ROOTWOOD MATERIAL UPDATE\n- '+'\n- '.join(alerts))
 return 0
if __name__=='__main__':raise SystemExit(main())
