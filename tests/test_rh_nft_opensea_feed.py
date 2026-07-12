#!/usr/bin/env python3
import importlib.util
import sys
from pathlib import Path
MOD=Path(__file__).resolve().parents[1]/'scripts'/'rh_nft_intelligence.py'
sys.path.insert(0,str(MOD.parent))
spec=importlib.util.spec_from_file_location('nft',MOD);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_collection_only_mint_hydrates_receipt():
 tx='0x'+'1'*64;contract='0x'+'2'*40
 sample=f'''Event\nMint\n\n[SplitVerse: PANDA](http://opensea.io/collection/splitverse-panda)\n\n—\n\n1\n\n—\n\n[NullAddress](http://opensea.io/NullAddress)\n\n[0xabc](http://opensea.io/0xabc)\n\n[5s ago](https://robinhoodchain.blockscout.com/tx/{tx})\n'''
 old=m.receipt_mint_items;m.receipt_mint_items=lambda h:[{'contract':contract,'token_id':'280','to':'0x'+'3'*40}]
 try:rows=m.parse_activity(sample)
 finally:m.receipt_mint_items=old
 assert len(rows)==1 and rows[0]['contract']==contract and rows[0]['token_id']=='280'

def test_item_link_still_parses():
 tx='0x'+'4'*64;contract='0x'+'5'*40
 sample=f'''Event\nSale\n\n[#9](http://opensea.io/item/robinhood/{contract}/9)\n\n[C](http://opensea.io/collection/c)\n\n0.01 ETH\n\n1\n\n—\n\n[a](http://opensea.io/0x{'6'*40})\n\n[b](http://opensea.io/0x{'7'*40})\n\n[1m ago](https://robinhoodchain.blockscout.com/tx/{tx})\n'''
 rows=m.parse_activity(sample);assert len(rows)==1 and rows[0]['price_eth']==.01
