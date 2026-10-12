"""Freeze public BTC futures candles and audit independent 1h/2h/4h structures."""
import argparse
import csv
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from chan import Candle, analyze, json_default, parse_time, read_csv, validate_candles
from chan_structure import Movement
from chan_theory import divergence_candidates, evolving_centers, first_second_signals, second_retest_positions

UTC = timezone.utc
API = 'https://fapi.binance.com/fapi/v1/'


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_default))


def fetch(folder):
    pages = folder / 'raw_pages'
    pages.mkdir(parents=True, exist_ok=True)
    requests = []

    def get(name, params=None):
        url = API + name + ('?' + urlencode(params) if params else '')
        with urlopen(url, timeout=30) as response:
            raw = response.read()
            date = response.headers.get('Date')
        filename = hashlib.sha256(url.encode()).hexdigest()[:20] + '.json'
        (pages / filename).write_bytes(raw)
        requests.append(dict(url=url, file='raw_pages/'+filename,
                             sha256=hashlib.sha256(raw).hexdigest(), server_date=date,
                             downloaded_at=datetime.now(UTC)))
        return json.loads(raw)

    server = get('time')
    cutoff = min(server['serverTime'], int(datetime.now(UTC).timestamp()*1000))
    start = int(parse_time('2023-01-01T00:00:00+08:00').timestamp()*1000)
    for hours in (1, 2, 4):
        step = hours*3600000
        end = cutoff//step*step
        cursor, rows = start, []
        while cursor < end:
            stop = min(end, cursor+1000*step)
            part = get('klines', dict(symbol='BTCUSDT', interval=f'{hours}h',
                                     startTime=cursor, endTime=stop-1, limit=1000))
            if not isinstance(part, list) or len(part) != (stop-cursor)//step:
                raise ValueError(f'{hours}h incomplete response')
            if any(len(r) != 12 or r[0] != cursor+i*step or r[6] != r[0]+step-1 for i, r in enumerate(part)):
                raise ValueError(f'{hours}h invalid timestamps')
            rows.extend(part)
            cursor = stop
        bars = [Candle(datetime.fromtimestamp(r[0]/1000, UTC), *map(float, r[1:6])) for r in rows]
        validate_candles(bars)
        with (folder/f'history_{hours}h.csv').open('w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp','open','high','low','close','volume'])
            writer.writerows([c.time.isoformat(), c.open,c.high,c.low,c.close,c.volume] for c in bars)
        print(f'{hours}h: {len(bars)} closed bars, last open {bars[-1].time}', flush=True)
    ticker = get('ticker/24hr', dict(symbol='BTCUSDT'))
    dump(folder/'source.json', dict(symbol='BTCUSDT', market='Binance USD-M perpetual',
         analysis_cutoff=datetime.fromtimestamp(cutoff/1000, UTC), ticker=ticker,
         ticker_time=datetime.fromtimestamp(ticker['closeTime']/1000, UTC),
         context_start=datetime.fromtimestamp(start/1000, UTC), requests=requests,
         candle_sha256={f'{h}h': hashlib.sha256((folder/f'history_{h}h.csv').read_bytes()).hexdigest()
                        for h in (1,2,4)}))


def layer_audit(children, view, points, bars, hours, strict_signals=()):
    divs = divergence_candidates(children, view, points)
    signals = [asdict(s) for s in [*view['signals'], *first_second_signals(children, divs), *strict_signals]]
    retests = []
    for p in second_retest_positions(children):
        raw = [c for c in bars if p['origin_time'] <= c.time < p['time']+timedelta(hours=hours)]
        buy = p['label'].startswith('B')
        extreme = min(c.low for c in raw) if buy else max(c.high for c in raw)
        retests.append({**p, 'raw_path_extreme': extreme,
                        'raw_path_holds_origin': extreme >= p['origin_price'] if buy else extreme <= p['origin_price']})
    return dict(level=view['level'], centers=view['centers'], signals=signals,
                divergences=divs, geometric_retests=retests)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', type=Path, default=Path('reports/BTCUSDT_current_1h_2h_4h_20260926'))
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    folder = args.output_dir
    folder.mkdir(parents=True, exist_ok=True)
    if args.refresh:
        fetch(folder)
    source = json.loads((folder/'source.json').read_text())
    cutoff = parse_time(source['analysis_cutoff'])
    summary = dict(source=source, periods={},
                   code_sha256={p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in
                                ['chan.py','chan_structure.py','chan_theory.py','chan_types.py','chan_dynamics.py',__file__]})
    for hours in (1,2,4):
        bars = read_csv(folder/f'history_{hours}h.csv')
        assert bars[-1].time+timedelta(hours=hours) <= cutoff
        assert all(b.time-a.time == timedelta(hours=hours) for a,b in zip(bars,bars[1:]))
        report = analyze(bars, hours)
        dump(folder/f'analysis_{hours}h.json', report)
        b = report['blocks'][-1]
        hierarchy = b['theory']['type_hierarchy']
        points = b['theory']['macd']['points']
        children = hierarchy['base_movements']
        strict = {x['level']: x for x in b['theory']['strict_divergence']['levels']}
        layers = []
        for layer in hierarchy['levels']:
            layers.append(layer_audit(children,layer['structural_view'],points,bars,hours,
                                      strict[layer['level']]['signals']))
            children = layer['movements']
        strokes = [Movement(f'P{i}',0,'stroke_proxy',s.start,s.end,s.low,s.high,(),(),s.confirmed_at)
                   for i,s in enumerate(b['strokes']) if not s.provisional]
        pen_view = evolving_centers(strokes,1)
        latest = dict(last_closed_bar=bars[-1], last_strokes=b['strokes'][-12:],
                      last_segments=b['segments'][-10:], pending_segment=b['pending_segment'],
                      segment_diagnostics=b['segment_diagnostics'],
                      completed_movement_layers=layers,
                      stroke_observation=layer_audit(strokes,pen_view,points,bars,hours),
                      strict_confirmed=b['theory']['strict_divergence']['confirmed'],
                      volume_price=report['volume_price'])
        summary['periods'][f'{hours}h'] = latest
        print(f'{hours}h analyzed: {len(b["strokes"])} strokes, {len(b["segments"])} completed segments',flush=True)
    dump(folder/'summary.json',summary)
    print(folder/'summary.json')


if __name__ == '__main__':
    main()
