"""Audit archived HTML against continuity requirements, not its own render counts.

Exit 1 means the reviewed v6 report fails acceptance; evidence.json is still saved.
No market data is fetched and neither archived report is modified.
"""
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import asdict
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chan import Fractal, Stroke, json_default, parse_time, read_csv
from chan_structure import build_segments
from chan_theory import macd

OUT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def endpoint(node, side):
    return node[side], node[side + '_price']


def discontinuities(nodes):
    return [dict(previous=a, following=b, hours=(b['start']-a['end'])/3600)
            for a, b in zip(nodes, nodes[1:])
            if endpoint(a, 'end') != endpoint(b, 'start')]


def load_stroke(s):
    def point(p):
        return Fractal(**{**p, 'time': parse_time(p['time'])})
    return Stroke(point(s['start']), point(s['end']), s['provisional'],
                  parse_time(s['confirmed_at']) if s['confirmed_at'] else None)


def inspect(folder):
    html_path = folder / 'btc_tradingview.html'
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>',
                               html_path.read_text(), re.S).group(1))
    assert data == json.loads((folder / 'chart_data.json').read_text())
    result = dict(html_sha256=digest(html_path), embedded_equals_chart_data=True, periods={})
    for period, frame in data['frames'].items():
        report = json.loads((folder / f'analysis_{period}.json').read_text())
        nodes = frame['views']['strokes']['nodes']
        pens = [n for n in nodes if n['kind'] == 'stroke']
        segments = [n for n in nodes if n['kind'] == 'segment']
        assert [(n['start'], n['end']) for n in pens] == sorted(
            (n['start'], n['end']) for n in pens)
        source_gaps = [dict(before=a['time'], after=b['time'])
                       for a, b in zip(frame['bars'], frame['bars'][1:])
                       if b['time'] - a['time'] != frame['width']]
        orphan_segments = []
        for bi, block in enumerate(report['blocks']):
            pending = block['pending_segment']
            closed = block.get('stroke_diagnostics', {}).get('closed_at')
            if closed and pending and parse_time(pending['end']['time']).timestamp() >= data['start']:
                orphan_segments.append(dict(block=bi, closed_at=closed, pending=pending,
                                            diagnostics=block['segment_diagnostics']))
        result['periods'][period] = dict(
            source_gap_count=report['gap_count'], displayed_candle_gaps=source_gaps,
            stroke_count=len(pens), stroke_discontinuities=discontinuities(pens),
            segment_states=dict(Counter(n['state'] for n in segments)),
            segment_discontinuities=discontinuities(segments),
            closed_block_pending_segments=orphan_segments,
            structure_break_count_including_pre_chart_context=report.get('structure_break_count', 0))
    return result


def main():
    old = ROOT / 'reports/BTCUSDT_20260606_20261013_five_periods'
    new = ROOT / 'reports/BTCUSDT_20260606_20261013_causal_v6'
    # The original user-facing paths are updated after the v7 repair. Continue
    # auditing the frozen pre-repair artifacts rather than overwriting history.
    old = old.with_name(old.name+'_before_v7') if old.with_name(old.name+'_before_v7').exists() else old
    new = new.with_name(new.name+'_before_v7') if new.with_name(new.name+'_before_v7').exists() else new
    result = dict(acceptance='continuous source requires connected strokes and segments; '
                             'closed structural blocks must not leave live pending segments',
                  reports={'old': inspect(old), 'v6': inspect(new)})
    result['same_source_data'] = {
        name: digest(old/name) == digest(new/name)
        for name in ('history_1h.csv', 'history_15m.csv')}
    raw = read_csv(new/'history_1h.csv')
    assert all(b.time-a.time == timedelta(hours=1) for a, b in zip(raw, raw[1:]))
    report = json.loads((new/'analysis_1h.json').read_text())
    full_macd = {p.time: p for p in macd(raw)}
    begin = parse_time('2026-09-07T07:00:00+08:00')
    end = parse_time('2026-09-16T02:00:00+08:00')
    local_blocks = [b for b in report['blocks']
                    if parse_time(b['end']) >= begin and parse_time(b['start']) <= end]
    flat = [load_stroke(s) for b in local_blocks for s in b['strokes']]
    try:
        build_segments(flat)
    except ValueError as exc:
        result['combined_local_strokes_rejected_by_segment_engine'] = str(exc)
    else:
        result['combined_local_strokes_rejected_by_segment_engine'] = None
    result['macd_reinitialization_examples'] = []
    for block in local_blocks:
        if begin <= parse_time(block['start']) <= end:
            point = block['theory']['macd']['points'][0]
            result['macd_reinitialization_examples'].append(dict(
                beijing_time=parse_time(point['time']).astimezone(TZ).isoformat(),
                actual=point, continuous_source_expected=asdict(full_macd[parse_time(point['time'])])))
    data = json.loads((new/'chart_data.json').read_text())
    nodes = data['frames']['1h']['views']['strokes']['nodes']
    result['local_strokes'] = [n for n in nodes if n['kind'] == 'stroke'
                               and begin.timestamp() <= n['start'] < end.timestamp()]
    result['local_segments'] = [n for n in nodes if n['kind'] == 'segment'
                                and n['end'] >= begin.timestamp() and n['start'] <= end.timestamp()]
    violations = []
    for period, stats in result['reports']['v6']['periods'].items():
        if stats['source_gap_count'] == 0 and stats['stroke_discontinuities']:
            violations.append(f"{period}: {len(stats['stroke_discontinuities'])} broken stroke links")
        if stats['closed_block_pending_segments']:
            violations.append(f"{period}: {len(stats['closed_block_pending_segments'])} pending segments in closed blocks")
    result.update(accepted=not violations, violations=violations,
                  source_hashes={name: digest(ROOT/name) for name in (
                      'chan_strokes.py', 'chan.py', 'chan_structure.py', 'report_btc_4h_auto.py',
                      'btc_multiperiod_tradingview_template.html', 'test_causal_strokes.py')})
    (OUT/'evidence.json').write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                               default=json_default)+'\n')
    for v in violations:
        print(v)
    print('ACCEPTED' if result['accepted'] else 'REJECTED: v6 continuity requirements failed')
    return 0 if result['accepted'] else 1


if __name__ == '__main__':
    sys.exit(main())
