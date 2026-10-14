"""Reproducible v8 audit: OHLC -> strokes -> turn evidence and historical cutoffs."""
import csv
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chan import (Fractal, Stroke, find_fractals, json_default, merge_inclusions,
                  parse_time, read_csv)
from chan_strokes import connected_stroke_runs
from chan_structure import (build_segments, standardize_features, _feature,
                            _valid_span, _turn_evidence)

OUT = Path(__file__).resolve().parent
REPORT = ROOT/'reports/BTCUSDT_20260606_20261013_five_periods'
TZ = timezone(timedelta(hours=8))
def local(t):
    return (parse_time(t) if isinstance(t, str) else t).astimezone(TZ).strftime('%Y-%m-%d %H:%M')
def point(p):
    return Fractal(**{**p, 'time': parse_time(p['time'])})
def read(name):
    return json.loads((REPORT/name).read_text())

report = read('analysis_1h.json')
block = report['blocks'][0]
assert report['rule_manifest']['version'] == 'chan-rules-v8'
manifest = read('code_manifest.json')
for name, digest in manifest['sha256'].items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest
seq = [Stroke(point(s['start']), point(s['end']), s['provisional'],
              parse_time(s['confirmed_at']) if s['confirmed_at'] else None) for s in block['strokes']]
raw = read_csv(REPORT/'history_1h.csv')
assert hashlib.sha256((REPORT/'history_1h.csv').read_bytes()).hexdigest() == read('source.json')['history_sha256']
merged = merge_inclusions(raw)
raw_at = {c.time: c for c in raw}
lo, hi = parse_time('2026-08-14T22:00:00+08:00'), parse_time('2026-09-16T02:00:00+08:00')
full = build_segments(seq)
targets = [s for s in full.segments if lo <= s.start.time < hi]
assert [(s.first_stroke,s.last_stroke) for s in targets] == [(1586,1594),(1595,1599),(1600,1602),(1603,1613)]
nodes = read('chart_data.json')['frames']['1h']['views']['strokes']['nodes']
chart_targets = [n for n in nodes if n['kind']=='segment' and lo.timestamp()<=n['start']<hi.timestamp()]
assert len(chart_targets)==4
for s,n in zip(targets,chart_targets):
    assert (s.start.time.timestamp(),s.end.time.timestamp(),s.start.price,s.end.price)==(n['start'],n['end'],n['start_price'],n['end_price'])

def span(start,stop):
    p=seq[start:stop]
    return dict(first_stroke=start,last_stroke=stop-1,count=len(p),valid=_valid_span(seq,start,stop),
                first_three_overlap=[max(s.low for s in p[:3]),min(s.high for s in p[:3])],
                range_low=min(s.low for s in p),range_high=max(s.high for s in p))

def candidate(start,turn):
    audit={}
    ev=_turn_evidence(seq,start,turn,audit)
    direction=seq[start].direction
    left=standardize_features([_feature(seq,i) for i in range(start+1,turn,2)],direction)
    end=ev[0] if ev else audit.get('evidence_stroke',len(seq)-1)
    return dict(start=start,turn=turn,turn_time=local(seq[turn].start.time),span=span(start,turn),
                audit=audit,evidence=ev,left_standard=left,
                primary=standardize_features([_feature(seq,i) for i in range(turn,end+1,2)],direction),
                secondary=standardize_features([_feature(seq,i) for i in range(turn+1,end+1,2)],
                                               'up' if direction=='down' else 'down'),
                input_evidence_at=local(max(s.confirmed_at or s.end.time for s in seq[:end+1])))

result=dict(source=read('source.json')['as_of'],rule_manifest=report['rule_manifest'],
            source_sha256=read('source.json')['history_sha256'],
            algorithm_sha256=manifest['sha256']['chan_structure.py'],segments=targets,
            turns=[candidate(s.first_stroke,s.last_stroke+1) for s in targets],
            rejected_local_turns=[candidate(1595,1598),candidate(1603,1606),candidate(1603,1608)],
            alternative=[candidate(1586,1609),candidate(1609,1614)],
            first_origin_candidates=[],strokes=[],raw_replays=[])
for turn in range(1589,1610,2):
    c=span(1586,turn)
    if c['valid']:
        c=candidate(1586,turn)
    result['first_origin_candidates'].append(c)

# Independent numeric inequalities for the four actual boundaries.
assert 81270.5>max(79555.5,79200) and 77808.1>max(76649,77600)
assert max(76649,77808.1)<=min(79555.5,81270.5)
assert 76853.1<min(77600,77933.4) and 78314.9<min(79200,79384.4)
assert max(77600,76853.1)<=min(79200,78314.9)
assert 76947.2<77933.4 and 76368<76947.2
assert 74909.4<min(79125.3,75025) and 76275.4<min(80536.1,81933.9)
assert 76275.4<79125.3  # actual standard-feature gap
assert 87385.1>max(81933.9,87247.3) and 85080>max(80095.9,83450.1)

marks={e['turn_stroke'] for e in full.direct_breaks}
for i in range(1586,1622):
    s=seq[i];part=merged[s.start.index:s.end.index+1]
    assert s.end.index-s.start.index>=4
    assert min(s.start.price,s.end.price)==min(c.low for c in part)
    assert max(s.start.price,s.end.price)==max(c.high for c in part)
    for p in (s.start,s.end):
        assert p.price==getattr(raw_at[p.time],'high' if p.kind=='top' else 'low')
    if i>1586:assert seq[i-1].end==s.start
    result['strokes'].append(dict(index=i,start=local(s.start.time),end=local(s.end.time),
        start_price=s.start.price,end_price=s.end.price,confirmed_at=local(s.confirmed_at),
        merged_candle_count=len(part),direct_break=i in marks))
with (OUT/'strokes.csv').open('w',newline='',encoding='utf-8-sig') as f:
    writer=csv.DictWriter(f,fieldnames=list(result['strokes'][0]));writer.writeheader();writer.writerows(result['strokes'])

# Recompute from ONLY closed OHLC, not filtered final strokes. Use all warm-up history.
cutoffs=sorted({s.evidence_at+offset for s in targets for offset in (timedelta(hours=-1),timedelta())}
               | {hi,parse_time('2026-09-16T23:00:00+08:00'),parse_time('2026-09-04T13:00:00+08:00')})
for at in cutoffs:
    visible=[c for c in raw if c.time+timedelta(hours=1)<=at]
    m=merge_inclusions(visible)
    strokes=connected_stroke_runs(find_fractals(m[:-1]),m,4,timedelta(hours=1))[0]['strokes']
    built=build_segments(strokes)
    found=[s for s in built.segments if lo<=s.start.time<hi]
    expected=[s for s in targets if s.evidence_at<=at]
    assert [(s.start,s.end) for s in found]==[(s.start,s.end) for s in expected],local(at)
    assert all(s.evidence_at<=at for s in found)
    result['raw_replays'].append(dict(asof=local(at),closed_candles=len(visible),
        confirmed_segments=found,pending_segment=built.pending,
        last_strokes=strokes[-5:]))
    print(local(at),'confirmed target segments',len(found),flush=True)

(OUT/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=json_default)+'\n')
print('PASS: 4 boundaries, OHLC pen geometry, alternatives and 11 independent raw cutoffs.',flush=True)
