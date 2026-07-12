#!/usr/bin/env python3
import importlib.util,sys,time
from pathlib import Path
MOD=Path('/Users/jc_agent/.hermes/scripts/rh_nft_intelligence.py');sys.path.insert(0,str(MOD.parent));spec=importlib.util.spec_from_file_location('intel',MOD);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
now='2026-07-12T22:59:28.000000Z'
m.time.time=lambda:1783897268
row={'from':{'hash':m.ZERO},'to':{'hash':'0x'+'1'*40},'timestamp':now,'transaction_hash':'0x'+'2'*64,'token':{'address_hash':'0x'+'3'*40,'name':'Foundational Free Mint','symbol':'FFM'},'total':{'token_id':'7'}}
m.getj=lambda *a,**k:{'items':[row],'next_page_params':None}
m.tx_detail=lambda h:{'value':'0x0','from':'0x'+'4'*40}
out=m.chainwide_mint_activity();assert len(out)==1;assert out[0]['type']=='MINT';assert out[0]['price_eth']==0;assert out[0]['collection']=='Foundational Free Mint';assert out[0]['token_id']=='7'
print('chainwide mint discovery test passed')
