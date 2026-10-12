import json,sys
from types import SimpleNamespace
from pathlib import Path
from datetime import timedelta
sys.path.insert(0,'/root/chan')
from chan import Fractal,Stroke,parse_time,read_csv,json_default,resample
from chan_time import PERIODS
from chan_structure import Movement
from chan_theory import evolving_centers,center_relation,macd_area
from entry_plan import observation_legs,period_candidates,execution_setups,EntryPolicy,setup_status
P=Path('/root/chan/reports/NEARUSDT_nested_1h_current_20261008')
asof=parse_time(json.loads((P/'source.json').read_text())['as_of']); raw=read_csv(P/'history_1m.csv')
def convert(x):
 if isinstance(x,dict):return {k:parse_time(v) if k in ('start','end','finish','focus_time','evidence_at','observed_at','start_time','endpoint_time','origin_endpoint_time') and isinstance(v,str) else convert(v) for k,v in x.items()}
 if isinstance(x,list):return [convert(v) for v in x]
 return x
def frac(f):return Fractal(**{**f,'time':parse_time(f['time'])})
out={}
for period in ['1h','15m','5m','1m']:
 b=json.loads((P/f'analysis_{period}.json').read_text())['blocks'][-1];b['period']=period
 b['strokes']=[Stroke(frac(s['start']),frac(s['end']),s['provisional'],parse_time(s['confirmed_at']) if s['confirmed_at'] else None) for s in b['strokes']]
 b['theory']['macd']['points']=[SimpleNamespace(**{**x,'time':parse_time(x['time'])}) for x in b['theory']['macd']['points']]
 d=convert(json.loads((P/f'legs_{period}.json').read_text())); legs=d['last_legs']; live=d['developing_reverse']
 if period=='1h':legs=observation_legs(b,raw,asof)
 roots=period_candidates(b,legs,asof,period)
 if live:roots+=period_candidates(b,legs+[live],asof,period)
 # Historical endpoint geometry is reviewed with current structure, not replayed as live signal.
 recent=[]
 for n in range(max(3,len(legs)-4),len(legs)+1):
  prefix=legs[:n]
  if prefix[-1]['direction']=='up':recent.extend(x for x in period_candidates(b,prefix,asof,period) if x['label']=='S2')
 completed=[Movement(f'P{i}',0,'stroke_proxy',s.start,s.end,s.low,s.high,(),(),s.confirmed_at) for i,s in enumerate(b['strokes']) if not s.provisional]
 view=evolving_centers(completed,1);centers=view['centers'];last=legs[-1]
 reason=dict(center_count=len(centers),last_centers=centers[-2:])
 if period=='1h' and len(centers)>=2:
  prior,z=centers[-2:];inc=next((x for x in legs if x['index']==z.first_index-1),None)
  reason.update(relation=center_relation(prior,z),tail=last,incoming=inc)
  if inc:
   reason['incoming_area']=macd_area(b['theory']['macd']['points'],inc['start'],inc['end'],'up')
   reason['tail_area']=macd_area(b['theory']['macd']['points'],last['start'],last['end'],'up')
 bars,_=resample(raw,PERIODS['1m'],PERIODS[period],asof)
 local=[]
 for c in roots:
  if c['side']!='short':continue
  for s in execution_setups(bars,c['interval'],'short',period,asof,EntryPolicy(profile='nested_1h')):
   hits=[r for r in raw if r.time+timedelta(minutes=1)>=s['evidence_at'] and r.close<s['trigger_price']]
   local.append(dict(setup=s,status=setup_status(s,c,raw),first_cross=dict(close_at=hits[0].time+timedelta(minutes=1),close=hits[0].close) if hits else None))
 out[period]=dict(current_candidates=roots,recent_s2_geometry=recent,local_setups=local)
 if period=='1h':out[period]['s1_gate_audit']=reason
 print(period,json.dumps(out[period],ensure_ascii=False,default=json_default),flush=True)
(P/'sell_candidate_audit.json').write_text(json.dumps(dict(as_of=asof,scope='复核19:05冻结快照；低周期候选不自动获得1h执行许可；历史位置非实时回放',periods=out),ensure_ascii=False,indent=2,default=json_default))
