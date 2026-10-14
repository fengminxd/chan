"""Validate rebuilt reports against frozen pre-fix reports and feature geometry."""
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chan import Fractal, Stroke, json_default, parse_time
from chan_structure import Feature, build_segments, standardize_features

OUT = Path(__file__).resolve().parent
OLD = ROOT / 'reports/BTCUSDT_20260606_20261013_five_periods_before_v8'
if not OLD.exists():
    OLD = ROOT / 'reports/BTCUSDT_20260606_20261013_five_periods'
NEW = ROOT / 'reports/BTCUSDT_20260606_20261013_features_v8'

def read(folder, name):
    return json.loads((folder / name).read_text())

def stroke(s):
    def point(p):
        return Fractal(**{**p, 'time': parse_time(p['time'])})
    return Stroke(point(s['start']), point(s['end']), s['provisional'],
                  parse_time(s['confirmed_at']) if s['confirmed_at'] else None)

def chain(nodes):
    for a, b in zip(nodes, nodes[1:]):
        assert (a['end'], a['end_price']) == (b['start'], b['start_price'])
        assert (a['end_price'] > a['start_price']) != (b['end_price'] > b['start_price'])
    pending = [i for i, n in enumerate(nodes) if n['state'] == 'pending']
    assert not pending or pending == [len(nodes)-1]

result = dict(passed=True, periods={}, affected_15m_turns=[], user_window=[])
for name in ('source.json', 'history_1h.csv', 'history_15m.csv', 'partials.json'):
    assert (NEW/name).read_bytes() == (OLD/name).read_bytes(), name
manifest = read(NEW, 'code_manifest.json')
assert manifest['rule_manifest']['version'] == 'chan-rules-v8'
for name, digest in manifest['sha256'].items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == digest, name
html = (NEW/'btc_tradingview.html').read_text()
data = read(NEW, 'chart_data.json')
assert json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)) == data

for period in ('8h', '4h', '2h', '1h', '15m'):
    old = read(OLD, f'analysis_{period}.json')
    new = read(NEW, f'analysis_{period}.json')
    assert len(new['blocks']) == len(old['blocks']) == 1
    assert new['gap_count'] == new['structure_break_count'] == 0
    before, after = old['blocks'][0], new['blocks'][0]
    assert before['strokes'] == after['strokes']
    assert before['stroke_diagnostics'] == after['stroke_diagnostics']
    assert before['theory']['macd'] == after['theory']['macd']
    seq = [stroke(s) for s in after['strokes']]
    features = [Feature(s.low, s.high, (i,)) for i, s in enumerate(seq)]
    normalized_checks = 0
    for segment in after['segments']:
        start, turn = segment['first_stroke'], segment['last_stroke']+1
        direction = seq[start].direction
        evidence = segment['local_evidence_stroke']
        left = standardize_features(features[start+1:turn:2], direction)[-1]
        original = features[turn]
        direct = original.low <= left.low if direction == 'up' else original.high >= left.high
        if not direct:
            primary = standardize_features(features[turn:evidence+1:2], direction)
            middle = primary[0]
            gap = middle.low > left.high if direction == 'up' else middle.high < left.low
            assert segment['confirmation'] == ('gap_reverse_fractal' if gap else 'no_gap')
            if gap:
                assert len(segment['reverse_feature_indices']) == 3
            normalized_checks += 1
        assert segment['evidence_stroke'] >= evidence
        assert parse_time(segment['evidence_at']) <= parse_time(data['source']['as_of'])
    frame = data['frames'][period]
    assert not frame['continuity_breaks']
    marks = {e['turn_stroke'] for e in after['direct_breaks'] if e['direct_break']}
    for basis in ('strokes', 'types'):
        nodes = frame['views'][basis]['nodes']
        for kind in ('stroke', 'segment'):
            chain([n for n in nodes if n['kind'] == kind])
        for n in nodes:
            if n['kind'] == 'stroke':
                assert bool(n.get('direct_break')) == (int(n['id'].split(':')[-1]) in marks)
            else:
                assert not n.get('direct_break')
    result['periods'][period] = dict(
        old_full_segments=len(before['segments']), new_full_segments=len(after['segments']),
        displayed_segment_states=dict(Counter(n['state'] for n in frame['views']['strokes']['nodes'] if n['kind']=='segment')),
        standardized_gap_checks=normalized_checks, strokes_unchanged=True, macd_unchanged=True,
        stroke_discontinuities=0, segment_discontinuities=0,
        old_direct_marks=len(before['direct_breaks']), new_direct_marks=len(after['direct_breaks']))
    if period == '1h':
        lo, hi = parse_time('2026-08-14T22:00:00+08:00'), parse_time('2026-09-16T02:00:00+08:00')
        window = lambda b: [s for s in b['segments'] if lo <= parse_time(s['start']['time']) < hi]
        assert window(before) == window(after)
        assert len(window(after)) == 4
        result['user_window'] = window(after)
    if period == '15m':
        full = build_segments(seq)
        for turn in (635, 666, 743):
            previous = next(s for s in before['segments'] if s['last_stroke']+1 == turn)
            stop = previous['local_evidence_stroke']+1
            prefix = build_segments(seq[:stop])
            # The old premature confirmation must be absent at its old observation.
            assert not any(s.last_stroke+1 == turn for s in prefix.segments)
            assert prefix.segments == [s for s in full.segments if s.evidence_stroke < stop]
            revised = next((s for s in after['segments'] if s['last_stroke']+1 == turn), None)
            result['affected_15m_turns'].append(dict(turn_stroke=turn, old_segment=previous, new_segment=revised,
                                                   old_confirmation_removed_at_old_prefix=True))
        # Check publication immediately before/at new confirmations near the affected range.
        for segment in full.segments:
            if 625 <= segment.last_stroke <= 755:
                for stop in (segment.evidence_stroke, segment.evidence_stroke+1):
                    assert build_segments(seq[:stop]).segments == [s for s in full.segments if s.evidence_stroke < stop]
    print(period, result['periods'][period], flush=True)
(OUT/'validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, default=json_default)+'\n')
print('PASS: rules, fixed inputs, full-history gaps, real prefixes, chart continuity and marks.')
