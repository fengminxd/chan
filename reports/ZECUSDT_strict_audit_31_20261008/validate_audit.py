"""Check coverage, causal evidence, report links, and unchanged chart geometry."""
from pathlib import Path
import hashlib
import json
import re
import sys

P = Path(__file__).resolve().parent
ROOT = P.parents[1]
SOURCE = P.parent / 'ZECUSDT_nested_1h_fixed_20261008'
sys.path.insert(0, str(ROOT))
from chan import parse_time
from chan_time import PERIODS, period_delta


groups = json.loads((P/'audit_groups.json').read_text())
manifest = json.loads((P/'audit_manifest.json').read_text())
original = json.loads((SOURCE/'entries_grouped.json').read_text())
triggers = {t['id']: t for t in json.loads((SOURCE/'triggers.json').read_text())}
seen = set()
for number, (g, row) in enumerate(zip(groups, original), 1):
    assert g['number'] == number and g['original'] == row
    assert g['first']['plan_id'] == row['first_plan_id']
    assert g['first'] in g['records']
    for r in g['records']:
        assert r['plan_id'] not in seen
        seen.add(r['plan_id'])
        t = triggers[r['plan_id']]
        now, focus = parse_time(r['as_of']), parse_time(r['target_time'])
        assert r['as_of'] == t['triggered_at']
        assert r['trigger_close'] == t['trigger_observed_close']
        assert all(r['checks'].values())
        for period, proof in r['frame_proofs'].items():
            assert parse_time(proof['last_closed_at']) <= now
            width = period_delta(PERIODS[period])
            for item in proof['matches']:
                s = item['signal']
                assert parse_time(s['evidence_at']) <= now
                assert s['price'] == r['target_price']
                assert parse_time(s['time']) <= focus < parse_time(s['time'])+width
                assert s['label'] in proof['allowed_execution_labels']
                assert s['certainty'] in ('policy_confirmed','policy_dependent')
            for m in proof['completed_structures_at_target']:
                assert m['status'] == 'completed' and parse_time(m['evidence_at']) <= now
        reachable = {x['period'] for x in r['audited_route']}
        expected = [dict(period=p, **m) for p in ('15m','5m','1m') if p in reachable
                    for m in r['frame_proofs'][p]['matches']]
        assert r['execution_matches'] == expected
        assert r['execution_confirmed'] == bool(expected)
assert len(groups) == len(original) == 31 and seen == triggers.keys()
for name, digest in manifest['core_hashes'].items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest
data = json.loads((P/'chart_data.json').read_text())
old_data = json.loads((SOURCE/'chart_data.json').read_text())
assert data['frames'] == old_data['frames'] and data['strokes'] == old_data['strokes']
for e, old, g in zip(data['entries'], old_data['entries'], groups):
    assert {k:v for k,v in e.items() if k != 'audit'} == old
    assert e['audit']['confirmed'] == g['first_execution_confirmed']
    assert (P/e['audit']['details_file']).is_file()
for target in re.findall(r'\]\(([^)]+)\)', (P/'result.md').read_text()):
    assert (P/target).is_file(), target
result = dict(passed=True, groups=len(groups), trigger_records=len(seen),
              causal_closed_bars=True, exact_target_matching=True,
              classification_matches_evidence=True, original_prices_unchanged=True,
              candle_and_stroke_geometry_unchanged=True, core_hashes_unchanged=True,
              all_report_and_evidence_links_exist=True)
(P/'audit_validation.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result))
