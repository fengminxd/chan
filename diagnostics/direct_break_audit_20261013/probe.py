"""Audit the first reverse stroke against lesson 65/71; never edit production."""
import hashlib
import itertools
import json
import sys
import types
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import chan_structure as production
from chan import Fractal, Stroke, json_default, parse_time
from test_chan import strokes

OUT = Path(__file__).resolve().parent
source = (ROOT/'chan_structure.py').read_text()
needle = 'direct = middle.low <= left.low if direction == "up" else middle.high >= left.high'
assert source.count(needle) == 1
trial = types.ModuleType('_first_pen_break_audit')
sys.modules[trial.__name__] = trial
exec(compile(source.replace(needle, 'direct = middle.low <= left.high if direction == "up" else middle.high >= left.low'),
             '<isolated threshold trial>', 'exec'), trial.__dict__)

def inspect(mod, seq):
    audit = {}
    ev = mod._turn_evidence(seq, 0, 3, audit)
    return dict(evidence=ev, audit=audit,
                segments=[asdict(s) for s in mod.build_segments(seq).segments])

result = dict(production_sha256=hashlib.sha256(source.encode()).hexdigest(),
              cases=[], exhaustive={}, periods={})
cases = [[0,8,4,10,6,9,7,8,6], [0,8,4,14,6,13,9,12,8,11,5],
         [0,8,4,10,6,9,5], [0,8,4,10,4,9,3], [0,8,4,10,6,9,7,11,5]]
for p in cases:
    for sign in (1,-1):
        seq = strokes([sign*v for v in p])
        result['cases'].append(dict(prices=[sign*v for v in p], production=inspect(production,seq),
                                    trial=inspect(trial,seq)))

# Independent interval-resolution oracle: old up segment 0->8->4->14;
# first reverse endpoint r <= 8 already satisfies d3 <= g1 (j=3, i=1).
# Thereafter only first exit of [r,14] resolves the candidate. Touching waits.
total = direct_misses = early = late = trial_errors = 0
examples = []
for r in (2,4,6,8):
    for tail in itertools.product(range(0,17,2), repeat=4):
        p = [0,8,4,14,r,*tail]
        if any((b-a)*(1 if i%2==0 else -1)<=0 for i,(a,b) in enumerate(zip(p,p[1:]))):
            continue
        expected = None
        for point_index in range(5,len(p)):
            if p[point_index]>14:
                break
            if p[point_index]<r:
                expected=point_index-1
                break
        for sign in (1,-1):
            seq=strokes([sign*v for v in p]);total+=1
            a={};old=production._turn_evidence(seq,0,3,a)
            new=trial._turn_evidence(seq,0,3)
            old_index=old[0] if old else None
            new_index=new[0] if new else None
            direct_misses+=not a['direct_break']
            trial_errors+=new_index!=expected
            if old_index!=expected:
                if old_index is not None and (expected is None or old_index<expected):early+=1
                else:late+=1
                if len(examples)<8:
                    examples.append(dict(prices=[sign*v for v in p],expected=expected,actual=old_index))
result['exhaustive']=dict(cases=total,direct_flag_misses=direct_misses,
                         premature_confirmations=early,missed_or_late_confirmations=late,
                         isolated_trial_errors=trial_errors,examples=examples)
assert total>1000 and early>0 and late>0 and trial_errors==0
print('exhaustive',result['exhaustive'],flush=True)

def point(p):return Fractal(**{**p,'time':parse_time(p['time'])})
lo=parse_time('2026-08-14T22:00:00+08:00');hi=parse_time('2026-09-16T02:00:00+08:00')
for period in ('8h','4h','2h','1h','15m'):
    report=json.loads((ROOT/f'reports/BTCUSDT_20260606_20261013_five_periods/analysis_{period}.json').read_text())
    block=report['blocks'][0]
    seq=[Stroke(point(s['start']),point(s['end']),s['provisional'],parse_time(s['confirmed_at']) if s['confirmed_at'] else None)
         for s in block['strokes']]
    before,after=production.build_segments(seq),trial.build_segments(seq)
    window=lambda rr:[asdict(s) for s in rr.segments if lo<=s.start.time<hi]
    def edges(rr):return [(s.first_stroke,s.last_stroke,s.evidence_stroke,s.confirmation) for s in rr.segments]
    old_set=set(edges(before));new_set=set(edges(after))
    removed=[asdict(s) for s in before.segments if (s.first_stroke,s.last_stroke,s.evidence_stroke,s.confirmation) not in new_set]
    added=[asdict(s) for s in after.segments if (s.first_stroke,s.last_stroke,s.evidence_stroke,s.confirmation) not in old_set]
    result['periods'][period]=dict(production_count=len(before.segments),trial_count=len(after.segments),
        old_marks=len(before.direct_breaks),new_marks=len(after.direct_breaks),
        changed_old_segments=removed,changed_new_segments=added,
        current_user_window=window(before),trial_user_window=window(after))
    if period=='1h':
        result['user_window_same_geometry_and_evidence']=window(before)==window(after)
        result['user_window_audits']=[]
        for s in before.segments:
            if lo<=s.start.time<hi:
                a={};b={}
                production._turn_evidence(seq,s.first_stroke,s.last_stroke+1,a)
                trial._turn_evidence(seq,s.first_stroke,s.last_stroke+1,b)
                result['user_window_audits'].append(dict(segment=asdict(s),old=a,trial=b))
    print(period,len(before.segments),len(after.segments),'marks',len(before.direct_breaks),len(after.direct_breaks),flush=True)
assert (ROOT/'chan_structure.py').read_text()==source
(OUT/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=json_default)+'\n')
print('PASS: audit written; production algorithm untouched.',flush=True)
