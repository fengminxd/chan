"""Frozen prefix replay using unchanged current program rules; all inputs closed bars."""
import hashlib,json,sys
from datetime import datetime,timedelta,timezone
from pathlib import Path
P=Path(__file__).resolve().parent
ROOT=P.parents[1];sys.path.insert(0,str(ROOT))
from chan import analyze,read_csv
from report_btc_4h_auto import automatic_views,dump
TZ=timezone(timedelta(hours=8))
start=datetime(2026,10,6,22,tzinfo=TZ)
end=datetime(2026,10,7,13,tzinfo=TZ)
result=[]
for tf,hours,step in [('15m',.25,15),('1h',1,60),('4h',4,60)]:
 bars=read_csv(P/f'history_{tf}.csv');last=None
 t=start
 while t<=end:
  selected=[c for c in bars if c.time+timedelta(hours=hours)<=t]
  if selected[-1].time==last:t+=timedelta(minutes=step);continue
  last=selected[-1].time
  a=analyze(selected,hours);b=a['blocks'][-1]
  v=automatic_views(a,selected,start-timedelta(days=5),t,period_hours=hours)
  row=dict(period=tf,asof=t,closed_bar=selected[-1],strokes=b['strokes'][-8:],fractals=b['fractals'][-5:],segments=b['segments'][-2:],pending_segment=b['pending_segment'],tail=b['segment_diagnostics']['tail'],macd=b['theory']['macd']['points'][-12:],strict=[dict(level=x['level'],signals=x['signals'],audits=x['audits']) for x in b['theory']['strict_divergence']['levels']],views={k:dict(centers=list(x['centers'].values())[-2:],signals=x['signals'],divergences=x['divergences']) for k,x in v['views'].items()},direct_breaks=[x for x in v['views']['strokes']['nodes'] if x.get('direct_break')])
  result.append(row)
  if t.minute==0:print(tf,t.isoformat(),len(b['strokes']),b['strokes'][-1].end.price,flush=True)
  t+=timedelta(minutes=step)
 dump(P/f'replay_{tf}.json',[r for r in result if r['period']==tf])
files=['chan.py','chan_structure.py','chan_theory.py','chan_types.py','chan_dynamics.py','chan_signals.py','chan_policy.py','report_btc_4h_auto.py','futures_multiperiod_template.html']
dump(P/'code_manifest.json',{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files})
