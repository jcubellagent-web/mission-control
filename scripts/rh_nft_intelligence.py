#!/usr/bin/env python3
"""Robinhood NFT intelligence: holdings, live activity, collector DB and alerts.

Read-only. It writes an auto-mint candidate only when strict evidence is met;
signing/broadcast is isolated in rh_nft_free_mint_executor.py.
"""
from __future__ import annotations
import concurrent.futures, json, math, os, re, statistics, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import rh_nft_analytics as nft_analytics

WALLET = os.environ.get('RH_NFT_WALLET','0xf7B3bbD497ff72c7616b401F99c032ed1ae16Ee4').lower()
BLOCK = 'https://robinhoodchain.blockscout.com/api/v2'
RPC = 'https://rpc.mainnet.chain.robinhood.com'
MINT_ACTIVITY = 'https://r.jina.ai/http://opensea.io/activity?chains=robinhood%26activityTypes=mint'
SALE_ACTIVITY = 'https://r.jina.ai/http://opensea.io/activity?chains=robinhood%26activityTypes=sale'
REPORT = Path('/Users/jc_agent/reports/rh_nft_intelligence_latest.json')
INVENTORY = Path('/Users/jc_agent/reports/rh_nft_owned_inventory.json')
EVENTS = Path('/Users/jc_agent/reports/rh_nft_activity_events.jsonl')
SMART_DB = Path('/Users/jc_agent/reports/rh_nft_smart_wallets.json')
STATE = Path('/Users/jc_agent/.hermes/cron/state/rh_nft_intelligence_state.json')
CANDIDATE = Path('/Users/jc_agent/reports/rh_nft_free_mint_candidate.json')
MAX_MINT_PRICE_USD=float(os.environ.get('RH_NFT_MAX_MINT_PRICE_USD','0.10'))
UA={'User-Agent':'Mozilla/5.0 JAIMES-RH-NFT','Origin':'https://opensea.io','Referer':'https://opensea.io/'}
ZERO='0x0000000000000000000000000000000000000000'
WETH='0x0bd7d308f8e1639fab988df18a8011f41eacad73'
TRANSFER_TOPIC='0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
TX_CACHE={}
MINT_ITEMS_CACHE={}
TRANSFER_CACHE=None


def now(): return datetime.now(timezone.utc).isoformat()
def load(p,d):
    try:return json.loads(p.read_text())
    except:return d
def save(p,x):
    p.parent.mkdir(parents=True,exist_ok=True);q=p.with_suffix(p.suffix+'.tmp');q.write_text(json.dumps(x,indent=2,sort_keys=True)+'\n');q.replace(p)
def fetch(url,tries=4,timeout=45):
    last=None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=timeout) as r:return r.read().decode('utf-8','ignore')
        except Exception as e:last=e;time.sleep(1.5*(i+1))
    raise last
def getj(url,tries=4,timeout=25): return json.loads(fetch(url,tries,timeout))
def rpc(method,params):
    b=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode()
    req=urllib.request.Request(RPC,data=b,headers={'Content-Type':'application/json','User-Agent':'Mozilla/5.0','Origin':'https://docs.robinhood.com/','Referer':'https://docs.robinhood.com/'})
    with urllib.request.urlopen(req,timeout=30) as r:d=json.loads(r.read())
    if d.get('error'):raise RuntimeError(str(d['error']))
    return d['result']
def eth_call(to,data):
    try:return int(rpc('eth_call',[{'to':to,'data':data},'latest']),16)
    except:return None

def eth_usd():
    try:
        d=getj('https://api.coinbase.com/v2/prices/ETH-USD/spot',tries=2,timeout=12)
        return float((d.get('data') or {}).get('amount') or 0)
    except:return 0.0

def age_sec(s):
    m=re.search(r'(\d+)\s*([smhd])\s*ago',s or '')
    if not m:return 999999
    return int(m.group(1))*{'s':1,'m':60,'h':3600,'d':86400}[m.group(2)]
def price_eth(s):
    if not s or s.strip() in {'—','-'}:return 0.0
    m=re.search(r'<?\s*([0-9.]+)\s*(?:W?ETH)',s,re.I)
    return float(m.group(1)) if m else 0.0

def receipt_mint_items(tx_hash):
    """Recover collection/token IDs when OpenSea renders a collection-only row."""
    if tx_hash in MINT_ITEMS_CACHE:return MINT_ITEMS_CACHE[tx_hash]
    try: receipt=rpc('eth_getTransactionReceipt',[tx_hash]) or {}
    except Exception:return []
    out=[]
    for log in receipt.get('logs') or []:
        topics=log.get('topics') or []
        if len(topics)!=4 or str(topics[0]).lower()!=TRANSFER_TOPIC:continue
        frm=topic_address(topics[1]);to=topic_address(topics[2])
        if frm!=ZERO:continue
        contract=str(log.get('address') or '').lower()
        try:token_id=str(int(str(topics[3]),16))
        except Exception:continue
        if re.fullmatch(r'0x[a-f0-9]{40}',contract):out.append({'contract':contract,'token_id':token_id,'to':to})
    MINT_ITEMS_CACHE[tx_hash]=out
    return out


def parse_activity(text):
    text=text.split('\nEvent\n',1)[-1]
    blocks=re.split(r'(?m)^(?=(?:Mint|Sale)\n\n)',text)
    out=[]
    for b in blocks:
        typ=(b.splitlines() or [''])[0].strip()
        if typ not in {'Mint','Sale'}:continue
        im=re.search(r'opensea\.io/item/robinhood/(0x[a-fA-F0-9]{40})/(\d+)',b)
        cm=re.search(r'\[([^\]]+)\]\(http://opensea\.io/collection/([^\)]+)\)',b)
        tx=re.search(r'blockscout\.com/tx/(0x[a-fA-F0-9]{64})',b)
        tm=re.search(r'\[([^\]]*ago)\]\(https://robinhoodchain\.blockscout\.com/tx/',b)
        if not (cm and tx):continue
        tail=b[cm.end():]
        vals=[x.strip() for x in tail.splitlines() if x.strip()]
        price=vals[0] if vals else '—'
        slugs=[s for s in re.findall(r'\]\(http://opensea\.io/([^\)]+)\)',b) if not s.startswith(('item/','collection/'))]
        slugs=[s for s in slugs if s not in {'profile'}]
        frm=ZERO if typ=='Mint' else (slugs[-2] if len(slugs)>=2 else '')
        to=slugs[-1] if slugs else ''
        if not to.startswith('0x'):
            addrs=re.findall(r'0x[a-fA-F0-9]{40}',b);to=addrs[-1] if len(addrs)>1 else to
        items=[{'contract':im.group(1).lower(),'token_id':im.group(2),'to':to.lower()}] if im else (receipt_mint_items(tx.group(1).lower()) if typ=='Mint' else [])
        for item in items:
            e={'type':typ.upper(),'contract':item['contract'],'token_id':item['token_id'],'collection':cm.group(1).strip(),'slug':cm.group(2),'price_text':price,'price_eth':price_eth(price),'from':frm.lower(),'to':str(item.get('to') or to).lower(),'tx_hash':tx.group(1).lower(),'age_text':tm.group(1) if tm else '', 'age_seconds':age_sec(tm.group(1) if tm else '')}
            e['event_id']=f"{e['tx_hash']}:{e['contract']}:{e['token_id']}:{e['type']}";out.append(e)
    unique={e['event_id']:e for e in out}
    return list(unique.values())

def chainwide_mint_activity(max_pages=3):
    """Capture recent ERC-721 mints directly from Blockscout, including projects not indexed by OpenSea yet."""
    rows=[];params={}
    for _ in range(max_pages):
        query=urllib.parse.urlencode({'type':'ERC-721',**params})
        try:data=getj(f'{BLOCK}/token-transfers?{query}',tries=3,timeout=35)
        except Exception:break
        rows.extend(data.get('items') or [])
        params=data.get('next_page_params') or {}
        if not params:break
    cutoff=time.time()-1800;mint_rows=[]
    for x in rows:
        frm=x.get('from') or {};frm=str(frm.get('hash') if isinstance(frm,dict) else frm or '').lower()
        if frm!=ZERO:continue
        try:seen=datetime.fromisoformat(str(x.get('timestamp')).replace('Z','+00:00')).timestamp()
        except Exception:continue
        if seen<cutoff:continue
        token=x.get('token') or {};total=x.get('total') or {};inst=total.get('token_instance') or {};to=x.get('to') or {};to=str(to.get('hash') if isinstance(to,dict) else to or '').lower();contract=str(token.get('address_hash') or '').lower();token_id=str(total.get('token_id') or inst.get('id') or '')
        if not contract or not token_id:continue
        mint_rows.append((x,contract,token_id,to,seen))
    tx_counts=Counter(str(x[0].get('transaction_hash') or '').lower() for x in mint_rows)
    out=[]
    for x,contract,token_id,to,seen in mint_rows:
        h=str(x.get('transaction_hash') or '').lower();detail=tx_detail(h);value=detail.get('value') or 0;unit=(int(value,0) if isinstance(value,str) else int(value))/1e18/max(1,tx_counts[h]);token=x.get('token') or {};collection=token.get('name') or token.get('symbol') or contract[:10]
        e={'type':'MINT','contract':contract,'token_id':token_id,'collection':collection,'slug':'','price_text':f'{unit:.8f} ETH','price_eth':unit,'from':ZERO,'to':to,'tx_hash':h,'tx_actor':str(detail.get('from') or '').lower(),'age_text':'chain','age_seconds':max(0,int(time.time()-seen))}
        e['event_id']=f"{h}:{contract}:{token_id}:MINT";out.append(e)
    return out

def wallet_inventory():
    data=getj(f'{BLOCK}/addresses/{WALLET}/tokens?type=ERC-721',tries=6,timeout=30)
    rows=[]
    for x in data.get('items') or []:
        t=x.get('token') or {};a=str(t.get('address_hash') or '').lower()
        if not a:continue
        rows.append({'address':a,'name':t.get('name') or t.get('symbol') or a[:10],'symbol':t.get('symbol'),'count':int(x.get('value') or 0),'holders':int(t.get('holders_count') or 0),'total_supply':int(t.get('total_supply') or 0)})
    rows.sort(key=lambda r:(r['count'],r['holders']),reverse=True)
    return rows

def wallet_contents(addr):
    try:d=getj(f'{BLOCK}/addresses/{addr}/tokens?type=ERC-721',tries=2,timeout=15)
    except:return {'collections':0,'nfts':0,'names':[],'ok':False}
    rows=[]
    for x in d.get('items') or []:
        t=x.get('token') or {}; rows.append((t.get('name') or t.get('symbol') or str(t.get('address_hash'))[:10],int(x.get('value') or 0)))
    return {'collections':len(rows),'nfts':sum(v for _,v in rows),'names':[n for n,_ in sorted(rows,key=lambda z:z[1],reverse=True)[:12]],'ok':True}

def tx_detail(h):
    if h in TX_CACHE:return TX_CACHE[h]
    try:d=rpc('eth_getTransactionByHash',[h]) or {}
    except:d={}
    if not d:
        try:d=getj(f'{BLOCK}/transactions/{h}',tries=3)
        except:d={}
    TX_CACHE[h]=d
    return d


def wallet_nft_transfers():
    """Fetch all ERC-721 movements for the tracked wallet once per run."""
    global TRANSFER_CACHE
    if TRANSFER_CACHE is not None:return TRANSFER_CACHE
    url=(f'https://robinhoodchain.blockscout.com/api?module=account&action=tokennfttx'
         f'&address={WALLET}&page=1&offset=10000&sort=asc')
    try:data=getj(url,tries=5,timeout=60);rows=data.get('result') or []
    except Exception:rows=[]
    TRANSFER_CACHE=[r for r in rows if isinstance(r,dict)]
    return TRANSFER_CACHE


def topic_address(topic):
    raw=str(topic or '').lower().removeprefix('0x')
    return ('0x'+raw[-40:]) if len(raw)>=40 else ''


def sale_proceeds_eth(tx_hash):
    """Estimate native/WETH proceeds paid to the tracked wallet."""
    proceeds=0.0;found=False
    try:
        receipt=rpc('eth_getTransactionReceipt',[tx_hash]) or {}
        for log in receipt.get('logs') or []:
            topics=log.get('topics') or []
            if str(log.get('address') or '').lower()!=WETH or len(topics)<3:continue
            if str(topics[0]).lower()!=TRANSFER_TOPIC or topic_address(topics[2])!=WALLET:continue
            proceeds+=int(log.get('data') or '0x0',16)/1e18;found=True
    except Exception:pass
    try:
        data=getj(f'{BLOCK}/transactions/{tx_hash}/internal-transactions',tries=2,timeout=20)
        for row in data.get('items') or []:
            to=row.get('to') or {};to_addr=str(to.get('hash') if isinstance(to,dict) else to or '').lower()
            if to_addr!=WALLET:continue
            value=row.get('value') or 0
            proceeds+=(int(value,0) if isinstance(value,str) else int(value))/1e18;found=True
    except Exception:pass
    return proceeds if found else None


def purchase_outflow_eth(tx_hash):
    """Return WETH paid by the tracked wallet, or None when not observed."""
    paid=0.0;found=False
    try:
        receipt=rpc('eth_getTransactionReceipt',[tx_hash]) or {}
        for log in receipt.get('logs') or []:
            topics=log.get('topics') or []
            if str(log.get('address') or '').lower()!=WETH or len(topics)<3:continue
            if str(topics[0]).lower()!=TRANSFER_TOPIC or topic_address(topics[1])!=WALLET:continue
            paid+=int(log.get('data') or '0x0',16)/1e18;found=True
    except Exception:pass
    return paid if found else None


def wallet_collection_position(contract,count,market_price_eth=0.0):
    """FIFO basis/PnL estimate from wallet NFT transfers and tx cashflows."""
    contract=str(contract or '').lower();rows=wallet_nft_transfers()
    all_by_hash=defaultdict(list)
    for r in rows:all_by_hash[str(r.get('hash') or '').lower()].append(r)
    relevant=[r for r in rows if str(r.get('contractAddress') or '').lower()==contract]
    by_hash=defaultdict(list)
    for r in relevant:by_hash[str(r.get('hash') or '').lower()].append(r)
    lots=[];realized=0.0;realized_known=True;unknown_basis=0
    for h,items in sorted(by_hash.items(),key=lambda kv:int((kv[1][0].get('blockNumber') or 0))):
        incoming=[r for r in items if str(r.get('to') or '').lower()==WALLET]
        outgoing=[r for r in items if str(r.get('from') or '').lower()==WALLET]
        if incoming:
            d=tx_detail(h);sender=str(d.get('from') or '').lower()
            value=d.get('value') or 0
            paid=(int(value,0) if isinstance(value,str) else int(value))/1e18
            gas=0.0
            if sender==WALLET:
                first=items[0];gas=int(first.get('gasUsed') or 0)*int(first.get('gasPrice') or 0)/1e18
            total_in_tx=sum(1 for r in all_by_hash.get(h,[]) if str(r.get('to') or '').lower()==WALLET)
            is_mint=all(str(r.get('from') or '').lower()==ZERO for r in incoming)
            weth_paid=purchase_outflow_eth(h) if sender==WALLET and paid==0 and not is_mint else None
            total_paid=paid+(weth_paid or 0)+gas
            basis_known=bool(is_mint or paid>0 or weth_paid is not None)
            unit_cost=(total_paid/max(total_in_tx,1)) if basis_known else None
            if not basis_known:unknown_basis+=len(incoming)
            lots.extend([unit_cost]*len(incoming))
        if outgoing:
            removed=[]
            for _ in outgoing:
                removed.append(lots.pop(0) if lots else None)
            proceeds=sale_proceeds_eth(h)
            if proceeds is None or any(v is None for v in removed):realized_known=False
            else:realized+=proceeds-sum(removed)
    # Reconcile indexer/history gaps to the verified live holding count.
    count=max(0,int(count or 0))
    if len(lots)>count:lots=lots[-count:] if count else []
    elif len(lots)<count:
        unknown_basis+=count-len(lots);lots.extend([None]*(count-len(lots)))
    known_basis=sum(v for v in lots if v is not None);basis_known=all(v is not None for v in lots)
    basis=known_basis if basis_known else None;avg=(basis/count) if count and basis is not None else None
    market=float(market_price_eth or 0);value=count*market if market>0 else None
    unreal=(value-basis) if value is not None and basis is not None else None
    unreal_pct=(unreal/basis) if unreal is not None and basis and basis>0 else None
    confidence='high' if unknown_basis==0 else ('medium' if unknown_basis<max(count,1) else 'low')
    return {'held':count,'avg_entry_eth':avg,'cost_basis_eth':basis,'known_basis_lower_bound_eth':known_basis,'market_price_eth':market or None,
            'estimated_value_eth':value,'unrealized_eth':unreal,'unrealized_pct':unreal_pct,
            'realized_eth':realized if realized_known else None,'basis_confidence':confidence,
            'unknown_basis_units':unknown_basis,'transfer_events':len(relevant)}


def fmt_eth(value):
    return 'n/a' if value is None else f'{float(value):+.5f}' if float(value)<0 else f'{float(value):.5f}'


def wallet_position_table(position):
    p=position or {};held=int(p.get('held') or 0)
    avg='n/a' if p.get('avg_entry_eth') is None else f"{float(p.get('avg_entry_eth') or 0):.6f} ETH/NFT"
    basis='n/a' if p.get('cost_basis_eth') is None else f"{float(p['cost_basis_eth']):.5f} ETH"
    if p.get('cost_basis_eth') is None and p.get('known_basis_lower_bound_eth'):
        basis=f">={float(p['known_basis_lower_bound_eth']):.5f} ETH known"
    mark='n/a' if p.get('market_price_eth') is None else f"{float(p['market_price_eth']):.6f} ETH/NFT"
    floor='n/a' if p.get('estimated_value_eth') is None else f"{fmt_eth(p['estimated_value_eth'])} ETH"
    unreal='n/a' if p.get('unrealized_eth') is None else f"{fmt_eth(p['unrealized_eth'])} ETH ({float(p.get('unrealized_pct') or 0):+.1%})"
    realized='n/a' if p.get('realized_eth') is None else f"{fmt_eth(p['realized_eth'])} ETH"
    return '\n'.join([
        '── POSITION ──────────────────',
        f"Held        {held} NFTs",
        f"Avg entry   {avg}",
        f"Cost basis  {basis}",
        f"Floor value {floor}",

        f"Realized    {realized}",
        f"Basis/mark  {str(p.get('basis_confidence') or 'n/a').title()} / {mark}",
    ])


def resolve_event_wallets(events):
    for e in events:
        d=tx_detail(e['tx_hash'])
        raw_from=d.get('from')
        actor=str((raw_from.get('hash') if isinstance(raw_from,dict) else raw_from) or '').lower()
        if re.fullmatch(r'0x[a-f0-9]{40}',actor):
            e['tx_actor']=actor
            if not re.fullmatch(r'0x[a-f0-9]{40}',str(e.get('to') or '')):e['to']=actor
    return events

def token_supply(contract):
    # ERC-721 totalSupply() and common max-supply variants.
    total=eth_call(contract,'0x18160ddd')
    caps=[]
    for sel in ('0xd5abeb01','0xe2c79281','0x32cb6b0c'): # maxSupply(), MAX_SUPPLY(), collectionSize()
        v=eth_call(contract,sel)
        if v and v>=int(total or 0):caps.append(v)
    try:
        t=getj(f'{BLOCK}/tokens/{contract}',tries=2)
        api_total=int(t.get('total_supply') or 0)
        holders=int(t.get('holders_count') or 0)
    except:api_total=holders=0
    return {'minted':int(total or api_total),'max_supply':min(caps) if caps else None,'holders':holders}

def smart_score(row):
    contents=row.get('contents') or {};obs=row.get('observations') or {}
    collections=int(contents.get('collections') or 0);nfts=int(contents.get('nfts') or 0);distinct=int(obs.get('distinct_collections') or 0)
    score=min(35,collections*5)+min(15,nfts)+min(20,distinct*5)+min(10,int(obs.get('secondary_buys') or 0)*2)+min(10,int(obs.get('sales') or 0)*2)
    if collections<=1 and int(obs.get('mints') or 0)>=4:score-=25
    if float(obs.get('self_trade_ratio') or 0)>0.1:score-=35
    return max(0,min(100,score))

def update_wallet_db(events,db):
    rows=db.setdefault('wallets',{})
    active=[]
    for e in events:
        a=e.get('to','').lower()
        if not re.fullmatch(r'0x[a-f0-9]{40}',a) or a==ZERO:continue
        r=rows.setdefault(a,{'first_seen':now(),'observations':{'mints':0,'secondary_buys':0,'sales':0,'collections':[]}})
        o=r['observations'];o['mints' if e['type']=='MINT' else 'secondary_buys']+=1;o['last_seen']=now()
        o['collections']=sorted(set((o.get('collections') or [])+[e['contract']]))
        o['distinct_collections']=len(o['collections']);active.append(a)
    # Enrich a bounded number of most active/current wallets per tick.
    targets=list(dict.fromkeys(active))[:8]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        content_map=dict(zip(targets,pool.map(wallet_contents,targets)))
    for a in targets:
        rows[a]['contents']=content_map[a];rows[a]['smart_score']=smart_score(rows[a]);rows[a]['classification']='smart_collector' if rows[a]['smart_score']>=65 else ('real_collector' if rows[a]['smart_score']>=35 else 'unproven')
    db['updated_at']=now();return db

def group_collections(events,db,eth_price_usd):
    groups=defaultdict(list)
    for e in events:
        if e['age_seconds']<=900:groups[e['contract']].append(e)
    out=[]
    for c,es in groups.items():
        m=[e for e in es if e['type']=='MINT'];s=[e for e in es if e['type']=='SALE'];buyers=[e['to'] for e in es if e.get('to')]
        uniq=len(set(buyers));top=(Counter(buyers).most_common(1)[0][1]/len(buyers)) if buyers else 1
        smart=sum(1 for a in set(buyers) if ((db.get('wallets') or {}).get(a) or {}).get('smart_score',0)>=35)
        supply=token_supply(c);prices=[e['price_eth'] for e in s if e['price_eth']>0]
        free_ok=0;safe_data=None;sample_values=[]
        for e in m[:3]:
            d=tx_detail(e['tx_hash']);v=d.get('value') or 0;val=int(v,0) if isinstance(v,str) else int(v)
            same_tx=[x for x in receipt_mint_items(e['tx_hash']) if x.get('contract')==c]
            if same_tx:val//=len(same_tx)
            raw=str(d.get('raw_input') or d.get('input') or '')
            sample_values.append(val)
            if val==0:free_ok+=1
            if len(raw)==10 or (len(raw)==74 and int(raw[10:],16)==1):safe_data=raw
        positive=sorted(v for v in sample_values if v>0);mint_value_wei=positive[len(positive)//2] if positive else 0
        paid_verified=sum(1 for v in positive if mint_value_wei and abs(v-mint_value_wei)/mint_value_wei<=.05)
        mint_price_eth=mint_value_wei/1e18;mint_price_usd=mint_price_eth*eth_price_usd
        cheap_paid=bool(paid_verified>=2 and eth_price_usd>0 and mint_price_usd<=MAX_MINT_PRICE_USD);price_qualified=free_ok>=2 or cheap_paid
        cap=supply.get('max_supply');ratio=(supply['minted']/cap) if cap else None
        fresh_ratio=sum(1 for a in set(buyers) if (((db.get('wallets') or {}).get(a) or {}).get('contents') or {}).get('collections',0)<=1)/max(uniq,1)
        wash=min(1.0,max(top-0.25,0)*1.4+fresh_ratio*0.35+(0.4 if uniq<=2 and len(es)>=5 else 0))
        legitimacy=max(0,min(100,uniq*5+smart*12+min(20,len(s)*4)+min(20,len(m)*2)-wash*55))
        going=bool(m and cap and ratio is not None and .70<=ratio<.995 and (cap-supply['minted'])<=max(300,int(cap*.25)))
        auto=bool(len(m)>=12 and uniq>=8 and price_qualified and safe_data and going and wash<=.25 and smart>=2 and legitimacy>=75)
        out.append({'contract':c,'collection':es[0]['collection'],'slug':es[0]['slug'],'mints_15m':len(m),'sales_15m':len(s),'unique_collectors':uniq,'smart_collectors':smart,'sale_volume_eth':sum(prices),'median_sale_eth':statistics.median(prices) if prices else 0,'top_buyer_share':top,'fresh_wallet_ratio':fresh_ratio,'wash_risk':round(wash,3),'legitimacy_score':round(legitimacy,1),'free_mint_verified_samples':free_ok,'paid_mint_verified_samples':paid_verified,'mint_value_wei':mint_value_wei,'mint_price_eth':mint_price_eth,'mint_price_usd':mint_price_usd,'cheap_paid_mint':cheap_paid,'price_qualified':price_qualified,'minted':supply['minted'],'max_supply':cap,'minted_ratio':ratio,'going_to_mint_out':going,'safe_mint_calldata':safe_data,'auto_mint_ready':auto,'events':len(es)})
    out.sort(key=lambda r:(r['auto_mint_ready'],r['legitimacy_score'],r['sales_15m']+r['mints_15m']),reverse=True);return out

def attach_wallet_position(row,owned):
    contract=str(row.get('contract') or '').lower();holding=owned.get(contract) or {}
    count=int(row.get('new') if row.get('new') is not None else holding.get('count') or 0)
    market=float(row.get('median_sale_eth') or 0)
    if contract and (count>0 or row.get('old') is not None):
        row['wallet_position']=wallet_collection_position(contract,count,market)
    else:
        row['wallet_position']={'held':count,'avg_entry_eth':None,'cost_basis_eth':0.0,
            'market_price_eth':market or None,'estimated_value_eth':None,'unrealized_eth':None,
            'unrealized_pct':None,'realized_eth':None,'basis_confidence':'n/a','unknown_basis_units':0}
    return row


def format_nft_alert(kind: str, row: dict[str, Any]) -> str:
    """Render a mobile-first NFT decision card."""
    label={'holding_change':'OWNED MOVEMENT','owned_activity':'OWNED COLLECTION','mint_watch':'EARLY MINT WATCH','mint_ready':'MINT READY','secondary':'SECONDARY TRACTION'}[kind]
    status={'holding_change':'VERIFY WALLET MOVEMENT','owned_activity':'MONITOR OWNED EXPOSURE','mint_watch':'FREE/CHEAP MINT GAINING TRACTION','mint_ready':'GUARDED EXECUTOR ELIGIBLE','secondary':'TRACTION CONFIRMED'}[kind]
    a=row.get('analytics') or {};s=a.get('scores') or {};m=a.get('market') or {};h=a.get('holders') or {};p=row.get('wallet_position') or {}
    decision=s.get('decision') or status
    action='n/a' if s.get('action_score') is None else f"{float(s['action_score']):.0f}/100"
    pnl='n/a' if p.get('unrealized_pct') is None else f"{float(p['unrealized_pct']):+.1%}"
    risks=[]
    if s.get('wash_risk') is not None:risks.append(f"wash {float(s['wash_risk']):.0%} heuristic")
    if h.get('top10_share') is not None:risks.append(f"top-10 {float(h['top10_share']):.1%}")
    risks=(risks+(s.get('invalidation') or [])+['unavailable','unavailable'])[:2]
    trigger={'holding_change':'Verified wallet count changed','mint_ready':'All guarded mint gates cleared','secondary':'Secondary quality gates cleared','owned_activity':'Owned activity met material gate'}[kind]
    plain={'EXIT REVIEW':'Review bids before selling','AVOID':'Do not buy; wait','BUY/ADD':'Review guarded entry','HOLD/WATCH':'Hold and monitor','WATCH':'Watch; do not chase'}.get(decision,'Review before acting')
    wash_context=f"{risks[0]} · {int(row.get('sales_15m') or 0)} sales/{int(row.get('unique_collectors') or 0)} collector"
    lines=[f'NFT ALERT · {label}','━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━','── COLLECTION ────────────────',f"Name       {row.get('collection') or 'Unknown'}",'Chain      Robinhood',f"Contract   {row.get('contract') or 'Unavailable'}",'','── ALERT STATUS ──────────────',status,'','── QUICK READ ────────────────',f"Decision   {decision} · action {action}",f"Owned P/L  {pnl}",f"Floor/bid  {nft_analytics.fmt_num(m.get('floor_eth'))} / {nft_analytics.fmt_num(m.get('top_bid_eth'))} ETH · {nft_analytics.fmt_ratio(m.get('bid_ask_spread_pct'))}",f"Positive   {(s.get('catalysts') or ['No confirmed catalyst'])[0]}",f"Risk 1     {wash_context}",f"Risk 2     {risks[1]}",f"Trigger    {trigger} · {plain}",'',wallet_position_table(p)]
    if kind=='holding_change':
        old=int(row.get('old') or 0);new=int(row.get('new') or 0)
        lines += [f"Holdings   {old} → {new} ({new-old:+d})",'',nft_analytics.render(a),'','── VERIFY ───────────────────','Expected mint, transfer, sale, or indexer sync?']
    else:
        minted=int(row.get('minted') or 0);cap=row.get('max_supply')
        supply=f"{minted:,}/{int(cap):,} ({float(row.get('minted_ratio') or 0):.1%})" if cap else f"{minted:,}/unknown"
        activity='\n'.join(['── ACTIVITY ──────────────────',f"Mints 15m  {int(row.get('mints_15m') or 0)}",f"Sales 15m  {int(row.get('sales_15m') or 0)} verified",f"Collectors {int(row.get('unique_collectors') or 0)} unique",f"Smart      {int(row.get('smart_collectors') or 0)} collectors",f"Supply     {supply}",f"Mint       ${float(row.get('mint_price_usd') or 0):.3f}",f"Volume     {float(row.get('sale_volume_eth') or 0):.4f} ETH / 15m",f"Mint path  {'verified' if row.get('safe_mint_calldata') else 'not verified'}"])
        detail=nft_analytics.render(a).replace('── HOLDERS',activity+'\n\n── HOLDERS',1)
        lines += ['',detail]
    return '\n'.join(line[:53] for block in lines for line in str(block).splitlines())


def main():
    ts=now();state=load(STATE,{'seen_events':{},'owned_baseline':{},'alerts':{}});db=load(SMART_DB,{'wallets':{}})
    inventory=wallet_inventory();owned={r['address']:r for r in inventory};prior=state.get('owned_baseline') or {}
    holding_changes=[]
    for a,r in owned.items():
        if a in prior and int(prior[a].get('count',0))!=r['count']:holding_changes.append({'collection':r['name'],'old':prior[a].get('count',0),'new':r['count'],'contract':a})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        mint_future=pool.submit(fetch,MINT_ACTIVITY,tries=3,timeout=55)
        sale_future=pool.submit(fetch,SALE_ACTIVITY,tries=3,timeout=55)
        mint_text=mint_future.result();sale_text=sale_future.result()
    parsed=parse_activity(mint_text)+parse_activity(sale_text)+chainwide_mint_activity()
    events=resolve_event_wallets(list({e['event_id']:e for e in parsed}.values()))
    seen=state.setdefault('seen_events',{});new_events=[e for e in events if e['event_id'] not in seen]
    if new_events:
        with EVENTS.open('a') as f:
            for e in new_events:f.write(json.dumps({'observed_at':ts,**e},sort_keys=True)+'\n');seen[e['event_id']]=ts
    price=eth_usd();db=update_wallet_db(events,db);collections=group_collections(events,db,price)
    owned_activity=[r for r in collections if r['contract'] in owned and (r['sales_15m']>=3 or r['mints_15m']>=8 or r['legitimacy_score']>=70)]
    mint_watch=[r for r in collections if r['price_qualified'] and r['mints_15m']>=5 and r['unique_collectors']>=4 and r['wash_risk']<=.35 and r['legitimacy_score']>=55]
    secondary=[r for r in collections if r['sales_15m']>=4 and r['unique_collectors']>=3 and r['wash_risk']<=.25 and r['legitimacy_score']>=60]
    ready=[r for r in collections if r['auto_mint_ready']]
    alert_rows=holding_changes+owned_activity[:3]+mint_watch[:2]+ready[:1]+secondary[:2]
    analytics_history=state.setdefault('analytics_history',{})
    for row in alert_rows:
        attach_wallet_position(row,owned)
        contract=str(row.get('contract') or '').lower()
        try:
            row['analytics']=nft_analytics.enrich(contract,row.get('slug'),row,WALLET,analytics_history.get(contract) or {})
            analytics_history[contract]=row['analytics']
        except Exception as exc:
            row['analytics']={'error':f'{exc.__class__.__name__}: {str(exc)[:160]}','data_quality':{'grade':'low','available_layers':0,'total_layers':7}}
    state['analytics_history']={k:v for k,v in list(analytics_history.items())[-250:]}
    candidate={'as_of':ts,'wallet':WALLET,'mint_wallet':'0xa8Fed22B1DF934370B7E9E0F611a3A894Fc257d8','status':'READY' if ready else 'NONE','candidate':ready[0] if ready else None,'eth_usd':price,'max_mint_price_usd':MAX_MINT_PRICE_USD,'rules':'free or <=$0.10 verified mint price; safe public mint calldata; >=12 mints/15m; >=8 collectors; >=2 smart collectors; >=70% minted with known cap; wash risk <=0.25; one mint per contract'}
    save(CANDIDATE,candidate);save(INVENTORY,{'as_of':ts,'wallet':WALLET,'collection_count':len(inventory),'nft_count':sum(r['count'] for r in inventory),'collections':inventory});save(SMART_DB,db)
    out={'as_of':ts,'wallet':WALLET,'mint_wallet':'0xa8Fed22B1DF934370B7E9E0F611a3A894Fc257d8','eth_usd':price,'max_mint_price_usd':MAX_MINT_PRICE_USD,'inventory_collections':len(inventory),'inventory_nfts':sum(r['count'] for r in inventory),'activity_events':len(events),'new_events':len(new_events),'holding_changes':holding_changes,'owned_material':owned_activity[:8],'mint_watch':mint_watch[:8],'free_mint_watch':[r for r in mint_watch if r['mint_value_wei']==0][:8],'secondary_traction':secondary[:8],'top_collections':collections[:12],'smart_wallet_count':len(db.get('wallets') or {}),'smart_collectors':sum(1 for r in (db.get('wallets') or {}).values() if r.get('smart_score',0)>=65),'auto_mint_ready':len(ready),'errors':[]}
    save(REPORT,out);state['owned_baseline']=owned;state['last_run_at']=ts;state['seen_events']={k:v for k,v in list(seen.items())[-3000:]};save(STATE,state)
    alerts=[]
    for x in holding_changes:alerts.append(('holding_change',x))
    for r in owned_activity[:3]:alerts.append(('owned_activity',r))
    for r in [x for x in mint_watch if not x['auto_mint_ready']][:2]:alerts.append(('mint_watch',r))
    for r in ready[:1]:alerts.append(('mint_ready',r))
    for r in secondary[:2]:alerts.append(('secondary',r))
    # Cool down collection/category alerts for 30 minutes.
    emit=[];at=time.time();cd=state.setdefault('alerts',{})
    for kind,row in alerts:
        key=f"{kind}|{row.get('contract') or row.get('collection')}"
        if at-float(cd.get(key,0))>=1800:
            emit.append(format_nft_alert(kind,row));cd[key]=at
    save(STATE,state)
    if emit:print('\n\n'.join(emit))
    return 0
if __name__=='__main__':raise SystemExit(main())
