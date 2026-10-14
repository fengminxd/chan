"""Combined isolated trial; production source and published reports stay unchanged."""
import hashlib
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
source = (ROOT / 'chan_structure.py').read_text()
direct = 'direct = middle.low < left.low if direction == "up" else middle.high > left.high'
append = '            _append_feature(primary, _feature(strokes, i), direction)'
assert source.count(direct) == source.count(append) == 1
modified = source.replace(direct, direct.replace(' < ', ' <= ').replace(' > ', ' >= '))
modified = modified.replace(append, append + '''
            if not direct:
                gap = primary[0].low > left.high if direction == "up" else primary[0].high < left.low''')
trial = types.ModuleType('_combined_feature_audit')
sys.modules[trial.__name__] = trial
exec(compile(modified, '<isolated feature audit>', 'exec'), trial.__dict__)

for sign in (1, -1):
    boundary = trial.build_segments(strokes([sign * p for p in [0, 8, 4, 10, 4, 9, 3]]))
    assert [(s.start.price, s.end.price) for s in boundary.segments] == [(0, sign * 10)]
    gap = trial.build_segments(strokes([sign * p for p in [0, 8, 4, 14, 6, 13, 9, 12, 8, 11, 5]]))
    assert gap.segments == []
    # Touching the first reverse stroke's endpoint is not the third stroke
    # strictly breaking it. Preserve that separate waiting rule.
    touch = trial.build_segments(strokes([sign * p for p in [0, 8, 4, 10, 2, 7, 2]]))
    assert touch.segments == []

record = json.loads((ROOT / 'reports/BTCUSDT_20260606_20261013_five_periods/analysis_1h.json').read_text())
def point(p):
    return Fractal(**{**p, 'time': parse_time(p['time'])})
seq = [Stroke(point(s['start']), point(s['end']), s['provisional'],
              parse_time(s['confirmed_at']) if s['confirmed_at'] else None)
       for s in record['blocks'][0]['strokes']]
before, after = production.build_segments(seq), trial.build_segments(seq)
lo = parse_time('2026-08-14T22:00:00+08:00')
hi = parse_time('2026-09-16T02:00:00+08:00')
def window(result):
    return [asdict(s) for s in result.segments if lo <= s.start.time < hi]
assert window(before) == window(after)
assert len(window(before)) == 4
assert (ROOT / 'chan_structure.py').read_text() == source
result = dict(production_sha256=hashlib.sha256(source.encode()).hexdigest(),
              synthetic_regressions_passed=4, third_stroke_touch_checks_passed=2,
              production_segment_count=len(before.segments),
              combined_trial_segment_count=len(after.segments),
              user_window_unchanged=True, user_window_segments=window(after))
(OUT / 'combined_results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=json_default) + '\n')
print('PASS: combined trial resolves four counterexamples; target 1h window remains four identical segments.')
