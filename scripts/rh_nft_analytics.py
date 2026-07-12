#!/usr/bin/env python3
"""Read-only deep analytics for Robinhood NFT alert candidates.

Every unavailable field stays None/n/a. No metric is fabricated from a missing API.
"""
from __future__ import annotations
import fcntl, hashlib, json, math, os, re, statistics, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BLOCK='https://robinhoodchain.blockscout.com/api/v2'
LEGACY='https://robinhoodchain.blockscout.com/api'
RPC='https://rpc.mainnet.chain.robinhood.com'
ZERO='0x0000000000000000000000000000000000000000'
DEAD='0x000000000000000000000000000000000000dead'
UA={'User-Agent':'Mozilla/5.0 JAIMES-RH-NFT-Analytics','Origin':'https://docs.robinhood.com','Referer':'https://docs.robinhood.com/'}
CACHE=Path('/Users/jc_agent/.hermes/cron/state/rh_nft_analytics_cache.json')  # legacy; removed after migration
CACHE_DIR=Path('/Users/jc_agent/.hermes/cron/state/rh_nft_analytics_cache')
LATEST_REPORT=Path('/Users/jc_agent/reports/rh_nft_intelligence_latest.json')


def now_ts():return datetime.now(timezone.utc).isoformat()
def f(v,default=0.0):
    try:return float(v) if v not in (None,'') else default
    except:return default
def pct(new,old):return ((new/old)-1) if old else None
def getj(url,timeout=45):
    req=urllib.request.Request(url,headers=UA)
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode('utf-8','ignore'))
def gettext(url,timeout=45):
    req=urllib.request.Request(url,headers=UA)
    with urllib.request.urlopen(req,timeout=timeout) as r:return r.read().decode('utf-8','ignore')
def rpc(method,params):
    body=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode()
    req=urllib.request.Request(RPC,data=body,headers={**UA,'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as r:d=json.loads(r.read())
    if d.get('error'):raise RuntimeError(str(d['error']))
    return d.get('result')
def cache_path(key):return CACHE_DIR/(hashlib.sha256(key.encode()).hexdigest()+'.json')
def cached(key,ttl,fn):
    CACHE_DIR.mkdir(parents=True,exist_ok=True);path=cache_path(key);lock=path.with_suffix('.lock')
    with lock.open('a+') as handle:
        fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
        try:
            row=json.loads(path.read_text()) if path.exists() else {}
        except Exception:row={}
        if time.time()-f(row.get('at'))<ttl:return row.get('value')
        value=fn();tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps({'key':key,'at':time.time(),'value':value},separators=(',',':'))+'\n');tmp.replace(path)
        return value

def first_float(pattern,text):
    m=re.search(pattern,text or '',re.I|re.S)
    return float(m.group(1).replace(',','')) if m else None

def market_snapshot(slug,row):
    out={'available':False,'floor_eth':None,'top_bid_eth':None,'bid_floor_ratio':None,'listed_pct':None,'volume_24h_eth':None,
         'owners_marketplace':None,'reference_price_eth':f(row.get('median_sale_eth')) or None,'data_source':'activity'}
    if not slug:return out
    try:
        page=cached(f'page:{slug}',300,lambda:gettext(f'https://r.jina.ai/http://opensea.io/collection/{slug}',55))
        out.update({
            'floor_eth':first_float(r'Floor price\s+([0-9.]+)\s+ETH',page),
            'top_bid_eth':first_float(r'Top offer\s+([0-9.]+)\s+WETH',page),
            'listed_pct':first_float(r'Listed\s+([0-9.]+)%',page),
            'volume_24h_eth':first_float(r'24h volume\s+([0-9.,]+)\s+ETH',page),
            'owners_marketplace':int(first_float(r'Owners \(Unique\)\s*([0-9,]+)',page) or 0) or None,
            'data_source':'opensea+jina',
        })
        links=sorted(set(re.findall(r'https?://(?:x\.com|twitter\.com|discord\.(?:gg|com)|t\.me|[A-Za-z0-9.-]+\.(?:com|io|art|xyz|fi))/[^\s\)\]]*',page,re.I)))
        excluded=('opensea.io','seadn.io','dweb.link','ipfs.io','googleapis.com','cloudfront.net')
        out['project_links']=[x for x in links if not any(host in urllib.parse.urlparse(x).netloc.lower() for host in excluded)][:8]
    except Exception as e:out['error']=e.__class__.__name__
    out['reference_price_eth']=out['floor_eth'] or out['reference_price_eth']
    out['bid_floor_ratio']=(out['top_bid_eth']/out['floor_eth']) if out['top_bid_eth'] and out['floor_eth'] else None
    out['bid_ask_spread_pct']=(1-out['bid_floor_ratio']) if out['bid_floor_ratio'] is not None else None
    out['listings_estimate']=round((out['listed_pct']/100)*f(row.get('minted'))) if out['listed_pct'] is not None and row.get('minted') else None
    out['available']=any(out.get(k) is not None for k in ('floor_eth','top_bid_eth','volume_24h_eth','reference_price_eth'))
    return out

def holder_rows(contract,max_pages=25,page_size=1000):
    rows=[];truncated=False
    for page in range(1,max_pages+1):
        q=urllib.parse.urlencode({'module':'token','action':'getTokenHolders','contractaddress':contract,'page':page,'offset':page_size})
        d=getj(f'{LEGACY}?{q}',60);batch=d.get('result')
        if not isinstance(batch,list):raise RuntimeError(str(d.get('message') or 'holder API returned no list'))
        rows.extend({'address':str(x.get('address') or '').lower(),'value':str(x.get('value') or '0')} for x in batch)
        if len(batch)<page_size:break
        if page==max_pages:truncated=True
    return {'rows':rows,'truncated':truncated}
def gini(values):
    vals=sorted(v for v in values if v>=0);n=len(vals);total=sum(vals)
    if not n or not total:return None
    return (2*sum((i+1)*x for i,x in enumerate(vals))/(n*total))-((n+1)/n)
def holder_snapshot(contract,supply):
    try:
        payload=cached(f'holders:{contract}',900,lambda:holder_rows(contract));rows=payload.get('rows',[]) if isinstance(payload,dict) else payload
        truncated=bool(payload.get('truncated')) if isinstance(payload,dict) else len(rows)>=10000
    except Exception as e:return {'available':False,'error':e.__class__.__name__}
    if not rows:return {'available':False,'error':'empty holder result'}
    clean=[];burned=0
    for r in rows:
        a=str(r.get('address') or '').lower();v=f(r.get('value'))
        if a in {ZERO,DEAD}:burned+=v;continue
        if v>0:clean.append((a,v))
    clean.sort(key=lambda x:x[1],reverse=True);vals=[v for _,v in clean];circulating=sum(vals) or f(supply)
    share=lambda n:sum(vals[:n])/circulating if circulating and not truncated else None
    return {'available':not truncated,'sampled':truncated,'owners':len(vals) if not truncated else None,'sample_owners':len(vals),
            'owner_supply_ratio':len(vals)/f(supply) if supply and not truncated else None,'average_per_owner':circulating/len(vals) if vals and not truncated else None,
            'median_balance':statistics.median(vals) if vals and not truncated else None,'single_holder_ratio':sum(v==1 for v in vals)/len(vals) if vals and not truncated else None,
            'top1_share':share(1),'top5_share':share(5),'top10_share':share(10),'top20_share':share(20),'gini':gini(vals) if not truncated else None,
            'burned_units':burned,'largest_wallet':clean[0][0] if vals and clean else None,'rows':len(rows),'truncated':truncated}

def transfer_rows(contract):
    q=urllib.parse.urlencode({'module':'account','action':'tokennfttx','contractaddress':contract,'page':1,'offset':10000,'sort':'asc'})
    d=getj(f'{LEGACY}?{q}',75);rows=d.get('result')
    if not isinstance(rows,list):raise RuntimeError(str(d.get('message') or 'transfer API returned no list'))
    keep=('from','to','tokenID','timeStamp','hash','blockNumber')
    return [{k:x.get(k) for k in keep} for x in rows]
def row_time(r):
    try:return int(r.get('timeStamp') or 0)
    except:return 0
def flow_snapshot(contract):
    try:rows=cached(f'transfers:{contract}',900,lambda:transfer_rows(contract))
    except Exception as e:return {'available':False,'error':e.__class__.__name__}
    now=time.time();nonzero=[];mints=burns=selfs=0;token_path=defaultdict(list);pairs=Counter();buyers=Counter();sellers=Counter();holds=[];last_in={}
    for r in rows:
        frm=str(r.get('from') or '').lower();to=str(r.get('to') or '').lower();tid=str(r.get('tokenID') or '');ts=row_time(r)
        token_path[tid].append((frm,to,ts))
        if frm==ZERO:mints+=1
        elif to in {ZERO,DEAD}:burns+=1
        else:
            nonzero.append(r);pairs[(frm,to)]+=1;buyers[to]+=1;sellers[frm]+=1
            if frm==to:selfs+=1
            if (frm,tid) in last_in and ts:holds.append(max(0,ts-last_in[(frm,tid)]))
        if to not in {ZERO,DEAD}:last_in[(to,tid)]=ts
    roundtrips=0
    for path in token_path.values():
        edges={(a,b) for a,b,_ in path if a not in {ZERO,DEAD} and b not in {ZERO,DEAD}}
        if any((b,a) in edges for a,b in edges):roundtrips+=1
    oneh=[r for r in nonzero if row_time(r) and now-row_time(r)<=3600];day=[r for r in nonzero if row_time(r) and now-row_time(r)<=86400]
    repeated=max(pairs.values())/len(nonzero) if nonzero and pairs else 0
    wash=min(1.0,selfs/max(len(nonzero),1)*2+roundtrips/max(len(token_path),1)*2+max(0,repeated-.15)*1.5)
    return {'available':True,'transfer_rows':len(rows),'history_truncated':len(rows)>=10000,'mints':mints,'burns':burns,'secondary_transfers':len(nonzero),
            'secondary_transfer_velocity_1h':len(oneh),'secondary_transfer_velocity_24h':len(day),'unique_receivers_history':len(buyers),'unique_senders_history':len(sellers),
            'top_buyer_count_share':max(buyers.values())/sum(buyers.values()) if buyers else None,'top_seller_count_share':max(sellers.values())/sum(sellers.values()) if sellers else None,
            'self_transfers':selfs,'roundtrip_token_count':roundtrips,'repeated_counterparty_share':repeated,'median_hold_hours':statistics.median(holds)/3600 if holds else None,
            'transfer_pattern_risk':wash,'payment_verified':False}

def decode_string(data):
    try:
        raw=bytes.fromhex(str(data).removeprefix('0x'));off=int.from_bytes(raw[:32],'big');ln=int.from_bytes(raw[off:off+32],'big');return raw[off+32:off+32+ln].decode('utf-8','ignore')
    except:return None
def source_summary(contract):
    d=getj(f'{BLOCK}/smart-contracts/{contract}')
    return {'has_source':len(str(d.get('source_code') or ''))>100,'name':d.get('name'),'compiler_version':d.get('compiler_version')}
def contract_snapshot(contract):
    out={'available':False,'verified':None,'proxy':None,'upgradeable':None,'owner':None,'owner_check_available':False,'metadata_uri':None,'metadata_permanence':'unknown'}
    try:
        addr=cached(f'address:{contract}',3600,lambda:getj(f'{BLOCK}/addresses/{contract}'))
        proxy_type=str(addr.get('proxy_type') or '').lower() or None;implementations=addr.get('implementations') or []
        out.update({'available':True,'creator':str(addr.get('creator_address_hash') or '').lower() or None,'creation_tx':addr.get('creation_transaction_hash'),
                    'verified':bool(addr.get('is_verified')),'proxy':bool(proxy_type or implementations),'proxy_type':proxy_type,
                    'upgradeable':bool(proxy_type and proxy_type!='eip1167'),'implementations':implementations,'scam_flag':bool(addr.get('is_scam'))})
    except Exception as e:out['address_error']=e.__class__.__name__
    try:
        sc=cached(f'source:{contract}',3600,lambda:source_summary(contract))
        out['verified']=bool(out.get('verified') or sc.get('has_source'))
        out['source_name']=sc.get('name');out['compiler_version']=sc.get('compiler_version')
    except Exception as e:out['source_error']=e.__class__.__name__
    try:
        owner=rpc('eth_call',[{'to':contract,'data':'0x8da5cb5b'},'latest'])
        out['owner_check_available']=True
        if owner and int(owner,16):out['owner']='0x'+owner[-40:].lower()
    except:pass
    for tid in (1,0):
        try:
            data='0xc87b56dd'+hex(tid)[2:].rjust(64,'0');uri=decode_string(rpc('eth_call',[{'to':contract,'data':data},'latest']))
            if uri:out['metadata_uri']=uri;break
        except:pass
    uri=str(out.get('metadata_uri') or '')
    out['metadata_permanence']='decentralized' if uri.startswith(('ipfs://','ar://')) else ('centralized' if uri.startswith(('http://','https://')) else 'unknown')
    risk=0;reasons=[]
    if out['verified'] is False:risk+=25;reasons.append('source unverified')
    elif out['verified'] is None:risk+=15;reasons.append('source status unknown')
    if out['proxy'] is None:risk+=10;reasons.append('proxy status unknown')
    if out.get('upgradeable'):risk+=20;reasons.append('upgradeable proxy')
    elif out.get('proxy_type')=='eip1167':reasons.append('immutable EIP-1167 clone')
    if out['owner']:risk+=10;reasons.append('owner control present')
    elif not out['owner_check_available']:risk+=10;reasons.append('owner status unknown')
    if out.get('scam_flag'):risk+=50;reasons.append('explorer scam flag')
    if out['metadata_permanence']=='centralized':risk+=12;reasons.append('centralized metadata')
    elif out['metadata_permanence']=='decentralized':reasons.append('decentralized metadata')
    out['risk_score']=min(100,risk);out['reasons']=reasons or ['no obvious contract flags']
    return out

def creator_snapshot(creator):
    if not creator:return {'available':False,'reason':'creator unavailable'}
    try:
        d=cached(f'creator:{creator}',3600,lambda:getj(f'{BLOCK}/addresses/{creator}/transactions'))
        items=d.get('items') or [];created=[x for x in items if x.get('created_contract')]
        return {'available':True,'creator':creator,'recent_transactions':len(items),'contracts_created_recent_page':len(created),'wallet_nonce_proxy':len(items),
                'funded_at':items[-1].get('timestamp') if items else None}
    except Exception as e:return {'available':False,'creator':creator,'error':e.__class__.__name__}

def comparable_snapshot(row):
    try:data=json.loads(LATEST_REPORT.read_text())
    except Exception:return {'available':False,'peers':0,'reason':'prior report unavailable'}
    contract=str(row.get('contract') or '').lower();supply=f(row.get('max_supply') or row.get('minted'));peers=[]
    for x in data.get('top_collections') or []:
        if str(x.get('contract') or '').lower()==contract:continue
        peer_supply=f(x.get('max_supply') or x.get('minted'))
        if supply and peer_supply and not (.5<=peer_supply/supply<=2):continue
        peers.append(x)
    if not peers:return {'available':False,'peers':0,'reason':'no active comparable set'}
    legitimacy=[f(x.get('legitimacy_score')) for x in peers];activity=[int(x.get('sales_15m') or 0)+int(x.get('mints_15m') or 0) for x in peers]
    cur_activity=int(row.get('sales_15m') or 0)+int(row.get('mints_15m') or 0)
    return {'available':True,'peers':len(peers),'peer_names':[x.get('collection') for x in peers[:4]],
            'median_peer_legitimacy':statistics.median(legitimacy),'legitimacy_delta':f(row.get('legitimacy_score'))-statistics.median(legitimacy),
            'activity_percentile':sum(v<=cur_activity for v in activity)/len(activity),'median_peer_activity_15m':statistics.median(activity)}

def instance_trait_sample(contract,max_pages=10):
    rows=[];params={}
    for _ in range(max_pages):
        qs=('?'+urllib.parse.urlencode(params)) if params else ''
        d=getj(f'{BLOCK}/tokens/{contract}/instances{qs}',40)
        rows.extend(d.get('items') or []);params=d.get('next_page_params') or {}
        if not params:break
    return [{'token_id':str(x.get('id')),'traits':((x.get('metadata') or {}).get('attributes') or [])} for x in rows]

def wallet_items(contract,wallet,supply=0):
    try:
        q=urllib.parse.urlencode({'holder_address_hash':wallet})
        d=cached(f'walletitems:{contract}:{wallet}',900,lambda:getj(f'{BLOCK}/tokens/{contract}/instances?{q}'))
        rows=d.get('items') or [];items=[];trait_count=0
        for x in rows:
            attrs=((x.get('metadata') or {}).get('attributes') or []);trait_count+=len(attrs)
            items.append({'token_id':str(x.get('id')),'name':(x.get('metadata') or {}).get('name'),'traits':attrs[:12],'image_url':x.get('image_url') or x.get('media_url')})
        rarity_rank=None;rarity_token=None;sample_size=0
        if trait_count:
            sample=cached(f'traitsample:{contract}',3600,lambda:instance_trait_sample(contract))
            trait_rows=[x for x in sample if x.get('traits')];sample_size=len(trait_rows)
            counts=Counter((str(a.get('trait_type')),str(a.get('value'))) for x in trait_rows for a in x.get('traits') or [])
            def rscore(attrs):return sum(-math.log((counts[(str(a.get('trait_type')),str(a.get('value')))] + 1)/max(sample_size+1,1)) for a in attrs or [])
            population=sorted((rscore(x.get('traits')) for x in trait_rows),reverse=True)
            for item in items:
                if not item.get('traits'):continue
                score_value=rscore(item['traits']);better=sum(s>score_value for s in population)
                est=max(1,round((better+1)/max(sample_size,1)*max(int(supply or sample_size),1)))
                item['rarity_est_rank']=est;item['rarity_score']=round(score_value,3)
                if rarity_rank is None or est<rarity_rank:rarity_rank=est;rarity_token=item['token_id']
        return {'available':True,'count':len(items),'token_ids':[x['token_id'] for x in items],'items':items,'traits_available':trait_count>0,
                'rarity_rank':rarity_rank,'rarity_best_token':rarity_token,'rarity_sample_size':sample_size,'trait_floor_eth':None,
                'rarity_note':f'estimated from {sample_size} indexed metadata samples; not a marketplace trait floor' if sample_size else 'traits live; rarity sample unavailable'}
    except Exception as e:return {'available':False,'error':e.__class__.__name__,'token_ids':[],'items':[]}

def historical_changes(current,prior):
    pm=(prior or {}).get('market') or {};ph=(prior or {}).get('holders') or {};m=current['market'];h=current['holders']
    interval=None
    try:interval=(datetime.fromisoformat(current['as_of'])-datetime.fromisoformat(str((prior or {}).get('as_of')))).total_seconds()/60
    except Exception:pass
    return {'floor_change':pct(f(m.get('floor_eth')),f(pm.get('floor_eth'))) if m.get('floor_eth') is not None else None,
            'top_bid_change':pct(f(m.get('top_bid_eth')),f(pm.get('top_bid_eth'))) if m.get('top_bid_eth') is not None else None,
            'owners_change':int(h.get('owners') or 0)-int(ph.get('owners') or 0) if h.get('owners') is not None and ph.get('owners') is not None else None,
            'top10_change':f(h.get('top10_share'))-f(ph.get('top10_share')) if h.get('top10_share') is not None and ph.get('top10_share') is not None else None,
            'interval_minutes':interval}
def score(current,row):
    m=current['market'];h=current['holders'];fl=current['flow'];c=current['contract'];chg=current['changes']
    momentum=35+min(30,int(row.get('sales_15m') or 0)*5)+min(20,int(row.get('mints_15m') or 0)*2)
    if chg.get('floor_change') is not None:momentum+=max(-20,min(20,chg['floor_change']*100))
    momentum=max(0,min(100,momentum))
    liq=20+min(30,int(row.get('sales_15m') or 0)*5)
    if m.get('bid_floor_ratio') is not None:liq+=max(0,min(35,m['bid_floor_ratio']*35))
    if m.get('listed_pct') is not None and 1<=m['listed_pct']<=15:liq+=15
    liq=max(0,min(100,liq))
    holder=50
    if h.get('owner_supply_ratio') is not None:holder+=min(20,h['owner_supply_ratio']*100)
    if h.get('top10_share') is not None:holder-=max(0,(h['top10_share']-.15)*100)
    holder+=min(20,int(row.get('smart_collectors') or 0)*5);holder=max(0,min(100,holder))
    wash=f(row.get('wash_risk'))
    contract_risk=int(c['risk_score']) if c.get('risk_score') is not None else 40
    legitimacy=max(0,min(100,.45*f(row.get('legitimacy_score'))+.25*holder+.20*(100-wash*100)+.10*(100-contract_risk)))
    action=max(0,min(100,.28*momentum+.22*liq+.20*holder+.20*legitimacy+.10*(100-contract_risk)-wash*20))
    core_missing=sum([not bool(m.get('available')),not bool(h.get('available')),c.get('verified') is None,c.get('proxy') is None,not c.get('owner_check_available')])
    action=max(0,action-min(25,core_missing*5))
    held=int(((current.get('wallet') or {}).get('count') or 0))
    if wash>=.65 or contract_risk>=70:decision='EXIT REVIEW' if held else 'AVOID'
    elif action>=75 and wash<.3 and contract_risk<45:decision='BUY/ADD'
    elif action>=58:decision='HOLD/WATCH' if held else 'WATCH'
    elif held:decision='HOLD — WEAK'
    else:decision='AVOID'
    if core_missing>=2 and decision=='BUY/ADD':decision='WATCH — DATA THIN'
    catalysts=[];invalid=[]
    if int(row.get('sales_15m') or 0)>=5:catalysts.append('verified marketplace sales expanding')
    if chg.get('owners_change') and chg['owners_change']>0:catalysts.append('owner count growing')
    if m.get('bid_floor_ratio') and m['bid_floor_ratio']>=.75:catalysts.append('bid support near floor')
    if f(h.get('top10_share'))>.30:invalid.append('top-10 concentration remains high')
    invalid+=['floor -20% without bid recovery','smart-wallet net flow reverses','wash risk exceeds 50%']
    return {'action_score':round(action,1),'decision':decision,'momentum_score':round(momentum,1),'liquidity_score':round(liq,1),'holder_quality_score':round(holder,1),
            'legitimacy_score_v2':round(legitimacy,1),'wash_risk':round(wash,3),'contract_risk':contract_risk,'catalysts':catalysts[:3] or ['no confirmed catalyst'],
            'invalidation':invalid[:4],'core_missing_fields':core_missing}

def enrich(contract,slug,row,wallet,prior=None):
    supply=int(row.get('minted') or 0)
    current={'as_of':now_ts(),'market':market_snapshot(slug,row),'holders':holder_snapshot(contract,supply),'flow':flow_snapshot(contract),'contract':contract_snapshot(contract)}
    current['flow']['observed_sales_15m']=int(row.get('sales_15m') or 0)
    current['flow']['observed_mints_15m']=int(row.get('mints_15m') or 0)
    current['flow']['smart_buyers_15m']=int(row.get('smart_collectors') or 0)
    current['creator']=creator_snapshot(current['contract'].get('creator'))
    current['comparables']=comparable_snapshot({**row,'contract':contract})
    links=current['market'].get('project_links') or []
    current['social']={'available':bool(links),'links':links,'link_count':len(links),'follower_growth':None,'engagement_quality':None,
                       'source_confidence':'presence-only; social quality requires a dedicated verified-CA research pass'}
    current['wallet']=wallet_items(contract,wallet,supply)
    current['changes']=historical_changes(current,prior or {})
    current['scores']=score(current,row)
    layer_ok={'market':bool(current['market'].get('available')),'holders':bool(current['holders'].get('available')),
              'flow':bool(current['flow'].get('available')),'contract':bool(current['contract'].get('verified') is not None and current['contract'].get('proxy') is not None and current['contract'].get('owner_check_available')),
              'creator':bool(current['creator'].get('available')),'social':bool(current['social'].get('available')),
              'wallet':bool(current['wallet'].get('available')),'comparables':bool(current['comparables'].get('available'))}
    available=sum(layer_ok.values())
    gaps=['order-book bid depth/slippage','marketplace trait floors' if current['wallet'].get('trait_floor_eth') is None else None,
          'social follower/engagement quality' if current['social'].get('engagement_quality') is None else None,
          'market facts unavailable' if not layer_ok['market'] else None,'holder population incomplete' if not layer_ok['holders'] else None,
          'transfer history unavailable' if not layer_ok['flow'] else None,'contract controls incomplete' if not layer_ok['contract'] else None]
    gaps=[x for x in gaps if x]
    current['data_quality']={'available_layers':available,'total_layers':8,'grade':'high' if available==8 and not gaps else ('medium' if available>=4 else 'low'),
                             'unavailable':gaps}
    return current

def fmt_pct(v):return 'n/a' if v is None else f'{v:+.1%}'
def fmt_ratio(v):return 'n/a' if v is None else f'{v:.1%}'
def fmt_num(v,n=4):return 'n/a' if v is None else f'{float(v):.{n}f}'
def score_label(value):
    if value is None:return 'unavailable'
    value=float(value)
    if value>=75:return 'strong'
    if value>=55:return 'mixed'
    if value>=35:return 'weak'
    return 'very weak'
def render(analytics):
    a=analytics or {};m=a.get('market') or {};h=a.get('holders') or {};fl=a.get('flow') or {};c=a.get('contract') or {};w=a.get('wallet') or {};s=a.get('scores') or {};ch=a.get('changes') or {};q=a.get('data_quality') or {};comp=a.get('comparables') or {}
    verified_label='verified' if c.get('verified') is True else ('unverified' if c.get('verified') is False else 'verification n/a')
    contract_form='EIP-1167 clone' if c.get('proxy_type')=='eip1167' else ('upgradeable proxy' if c.get('upgradeable') else ('direct' if c.get('proxy') is False else 'proxy n/a'))
    owner_label='yes' if c.get('owner') else ('no' if c.get('owner_check_available') else 'n/a')
    risk_label=f"{int(c['risk_score'])}/100" if c.get('risk_score') is not None else 'n/a'
    creator_label=str(int((a.get('creator') or {}).get('contracts_created_recent_page') or 0)) if (a.get('creator') or {}).get('available') else 'n/a'
    interval_label=f"{ch.get('interval_minutes'):.0f}m" if ch.get('interval_minutes') is not None else 'n/a'
    lines=['── DECISION DRIVERS ─────────']
    lines += [f"Liquidity  {score_label(s.get('liquidity_score'))}",
              f"Holders    {score_label(s.get('holder_quality_score'))}",
              f"Legitimacy {score_label(s.get('legitimacy_score_v2'))}",
              f"Momentum   {score_label(s.get('momentum_score'))}",
              f"Invalid    {(s.get('invalidation') or ['none'])[0]}", '']
    market_known=any(m.get(k) is not None for k in ('floor_eth','top_bid_eth','listed_pct','volume_24h_eth'))
    lines += ['── MARKET ───────────────────']
    if market_known:
        lines += [
            f"Listed      {fmt_num(m.get('listed_pct'),1)}%",
            f"24h volume  {fmt_num(m.get('volume_24h_eth'),2)} ETH",

            f"Prior {interval_label:<5} floor {fmt_pct(ch.get('floor_change'))} | owners {ch.get('owners_change') if ch.get('owners_change') is not None else 'n/a'}",
        ]
    else:
        lines += ['Status      unavailable this scan']
    if comp.get('available'):
        lines += [f"Comparables {int(comp.get('peers') or 0)} peers | activity {fmt_ratio(comp.get('activity_percentile'))}"]

    lines += ['', '── HOLDERS ──────────────────']
    if h.get('available'):
        lines += [
            f"Owners      {h.get('owners') or 'n/a'}",
            f"Top 20      {fmt_ratio(h.get('top20_share'))}",
            f"Gini/single {fmt_num(h.get('gini'),3)} / {fmt_ratio(h.get('single_holder_ratio'))}",

        ]
    else:
        lines += ['Status      unavailable this scan']

    lines += ['', '── RISK ─────────────────────']
    lines += [
        f"Code        {verified_label} | {contract_form}",
        'Code check  does not prove market legitimacy',
        f"Control     owner {owner_label} | risk {risk_label}",
        f"Metadata    {c.get('metadata_permanence') or 'unknown'}",
        'Wash basis  heuristic, not proven fraud',
        f"Evidence    {int(fl.get('verified_sales') or fl.get('observed_sales_15m') or 0)} verified sales",
        f"Transfers   {fmt_ratio(fl.get('transfer_pattern_risk'))} heuristic",
        f"Round trips {int(fl.get('roundtrip_token_count') or 0) if fl.get('available') else 'n/a'}",
    ]
    if creator_label!='n/a':lines += [f"Creator     {creator_label} recent contracts"]

    token_ids=(w.get('token_ids') or [])[:8]
    lines += ['', '── ASSETS ───────────────────']
    if token_ids:
        for i in range(0,len(token_ids),4):lines += [('IDs         ' if i==0 else '            ')+', '.join(token_ids[i:i+4])]
    else:lines += ['Token IDs   unavailable']
    if w.get('traits_available'):
        lines += [f"Best rarity est. #{w.get('rarity_rank') or 'n/a'} | token {w.get('rarity_best_token') or 'n/a'}"]
    else:lines += ['Traits      unavailable']


    lines += ['', '── DATA QUALITY ─────────────']
    lines += [f"Coverage    {q.get('grade','unknown')} | {q.get('available_layers',0)}/{q.get('total_layers',8)} layers"]
    gaps=q.get('unavailable') or []
    if gaps:lines += ['Missing']+[f"• {item}" for item in gaps]
    else:lines += ['Missing     none']
    return '\n'.join(lines)
