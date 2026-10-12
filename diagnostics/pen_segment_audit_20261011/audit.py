"""只读审计笔/段；返回 1 表示仍有反例，不修改生产规则或历史报告。

运行：python3 diagnostics/pen_segment_audit_20261011/audit.py
当前结果写入同目录 current_results.json，保留修复前 results.json。
完整修复复核请运行 diagnostics/pen_segment_fix_20261011/verify.py。
本文件不加入常规 unittest 自动发现。
"""
from bisect import bisect_left
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chan import (Candle, Fractal, Stroke, analyze, build_strokes, find_fractals,
                  json_default, merge_inclusions, read_csv, write_chart)
from chan_structure import build_segments, _turn_evidence, _valid_span
from report_btc_4h_auto import structure_lines
from verify_cases import verify

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def legs(prices):
    up = prices[1] > prices[0]
    points = [Fractal(i * 4, 'bottom' if (i % 2 == 0) == up else 'top',
                      p, BASE + timedelta(hours=4*i)) for i, p in enumerate(prices)]
    return [Stroke(a, b) for a, b in zip(points, points[1:])]


def candle(i, low, high):
    return Candle(BASE + timedelta(hours=i), low, high, low, high, 1)


def block(seq):
    r = build_segments(seq)
    return dict(strokes=seq, segments=r.segments, pending_segment=r.pending,
                segment_diagnostics={'tail': r.tail_diagnostics}, centers=[])


def signature(s):
    return (s.first_stroke, s.last_stroke, s.evidence_stroke)


def marked(b):
    return [int(n['id'].rsplit(':', 1)[1]) for n in structure_lines(b, 'audit')
            if n.get('direct_break')]


def main():
    checks = []

    def check(name, passed, expected, actual):
        checks.append(dict(name=name, passed=passed, expected=expected, actual=actual))

    # 固定已完成笔：按报告的证据索引截断时，应该能重现该段。
    p = [125,101,102,92,105,83,99,98,99,87,89,84,103]
    for sign in (1, -1):
        seq = legs([sign*x for x in p])
        full = build_segments(seq).segments
        prefix = build_segments(seq[:-1]).segments
        advertised = [s for s in full if s.evidence_stroke < len(seq)-1]
        check(f'causal_prefix_sign_{sign}', prefix == advertised,
              'prefix == full segments whose evidence is already available',
              dict(prices=[sign*x for x in p], full=list(map(signature, full)),
                   prefix=list(map(signature, prefix)), advertised=list(map(signature, advertised))))

    # 起始三笔终点未创新高，不等于从同一起点无法形成五笔段。
    p = [0,10,4,8,2,12,6,11,5]
    for sign in (1, -1):
        seq = legs([sign*x for x in p])
        r = build_segments(seq)
        check(f'initial_five_stroke_span_sign_{sign}',
              bool(r.segments and r.segments[0].first_stroke == 0 and r.segments[0].last_stroke == 4),
              'first segment spans strokes 0..4, confirmed by stroke 7',
              dict(prices=[sign*x for x in p], valid_span=_valid_span(seq, 0, 5),
                   turn_evidence=_turn_evidence(seq, 0, 5), skipped=r.skipped_prefix_strokes,
                   segments=list(map(signature, r.segments))))

    # 新增 K 线只延长最后一个合并块，三条笔端点均未变化。
    p = [1,0,2,4,6,8,10,8,6,4,2,0,2,4,6,8,10,8,6]
    raw = [candle(i, x, x+.5) for i, x in enumerate(p)]
    a = analyze(raw, 1)['blocks'][0]['strokes']
    b = analyze(raw+[candle(19,6.1,6.4),candle(20,6.2,6.3)], 1)['blocks'][0]['strokes']
    same = [(s.start, s.end, s.provisional) for s in a] == [(s.start, s.end, s.provisional) for s in b]
    check('stable_stroke_confirmation_time', same and a[1].confirmed_at == b[1].confirmed_at,
          'unchanged stroke keeps its already available confirmation evidence',
          dict(same_geometry=same, before=a[1].confirmed_at, after=b[1].confirmed_at))

    # 公共 API 允许给每笔独立 confirmed_at；所有依赖都要可用。
    seq = legs([0,8,4,10,6,9,5])
    seq[0] = replace(seq[0], confirmed_at=BASE+timedelta(days=10))
    r = build_segments(seq)
    check('segment_waits_for_all_input_evidence', r.segments[0].evidence_at >= seq[0].confirmed_at,
          'segment evidence >= each dependent stroke confirmation',
          dict(segment_evidence=r.segments[0].evidence_at, first_stroke_evidence=seq[0].confirmed_at))

    # 一笔破坏之后旧段可以延续，但发起破坏的历史笔仍应保留标记。
    p = [0,8,4,10,-2,9,7,8,6,11,5,10,4]
    short, full = block(legs(p[:8])), block(legs(p))
    check('direct_break_history_preserved', 3 in marked(short) and 3 in marked(full),
          'stroke 3 (10 -> -2) remains marked after segment extension',
          dict(short_marked=marked(short), full_marked=marked(full)))

    # 当前公共导出已标记，通用 SVG 入口也应遵守同一显示约定。
    fixture = block(legs([0,8,4,10,2,7,1]))
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/'chart.html'
        rows = [candle(i, -1, 12) for i in range(25)]
        write_chart(path, {'1h': dict(blocks=[fixture], method='feature_sequence_recursive')}, {'1h': rows})
        html = path.read_text()
        check('generic_svg_direct_break_width', 'stroke-width="3.2"' in html,
              'attacking stroke uses width 3.2 with original color and dash state',
              dict(export_marked=marked(fixture), has_width_3_2='stroke-width="3.2"' in html))

    # 固定种子增加测试覆盖；计数按序列，不把多个失败前缀重复计数。
    rng = random.Random(20261011)
    failures = []
    for case in range(500):
        p = [100]
        for i in range(40):
            p.append(p[-1]+(1 if i%2 == 0 else -1)*rng.randint(1,25))
        seq = legs(p)
        full = build_segments(seq).segments
        for stop in sorted({s.evidence_stroke+1 for s in full}):
            prefix = build_segments(seq[:stop]).segments
            if prefix != [s for s in full if s.evidence_stroke < stop]:
                failures.append(dict(case=case, stop=stop, prices=p))
                break

    # 独立几何约束：包含消除、笔交替/连续、间距、全跨度极值、末笔状态及价格镜像。
    stroke_rng = random.Random(62)
    stroke_failures = []
    for case in range(500):
        price = 100
        rows = []
        for i in range(120):
            price += stroke_rng.randint(-8, 8)
            rows.append(candle(i, price-stroke_rng.randint(0, 5), price+stroke_rng.randint(0, 5)))
        bars = merge_inclusions(rows)
        fs = find_fractals(bars[:-1])
        ss = build_strokes(fs, bars)
        try:
            for a,b in zip(bars, bars[1:]):
                assert not (a.low <= b.low and a.high >= b.high or b.low <= a.low and b.high >= a.high)
            for i,s in enumerate(ss):
                span = bars[s.start.index:s.end.index+1]
                assert s.end.index-s.start.index >= 4
                assert s.low == min(b.low for b in span) and s.high == max(b.high for b in span)
                assert s.start.kind != s.end.kind and s.start.time < s.end.time
                assert s.provisional == (i == len(ss)-1)
                if i:
                    assert ss[i-1].end == s.start and ss[i-1].direction != s.direction
            reflected = [Candle(c.time, -c.open, -c.low, -c.high, -c.close, c.volume) for c in rows]
            rb = merge_inclusions(reflected, 'down')
            rs = build_strokes(find_fractals(rb[:-1]), rb)
            assert [(s.start.index,s.end.index,-s.start.price,-s.end.price) for s in ss] == [
                (s.start.index,s.end.index,s.start.price,s.end.price) for s in rs]
        except AssertionError:
            stroke_failures.append(case)

    market = []
    samples = [
        ('reports/BTCUSDT_20260626_20261010_4h_2h_1h/history_4h.csv', 4),
        ('reports/BTCUSDT_20260626_20261010_4h_2h_1h/history_1h.csv', 1),
        ('reports/STRKUSDT_1h_20260817_20261010/history_1h.csv', 1),
        ('reports/ZECUSDT_month_20261008/history_1h.csv', 1),
    ]
    for relative, hours in samples:
        path = ROOT/relative
        raw = read_csv(path)
        times = [c.time for c in raw]
        assert all(b.time-a.time == timedelta(hours=hours) for a,b in zip(raw, raw[1:]))
        merged = merge_inclusions(raw)
        seq = build_strokes(find_fractals(merged[:-1]), merged)
        result = build_segments(seq)
        candidates = [(i, merged[seq[i+1].end.index+2]) for i,s in enumerate(seq)
                      if not s.provisional and merged[seq[i+1].end.index+2].start != merged[seq[i+1].end.index+2].end]
        verified = []
        for i, witness in candidates[:10]:
            observed = []
            for t in (witness.start, witness.end):
                prefix = raw[:bisect_left(times, t)+1]
                local = analyze(prefix, hours)['blocks'][0]['strokes']
                match = next((j for j,s in enumerate(local) if s.start == seq[i].start
                              and s.end == seq[i].end and not s.provisional), None)
                observed.append(None if match is None else local[match].confirmed_at)
            if all(observed) and observed[0] != observed[1]:
                verified.append(dict(stroke=i, start=seq[i].start.time,
                                     end=seq[i].end.time, evidence_before=observed[0], evidence_after=observed[1]))
        market.append(dict(path=relative, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                           bars=len(raw), strokes=len(seq), segments=len(result.segments),
                           skipped_prefix=result.skipped_prefix_strokes,
                           backward_segment_evidence=sum(b.evidence_stroke<a.evidence_stroke
                               for a,b in zip(result.segments, result.segments[1:])),
                           mutable_witness_candidates=len(candidates),
                           checked_witnesses=min(10,len(candidates)), verified_time_drifts=verified))

    cases = verify()
    output = dict(checks=checks, failed_checks=sum(not c['passed'] for c in checks),
                  stroke_geometry=dict(seed=62, sequences=500, candles_per_sequence=120,
                                       failing_cases=stroke_failures),
                  randomized=dict(seed=20261011, sequences=500, failing_sequences=len(failures),
                                  first_examples=failures[:3]), market_samples=market,
                  reference_cases=cases['summary'], historical_fully_reproduced=cases['historical_fully_reproduced'],
                  source_sha256={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                 for name in ('chan.py','chan_structure.py','report_btc_4h_auto.py')})
    Path(__file__).with_name('current_results.json').write_text(
        json.dumps(output, default=json_default, ensure_ascii=False, indent=2)+'\n')
    for c in checks:
        print(('PASS' if c['passed'] else 'FAIL')+' '+c['name'])
    print('Random prefix consistency:', len(failures), '/ 500 sequences failed')
    print('Stroke geometry and reflection:', len(stroke_failures), '/ 500 sequences failed')
    print('Saved:', Path(__file__).with_name('current_results.json'))
    return int(any(not c['passed'] for c in checks) or bool(failures) or bool(stroke_failures))


if __name__ == '__main__':
    raise SystemExit(main())
