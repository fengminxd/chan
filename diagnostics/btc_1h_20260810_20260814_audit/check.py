"""Audit the frozen chart snapshot without changing production rules or annotations."""
import hashlib
import json
import sys
from dataclasses import asdict, replace
from datetime import timedelta, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from chan import (read_csv, parse_time, merge_inclusions, find_fractals,
                  build_strokes, json_default)
from chan_structure import build_segments
FOLDER=ROOT/'reports/BTCUSDT_20260606_20261013_five_periods'
OUT=Path(__file__).resolve().parent
report=json.loads((FOLDER/'analysis_1h.json').read_text())
block=report['blocks'][0]
data=json.loads((FOLDER/'chart_data.json').read_text())
raw=read_csv(FOLDER/'history_1h.csv')
merged=merge_inclusions(raw)
start=parse_time('2026-08-10T06:00:00+08:00')
end=parse_time('2026-08-14T22:00:00+08:00')
segments=[s for s in block['segments'] if start<=parse_time(s['start']['time'])<end]
assert len(segments)==3
nodes=[n for n in data['frames']['1h']['views']['strokes']['nodes']
       if n['kind']=='segment' and start.timestamp()<=n['start']<end.timestamp()]
assert len(nodes)==3
for s,n in zip(segments,nodes):
    assert n['start']==parse_time(s['start']['time']).timestamp()
    assert n['end']==parse_time(s['end']['time']).timestamp()
    assert (n['start_price'],n['end_price'])==(s['start']['price'],s['end']['price'])
checks=[]
for i in range(1577,1586):
    s=block['strokes'][i];a=s['start']['index'];b=s['end']['index']
    assert b-a>=4
    for point in (s['start'],s['end']):
        left,mid,right=merged[point['index']-1:point['index']+2]
        compare=(lambda x,y:x>y) if point['kind']=='top' else (lambda x,y:x<y)
        assert all(compare(getattr(mid,k),getattr(left,k)) and compare(getattr(mid,k),getattr(right,k)) for k in ('low','high'))
    assert min(s['start']['price'],s['end']['price'])==min(c.low for c in merged[a:b+1])
    assert max(s['start']['price'],s['end']['price'])==max(c.high for c in merged[a:b+1])
    checks.append(dict(index=i,**s,merged_bars=b-a+1,independent_bars_between_fractals=b-a-3))
def feature(i):
    s=block['strokes'][i]
    return min(s['start']['price'],s['end']['price']),max(s['start']['price'],s['end']['price'])
features=[feature(i) for i in (1578,1580,1582)]
a,b,c=features
assert b[0]<min(a[0],c[0]) and b[1]<min(a[1],c[1])
assert max(a[0],b[0])<=min(a[1],b[1])
assert all(not ((x[0]<=y[0] and x[1]>=y[1]) or (y[0]<=x[0] and y[1]>=x[1])) for x,y in zip(features,features[1:]))
# Second turn: first down stroke breaks the preceding down feature;
# the third down stroke breaks the first down stroke's endpoint.
assert feature(1583)[0]<feature(1581)[0]
assert feature(1585)[0]<feature(1583)[0]
# Independently check three-pen overlap and the directional endpoint.
for first in (1577,1580,1583):
    fs=[feature(i) for i in range(first,first+3)]
    assert max(f[0] for f in fs)<=min(f[1] for f in fs)
    prices=[block['strokes'][i]['end']['price'] for i in range(first,first+3)]
    assert prices[-1]==(min(prices) if first!=1580 else max(prices))
result=dict(passed=True,source_sha256={name:hashlib.sha256((FOLDER/name).read_bytes()).hexdigest()
    for name in ('analysis_1h.json','history_1h.csv','chart_data.json')},
    chart_matches_analysis=True,segments=segments,strokes=checks,
    first_turn=dict(feature_indices=[1578,1580,1582],features=features,overlap=[63788.0,63886.0],
                    pairwise_containment=False,bottom_fractal=True,direct_break=False),
    second_turn=dict(direct_break_stroke=1583,breaks_previous_low=[63283.0,63574.6],
                     third_stroke=1585,breaks_first_low=[62484.2,63283.0]),
    checkpoints=[])
# Rebuild from genuinely truncated hourly input, not the current frozen pen list.
for cutoff in ('2026-08-13T07:00:00+08:00','2026-08-14T23:00:00+08:00'):
    stop=parse_time(cutoff)
    prefix=[c for c in raw if c.time+timedelta(hours=1)<=stop]
    mb=merge_inclusions(prefix)
    pens=build_strokes(find_fractals(mb[:-1]),mb)
    pens=[replace(s,confirmed_at=mb[pens[i+1].end.index+2].start+timedelta(hours=1))
          if not s.provisional else s for i,s in enumerate(pens)]
    structure=build_segments(pens)
    selected=[s for s in structure.segments if start<=s.start.time<end]
    checkpoint=dict(asof=cutoff,segments=selected,pending=structure.pending,
                    strokes=[s for s in pens if start<=s.start.time<end])
    result['checkpoints'].append(checkpoint)
    print(cutoff, 'confirmed segments in window',len(selected),'pending',structure.pending,flush=True)
(OUT/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=json_default)+'\n')
print('PASS: raw-candle pen checks, feature proof, chart parity and two historical prefixes')
