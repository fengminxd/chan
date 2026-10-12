"""Verify display-only augmentation against the pre-fix frozen BTC snapshot."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
FOLDER=ROOT/'reports/BTCUSDT_20260606_current_tradingview'


def digest(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


old=json.loads((HERE/'before.json').read_text())
new=json.loads((FOLDER/'chart_data.json').read_text())
checks={}
for period,f in new['frames'].items():
    v=f['views']['strokes']
    assert digest(f['views']['types'])==old[period]['types_sha256']
    assert digest(v['nodes'])==old[period]['nodes_sha256']
    assert digest(v['centers'])==old[period]['centers_sha256']
    added=[s for s in v['signals'] if s['certainty']=='pending_stroke_candidate']
    assert [s for s in v['signals'] if s['certainty']!='pending_stroke_candidate']==old[period]['signals']
    for s in added:
        assert s['label'] in ('B2','S2','B3','S3')
        assert s['time']<s['evidence_at']<=new['asof']
        assert not s['actionable'] and not s['higher_point_confirmed']
        assert not s['details']['stroke_completed']
        if s['center_id']:assert s['center_id'] in v['centers']
    checks[period]=dict(added=added,existing_signals_unchanged=True,
                       completed_type_view_unchanged=True,centers_unchanged=True,
                       lines_and_direct_break_marks_unchanged=True)

target_time=int(datetime.fromisoformat('2026-10-12T14:00:00+08:00').timestamp())
target=[s for s in checks['1h']['added'] if s['time']==target_time and s['price']==84340.2]
assert {s['label'] for s in target}=={'S2','S3'}
for s in target:
    assert s['evidence_at']==int(datetime.fromisoformat('2026-10-12T18:00:00+08:00').timestamp())
sell3=next(s for s in target if s['label']=='S3')
assert (sell3['details']['zd'],sell3['details']['zg'])==(85072.7,86034.0)
result=dict(passed=True,checks=checks,target_time_beijing='2026-10-12 14:00',target_price=84340.2)
(HERE/'snapshot_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(dict(passed=True,added={k:[s['label'] for s in v['added']] for k,v in checks.items()})))
