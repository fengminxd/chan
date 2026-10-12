"""Recheck every published point on its claimed evidence-time candle prefix."""
from pathlib import Path
import sys,json,time
from datetime import timedelta
from collections import defaultdict,Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from chan import read_csv,analyze,parse_time
from report_btc_4h_auto import automatic_views,dump
P=Path(__file__).resolve().parent
p=json.loads((P/'chart_data.json').read_text());data=p['histories']['recent']['periods']['1h']
bars=read_csv(P/'history_1h.csv');start=parse_time(p['start'])
groups=defaultdict(list)
for basis,view in data['views'].items():
    for cat in ('signals','divergences'):
        for s in view[cat]:groups[s['evidence_at']].append((basis,cat,s))
def key(s,cat):
    return (s.get('label') if cat=='signals' else (s['kind'],s['side']),str(s['time']),s['price'],s['level'])
results=[]
for i,(t,items) in enumerate(sorted(groups.items())):
    end=parse_time(t);prefix=[b for b in bars if b.time+timedelta(hours=1)<=end]
    report=analyze(prefix,1)
    v=json.loads(json.dumps(automatic_views(report,prefix,start,end,period_hours=1),default=lambda x:x.isoformat()))
    for basis,cat,s in items:
        matches=[x for x in v['views'][basis][cat] if key(x,cat)==key(s,cat)]
        results.append(dict(id=s['id'],basis=basis,category=cat,evidence_at=t,present_at_claimed_evidence=bool(matches),
            matched_certainty=[x['certainty'] for x in matches],prefix_closed_bars=len(prefix),
            scope='Evidence-time prefix check only; not first-observation replay or order execution'))
    print(i+1,len(groups),t,flush=True)
dump(P/'point_time_audit.json',dict(asof=p['asof'],results=results,counts=dict(Counter(str(r['present_at_claimed_evidence']) for r in results))))
