#!/usr/bin/env python3
"""GUI-domain runner and direct sender for Robinhood NFT Alerts topic 2131."""
import html, json, os, re, subprocess, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
PY='/Users/jc_agent/.venvs/rh-burner/bin/python'
INTEL='/Users/jc_agent/.hermes/scripts/rh_nft_intelligence.py'
MINT='/Users/jc_agent/.hermes/scripts/rh_nft_free_mint_executor.py'
STATE=Path('/Users/jc_agent/.hermes/cron/state/rh_nft_gui_lane.json')
CHAT='-1003589561528';TOPIC=2131

def token():
 t=os.environ.get('TELEGRAM_BOT_TOKEN','').strip()
 if t:return t
 for line in (Path.home()/'.hermes/.env').read_text().splitlines():
  if line.startswith('TELEGRAM_BOT_TOKEN='):return line.split('=',1)[1].strip().strip('"\'')
 raise RuntimeError('Telegram token unavailable')
def send(text):
 if len(text)>3800:raise ValueError(f'alert chunk too large: {len(text)}')
 safe=html.escape(text);p={'chat_id':CHAT,'message_thread_id':TOPIC,'text':f'<pre>{safe}</pre>','parse_mode':'HTML','disable_web_page_preview':True};u=f'https://api.telegram.org/bot{token()}/sendMessage';req=urllib.request.Request(u,data=json.dumps(p).encode(),headers={'Content-Type':'application/json'});d=json.loads(urllib.request.urlopen(req,timeout=20).read());return d['result']['message_id']
def alert_chunks(text,limit=3700):
 cards=[x.strip() for x in re.split(r'(?m)(?=^NFT ALERT — )',text or '') if x.strip()]
 out=[]
 for card in cards:
  if len(card)<=limit:out.append(card);continue
  current=''
  for section in card.split('\n\n'):
   if len(section)>limit:
    if current:out.append(current+'\n\n[continued]');current=''
    for i in range(0,len(section),limit-30):
     prefix='' if i==0 else '[continued]\n';out.append(prefix+section[i:i+limit-30])
    continue
   candidate=(current+'\n\n'+section).strip()
   if current and len(candidate)>limit:out.append(current+'\n\n[continued]');current='[continued]\n'+section
   else:current=candidate
  if current:out.append(current)
 return out
def run(path,timeout):
 p=subprocess.run([PY,path],capture_output=True,text=True,timeout=timeout,env={**os.environ,'PYTHONPATH':'','PYTHONHOME':''});return {'returncode':p.returncode,'stdout':p.stdout.strip(),'stderr':p.stderr.strip()[-600:]}
def main():
 out={'as_of':datetime.now(timezone.utc).isoformat(),'steps':{}}
 for name,path,to in [('intelligence',INTEL,300),('free_mint_executor',MINT,120)]:
  r=run(path,to);out['steps'][name]={'returncode':r['returncode'],'stderr':r['stderr']}
  if r['stdout']:
   try:out['steps'][name]['message_ids']=[send(chunk) for chunk in alert_chunks(r['stdout'])]
   except Exception as e:out['steps'][name]['send_error']=f'{type(e).__name__}: {e}'
 out['ok']=all(s['returncode']==0 for s in out['steps'].values());STATE.parent.mkdir(parents=True,exist_ok=True);STATE.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');return 0 if out['ok'] else 1
if __name__=='__main__':raise SystemExit(main())
