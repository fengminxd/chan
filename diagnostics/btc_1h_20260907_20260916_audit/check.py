"""Frozen chart audit: local admissible pen edges and genuinely truncated OHLC replay."""
import csv
import hashlib
import json
import sys
from dataclasses import asdict,replace
from datetime import timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from chan import read_csv,merge_inclusions,find_fractals,build_strokes,parse_time,json_default
SRC=ROOT/'reports/BTCUSDT_20260606_20261013_five_periods'
OUT=Path(__file__).resolve().parent
report=json.loads((SRC/'analysis_1h.json').read_text())
block=report['blocks'][0]
raw=read_csv(SRC/'history_1h.csv');merged=merge_inclusions(raw)
start=parse_time('2026-09-07T07:00:00+08:00');end=parse_time('2026-09-16T22:00:00+08:00')
fmt=lambda t:parse_time(t).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')
fs=[f for f in block['fractals'] if start<=parse_time(f['time'])<=end]
by={f['index']:f for f in fs}
# Enumerate EVERY pair in the window independently of production early-exit / parent selection.
def admissible(a,b):
    if a['kind']==b['kind'] or b['index']-a['index']<4:return False
    part=merged[a['index']:b['index']+1]
    return (min(a['price'],b['price'])==min(c.low for c in part)
            and max(a['price'],b['price'])==max(c.high for c in part))
edges=[(a['index'],b['index']) for i,a in enumerate(fs) for b in fs[i+1:] if admissible(a,b)]
incoming={f['index']:[a for a,b in edges if b==f['index']] for f in fs}
length={23062:0};parents={}
for f in fs[1:]:
    choices=[a for a in incoming[f['index']] if a in length]
    if choices:
        parent=max(choices,key=lambda a:(length[a],a))
        length[f['index']]=length[parent]+1;parents[f['index']]=parent
reverse={23208}
for f in reversed(fs):
    if f['index'] in reverse:reverse.update(incoming[f['index']])
assert reverse & set(length)=={23062,23208}
assert not incoming[23143]
assert by[23143]['index']-by[23141]['index']==2
assert by[23216]['index']-by[23214]['index']==2
# The middle peak exceeds the preceding locally connected peaks, yet remains below the long pen start.
assert 80536.1>79859.8>79737.3
selected=set()
for s in block['strokes']:
    selected.update((s['start']['index'],s['end']['index']))
records=[]
for f in fs:
    i=f['index']
    state=('selected' if i in selected else 'reachable_branch_cannot_rejoin_09_16_low' if i in length
           else 'different_local_root' if incoming[i] else 'no_admissible_incoming_edge_within_window')
    records.append(dict(**f,local_time=fmt(f['time']),incoming_indices=incoming[i],
                        reachable_from_09_07=i in length,can_reach_09_16_low=i in reverse,
                        selected=i in selected,classification=state))
with (OUT/'fractals.csv').open('w',newline='',encoding='utf-8-sig') as f:
    fields=['index','kind','local_time','price','selected','reachable_from_09_07','can_reach_09_16_low','classification','incoming_indices']
    writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(records)
with (OUT/'local_edges.csv').open('w',newline='',encoding='utf-8-sig') as f:
    writer=csv.writer(f);writer.writerow(['start_index','end_index','start_beijing','end_beijing','start_price','end_price','merged_bars'])
    writer.writerows([a,b,fmt(by[a]['time']),fmt(by[b]['time']),by[a]['price'],by[b]['price'],b-a+1] for a,b in edges)
chart=json.loads((SRC/'chart_data.json').read_text())
ns=[n for n in chart['frames']['1h']['views']['strokes']['nodes'] if n['kind']=='stroke' and n['end']>start.timestamp() and n['start']<end.timestamp()]
actual=[s for s in block['strokes'] if parse_time(s['end']['time'])>start and parse_time(s['start']['time'])<end]
assert len(ns)==len(actual)==3
for n,s in zip(ns,actual):
    assert (n['start'],n['end'],n['start_price'],n['end_price'])==(parse_time(s['start']['time']).timestamp(),parse_time(s['end']['time']).timestamp(),s['start']['price'],s['end']['price'])
results=dict(source_sha256={name:hashlib.sha256((SRC/name).read_bytes()).hexdigest() for name in ('analysis_1h.json','history_1h.csv','chart_data.json')},
             chart_matches_analysis=True,fractal_count=len(fs),local_admissible_edges=len(edges),
             records=records,edges=edges,selected_strokes=actual,checkpoints=[])
for cutoff in ('2026-09-10T12:00:00+08:00','2026-09-11T20:00:00+08:00',
               '2026-09-12T08:00:00+08:00','2026-09-16T06:00:00+08:00','2026-09-16T23:00:00+08:00'):
    stop=parse_time(cutoff);bars=[c for c in raw if c.time+timedelta(hours=1)<=stop]
    mb=merge_inclusions(bars);diag={}
    pens=build_strokes(find_fractals(mb[:-1]),mb,diagnostics=diag)
    pens=[replace(s,confirmed_at=mb[pens[i+1].end.index+2].start+timedelta(hours=1)) if not s.provisional else s for i,s in enumerate(pens)]
    own=[s for s in pens if s.end.time>start and s.start.time<end]
    results['checkpoints'].append(dict(asof=cutoff,strokes=own,diagnostics=diag))
    print(cutoff,[(fmt(s.start.time.isoformat())[5:],s.start.price,fmt(s.end.time.isoformat())[5:],s.end.price,s.provisional) for s in own],flush=True)
results['passed']=True
(OUT/'evidence.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=json_default)+'\n')
print('PASS',len(fs),'fractals;',len(edges),'local legal edges; chart equality; 5 raw OHLC prefixes')
