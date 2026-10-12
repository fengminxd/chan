"""冻结输入的段转折审计；不以重跑同一函数作为唯一正确性证据。

独立核验笔跨度、特征元素证据和时间依赖；穷举短序列、随机长序列，
并检查所有采样前缀和价格镜像。运行结果写入本目录 results.json。
"""
from dataclasses import asdict
from datetime import datetime, timezone
from itertools import product
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chan import Fractal, Stroke, json_default, parse_time
from chan_structure import build_segments
from test_chan import strokes

FOLDER = ROOT / 'reports/BTCUSDT_20260606_current_tradingview'


def normalized(seq, indices, up):
    """独立区间折叠，仅核对证据；不调用生产特征序列/分型/跨度函数。"""
    result = []
    for i in indices:
        low, high = seq[i].low, seq[i].high
        if result and ((low <= result[-1][0] and high >= result[-1][1]) or
                       (low >= result[-1][0] and high <= result[-1][1])):
            old = result.pop()
            rising = up if not result else old[1] > result[-1][1]
            select = max if rising else min
            result.append((select(low, old[0]), select(high, old[1]), old[2] + (i,)))
        else:
            result.append((low, high, (i,)))
    return result


def is_fractal(items, top):
    a, b, c = items
    return all(b[j] > max(a[j], c[j]) if top else b[j] < min(a[j], c[j]) for j in (0, 1))


def span(seq, start, stop):
    part = seq[start:stop]
    if len(part) < 3 or len(part) % 2 != 1:
        return False
    return (max(s.low for s in part[:3]) <= min(s.high for s in part[:3]) and
            (part[-1].end.price == max(s.high for s in part) if part[0].direction == 'up'
             else part[-1].end.price == min(s.low for s in part)))


def proof(seq, result):
    for pos, seg in enumerate(result.segments):
        start, turn = seg.first_stroke, seg.last_stroke + 1
        local, published = seg.local_evidence_stroke, seg.evidence_stroke
        assert span(seq, start, turn)
        assert (seg.start, seg.end) == (seq[start].start, seq[turn].start)
        assert seg.low == min(s.low for s in seq[start:turn])
        assert seg.high == max(s.high for s in seq[start:turn])
        assert turn + 2 <= local <= published < result.usable_stroke_count
        assert any(span(seq, turn, end) for end in range(turn + 3, local + 2, 2))
        assert all(not s.provisional for s in seq[:published + 1])
        assert seg.evidence_at == max(max(s.end.time, s.confirmed_at or s.end.time)
                                      for s in seq[:published + 1])
        assert all(i <= local for group in seg.feature_indices + seg.reverse_feature_indices for i in group)
        if pos:
            prev = result.segments[pos - 1]
            assert prev.end == seg.start and prev.direction != seg.direction
            assert published == max(local, prev.evidence_stroke)
        else:
            assert published == local
        up = seg.direction == 'up'
        peak = seg.end.price
        assert all(s.high <= peak if up else s.low >= peak for s in seq[turn:local + 1])
        left = normalized(seq, range(start + 1, turn, 2), up)[-1]
        first = seq[turn]
        gap = first.low > left[1] if up else first.high < left[0]
        direct = first.low < left[0] if up else first.high > left[1]
        if direct:
            assert not gap and seg.confirmation == 'no_gap'
            assert seq[local].low < first.low if up else seq[local].high > first.high
            assert seg.feature_indices == (left[2], (turn,), (local,))
        else:
            # 第一分型必须在公布的局部证据前真实存在，且引用来源一致。
            found = False
            for end in range(turn + 3, local + 2, 2):
                right = normalized(seq, range(turn, end, 2), up)
                tri = [left, *right[:2]]
                if len(tri) == 3 and is_fractal(tri, up):
                    found |= tuple(x[2] for x in tri) == seg.feature_indices
            assert found
            assert seg.confirmation == ('gap_reverse_fractal' if gap else 'no_gap')
        if gap:
            found = False
            for end in range(turn + 2, local + 2, 2):
                second = normalized(seq, range(turn + 1, end, 2), not up)
                for j in range(len(second) - 2):
                    tri = second[j:j + 3]
                    if is_fractal(tri, not up):
                        found |= tuple(x[2] for x in tri) == seg.reverse_feature_indices
            assert found
        else:
            assert not seg.reverse_feature_indices
    for event in result.direct_breaks:
        if event['reason'] == 'confirmed_direct_break':
            assert event['reverse_span_ready'] is True
    if result.pending:
        pending = result.pending
        assert not pending.confirmed and span(seq, pending.first_stroke, pending.last_stroke + 1)
        assert pending.last_stroke < result.usable_stroke_count
        if result.segments:
            assert result.segments[-1].end == pending.start


def check(seq, all_prefixes=False):
    full = build_segments(seq)
    proof(seq, full)
    stops = (range(3, len(seq) + 1) if all_prefixes else
             sorted({s.evidence_stroke + 1 for s in full.segments} | set(range(3, len(seq), 7))))
    previous_marks = set()
    for stop in stops:
        prefix = build_segments(seq[:stop])
        assert prefix.segments == [s for s in full.segments if s.evidence_stroke < stop]
        marks = {e['turn_stroke'] for e in prefix.direct_breaks}
        assert previous_marks <= marks
        previous_marks = marks
    return full


def signature(result):
    return [(s.first_stroke, s.last_stroke, s.evidence_stroke, s.confirmation,
             s.feature_indices, s.reverse_feature_indices) for s in result.segments]


def main():
    counts = {'exhaustive_sequences': 0, 'random_sequences': 0, 'confirmed_proofs': 0}
    for steps in product((2, 5, 11), repeat=9):
        prices = [100, 108]
        for i, size in enumerate(steps):
            prices.append(prices[-1] + (-1 if i % 2 == 0 else 1) * size)
        result = check(strokes(prices), all_prefixes=True)
        reflected = build_segments(strokes([-p for p in prices]))
        proof(strokes([-p for p in prices]), reflected)
        assert signature(result) == signature(reflected)
        counts['exhaustive_sequences'] += 1
        counts['confirmed_proofs'] += len(result.segments) + len(reflected.segments)
    print('Exhaustive:', counts, flush=True)
    rng = random.Random(20261012)
    for _ in range(1000):
        prices = [100]
        for i in range(60):
            prices.append(prices[-1] + (1 if i % 2 == 0 else -1) * rng.randint(1, 30))
        seq = strokes(prices)
        result = check(seq)
        mirror = build_segments(strokes([1000 - p for p in prices]))
        proof(strokes([1000 - p for p in prices]), mirror)
        assert signature(result) == signature(mirror)
        counts['random_sequences'] += 1
        counts['confirmed_proofs'] += len(result.segments) + len(mirror.segments)
    print('Random:', counts, flush=True)
    market = {}
    for hours in (8, 4, 2, 1):
        path = FOLDER / f'analysis_{hours}h.json'
        report = json.loads(path.read_text())
        blocks = []
        for block in report['blocks']:
            def point(d):
                return Fractal(d['index'], d['kind'], d['price'], parse_time(d['time']))
            seq = [Stroke(point(s['start']), point(s['end']), s['provisional'],
                          parse_time(s['confirmed_at']) if s['confirmed_at'] else None)
                   for s in block['strokes']]
            result = check(seq)
            serialized = json.loads(json.dumps([asdict(s) for s in result.segments], default=json_default))
            assert serialized == block['segments'], 'audit fix must not change segment geometry/evidence'
            assert [e['turn_stroke'] for e in result.direct_breaks] == [e['turn_stroke'] for e in block['direct_breaks']]
            tail = dict(strokes=[asdict(s) for s in seq[-12:]],
                        confirmed=[asdict(s) for s in result.segments[-3:]],
                        pending=asdict(result.pending) if result.pending else None,
                        diagnostics=result.tail_diagnostics)
            blocks.append(dict(strokes=len(seq), segments=len(result.segments), tail=tail))
            counts['confirmed_proofs'] += len(result.segments)
        market[f'{hours}h'] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), blocks=blocks)
        print('Market:', hours, 'passed', flush=True)
    source = json.loads((FOLDER / 'source.json').read_text())
    output = dict(passed=True, seed=20261012, counts=counts, market=market,
                  audited_at=datetime.now(timezone.utc), source_asof=source['as_of'],
                  limitations=['固定已完成笔前缀；不承诺 K 线选笔永不重绘',
                               '规则重建与不变量检验；不等同于原课所有图例逐点复现',
                               '反向合法跨度是本项目的额外确认政策'],
                  code_sha256={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest()
                               for p in ('chan.py', 'chan_structure.py', 'test_structure.py')})
    Path(__file__).with_name('results.json').write_text(json.dumps(output, ensure_ascii=False,
                                                                 indent=2, default=json_default) + '\n')
    print('PASS', counts)


if __name__ == '__main__':
    main()
