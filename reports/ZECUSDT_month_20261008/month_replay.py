"""逐主周期收盘复盘；历史端点与首次可观察时间分开保存。"""
import csv
from dataclasses import asdict
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys
import time

P = Path(__file__).resolve().parent
ROOT = P.parents[1]
sys.path.insert(0, str(ROOT))
from chan import analyze, read_csv, resample, parse_time, json_default
from chan_time import PERIODS
from chan_theory import macd_area
from report_btc_4h_auto import automatic_views
from entry_plan import observation_legs, developing_reverse, period_candidates


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_default, allow_nan=False))


def local_strength(block, start):
    strokes = [s for s in block['strokes'] if not s.provisional]
    points = block['theory']['macd']['points']
    result = []
    for a, _, c in zip(strokes, strokes[1:], strokes[2:]):
        if c.end.time < start:
            continue
        up = c.direction == 'up'
        if a.direction != c.direction or not (c.end.price > a.end.price if up else c.end.price < a.end.price):
            continue
        aa, ca = [macd_area(points, s.start.time, s.end.time, s.direction) for s in (a, c)]
        peaks = []
        for s in (a, c):
            seq = [p for p in points if s.start.time <= p.time <= s.end.time]
            peaks.append(max((max(0, p.dif if up else -p.dif) for p in seq), default=0))
        area_weaker = aa['area'] > 0 and ca['area'] < aa['area']
        peak_weaker = peaks[0] > 0 and peaks[1] < peaks[0]
        if not (area_weaker or peak_weaker):
            continue
        result.append(dict(label='TOP_DIV' if up else 'BOTTOM_DIV', level=0,
            time=c.end.time, price=c.end.price, evidence_at=max(a.confirmed_at, c.confirmed_at),
            certainty='local_indicator_divergence_not_strict_first_point',
            a_start=a.start.time, a_end=a.end.time, a_price=a.end.price,
            c_start=c.start.time, a_area=aa['area'], c_area=ca['area'],
            a_dif_peak=peaks[0], c_dif_peak=peaks[1],
            area_weaker=area_weaker, peak_weaker=peak_weaker))
    return result


def run(period):
    source = json.loads((P/'source.json').read_text())
    cutoff = parse_time(source['as_of'])
    start = cutoff - timedelta(days=30)
    minutes = read_csv(P/'history_1m.csv')
    if hashlib.sha256((P/'history_1m.csv').read_bytes()).hexdigest() != source['history_sha256']:
        raise ValueError('行情源文件已改变')
    hours = PERIODS[period]
    width = timedelta(hours=hours)
    bars, quality = resample(minutes, PERIODS['1m'], hours, cutoff)
    with (P/f'history_{period}.csv').open('w', newline='') as f:
        w = csv.writer(f); w.writerow(['timestamp','open','high','low','close','volume'])
        w.writerows([b.time.isoformat(), b.open, b.high, b.low, b.close, b.volume] for b in bars)
    first = next(i for i, b in enumerate(bars) if b.time + width > start)
    records, prev, snapshots = {}, {}, 0
    began = time.monotonic()
    with (P/f'replay_{period}.jsonl').open('w') as events_file:
        for stop in range(first, len(bars)+1):
            if not stop:
                continue
            now = bars[stop-1].time + width
            current_bars = bars[:stop]
            analysis = analyze(current_bars, hours)
            view = automatic_views(analysis, current_bars, start, now, period_hours=hours)
            current = {}
            for basis in ['strokes', 'types']:
                for s in view['views'][basis]['signals']:
                    key = '|'.join(map(str, (basis, s['label'], s['level'], s['time'].isoformat(), s['price'])))
                    current[key] = {**s, 'view': basis}
            for s in local_strength(analysis['blocks'][-1], start):
                key = '|'.join(map(str, ('local_strength', s['label'], s['time'].isoformat(), s['price'])))
                current[key] = {**s, 'view': 'local_strength'}
            changes = []
            for key, s in current.items():
                if s['evidence_at'] > now:
                    raise ValueError('回放引用未来证据')
                if key not in records:
                    records[key] = dict(key=key, period=period, first_observed_at=now,
                        first_observed_close=current_bars[-1].close, first_signal=s,
                        preexisting_at_window_start=now <= start, retractions=[], revisions=[])
                if key not in prev or prev[key] != s:
                    records[key]['revisions'].append(dict(observed_at=now, signal=s))
                    changes.append(dict(action='added' if key not in prev else 'updated', key=key, signal=s))
                records[key].update(last_observed_at=now, latest_signal=s)
            for key in prev.keys() - current.keys():
                records[key]['retractions'].append(now)
                changes.append(dict(action='retracted', key=key))
            events_file.write(json.dumps(dict(as_of=now, closed_bar_count=stop, events=changes),
                                        ensure_ascii=False, default=json_default)+'\n')
            prev = current
            snapshots += 1
            if snapshots % 40 == 0 or stop == len(bars):
                print(period, snapshots, '/', len(bars)-first+1, now.isoformat(),
                      f'{time.monotonic()-began:.1f}s', flush=True)
    for key, value in records.items():
        value['present_at_end'] = key in prev
    dump(P/f'analysis_{period}.json', analysis)
    dump(P/f'views_{period}.json', view)
    block = {**analysis['blocks'][-1], 'period': period}
    legs = observation_legs(block, minutes, cutoff)
    reverse = developing_reverse(legs, minutes, cutoff, period)
    if reverse:
        legs.append(reverse)
    candidates = period_candidates(block, legs, cutoff, period)
    if reverse:
        candidates += period_candidates(block, legs[:-1], cutoff, period)
    dump(P/f'current_candidates_{period}.json', candidates)
    dump(P/f'points_{period}.json', dict(period=period, start=start, as_of=cutoff,
        last_closed_main_bar_at=bars[-1].time+width, snapshots=snapshots,
        sampling='each main-period closed bar; no minute execution replay',
        warmup_start=minutes[0].time, data_quality=quality,
        records=list(records.values()), current_candidates=candidates,
        rule_manifest=analysis['rule_manifest']))
    print('COMPLETE', period, 'points', len(records), flush=True)


if __name__ == '__main__':
    run(sys.argv[1])
