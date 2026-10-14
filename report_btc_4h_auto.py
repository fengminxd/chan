"""以当前程序规则生成自动4h报告，不接受人工买卖点输入。"""
import argparse
import csv
import hashlib
import json
from bisect import bisect_left
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from chan_time import period_name

from chan import (Candle, analyze, find_fractals, json_default, merge_inclusions,
                  parse_time, read_csv, validate_candles)
from chan_structure import Movement, direct_break_marks
from chan_theory import (divergence_candidates, evolving_centers, first_second_signals,
                         second_retest_positions)

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
TZ = timezone(timedelta(hours=8))
STEP = 4 * 3600 * 1000
ENDPOINT = 'https://fapi.binance.com/fapi/v1/klines'


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_default))


def local(t):
    return (parse_time(t) if isinstance(t, str) else t).astimezone(TZ).strftime('%Y-%m-%d %H:%M')


def fetch_history(folder, context_start):
    pages = folder / 'raw_pages'
    pages.mkdir(parents=True, exist_ok=True)
    with urlopen('https://fapi.binance.com/fapi/v1/time', timeout=25) as response:
        server = json.load(response)
    cutoff = min(server['serverTime'], int(datetime.now(UTC).timestamp()*1000)) // 60000 * 60000
    closed_end = cutoff // STEP * STEP
    cursor = int(context_start.timestamp()*1000)
    if cursor % STEP:
        raise ValueError('前置历史须对齐4h开盘网格')
    rows, requests = [], []

    def request(interval, begin, end, step):
        url = ENDPOINT + '?' + urlencode(dict(symbol='BTCUSDT', interval=interval,
                                               startTime=begin, endTime=end-1, limit=1000))
        path = pages / f'{interval}_{begin}_{end}.json'
        meta_path = path.with_suffix('.meta.json')
        if path.exists() and meta_path.exists():
            raw = path.read_bytes()
            meta = json.loads(meta_path.read_text())
            if meta['url'] != url or meta['sha256'] != hashlib.sha256(raw).hexdigest():
                raise ValueError('行情缓存来源或校验和不符')
        else:
            with urlopen(url, timeout=30) as response:
                raw = response.read()
                meta = dict(url=url, downloaded_at=datetime.now(UTC).isoformat(),
                            server_date=response.headers.get('Date'), sha256=hashlib.sha256(raw).hexdigest())
            path.write_bytes(raw)
            dump(meta_path, meta)
        page = json.loads(raw)
        if not isinstance(page, list) or len(page) != (end-begin)//step:
            raise ValueError('接口未完整覆盖请求时间')
        if any(len(r) != 12 or r[0] != begin+i*step or r[6] != r[0]+step-1 for i, r in enumerate(page)):
            raise ValueError('K线开闭时间、顺序或字段异常')
        validate_candles([Candle(datetime.fromtimestamp(r[0]/1000, UTC), *map(float, r[1:6])) for r in page])
        requests.append({**meta, 'file': str(path.relative_to(folder)), 'count': len(page)})
        return page

    while cursor < closed_end:
        end = min(closed_end, cursor+1000*STEP)
        rows.extend(request('4h', cursor, end, STEP))
        cursor = end
        print(f'已校验 {len(rows)} 根完整4h K线', flush=True)
    with (folder / 'history_4h.csv').open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        w.writerows([datetime.fromtimestamp(r[0]/1000, UTC).isoformat(), *r[1:6]] for r in rows)
    partial = None
    if cutoff > closed_end:
        minutes = request('1m', closed_end, cutoff, 60000)
        partial = dict(time=datetime.fromtimestamp(closed_end/1000, UTC), open=float(minutes[0][1]),
                       high=max(float(r[2]) for r in minutes), low=min(float(r[3]) for r in minutes),
                       close=float(minutes[-1][4]), volume=sum(float(r[5]) for r in minutes),
                       partial=True, available_minutes=len(minutes))
    dump(folder / 'partial_4h.json', partial)
    dump(folder / 'source.json', dict(symbol='BTCUSDT', market='Binance USD-M perpetual',
         price_type='trade_price', as_of=datetime.fromtimestamp(cutoff/1000, UTC), server_time=server,
         context_start=context_start, closed_4h_count=len(rows), partial_4h_minutes=(cutoff-closed_end)//60000,
         endpoint=ENDPOINT, requests=requests, history_sha256=hashlib.sha256((folder/'history_4h.csv').read_bytes()).hexdigest(),
         validation='all requested closed bars present exactly once; current bucket built from closed minutes only'))


def structure_lines(block, prefix):
    result = []
    # Reuse segment diagnostics: bold the attacking stroke, not the whole segment.
    breaks = direct_break_marks(block)
    for kind, items in [('stroke', block['strokes']), ('segment', block['segments'] +
                        ([block['pending_segment']] if block['pending_segment'] else []))]:
        for i, s in enumerate(items):
            done = not s.provisional if kind == 'stroke' else s.confirmed
            result.append(dict(id=f'{prefix}:{kind}:{i}', kind=kind, level=-1 if kind == 'stroke' else 0,
                               start=s.start.time, end=s.end.time, start_price=s.start.price, end_price=s.end.price,
                               state='completed' if done else 'pending',
                               evidence_at=s.confirmed_at if kind == 'stroke' else s.evidence_at,
                               **(breaks.get(i, {}) if kind == 'stroke' else {})))
    return result


def unique_signals(signals):
    # 同端点、同类点只显示证据较强的一条，其余来源仍可审计。
    rank = {'policy_confirmed': 6, 'structural': 5, 'stroke_structural': 5,
            'policy_dependent': 4, 'dependent_candidate': 3, 'macd_candidate': 3,
            'second_retest_geometry': 1, 'pending_stroke_candidate': 0}
    selected = {}
    for s in signals:
        key = (s['label'], s['level'], s['time'], s['price'])
        if key not in selected or rank.get(s['certainty'], 0) > rank.get(selected[key]['certainty'], 0):
            selected[key] = s
    return sorted(selected.values(), key=lambda s: (s['time'], s['label'], s['level']))


def auto_layer(children, view, points, prefix, basis, extra_signals=(), candles=(), period_hours=4):
    centers = {z.id: {**asdict(z), 'id': f'{prefix}:{z.id}', 'kind': 'center', 'state': z.status,
                         'basis': basis,
                         'historical': z in view.get('historical_centers', [])}
               for z in [*view['centers'], *view.get('historical_centers', [])]}
    divs = divergence_candidates(children, view, points)
    sigs = [asdict(s) for s in [*view['signals'], *extra_signals, *first_second_signals(children, divs)]]
    by_id = {m.id: m for m in children}
    for p in second_retest_positions(children):
        part = [c for c in candles if p['origin_time'] <= c.time < p['time']+timedelta(hours=period_hours)]
        buy = p['label'] == 'B2'
        if part and (min(c.low for c in part) < p['origin_price'] if buy else max(c.high for c in part) > p['origin_price']):
            continue
        sigs.append({**p, 'id': f'retest:{p["label"]}:{p["source_ids"][-1]}', 'center_id': None,
                     'details': {'scope': p['scope'], 'pending': p['pending'], 'origin_time': p['origin_time'],
                                 'origin_price': p['origin_price'], 'first_point_confirmed': False}})
    for s in sigs:
        s['id'] = f'{prefix}:{s["id"]}'
        s['basis'] = basis
        if basis.endswith('笔级') and s['certainty'] == 'structural':
            s['certainty'] = 'stroke_structural'
        s['center_id'] = f'{prefix}:{s["center_id"]}' if s.get('center_id') else None
    for d in divs:
        d['id'] = f'{prefix}:{d["id"]}'
        d['center_id'] = f'{prefix}:{d["center_id"]}'
        d['basis'] = basis
        d['a_start'], d['a_end'] = by_id[d['a_id']].start.time, by_id[d['a_id']].end.time
        d['time'] = d['end']
    return list(centers.values()), unique_signals(sigs), divs


def pending_stroke_candidates(block, candles, asof, prefix, period_hours=4, initial_direction='up'):
    """Display stable-endpoint tail candidates without promoting the tail to completed input.

    Reuse the entry planner's B2/S2 and B3/S3 policy, including raw ranges,
    boundary touches and consumed centers. This is a chart snapshot, not an
    execution plan or an observation-time replay. No partial candles enter it.
    """
    from entry_plan import period_candidates

    strokes = block['strokes']
    if not strokes or not strokes[-1].provisional or any(s.provisional for s in strokes[:-1]):
        return []
    duration = timedelta(hours=period_hours)
    closed = [c for c in candles if c.time+duration <= asof]
    if not closed:
        return []
    tail = strokes[-1]
    diag = block.get('stroke_diagnostics', {})
    if diag.get('policy') == 'connected_strict_revision_events_v1':
        stable_at = diag.get('tail_endpoint_stable_at')
        if not stable_at or diag.get('closed_at'):
            return []
    else:
        merged = merge_inclusions(closed, initial_direction)
        if tail.end not in find_fractals(merged[:-1]):
            return []
        stable_at = merged[tail.end.index+2].start+duration
    evidence = max([stable_at, *[s.confirmed_at or s.end.time for s in strokes[:-1]]])
    if evidence > asof:
        return []
    # A later closed candle can invalidate an old endpoint before a new stable
    # fractal is selected. Do not keep advertising the obsolete candidate.
    after = [c for c in closed if c.time >= tail.end.time+duration]
    if any(c.high > tail.end.price if tail.direction=='up' else c.low < tail.end.price for c in after):
        return []
    times = [c.time for c in closed]
    legs = []
    for i,s in enumerate(strokes):
        finish = s.end.time+duration
        part = closed[bisect_left(times,s.start.time):bisect_left(times,finish)]
        if not part:
            return []
        legs.append(dict(index=i,start=s.start.time,end=s.end.time,finish=finish,
                         direction=s.direction,price=s.end.price,
                         low=min(c.low for c in part),high=max(c.high for c in part),
                         focus_time=s.end.time,focus_price=s.end.price))
    result = []
    for c in period_candidates(block,legs,asof,period_name(period_hours)):
        if c['label'] not in ('B2','S2','B3','S3'):
            continue
        center = c['details'].get('center_id')
        result.append(dict(id=f'{prefix}:pending:{c["label"]}:P{len(strokes)-1}',
            label=c['label'],level=1,time=tail.end.time,price=tail.end.price,
            evidence_at=evidence,observed_at=asof,certainty='pending_stroke_candidate',
            basis=f'{period_name(period_hours)}笔级',child_index=len(strokes)-1,
            center_id=f'{prefix}:{center}' if center else None,
            source_ids=[f'P{i}' for i in range(max(0,len(strokes)-3),len(strokes))],
            actionable=False,higher_point_confirmed=False,
            details={**c['details'],'scope':'stable_tail_endpoint_candidate_only',
                     'candidate_policy':'entry_plan_period_candidates',
                     'endpoint_stable_at':stable_at,'stroke_completed':False,
                     'parent_invalidation':c['parent_invalidation']}))
    return result


def automatic_views(report, candles, start, asof, partial=None, period_hours=4):
    expected_blocks = report['gap_count']+1 if report['bar_count'] else 0
    if len(report['blocks']) != expected_blocks:
        raise ValueError('连续行情存在人工结构断点，拒绝发布图表')
    for block in report['blocks']:
        for kind, items in [('笔', block['strokes']), ('段', block['segments'] +
                ([block['pending_segment']] if block['pending_segment'] else []))]:
            for previous, following in zip(items, items[1:]):
                if previous.end != following.start or previous.direction == following.direction:
                    raise ValueError(f'{kind}链不连续，拒绝发布图表')
    period = period_name(period_hours)
    lines = []
    accumulated = {k: dict(centers=[], signals=[], divergences=[]) for k in ('strokes', 'types')}
    for bi, b in enumerate(report['blocks']):
        prefix = f'{period}:block:{bi}'
        lines.extend(structure_lines(b, prefix))
        run = [c for c in candles if b['start'] <= c.time <= b['end']]
        points = b['theory']['macd']['points']
        children = [Movement(f'P{i}', 0, 'stroke_proxy', s.start, s.end, s.low, s.high,
                             (), (), s.confirmed_at) for i, s in enumerate(b['strokes']) if not s.provisional]
        stroke_view = evolving_centers(children, 1)
        result = auto_layer(children, stroke_view, points, prefix+':strokes', period+'笔级',
                            candles=run, period_hours=period_hours)
        for key, value in zip(('centers', 'signals', 'divergences'), result):
            accumulated['strokes'][key].extend(value)
        accumulated['strokes']['signals'].extend(pending_stroke_candidates(
            b,run,asof,prefix+':strokes',period_hours,
            report.get('rule_manifest',{}).get('parameters',{}).get('initial_direction','up')))
        hierarchy = b['theory']['type_hierarchy']
        children = hierarchy['base_movements']
        strict = {l['level']: l for l in b['theory']['strict_divergence']['levels']}
        for layer in hierarchy['levels']:
            result = auto_layer(children, layer['structural_view'], points, prefix+':types', period+'完成走势',
                                strict[layer['level']]['signals'], candles=run, period_hours=period_hours)
            for key, value in zip(('centers', 'signals', 'divergences'), result):
                accumulated['types'][key].extend(value)
            # 严格确认的背驰也进入图表；未通过/待证审计只在analysis中保留。
            for audit in strict[layer['level']]['audits']:
                if audit['strict_divergence_confirmed']:
                    accumulated['types']['divergences'].append({**audit, 'id': prefix+':types:'+audit['id'],
                        'center_id': prefix+':types:'+audit['center_id'], 'basis': period+'完成走势',
                        'time': audit['end'], 'certainty': 'policy_confirmed'})
            children = layer['movements']
    views = {}
    for key, data in accumulated.items():
        nodes = [n for n in [*lines, *data['centers']] if n['end'] >= start and n['start'] < asof]
        signals = unique_signals([s for s in data['signals'] if start <= s['time'] < asof])
        divergences = [d for d in data['divergences'] if start <= d['time'] < asof]
        views[key] = dict(nodes=nodes, signals=signals, divergences=divergences,
                          centers={z['id']: z for z in data['centers']},
                          counts=dict(Counter(n['kind'] for n in nodes)),
                          signal_counts=dict(Counter(s['label'] for s in signals)))
    visible = [asdict(c) for c in candles if c.time+timedelta(hours=period_hours) > start]
    if partial:
        visible.append(partial)
    breaks = []
    for i,b in enumerate(report['blocks']):
        diag = b.get('stroke_diagnostics', {})
        when = diag.get('closed_at')
        if when and start <= when <= asof:
            following = report['blocks'][i+1] if i+1 < len(report['blocks']) else None
            breaks.append(dict(observed_at=when,
                start=b['strokes'][-1].end.time if b['strokes'] else b['end'],
                end=following['start'] if following else when,
                reason='已完成笔保留；短间距急转无法合法衔接，后续结构独立起算'))
    return dict(start=start, asof=asof, bars=visible, views=views, continuity_breaks=breaks,
                analysis_period=period, manual_annotations=[], rule_manifest=report['rule_manifest'],
                chart_signal_policy='completed_inputs_plus_stable_pending_stroke_candidates_v1')


def write_report(folder, start):
    source = json.loads((folder/'source.json').read_text())
    asof = parse_time(source['as_of'])
    if start >= asof:
        raise ValueError('展示开始时间必须早于行情截止时间')
    candles = read_csv(folder/'history_4h.csv')
    if not candles or candles[0].time > start or candles[-1].time+timedelta(hours=4) > asof:
        raise ValueError('4h历史未覆盖目标开始或包含未收盘K线')
    if any(b.time-a.time != timedelta(hours=4) for a, b in zip(candles, candles[1:])):
        raise ValueError('4h历史存在缺口')
    report = analyze(candles, 4)
    partial = json.loads((folder/'partial_4h.json').read_text())
    payload = automatic_views(report, candles, start, asof, partial)
    payload['source'] = source
    dump(folder/'analysis_4h.json', report)
    dump(folder/'chart_data.json', payload)
    template = (ROOT/'btc_4h_auto_template.html').read_text()
    page = template.replace('PAYLOAD_JSON', json.dumps(payload, ensure_ascii=False, default=json_default,
                                                      separators=(',', ':')).replace('<', '\\u003c'))
    (folder/'btc_4h.html').write_text(page)
    summary = dict(start=start, asof=asof, context_start=candles[0].time,
                   displayed_closed_bars=sum(c.time >= start for c in candles),
                   partial_minutes=source['partial_4h_minutes'], manual_annotation_count=0,
                   views={k: {**v['counts'], 'signals': v['signal_counts'], 'divergences': len(v['divergences'])}
                          for k, v in payload['views'].items()})
    dump(folder/'summary.json', summary)
    for name in ('signals', 'divergences'):
        with (folder/f'{name}.csv').open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['结构口径', '类型', '端点K线_北京', '价格', '证据时间_北京', '性质', '中枢ID', '力度比'])
            for key, view in payload['views'].items():
                for x in view[name]:
                    w.writerow([key, x.get('label', x.get('kind')), local(x['time']), x['price'],
                                local(x['evidence_at']), x['certainty'], x.get('center_id'), x.get('area_ratio', '')])
    with (folder/'structures.csv').open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['口径', '类型', 'ID', '开始_北京', '结束_北京', '起点价', '终点价', 'ZD', 'ZG', '状态'])
        for key, view in payload['views'].items():
            for n in view['nodes']:
                w.writerow([key, n['kind'], n['id'], local(n['start']), local(n['end']), n.get('start_price'),
                            n.get('end_price'), n.get('zd'), n.get('zg'), n['state']])
    lines = ['# BTCUSDT 4h 自动结构图', '', '[打开图表](btc_4h.html)', '',
             f'北京时间 **{local(start)} — {local(asof)}**。前置历史从 {local(candles[0].time)} 开始。', '',
             'Binance USDT本位永续成交价。完整4h K线参与计算；最后未收盘桶由截至时刻的已收盘分钟线拼成，仅供显示。', '',
             '**所有标注来自当前程序。人工标注为0；不读取参考图片，也不沿用指定日期的一买。**', '',
             '默认显示4h笔中枢口径，可切换完成走势中枢口径；两者共享同一组4h笔段。笔中枢仍是笔重叠观察模型，不等于完成次级走势递归中枢。', '',
             '| 口径 | 笔（含末笔） | 段（含待确认） | 中枢 | 买卖点 | 背驰位置 |', '| --- | ---: | ---: | ---: | --- | ---: |']
    for k, v in summary['views'].items():
        lines.append(f"| {k} | {v.get('stroke',0)} | {v.get('segment',0)} | {v.get('center',0)} | {v['signals']} | {v['divergences']} |")
    lines += ['', '空心数字是MACD辅助/依赖/首次回试候选；实心为相应结构规则输出。紫色菱形标出程序检出的趋势背驰或盘整背驰辅助位置，点击查看比较区间、面积比和引用中枢。没有满足条件的一类点时保持零输出。', '',
              'MACD面积减弱不是严格背驰证明；严格均线力度审计另存在analysis_4h.json。端点标在所属4h K线开盘时刻，结构证据时间另列，未运行全历史逐根回放。', '',
              '[信号CSV](signals.csv) · [背驰CSV](divergences.csv) · [结构CSV](structures.csv) · [数据来源](source.json) · [完整分析](analysis_4h.json)', '',
              '复现：`python3 report_btc_4h_auto.py`；更新到当前：`python3 report_btc_4h_auto.py --refresh`。']
    (folder/'result.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(summary, ensure_ascii=False, default=json_default), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2025-10-06T00:00:00+08:00')
    parser.add_argument('--context-start', default='2023-01-01T00:00:00+08:00')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'reports/BTCUSDT_4h_20251006_current')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--fetch-only', action='store_true')
    args = parser.parse_args()
    start, context = parse_time(args.start), parse_time(args.context_start)
    if context > start:
        parser.error('前置历史不能晚于目标开始时间')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.refresh or not (args.output_dir/'source.json').exists():
        fetch_history(args.output_dir, context)
    if not args.fetch_only:
        write_report(args.output_dir, start)


if __name__ == '__main__':
    main()
