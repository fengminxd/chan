"""Reproducible BTC perpetual bottom-divergence audit, independent chart periods."""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import chan_time
from chan import Candle, analyze, json_default, parse_time, read_csv, validate_candles

ROOT = Path('reports/BTCUSDT_bottoms_20251006_20260819')
START = parse_time('2025-10-06T00:00:00+08:00')
END = parse_time('2026-08-20T00:00:00+08:00')  # Include the whole final date.
CONTEXT = parse_time('2023-01-01T00:00:00+08:00')
TZ = timezone(timedelta(hours=8))
PERIODS = {'8h': 8, '12h': 12, '1d': 24, '3d': 72, '1w': 168}
# Local fixed-duration extensions; no modification of the shared engine.
for name, hours in PERIODS.items():
    chan_time.PERIOD_MINUTES[name] = hours * 60
    chan_time.PERIODS[name] = hours


def save(name, value):
    (ROOT / name).write_text(json.dumps(value, default=json_default, ensure_ascii=False, indent=2))


def fetch():
    pages = ROOT / 'raw_pages'
    pages.mkdir(parents=True, exist_ok=True)
    cursor, stop = int(CONTEXT.timestamp()*1000), int(END.timestamp()*1000)
    rows, requests = [], []
    endpoint = 'https://fapi.binance.com/fapi/v1/klines'
    while cursor < stop:
        page_end = min(stop, cursor + 1000*3600000)
        url = endpoint + '?' + urlencode(dict(symbol='BTCUSDT', interval='1h',
            startTime=cursor, endTime=page_end-1, limit=1000))
        path = pages / f'{cursor}.json'
        meta_path = path.with_suffix('.meta.json')
        if path.exists() and meta_path.exists():
            payload, meta = path.read_bytes(), json.loads(meta_path.read_text())
            assert meta['url'] == url and meta['sha256'] == hashlib.sha256(payload).hexdigest()
        else:
            with urlopen(url, timeout=40) as response:
                payload = response.read()
                meta = dict(url=url, downloaded_at=datetime.now(timezone.utc).isoformat(),
                            server_date=response.headers.get('Date'), sha256=hashlib.sha256(payload).hexdigest())
            path.write_bytes(payload)
            meta_path.write_text(json.dumps(meta, indent=2))
        page = json.loads(payload)
        assert isinstance(page, list) and len(page) == (page_end-cursor)//3600000
        assert all(len(r) == 12 and r[0] == cursor+i*3600000 and r[6] == r[0]+3599999 for i,r in enumerate(page))
        rows.extend(page)
        requests.append({**meta, 'count': len(page), 'file': str(path.relative_to(ROOT))})
        cursor = page_end
        print(f'downloaded/verified {len(rows)} hourly bars', flush=True)
    bars = [Candle(datetime.fromtimestamp(r[0]/1000, timezone.utc), *map(float, r[1:6])) for r in rows]
    validate_candles(bars)
    assert all(b.time-a.time == timedelta(hours=1) for a,b in zip(bars,bars[1:]))
    with (ROOT/'history_1h.csv').open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['timestamp','open','high','low','close','volume'])
        w.writerows([b.time.isoformat(), *r[1:6]] for b,r in zip(bars,rows))
    save('source.json', dict(symbol='BTCUSDT', market='Binance USD-M perpetual',
         price_type='trade_price', endpoint=endpoint, start=START, end_exclusive=END,
         context_start=CONTEXT, count=len(bars), requests=requests,
         csv_sha256=hashlib.sha256((ROOT/'history_1h.csv').read_bytes()).hexdigest()))


def aggregate(raw, hours):
    # Weekly candles begin Monday 00:00 UTC+8; 3d begins 1970-01-01 UTC+8.
    anchor = parse_time('1970-01-05T00:00:00+08:00' if hours == 168 else '1970-01-01T00:00:00+08:00')
    width = timedelta(hours=hours)
    groups = {}
    for b in raw:
        key = anchor + ((b.time-anchor)//width)*width
        groups.setdefault(key, []).append(b)
    result = []
    for t, items in groups.items():
        if len(items) != hours or t+width > END:
            continue
        assert all(b.time == t+timedelta(hours=i) for i,b in enumerate(items))
        result.append(Candle(t,items[0].open,max(b.high for b in items),min(b.low for b in items),
                             items[-1].close,sum(b.volume for b in items)))
    return result


def selected(report):
    candidates, audits = [], []
    for block in report['blocks']:
        for layer in block['theory']['levels']:
            candidates.extend(d for d in layer['divergences']
                              if d['side'] == 'down' and START <= d['end'] < END)
        for layer in block['theory']['strict_divergence']['levels']:
            audits.extend(d for d in layer['audits']
                          if d['side'] == 'down' and START <= d['end'] < END)
    return candidates, audits


def run():
    raw = read_csv(ROOT/'history_1h.csv')
    assert raw[0].time == CONTEXT and raw[-1].time+timedelta(hours=1) == END
    assert all(b.time-a.time == timedelta(hours=1) for a,b in zip(raw,raw[1:]))
    old_path = Path('reports/BTCUSDT_perpetual_20260606_20260819/context_1h.csv')
    overlap = 0
    if old_path.exists():
        old = read_csv(old_path)
        lookup = {b.time:b for b in raw}
        assert all(lookup[b.time] == b for b in old)
        overlap = len(old)
    save('data_validation.json',dict(status='passed',hourly_count=len(raw),
         target_hourly_count=sum(b.time>=START for b in raw),first=raw[0].time,
         last_open=raw[-1].time,end_exclusive=END,missing_hours=0,overlap_bars_matched=overlap))
    summary = {}
    for period, hours in PERIODS.items():
        bars = aggregate(raw, hours)
        report = analyze(bars, hours)
        save(f'analysis_{period}.json', report)
        candidates, audits = selected(report)
        summary[period] = dict(bar_count=len(bars), window_bars=sum(b.time >= START for b in bars),
            candidates=candidates, audits=audits, segments=sum(len(b['segments']) for b in report['blocks']),
            diagnostics=[b['segment_diagnostics'] for b in report['blocks']],
            last_segment_end=[b['segments'][-1].end.time if b['segments'] else None for b in report['blocks']],
            strict_status=[b['theory']['strict_divergence'].get('evaluation_status') for b in report['blocks']])
        print(period, json.dumps(summary[period],default=json_default,ensure_ascii=False),flush=True)
    save('summary.json', summary)
    sensitivity = {}
    for date in ['2024-01-01T00:00:00+08:00','2025-01-01T00:00:00+08:00']:
        sub = [b for b in raw if b.time >= parse_time(date)]
        sensitivity[date] = {}
        for p,h in PERIODS.items():
            candidates,audits = selected(analyze(aggregate(sub,h),h))
            sensitivity[date][p] = dict(candidates=candidates,
                strict_confirmed=[a for a in audits if a['state']=='confirmed_under_policy'])
    save('context_sensitivity.json',sensitivity)


def replay_scan():
    raw = read_csv(ROOT/'history_1h.csv')
    output = {}
    for period, hours in PERIODS.items():
        bars = aggregate(raw, hours)
        previous, records, events = {}, {}, []
        cutoffs = 0
        for stop in range(1, len(bars)+1):
            observed = bars[stop-1].time + timedelta(hours=hours)
            if observed < START:
                continue
            candidates, audits = selected(analyze(bars[:stop], hours))
            current = {}
            for d in candidates + [a for a in audits if a['state'] == 'confirmed_under_policy']:
                assert d['evidence_at'] <= observed
                key = f"{d['certainty']}:{d['level']}:{d['end'].isoformat()}:{d['price']}"
                current[key] = d
                if key not in records:
                    records[key] = dict(first_observed_at=observed, latest_observed_at=observed,
                                        value=d, observations=0, retractions=[])
                records[key].update(latest_observed_at=observed,value=d)
                records[key]['observations'] += 1
                if key not in previous or previous[key] != d:
                    events.append(dict(action='added' if key not in previous else 'updated',
                                       observed_at=observed, key=key,value=d))
            for key in previous.keys()-current.keys():
                records[key]['retractions'].append(observed)
                events.append(dict(action='retracted',observed_at=observed,key=key))
            previous = current
            cutoffs += 1
            if cutoffs % 150 == 0:
                print(period, 'replayed',cutoffs,'closes',flush=True)
        for key, r in records.items():
            r['present_at_final_close'] = key in previous
        output[period] = dict(cutoffs=cutoffs,records=records,events=events)
        save(f'replay_{period}.json',output[period])
        print(period,'done',cutoffs,'records',len(records),flush=True)
    save('replay_summary.json',output)


def local(value):
    if not value:
        return '—'
    t = parse_time(value) if isinstance(value,str) else value
    return t.astimezone(TZ).strftime('%Y-%m-%d %H:%M')


def make_report():
    from collections import Counter
    from chan_theory import macd
    summary = json.loads((ROOT/'summary.json').read_text())
    replay = json.loads((ROOT/'replay_summary.json').read_text())
    quality = json.loads((ROOT/'data_validation.json').read_text())
    lines = ['# BTCUSDT 永续：8h 及以上底部背驰检测', '',
        '区间：北京时间 2025-10-06 00:00 至 2026-08-20 00:00，包含 8 月 19 日全天。',
        '品种：Binance BTCUSDT USDT 本位永续，成交价 K 线。前置历史从 2023-01-01 开始。',
        f"已核验 {quality['hourly_count']:,} 根连续小时线，目标区间 {quality['target_hourly_count']:,} 根；与原有数据重叠的 {quality['overlap_bars_matched']:,} 根完全一致。", '',
        '按 8h、12h、1d、3d、1w 独立图表周期运行现有严格线段/中枢分析。不是 1m 统一递归树的周期投影；L1/L2 等结构层级不等同于图表周期。',
        '8h/12h/日线按北京时间自然日分桶；3d 以北京时间 1970-01-01 为锚；周线从周一北京时间 00:00 开始。只使用截止前完整收盘桶。不涵盖任意自定义周期或月线。', '',
        '## 结果', '',
        '| 图表周期 | 区间完整 K 线 | 收盘回放截面 | 截止时 MACD 底背驰辅助候选 | 回放曾出现候选端点 | 严格趋势底背驰确认 |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for p, s in summary.items():
        records = list(replay[p]['records'].values())
        lines.append(f"| {p} | {s['window_bars']} | {replay[p]['cutoffs']} | {len(s['candidates'])} | {sum(r['value']['certainty']=='macd_auxiliary' for r in records)} | {sum(r['value'].get('state')=='confirmed_under_policy' for r in records)} |")
    lines += ['', '严格确认数量为当前程序及政策的检测结果。日线、3日线和周线的已完成结构不足，不能由零输出断言市场没有背驰。', '',
        '## 候选明细与实际观察时间', '',
        '| 周期 | 类型 | 底部 K 线开盘时间 | 价格 USDT | 后段/前段负柱面积 | 回放首次观察 | 截止时保留 |',
        '| --- | --- | --- | ---: | ---: | --- | --- |']
    csv_rows = []
    for p, r in replay.items():
        for item in r['records'].values():
            d = item['value']
            ratio = d.get('area_ratio')
            kind = {'consolidation':'盘整辅助候选','trend':'趋势辅助候选'}.get(d['kind'],d['kind'])
            lines.append(f"| {p} | {kind} | {local(d['end'])} | {d['price']:,.1f} | {ratio:.2%} | {local(item['first_observed_at'])} | {'是' if item['present_at_final_close'] else '否'} |")
            csv_rows.append([p,kind,local(d['end']),d['price'],ratio,local(d['evidence_at']),local(item['first_observed_at']),item['present_at_final_close'],len(item['retractions'])])
    with (ROOT/'candidates.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['period','kind','bottom_bar_open_utc8','price','area_ratio','structural_evidence_utc8','first_observed_utc8','present_at_end','retractions']);w.writerows(csv_rows)
    lines += ['', '端点时间是所属图表 K 线开盘时间，不是分时成交时刻，也不是当时已发出的信号。首次观察来自逐根目标周期收盘前缀重算；只读取当时已有数据，仍允许后续修订/撤回。', '',
        '## 12h 候选的比较依据', '',
        '比较前段为 2025-10-07 00:00 至 2026-02-06 00:00，126,208.5 → 59,800.0；后段为 2026-05-06 12:00 至 2026-07-01 00:00，82,828.7 → 57,758.6。时间均为所属 12h K 线开盘时间。',
        '中间中枢核心 65,501.0–75,998.9；后段价格创新低。MACD(12,26,9) 负柱面积由 125,235.9120 降至 42,509.0034，比例 0.3394314，缩小 66.06%；中间 DIF/DEA 满足程序的回零阈值（前段 DIF 峰值的 20%）。面积按 [起点,终点) 负柱绝对值求和，单位为价格×K线根数，使用本项目原有参数。',
        '小时原始数据把 57,758.6 低点定位到 2026-07-01 09:00–10:00；因此表中 00:00 不能当作准确成交时间。逐根回放在 2026-07-30 00:00 首次观察到候选，之后未撤回；8 月 4 日 12:00 发生一次证据时间修订，比较区间与面积比例没有改变。最终快照中的 8 月 4 日证据时间不能替代真实首次观察时间，7 月 1 日的低点也不能回填成当天已确认的一买。',
        '将前置历史分别缩短至 2024-01-01、2025-01-01，截止快照仍保留同一 12h 候选，比较区间及面积比例一致；这不构成历史充分性的证明。', '',
        '![12h 价格与 MACD](chart_12h.svg)', '',
        '## 结构覆盖及限制', '',
        '| 周期 | 全历史确认线段数 | 最近确认线段端点（北京时间） | 待划入线段的已完成笔 |',
        '| --- | ---: | --- | ---: |']
    for p,s in summary.items():
        tail = sum(d['tail'].get('unassigned_completed_strokes',0) for d in s['diagnostics'])
        lines.append(f"| {p} | {s['segments']} | {local(s['last_segment_end'][-1])} | {tail} |")
    lines += ['', '尾部结构等待确认使大周期扫描覆盖受限。严格趋势背驰要求 a+A+b+B+c、两个同向相离中枢、次级别组成及完成证据，并比较完整 SMA(5,10) 吻间面积；MACD 面积减弱仅列辅助候选。盘整候选未由本程序实现独立的严格动力学确认。',
        '8h 与 12h 截止快照各有一项下跌趋势结构审计，均未通过；日线、3日线和周线没有可进行严格趋势背驰审计的完成结构。逐根回放中的零确认同样受这些规则限制。', '',
        '## 复现与来源', '',
        '运行 `python3 scan_btc_bottoms.py fetch`、`python3 scan_btc_bottoms.py analyze`、`python3 scan_btc_bottoms.py replay`、`python3 scan_btc_bottoms.py report`。',
        '[币安公开永续 K 线接口](https://fapi.binance.com/fapi/v1/klines)；原始响应与请求保存在 raw_pages/ 和 source.json。',
        '分析快照 analysis_*.json；回放完整候选新增/更新/撤回记录 replay_*.json；候选列表 candidates.csv；前置敏感性 context_sensitivity.json；数据质量 data_validation.json。']
    (ROOT/'report.md').write_text('\n'.join(lines)+'\n')
    save('code_manifest.json',{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in
         ['scan_btc_bottoms.py','chan.py','chan_time.py','chan_structure.py','chan_theory.py','chan_types.py','chan_dynamics.py','chan_policy.py']})
    raw=read_csv(ROOT/'history_1h.csv');bars=aggregate(raw,12);points=macd(bars)
    pairs=[(b,m) for b,m in zip(bars,points) if b.time>=START]
    lo=min(b.low for b,m in pairs)*.96;hi=max(b.high for b,m in pairs)*1.04
    x=lambda t: 80+(t-START).total_seconds()/(END-START).total_seconds()*1070
    y=lambda v: 45+(hi-v)/(hi-lo)*290
    extent=max(abs(m.histogram) for b,m in pairs)
    ym=lambda v: 437-v/extent*62
    svg=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 560" role="img" aria-label="BTCUSDT 12h price and MACD">',
         '<rect width="1200" height="560" fill="white"/>',
         '<g font-family="sans-serif" font-size="12" fill="#334155">',
         '<text x="80" y="25" font-size="19">BTCUSDT perpetual · 12h · UTC+8 · 2025-10-06 — 2026-08-19</text>']
    for i in range(6):
        price=lo+(hi-lo)*i/5
        svg.append(f'<path d="M80 {y(price):.2f}H1150" stroke="#e2e8f0"/><text x="5" y="{y(price):.2f}">{price:,.0f}</text>')
    d=summary['12h']['candidates'][0]
    for a,b in [('2025-10-07T00:00:00+08:00','2026-02-06T00:00:00+08:00'),('2026-05-06T12:00:00+08:00','2026-07-01T00:00:00+08:00')]:
        left,right=x(parse_time(a)),x(parse_time(b))
        svg.append(f'<rect x="{left:.2f}" y="45" width="{right-left:.2f}" height="460" fill="#2563eb" fill-opacity=".055"/>')
    for b,m in pairs:
        col='#059669' if b.close>=b.open else '#dc2626'
        svg.append(f'<path d="M{x(b.time):.2f} {y(b.high):.2f}V{y(b.low):.2f}" stroke="{col}" stroke-width=".8"/>')
        svg.append(f'<path d="M{x(b.time):.2f} 437V{ym(m.histogram):.2f}" stroke="{col if m.histogram>=0 else "#dc2626"}" stroke-width="1"/>')
    first = next(iter(replay['12h']['records'].values()))['first_observed_at']
    for stamp,label,color in [(parse_time(d['end']),'Low 57,758.6 (Jul 01)','#b91c1c'),(parse_time(first),'First observed Jul 30 00:00','#1d4ed8')]:
        xx=x(stamp)
        svg.append(f'<path d="M{xx:.2f} 45V505" stroke="{color}" stroke-dasharray="4 3"/><text x="{xx-8:.2f}" y="{365 if color=="#b91c1c" else 385}" text-anchor="end" fill="{color}">{label}</text>')
    svg.append('<path d="M80 437H1150" stroke="#94a3b8"/><text x="5" y="420">MACD</text><text x="5" y="438">12/26/9</text>')
    for i in range(7):
        t=START+(END-START)*i/6
        svg.append(f'<text x="{x(t):.2f}" y="535" text-anchor="middle">{t.astimezone(TZ):%Y-%m-%d}</text>')
    svg.append('</g></svg>')
    (ROOT/'chart_12h.svg').write_text(''.join(svg))
    print(ROOT/'report.md')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['fetch','analyze','replay','report'])
    args = parser.parse_args()
    ROOT.mkdir(parents=True,exist_ok=True)
    {'fetch':fetch,'analyze':run,'replay':replay_scan,'report':make_report}[args.action]()
