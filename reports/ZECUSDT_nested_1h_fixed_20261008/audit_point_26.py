"""Rebuild entry #26 from closed historical bars; do not change strategy/results."""
from pathlib import Path
from datetime import timedelta
from dataclasses import replace
import json
import sys

P = Path(__file__).resolve().parent
sys.path.insert(0, str(P.parents[1]))
from chan import (read_csv, resample, parse_time, analyze, json_default,
                  merge_inclusions, find_fractals, build_strokes)
from chan_time import PERIODS, period_delta
import entry_plan as ep

row = json.loads((P / 'entries_grouped.json').read_text())[25]
record = next(x for x in json.loads((P / 'triggers.json').read_text())
              if x['id'] == row['first_plan_id'])
now = parse_time(record['armed_at'])
trigger_time = parse_time(record['triggered_at'])
all_minutes = read_csv(P / 'history_1m.csv')
minutes = [c for c in all_minutes if c.time + ep.MINUTE <= now]
frames = {}
evidence = {}
for period in ep.CHAIN:
    width = period_delta(PERIODS[period])
    bars, _ = resample(minutes, PERIODS['1m'], PERIODS[period], now)
    merged = merge_inclusions(bars)
    fractals = find_fractals(merged[:-1])
    strokes = build_strokes(fractals, merged)
    strokes = [replace(s, confirmed_at=merged[strokes[i+1].end.index+2].end+width)
               if not s.provisional else s for i, s in enumerate(strokes)]
    if period == '1h':
        block = {**analyze(bars, PERIODS[period])['blocks'][-1], 'period': period}
        assert block['strokes'] == strokes
        legs = ep.observation_legs(block, minutes, now)
    else:
        offset = max(0, len(strokes)-8)
        legs = ep.observation_legs(dict(period=period, strokes=strokes[offset:]), minutes, now)
        for leg in legs:
            leg['index'] += offset
            leg['native_structure']['index'] += offset
    reverse = ep.developing_reverse(legs, minutes, now, period)
    combined = legs + ([reverse] if reverse else [])
    frames[period] = dict(bars=bars, legs=combined)
    evidence[period] = dict(last_strokes=strokes[-5:], last_legs=combined[-5:])
    if period == '1m':
        bottom = next(f for f in fractals if f.time == parse_time(record['anchor_time'])
                      and f.kind == 'bottom' and f.price == record['anchor_price'])
        evidence[period]['fractal_and_stabilizer_bars'] = merged[bottom.index-1:bottom.index+3]
    print('rebuilt', period, len(bars), 'bars', flush=True)

candidates = ep.period_candidates(block, frames['1h']['legs'][:-1]
                                 if frames['1h']['legs'][-1].get('live_reverse')
                                 else frames['1h']['legs'], now, '1h')
candidate = next(x for x in candidates if x['id'] == record['candidate_id'])
assert candidate['candidate_price'] == record['candidate_price']
located = ep.locate(candidate, frames, now, ep.EntryPolicy(profile='nested_1h'))
assert json.loads(json.dumps(located['path'], default=json_default)) == record['path']
assert json.loads(json.dumps(located['setup'], default=json_default)) == record['setup']
assert ep.setup_status(located['setup'], candidate, minutes) == 'armed'
plan = {**record, 'state': 'armed', 'evaluated_at': record['armed_at']}
triggered = ep.advance_plan(plan, all_minutes, trigger_time, ep.EntryPolicy(profile='nested_1h'))
assert triggered['state'] == 'triggered'
assert triggered['trigger_observed_close'] == 1381.19
stop = next(c for c in all_minutes if c.time >= trigger_time and c.low <= record['stop_price'])
origin_break = next(c for c in all_minutes if c.time >= trigger_time
                    and c.low < record['parent_invalidation']['price'])
before_stop = [c for c in all_minutes if trigger_time <= c.time < stop.time]
audit = dict(group=26, old_group_ids=row['old_group_ids'], entry=row,
             as_of=now, candidate=candidate, structure_evidence=evidence,
             reproduced_location=located, reproduced_trigger=triggered,
             trigger_minutes=[c for c in all_minutes if now <= c.time < trigger_time],
             first_stop_touch_minute=stop, first_origin_break_minute=origin_break,
             max_high_before_stop=max(c.high for c in before_stop),
             checks=dict(reference_structure_path_matches=True, reference_setup_matches=True,
                         armed_before_cross=True, subsequent_close_trigger_matches=True),
             conclusion='Valid v4 candidate execution; neither a confirmed strict 1h B2 nor a proven 1m B1/B2. Stable bottom fractal only.')
(P / 'audit_26.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=json_default))
print(json.dumps({k:audit[k] for k in ('checks','first_stop_touch_minute',
                 'first_origin_break_minute','max_high_before_stop')}, default=json_default, ensure_ascii=False))
