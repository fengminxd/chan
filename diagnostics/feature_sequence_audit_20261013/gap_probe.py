"""Check that published gap labels agree with their standardized features."""
import json,sys,types
from pathlib import Path
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import chan_structure as prod
from chan import Fractal,Stroke,parse_time,json_default
from test_chan import strokes
OUT=Path(__file__).resolve().parent
src=(ROOT/'chan_structure.py').read_text()
needle='            _append_feature(primary, _feature(strokes, i), direction)'
replacement=needle+'''\n            if not direct:
                gap = primary[0].low > left.high if direction == "up" else primary[0].high < left.low'''
trial=types.ModuleType('_standard_gap_audit');sys.modules[trial.__name__]=trial
exec(compile(src.replace(needle,replacement),str(ROOT/'chan_structure.py'),'exec'),trial.__dict__)

def inspect(seq,seg):
 start,turn=seg.first_stroke,seg.last_stroke+1
 d=seg.direction
 left=prod.standardize_features([prod._feature(seq,i) for i in range(start+1,turn,2)],d)[-1]
 primary=prod.standardize_features([prod._feature(seq,i) for i in range(turn,seg.local_evidence_stroke+1,2)],d)
 mid=primary[0]
 standardized_gap=mid.low>left.high if d=='up' else mid.high<left.low
 audit={};prod._turn_evidence(seq,start,turn,audit)
 return dict(segment=asdict(seg),left=asdict(left),standardized_middle=asdict(mid),
             standardized_gap=standardized_gap,audit=audit)

result=dict(synthetic_cases=[],periods={})
for sign in (1,-1):
 p=[sign*x for x in [0,8,4,14,6,13,9,12,8,11,5]]
 seq=strokes(p);r=prod.build_segments(seq);q=trial.build_segments(seq)
 assert len(r.segments)==1 and r.segments[0].confirmation=='no_gap'
 issue=inspect(seq,r.segments[0]);assert issue['standardized_gap'] and not issue['audit']['reverse_pattern_found']
 assert q.segments==[]
 result['synthetic_cases'].append(dict(prices=p,issue=issue,trial_segments=[asdict(s) for s in q.segments]))
for period in ['8h','4h','2h','1h','15m']:
 r=json.loads((ROOT/f'reports/BTCUSDT_20260606_20261013_five_periods/analysis_{period}.json').read_text())
 f=lambda p:Fractal(**{**p,'time':parse_time(p['time'])})
 seq=[Stroke(f(s['start']),f(s['end']),s['provisional'],parse_time(s['confirmed_at']) if s['confirmed_at'] else None) for s in r['blocks'][0]['strokes']]
 current=prod.build_segments(seq);altered=trial.build_segments(seq)
 all_issues=[inspect(seq,s) for s in current.segments if s.confirmation=='no_gap']
 all_issues=[i for i in all_issues if i['standardized_gap'] and i['audit']['reason']!='confirmed_direct_break']
 visible=[i for i in all_issues if i['segment']['end']['time']>=parse_time('2026-06-06T00:00:00+08:00')]
 lo=parse_time('2026-08-14T22:00:00+08:00');hi=parse_time('2026-09-16T02:00:00+08:00')
 window=lambda rr:[asdict(s) for s in rr.segments if lo<=s.start.time<hi]
 result['periods'][period]=dict(total_misclassified_no_gap=len(all_issues),visible_misclassified_no_gap=len(visible),
  examples=visible[:3],production_segment_count=len(current.segments),trial_segment_count=len(altered.segments),
  current_user_window=window(current),trial_user_window=window(altered))
 print(period,'misclassified',len(all_issues),'visible',len(visible),'segments',len(current.segments),len(altered.segments),flush=True)
(OUT/'gap_results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=json_default)+'\n')
print('PASS: standardized-gap mismatch reproduced without modifying production')
