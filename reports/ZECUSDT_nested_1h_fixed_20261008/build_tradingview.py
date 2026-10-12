"""Package the fixed entry groups in an offline TradingView Lightweight Charts view."""
from pathlib import Path
import json,sys,hashlib
from collections import defaultdict
P=Path(__file__).resolve().parent;ROOT=P.parents[1];sys.path.insert(0,str(ROOT))
from chan import read_csv,resample,parse_time,analyze,json_default
from chan_time import PERIODS
from report_btc_4h_auto import structure_lines
s=json.loads((P/'replay_summary.json').read_text());start=parse_time(s['start']);end=parse_time(s['as_of'])
minutes=read_csv(P/'history_1m.csv');rows=json.loads((P/'entries_grouped.json').read_text());plans={p['id']:p for p in json.loads((P/'triggers.json').read_text())}
assert rows and all(r['actionable'] for r in rows)
frames={}
for period,width in [('1h',3600),('15m',900),('5m',300),('1m',60)]:
 grouped={}
 for c in minutes:
  if c.time<start:continue
  t=int(c.time.timestamp())//width*width
  if t not in grouped:grouped[t]=dict(time=t,open=c.open,high=c.high,low=c.low,close=c.close,volume=c.volume,minutes=1)
  else:
   b=grouped[t];b.update(high=max(b['high'],c.high),low=min(b['low'],c.low),close=c.close,volume=b['volume']+c.volume,minutes=b['minutes']+1)
 bars=list(grouped.values())
 for b in bars:b['partial']=b['minutes']*60<width
 frames[period]=dict(width=width,bars=bars)
bars,_=resample(minutes,PERIODS['1m'],PERIODS['1h'],end)
a=analyze(bars,PERIODS['1h']);nodes=structure_lines(a['blocks'][-1],'zec:1h')
nodes=[{**n,'start':int(n['start'].timestamp()),'end':int(n['end'].timestamp())} for n in nodes if n['kind']=='stroke' and n['end']>=start]
entries=[]
for i,r in enumerate(rows,1):
 p=plans[r['first_plan_id']]
 assert p['actionable']
 e={**r,'number':i,'triggered_at':int(parse_time(p['triggered_at']).timestamp()),
    'candidate_at':int(parse_time(p['interval']['focus_time']).timestamp()),'armed_at':int(parse_time(p['armed_at']).timestamp()),
    'evidence_at':int(parse_time(p['evidence_at']).timestamp()),'parent_invalidation':p['parent_invalidation']}
 entries.append(e)
 for f in frames.values():
  bytime={b['time']:b for b in f['bars']}
  # Trigger is known AFTER the minute closes; place it on that closing minute's bar.
  for timestamp,price in [(e['triggered_at']-60,e['observed_close']),(e['candidate_at'],e['candidate_price'])]:
   b=bytime[timestamp//f['width']*f['width']]
   assert b['low']<=price<=b['high'],(i,timestamp,price,b)
data=dict(symbol='ZECUSDT',start=int(start.timestamp()),asof=int(end.timestamp()),last_close=s['last_close'],
          frames=frames,entries=entries,strokes=nodes,csv=(P/'entries.csv').read_text(encoding='utf-8-sig'))
payload=json.dumps(data,ensure_ascii=False,separators=(',',':'),default=json_default).replace('</','<\\/')
bundle=(P/'vendor/lightweight-charts-5.0.4.js').read_text()
assert '</script' not in bundle.lower()
html=(P/'tradingview_template.html').read_text().replace('LIBRARY_JS',bundle).replace('PAYLOAD_JSON',payload).replace('TOTAL_GROUPS',str(len(rows))).replace('BUY_GROUPS',str(sum(r['side']=='买' for r in rows))).replace('SELL_GROUPS',str(sum(r['side']=='卖' for r in rows)))
(P/'zec_fixed_tradingview.html').write_text(html)
(P/'chart_data.json').write_text(json.dumps(data,ensure_ascii=False,default=json_default))
manifest=dict(groups=len(rows),buys=sum(r["side"]=="买" for r in rows),sells=sum(r["side"]=="卖" for r in rows),frames={p:len(f['bars']) for p,f in frames.items()},
  strokes=len(nodes),direct_break=sum(bool(n.get('direct_break')) for n in nodes),direct_break_width=3.2,
  library='TradingView Lightweight Charts 5.0.4',library_source='https://unpkg.com/lightweight-charts@5.0.4/dist/lightweight-charts.standalone.production.js',
  library_sha256=hashlib.sha256(bundle.encode()).hexdigest(),data_sha256=hashlib.sha256(payload.encode()).hexdigest(),
  trigger_alignment='bar containing triggered_at minus 60 seconds; exact close price, no fill assumption',
  all_price_anchors_within_corresponding_ohlc=True,offline_single_file=True)
(P/'chart_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
print(json.dumps(manifest,ensure_ascii=False))
