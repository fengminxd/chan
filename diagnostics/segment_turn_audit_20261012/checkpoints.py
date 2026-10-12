"""只使用截面前已收盘 K 线，复核用户指定时点附近的段和卖点候选。"""
from datetime import timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chan import read_csv, analyze, parse_time, json_default
from report_btc_4h_auto import automatic_views
from replay import LifecycleTracker


def validate(rows):
    segments = [r['last_confirmed_segment'] for r in rows]
    geometry = lambda s: (s['start'], s['end'], s['first_stroke'], s['last_stroke'])
    assert all(geometry(s) == geometry(segments[0]) for s in segments)
    assert [parse_time(s['evidence_at']) for s in segments] == [
        parse_time('2026-10-12T06:00:00+08:00'), parse_time('2026-10-12T06:00:00+08:00'),
        parse_time('2026-10-12T18:00:00+08:00')]
    assert not rows[0]['target_signals'] and not rows[1]['target_signals']
    assert {s['label'] for s in rows[2]['target_signals']} == {'S2', 'S3'}
    assert all(s['certainty'] == 'pending_stroke_candidate' and not s['actionable']
               for s in rows[2]['target_signals'])
    tracker = LifecycleTracker()
    events = []
    for row in rows:
        seg = dict(row['last_confirmed_segment'])
        seg['evidence_at'] = parse_time(seg['evidence_at'])
        events.extend(tracker.update({'sampled_segment': dict(type='segment', value=seg)},
                                     parse_time(row['cutoff_beijing'])))
    # 三截面的首次观察只能定位到 14:00，不冒充逐根回放的首次确认时刻。
    first_observed = parse_time(rows[0]['cutoff_beijing'])
    assert all(e['lifecycle']['first_confirmed_observed_at'] == first_observed for e in events)
    result = dict(passed=True, same_segment_geometry=True, snapshot_evidence_time_changes=True,
                  sampled_first_confirmed_observed_at=first_observed,
                  sampled_first_observation_is_not_historical_first=True, lifecycle_events=events)
    Path(__file__).with_name('checkpoint_validation.json').write_text(
        json.dumps(result, default=json_default, ensure_ascii=False, indent=2) + '\n')


def main():
    raw = read_csv(ROOT / 'reports/BTCUSDT_20260606_current_tradingview/history_1h.csv')
    start = parse_time('2026-06-06T00:00:00+08:00')
    target = parse_time('2026-10-12T14:00:00+08:00')
    rows = []
    for t in ('2026-10-12T14:00:00+08:00', '2026-10-12T17:00:00+08:00',
              '2026-10-12T18:00:00+08:00'):
        cutoff = parse_time(t)
        bars = [b for b in raw if b.time + timedelta(hours=1) <= cutoff]
        report = analyze(bars, 1)
        block = report['blocks'][-1]
        views = automatic_views(report, bars, start, cutoff, None, period_hours=1)
        targets = [s for s in views['views']['strokes']['signals'] if s['time'] == target]
        assert all(s['evidence_at'] <= cutoff for s in targets)
        rows.append(dict(cutoff_beijing=t, closed_bars=len(bars),
                         last_stroke=block['strokes'][-1], last_confirmed_segment=block['segments'][-1],
                         pending_segment=block['pending_segment'], target_signals=targets))
        print(t, 'segment', block['segments'][-1].end.price,
              'target', [(s['label'], s['certainty']) for s in targets], flush=True)
    encoded = json.dumps(rows, default=json_default, ensure_ascii=False, indent=2)
    Path(__file__).with_name('kline_checkpoints.json').write_text(encoded + '\n')
    validate(json.loads(encoded))


if __name__ == '__main__':
    if '--verify-saved' in sys.argv:
        validate(json.loads(Path(__file__).with_name('kline_checkpoints.json').read_text()))
        print('Saved checkpoints and lifecycle validation: PASS')
    else:
        main()
