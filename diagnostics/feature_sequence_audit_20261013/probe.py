"""Read-only feature-rule audit; alternate comparator runs in an isolated module."""
import json,sys,types,hashlib,random
from pathlib import Path
from dataclasses import asdict
from datetime import timedelta,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import chan_structure as production
from chan import Fractal,Stroke,parse_time,json_default
from test_chan import strokes
OUT=Path(__file__).resolve().parent
src=(ROOT/'chan_structure.py').read_text()
needle='direct = middle.low < left.low if direction == "up" else middle.high > left.high'
assert src.count(needle)==1
trial=types.ModuleType('_feature_boundary_audit');sys.modules[trial.__name__]=trial
exec(compile(src.replace(needle,'direct = middle.low <= left.low if direction == "up" else middle.high >= left.high'),str(ROOT/'chan_structure.py'),'exec'),trial.__dict__)


def signature(result):
 return [(s.first_stroke,s.last_stroke,s.evidence_stroke,s.confirmation) for s in result.segments]


def point(p):return Fractal(**{**p,'time':parse_time(p['time'])})
def pens(block):
 return [Stroke(point(s['start']),point(s['end']),s['provisional'],parse_time(s['confirmed_at']) if s['confirmed_at'] else None) for s in block['strokes']]

result=dict(production_sha256=hashlib.sha256(src.encode()).hexdigest(),boundary_cases=[],periods={})
for sign in (1,-1):
 for endpoint in (3.99,4,4.01):
  prices=[sign*x for x in [0,8,4,10,endpoint,9,3]]
  seq=strokes(prices);before=production.build_segments(seq);after=trial.build_segments(seq)
  result['boundary_cases'].append(dict(prices=prices,production=signature(before),inclusive_trial=signature(after)))
  assert signature(after)==[(0,2,5,'no_gap')]
  assert bool(before.segments)==(endpoint!=4)
  # The two proposed spans independently satisfy three-pen overlap and extrema.
  for part in (seq[:3],seq[3:]):
   assert len(part)==3
   assert max(s.low for s in part)<=min(s.high for s in part)
   assert part[-1].end.price==(max(s.high for s in part) if part[0].direction=='up' else min(s.low for s in part))
for period in ('8h','4h','2h','1h','15m'):
 r=json.loads((ROOT/f'reports/BTCUSDT_20260606_20261013_five_periods/analysis_{period}.json').read_text())
 seq=pens(r['blocks'][0]);a=production.build_segments(seq);b=trial.build_segments(seq)
 changes=[dict(old=asdict(x),new=asdict(y)) for x,y in zip(a.segments,b.segments) if asdict(x)!=asdict(y)]
 result['periods'][period]=dict(production_segments=len(a.segments),trial_segments=len(b.segments),
                              same_geometry_and_evidence=[asdict(s) for s in a.segments]==[asdict(s) for s in b.segments],
                              production_direct_marks=len(a.direct_breaks),trial_direct_marks=len(b.direct_breaks),
                              first_differences=changes[:3])
 print(period,result['periods'][period]['same_geometry_and_evidence'],len(a.segments),len(b.segments),flush=True)
 if period=='1h':
  lo=parse_time('2026-08-14T22:00:00+08:00');hi=parse_time('2026-09-16T02:00:00+08:00')
  result['user_window_segments']=[asdict(s) for s in a.segments if lo<=s.start.time<hi]
  result['user_window_trial_segments']=[asdict(s) for s in b.segments if lo<=s.start.time<hi]
  assert result['user_window_segments']==result['user_window_trial_segments']
  result['user_window_turns']=[]
  for segment in a.segments:
   if lo<=segment.start.time<hi:
    audit={};ev=production._turn_evidence(seq,segment.first_stroke,segment.last_stroke+1,audit)
    result['user_window_turns'].append(dict(start=segment.start.time,end=segment.end.time,audit=audit,evidence=ev))
(OUT/'probe_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=json_default)+'\n')
print('PASS: exact-touch counterexample and five-period comparator impact written')
