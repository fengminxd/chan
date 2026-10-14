"""Verify rebuilt v9 reports, break resolution, publication prefixes and styling flags."""
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from chan import Fractal,Stroke,parse_time,json_default,read_csv,merge_inclusions,find_fractals
from chan_strokes import connected_stroke_runs
from chan_structure import Feature,build_segments,standardize_features

OUT=Path(__file__).resolve().parent
OLD=ROOT/'reports/BTCUSDT_20260606_20261013_five_periods_before_v9'
if not OLD.exists():OLD=ROOT/'reports/BTCUSDT_20260606_20261013_five_periods'
NEW=ROOT/'reports/BTCUSDT_20260606_20261013_break_v9'
def read(folder,name):return json.loads((folder/name).read_text())
def point(p):return Fractal(**{**p,'time':parse_time(p['time'])})
def stroke(s):return Stroke(point(s['start']),point(s['end']),s['provisional'],parse_time(s['confirmed_at']) if s['confirmed_at'] else None)
def chain(nodes):
    for a,b in zip(nodes,nodes[1:]):
        assert (a['end'],a['end_price'])==(b['start'],b['start_price'])
        assert (a['end_price']>a['start_price'])!=(b['end_price']>b['start_price'])
    pending=[i for i,n in enumerate(nodes) if n['state']=='pending']
    assert not pending or pending==[len(nodes)-1]

for name in ('source.json','history_1h.csv','history_15m.csv','partials.json'):
    assert (NEW/name).read_bytes()==(OLD/name).read_bytes(),name
manifest=read(NEW,'code_manifest.json')
assert manifest['rule_manifest']['version']=='chan-rules-v9'
for name,digest in manifest['sha256'].items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
data=read(NEW,'chart_data.json')
assert json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>',(NEW/'btc_tradingview.html').read_text(),re.S).group(1))==data
result=dict(passed=True,periods={},raw_prefixes=[])
for period in ('8h','4h','2h','1h','15m'):
    before=read(OLD,f'analysis_{period}.json')['blocks'][0]
    after=read(NEW,f'analysis_{period}.json')['blocks'][0]
    for key in ('strokes','stroke_diagnostics'):
        assert before[key]==after[key],(period,key)
    assert before['theory']['macd']==after['theory']['macd']
    seq=[stroke(s) for s in after['strokes']]
    features=[Feature(s.low,s.high,(i,)) for i,s in enumerate(seq)]
    counts=Counter()
    for seg in after['segments']:
        start,turn=seg['first_stroke'],seg['last_stroke']+1
        evidence=seg['local_evidence_stroke'];up=seq[start].direction=='up'
        left=standardize_features(features[start+1:turn:2],seq[start].direction)[-1]
        original=seq[turn]
        direct=original.low<=left.high if up else original.high>=left.low
        if direct:
            assert seg['confirmation']=='no_gap'
            assert evidence>=turn+2
            # Independently find the first strict departure below/above the
            # attack endpoint. A mere touch must never confirm this branch.
            endings=[i for i in range(turn+2,evidence+1,2)
                     if (seq[i].end.price<original.end.price if up else seq[i].end.price>original.end.price)]
            assert endings and endings[0]==evidence
            assert all((s.high<=original.start.price if up else s.low>=original.start.price)
                       for s in seq[turn:evidence+1])
            counts['first_break_confirmations']+=1
        else:
            assert seg['confirmation']=='gap_reverse_fractal'
            assert len(seg['feature_indices'])==len(seg['reverse_feature_indices'])==3
            counts['dual_sequence_confirmations']+=1
        assert seg['evidence_stroke']>=evidence
        assert parse_time(seg['evidence_at'])<=parse_time(data['source']['as_of'])
    for event in after['direct_breaks']:
        start,turn=event['segment_start_stroke'],event['turn_stroke']
        left=standardize_features(features[start+1:turn:2],seq[start].direction)[-1]
        assert (seq[turn].low<=left.high if seq[start].direction=='up' else seq[turn].high>=left.low)
    marks={e['turn_stroke'] for e in after['direct_breaks']}
    frame=data['frames'][period]
    assert not frame['continuity_breaks']
    for basis in ('strokes','types'):
        nodes=frame['views'][basis]['nodes']
        for kind in ('stroke','segment'):chain([n for n in nodes if n['kind']==kind])
        for n in nodes:
            assert bool(n.get('direct_break'))==(n['kind']=='stroke' and int(n['id'].split(':')[-1]) in marks)
    full=build_segments(seq)
    old_edges={(s['first_stroke'],s['last_stroke'],s['evidence_stroke'],s['confirmation']) for s in before['segments']}
    changed=[s for s in full.segments if (s.first_stroke,s.last_stroke,s.evidence_stroke,s.confirmation) not in old_edges]
    for seg in changed:
        for stop in (seg.evidence_stroke,seg.evidence_stroke+1):
            assert build_segments(seq[:stop]).segments==[s for s in full.segments if s.evidence_stroke<stop]
    if period=='1h':
        lo,hi=map(parse_time,('2026-08-14T22:00:00+08:00','2026-09-16T02:00:00+08:00'))
        window=lambda b:[s for s in b['segments'] if lo<=parse_time(s['start']['time'])<hi]
        assert window(before)==window(after) and len(window(after))==4
        assert {1595,1600,1603}<=marks
        assert 1614 not in marks
        result['user_window']=window(after)
    result['periods'][period]=dict(old_segments=len(before['segments']),new_segments=len(after['segments']),
        old_marks=len(before['direct_breaks']),new_marks=len(marks),
        displayed_segment_states=dict(Counter(n['state'] for n in frame['views']['strokes']['nodes'] if n['kind']=='segment')),
        displayed_marks=sum(bool(n.get('direct_break')) for n in frame['views']['strokes']['nodes']),
        confirmations=dict(counts),changed_confirmation_prefix_checks=len(changed)*2,
        strokes_unchanged=True,macd_unchanged=True,discontinuities=0)
    print(period,result['periods'][period],flush=True)

# Replay actual closed candles at/just before the three restored 15m turns.
raw=read_csv(NEW/'history_15m.csv')
for turn,at in [('2026-06-06T03:15:00+08:00','2026-06-07T18:45:00+08:00'),
                ('2026-06-12T03:30:00+08:00','2026-06-12T17:15:00+08:00'),
                ('2026-06-25T21:45:00+08:00','2026-06-28T02:15:00+08:00')]:
    for offset in (-15,0):
        cutoff=parse_time(at)+timedelta(minutes=offset)
        m=merge_inclusions([c for c in raw if c.time+timedelta(minutes=15)<=cutoff])
        pens=connected_stroke_runs(find_fractals(m[:-1]),m,4,timedelta(minutes=15))[0]['strokes']
        built=build_segments(pens)
        found=[s for s in built.segments if s.end.time==parse_time(turn)]
        assert bool(found)==(offset==0),(turn,cutoff)
        assert all(s.evidence_at<=cutoff for s in found)
        result['raw_prefixes'].append(dict(asof=cutoff,turn=turn,confirmed=bool(found)))
        print('raw 15m',cutoff,'turn confirmed',bool(found),flush=True)
(OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=json_default)+'\n')
print('PASS: source hashes, all break resolutions, changed prefixes, raw cutoffs and chart chains.')
