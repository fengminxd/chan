"""缠论特征序列线段与递归中枢。Python 3.10+，规则见 README.md。"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from chan_structure import build_hierarchy, build_segments, direct_break_marks
from chan_theory import analyze_theory
from chan_policy import AnalysisPolicy
from chan_signals import decision_layers, volume_price
from chan_time import PERIODS, PERIOD_MINUTES, compatible_periods, period_delta, period_minutes, period_name

UTC = timezone.utc


@dataclass(frozen=True)
class Candle:
    time: datetime  # 开盘时间；输入必须为已收盘或能由 as_of 判断收盘的 K 线
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class MergedBar:
    start: datetime
    end: datetime
    high: float
    low: float
    high_time: datetime
    low_time: datetime


@dataclass(frozen=True)
class Fractal:
    index: int
    kind: str  # top / bottom
    price: float
    time: datetime


@dataclass(frozen=True)
class Stroke:
    start: Fractal
    end: Fractal
    provisional: bool = False
    confirmed_at: datetime | None = None

    @property
    def low(self) -> float:
        return min(self.start.price, self.end.price)

    @property
    def high(self) -> float:
        return max(self.start.price, self.end.price)

    @property
    def direction(self) -> str:
        return "up" if self.end.price > self.start.price else "down"


@dataclass
class Center:
    start: datetime
    end: datetime
    formed_at: datetime  # 第三笔端点时间，并非实时可知/确认时间
    zd: float
    zg: float
    dd: float
    gg: float
    first_stroke: int
    last_stroke: int
    stroke_count: int
    status: str = "extending"
    break_stroke: int | None = None


def parse_time(value: str) -> datetime:
    """ISO 8601；无时区的输入按 UTC 解释。"""
    t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def validate_candles(candles: Sequence[Candle]) -> None:
    previous = None
    for c in candles:
        if c.time.tzinfo is None or c.time.utcoffset() is None:
            raise ValueError("K 线时间必须带时区")
        if previous is not None and c.time <= previous:
            raise ValueError("K 线时间必须严格递增，不能重复")
        values = (c.open, c.high, c.low, c.close, c.volume)
        if not all(math.isfinite(x) for x in values):
            raise ValueError(f"{c.time}: OHLCV 不能包含 NaN 或无穷大")
        if not c.low <= min(c.open, c.close) <= max(c.open, c.close) <= c.high:
            raise ValueError(f"{c.time}: OHLC 高低价关系错误")
        if c.volume < 0:
            raise ValueError(f"{c.time}: 成交量不能小于零")
        previous = c.time


def read_csv(path: str | Path) -> list[Candle]:
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required = {"timestamp", "open", "high", "low", "close"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("CSV 必须有 timestamp,open,high,low,close 列；volume 可选")
        candles = []
        for line, row in enumerate(reader, 2):
            try:
                candles.append(Candle(parse_time(row["timestamp"]),
                                      *(float(row[k]) for k in ("open", "high", "low", "close")),
                                      float(row.get("volume") or 0)))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"CSV 第 {line} 行无效: {exc}") from exc
    if not candles:
        raise ValueError("CSV 没有 K 线数据")
    validate_candles(candles)
    return candles


def resample(candles: Sequence[Candle], source_hours: int, target_hours: int,
             as_of: datetime, anchor_hour: int = 0) -> tuple[list[Candle], dict]:
    """按 UTC 固定时间桶合并。缺根的桶丢弃，不填充价格。"""
    validate_candles(candles)
    source_minutes, target_minutes = period_minutes(source_hours), period_minutes(target_hours)
    if target_minutes < source_minutes or target_minutes % source_minutes:
        raise ValueError("不能从大周期生成小周期，也不能合并非整数倍周期")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of 必须带时区")
    if not 0 <= anchor_hour <= 23:
        raise ValueError("anchor_hour 必须在 0..23")
    anchor = datetime(1970, 1, 1, anchor_hour, tzinfo=UTC)
    step = timedelta(minutes=source_minutes)
    width = timedelta(minutes=target_minutes)
    groups: dict[datetime, list[Candle]] = {}
    excluded_open = 0
    for c in candles:
        if (c.time - anchor) % step != timedelta(0):
            raise ValueError(f"{c.time}: 时间未对齐 {source_hours}h 开盘时间；检查时区及 anchor_hour")
        if c.time + step > as_of:
            excluded_open += 1
            continue
        bucket = anchor + ((c.time - anchor) // width) * width
        groups.setdefault(bucket, []).append(c)
    result = []
    dropped = 0
    for bucket, items in groups.items():
        expected = [bucket + i * step for i in range(target_minutes // source_minutes)]
        if [c.time for c in items] != expected or bucket + width > as_of:
            dropped += 1
            continue
        result.append(Candle(bucket, items[0].open, max(c.high for c in items),
                             min(c.low for c in items), items[-1].close,
                             sum(c.volume for c in items)))
    return result, {"excluded_unclosed_source_bars": excluded_open,
                    "dropped_incomplete_buckets": dropped}


def split_contiguous(candles: Sequence[Candle], hours: int) -> list[list[Candle]]:
    runs: list[list[Candle]] = []
    step = period_delta(hours)
    for c in candles:
        if not runs or c.time - runs[-1][-1].time != step:
            runs.append([])
        runs[-1].append(c)
    return runs


def merge_inclusions(candles: Sequence[Candle], initial_direction: str = "up") -> list[MergedBar]:
    """顺序消除包含：向上取高高/低高，向下取高低/低低。"""
    if initial_direction not in ("up", "down"):
        raise ValueError("initial_direction 必须是 up 或 down")
    merged: list[MergedBar] = []
    direction = initial_direction
    for c in candles:
        new = MergedBar(c.time, c.time, c.high, c.low, c.time, c.time)
        if not merged:
            merged.append(new)
            continue
        old = merged[-1]
        contains = ((old.high >= new.high and old.low <= new.low)
                    or (new.high >= old.high and new.low <= old.low))
        if not contains:
            direction = "up" if new.high > old.high else "down"
            merged.append(new)
            continue
        take_high = new.high >= old.high if direction == "up" else new.high <= old.high
        take_low = new.low >= old.low if direction == "up" else new.low <= old.low
        merged[-1] = MergedBar(old.start, new.end,
                               new.high if take_high else old.high,
                               new.low if take_low else old.low,
                               new.high_time if take_high else old.high_time,
                               new.low_time if take_low else old.low_time)
    return merged


def find_fractals(bars: Sequence[MergedBar]) -> list[Fractal]:
    result = []
    for i in range(1, len(bars) - 1):
        left, mid, right = bars[i - 1:i + 2]
        if mid.high > max(left.high, right.high) and mid.low > max(left.low, right.low):
            result.append(Fractal(i, "top", mid.high, mid.high_time))
        elif mid.low < min(left.low, right.low) and mid.high < min(left.high, right.high):
            result.append(Fractal(i, "bottom", mid.low, mid.low_time))
    return result


def build_strokes(fractals: Sequence[Fractal], bars: Sequence[MergedBar],
                  min_span: int = 4, diagnostics: dict | None = None) -> list[Stroke]:
    """保留合法端点备选的有向图选笔，不因未成笔的短波动删除历史。

    每条边仍须满足原有间距、方向和全跨度极值约束。采用明确的选择政策：
    最早可成笔起点、从该起点可达的最新终点；同一起点/终点选择笔数最多
    的合法路径，同分选择最近前驱。路径可修订，但不混接不相连的根节点。
    """
    if min_span < 4:
        raise ValueError("min_span 至少为 4（含端点至少 5 根去包含 K 线）")
    parents: list[int | None] = []
    lengths: list[int] = []
    roots: list[int] = []
    for i, f in enumerate(fractals):
        if not 0 <= f.index < len(bars) or f.kind not in ("top", "bottom"):
            raise ValueError("分型索引或类型无效")
        if i and (f.index <= fractals[i-1].index or f.time <= fractals[i-1].time):
            raise ValueError("分型索引及时间必须严格递增")
        if f.price != (bars[f.index].high if f.kind == "top" else bars[f.index].low):
            raise ValueError("分型价格与去包含 K 线极值不符")
        low, high, pos = bars[f.index].low, bars[f.index].high, f.index
        candidates = []
        for j in range(i - 1, -1, -1):
            a = fractals[j]
            for k in range(pos - 1, a.index - 1, -1):
                low, high = min(low, bars[k].low), max(high, bars[k].high)
            pos = a.index
            # 当前终点已不再是该方向极值，更早的起点也不可能合法。
            if f.kind == "top" and high > f.price or f.kind == "bottom" and low < f.price:
                break
            if a.kind == f.kind or f.index - a.index < min_span:
                continue
            if ((f.kind == "top" and a.price == low and a.price < f.price)
                    or (f.kind == "bottom" and a.price == high and a.price > f.price)):
                candidates.append(j)
        parent = min(candidates, key=lambda j: (roots[j], -lengths[j], -j)) if candidates else None
        parents.append(parent)
        lengths.append(lengths[parent] + 1 if parent is not None else 0)
        roots.append(roots[parent] if parent is not None else i)
    reachable = [i for i, length in enumerate(lengths) if length]
    chosen = []
    root = None
    if reachable:
        root = min(roots[i] for i in reachable)
        last = max(i for i in reachable if roots[i] == root)
        cursor = last
        while cursor is not None:
            chosen.append(fractals[cursor])
            cursor = parents[cursor]
        chosen.reverse()
    if diagnostics is not None:
        diagnostics.update(policy="earliest_origin_latest_reachable_max_edges_v1",
                           fractal_count=len(fractals), selected_point_count=len(chosen),
                           selected_start=chosen[0].time if chosen else None,
                           selected_end=chosen[-1].time if chosen else None,
                           skipped_prefix_fractals=root if root is not None else len(fractals),
                           unreachable_tail_fractals=len(fractals)-last-1 if reachable else len(fractals),
                           status="selected" if reachable else "no_valid_stroke",
                           note="末笔候选；不相连的新根不会替换已保留历史；不可达尾部显式报告")
    return [Stroke(a, b, provisional=(i == len(chosen) - 2))
            for i, (a, b) in enumerate(zip(chosen, chosen[1:]))]


def find_centers(strokes: Sequence[Stroke]) -> list[Center]:
    """三笔重叠候选；固定 ZD/ZG，反向笔完全离开区间时结束。

    这是笔级代理规则，不是完整的次级别走势类型递归算法。
    """
    centers: list[Center] = []
    active: Center | None = None
    seed_start = 0
    for i, s in enumerate(strokes):
        if s.provisional:
            break
        if i and (strokes[i - 1].end != s.start or strokes[i - 1].direction == s.direction):
            raise ValueError("中枢输入必须为端点连续、方向交替的笔")
        if active is not None:
            # 向上离开后的回落笔完全在 ZG 上，或向下离开后的反弹笔完全在 ZD 下。
            above = s.direction == "down" and s.low > active.zg
            below = s.direction == "up" and s.high < active.zd
            if above or below:
                active.status = "left_up" if above else "left_down"
                active.break_stroke = i
                # 下一中枢允许从上一中枢最后的离开笔开始形成。
                seed_start = i - 1
                active = None
            else:
                active.end = s.end.time
                active.last_stroke = i
                active.stroke_count += 1
                active.dd = min(active.dd, s.low)
                active.gg = max(active.gg, s.high)
                continue
        if i - seed_start < 2:
            continue
        triple = strokes[i - 2:i + 1]
        zd, zg = max(x.low for x in triple), min(x.high for x in triple)
        if zd >= zg:  # 单点接触不算有宽度的中枢
            continue
        active = Center(triple[0].start.time, s.end.time, s.end.time, zd, zg,
                        min(x.low for x in triple), max(x.high for x in triple),
                        i - 2, i, 3)
        centers.append(active)
    return centers


def price_position(price: float, center: Center) -> str:
    return "above" if price > center.zg else "below" if price < center.zd else "inside"


def analyze(candles: Sequence[Candle], hours: int, min_span: int = 4,
            initial_direction: str = "up", mode: str = "strict", max_levels: int = 5,
            zero_axis_ratio: float = 0.2, strength_fast: int = 5,
            strength_slow: int = 10) -> dict:
    policy = AnalysisPolicy(min_span, initial_direction, mode, max_levels,
                            zero_axis_ratio, strength_fast, strength_slow)
    validate_candles(candles)
    period_minutes(hours)
    if min_span < 4:
        raise ValueError("min_span 至少为 4")
    if mode not in ("strict", "bi"):
        raise ValueError("mode 必须为 strict 或 bi")
    if not 1 <= max_levels <= 20:
        raise ValueError("max_levels 必须在 1..20")
    if not math.isfinite(zero_axis_ratio) or not 0 <= zero_axis_ratio <= 1:
        raise ValueError("zero_axis_ratio 必须在 0..1")
    if not isinstance(strength_fast, int) or not isinstance(strength_slow, int) or not 1 <= strength_fast < strength_slow:
        raise ValueError("均线力度窗口须为整数且满足 1 <= fast < slow")
    runs = split_contiguous(candles, hours)
    blocks = []
    for run in runs:
        merged = merge_inclusions(run, initial_direction)
        # 最后一根去包含 K 线仍可能被之后的 K 线合并，不用于确认分型。
        fractals = find_fractals(merged[:-1])
        stroke_diagnostics = {}
        strokes = build_strokes(fractals, merged, min_span, stroke_diagnostics)
        # 下一笔端点的分型已有稳定右邻，才把前一笔作为结构输入。
        # 这是当前选笔快照的证据时刻，不承诺未来不会因撤笔而修订。
        # 证明块首根原始 K 线收盘时，右邻已稳定；后续包含不能推迟既有证据。
        strokes = [replace(s, confirmed_at=merged[strokes[i + 1].end.index + 2].start + period_delta(hours))
                   if not s.provisional else s for i, s in enumerate(strokes)]
        block = {"start": run[0].time, "end": run[-1].time,
                 "bar_count": len(run), "merged_count": len(merged),
                 "fractals": fractals, "strokes": strokes, "stroke_diagnostics": stroke_diagnostics}
        if mode == "strict":
            structure = build_segments(strokes)
            hierarchy = build_hierarchy(structure.segments, max_levels)
            centers = [z for layer in hierarchy["levels"] for z in layer["centers"]]
            block.update(segments=structure.segments, pending_segment=structure.pending,
                         direct_breaks=structure.direct_breaks,
                         segment_diagnostics={"skipped_prefix_strokes": structure.skipped_prefix_strokes,
                                              "unassigned_from": structure.unassigned_from,
                                              "usable_stroke_count": structure.usable_stroke_count,
                                              "tail": structure.tail_diagnostics},
                         hierarchy=hierarchy, theory=analyze_theory(hierarchy, run, zero_axis_ratio,
                                                                  hours, max_levels, strength_fast, strength_slow))
        else:
            centers = find_centers(strokes)
        block["centers"] = centers
        blocks.append(block)
    recent = blocks[-1]["centers"] if blocks else []
    # 默认位置摘要对应最低结构层 L1；高层各自提供摘要，不覆盖低层。
    basic = [z for z in recent if getattr(z, "level", 1) == 1]
    latest = basic[-1] if basic else None
    by_level = {}
    for z in recent:
        by_level[f"L{getattr(z, 'level', 1)}"] = {
            "zd": z.zd, "zg": z.zg, "end": z.end,
            "last_close_position": price_position(candles[-1].close, z)}
    return {"period": period_name(hours), "method": "feature_sequence_recursive" if mode == "strict" else "bi_overlap_proxy",
            "rule_manifest": policy.manifest(), "decision_layers": decision_layers(blocks),
            "volume_price": volume_price(runs[-1] if runs else [], hours),
            "bar_count": len(candles), "gap_count": max(0, len(runs) - 1),
            "has_center": any(b["centers"] for b in blocks),
            "latest_block_has_center": latest is not None,
            "last_close": candles[-1].close if candles else None,
            "last_close_position": price_position(candles[-1].close, latest) if latest else None,
            "latest_center_by_level": by_level,
            "blocks": blocks}


def json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError(type(value).__name__)


def write_chart(path: Path, results: dict, candles_by_period: dict[str, list[Candle]]) -> None:
    """生成离线 HTML/SVG，包含 K 线、笔和中枢，可横向滚动查看。"""
    sections = []
    labels = {"extending": "延伸中", "left_up": "向上离开", "left_down": "向下离开", "formed": "已形成"}
    for period, result in results.items():
        candles = candles_by_period[period]
        if not candles:
            sections.append(f"<h2>{period}：无完整 K 线</h2>")
            continue
        width, height = max(1000, len(candles) * 7 + 100), 440
        lo, hi = min(c.low for c in candles), max(c.high for c in candles)
        pad = (hi - lo) * 0.08 or max(abs(hi) * 0.01, 1)
        lo, hi = lo - pad, hi + pad
        positions = {c.time: i for i, c in enumerate(candles)}

        def x(t):
            return 70 + positions[t] * (width - 100) / max(len(candles) - 1, 1)

        def y(p):
            return 20 + (hi - p) / (hi - lo) * (height - 65)

        svg = [f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
               'xmlns="http://www.w3.org/2000/svg">']
        for j in range(6):
            price = lo + (hi - lo) * j / 5
            svg.append(f'<path d="M65 {y(price):.2f} H{width}" stroke="#e2e8f0"/>'
                       f'<text x="2" y="{y(price):.2f}" font-size="11">{price:.5g}</text>')
        rows = []
        for block in result["blocks"]:
            for z in block["centers"]:
                level = getattr(z, "level", 0)
                level_name = f"L{level}" if level else "笔中枢"
                colors = ("#d97706", "#7c3aed", "#0891b2", "#be185d")
                color = colors[max(0, level - 1) % len(colors)]
                label = f'{level_name} ZD={z.zd:.8g} ZG={z.zg:.8g} {labels[z.status]}'
                svg.append(f'<rect x="{x(z.start):.2f}" y="{y(z.zg):.2f}" '
                           f'width="{max(2, x(z.end)-x(z.start)):.2f}" '
                           f'height="{max(1, y(z.zd)-y(z.zg)):.2f}" fill="{color}" '
                           f'fill-opacity="0.12" stroke="{color}"><title>{label}</title></rect>')
                low = z.range_low if level else z.dd
                high = z.range_high if level else z.gg
                evidence = z.evidence_at.isoformat() if level else "—"
                rows.append(f'<tr><td>{level_name}</td><td>{z.start.isoformat()}</td><td>{z.end.isoformat()}</td>'
                            f'<td>{z.zd:.8g}</td><td>{z.zg:.8g}</td><td>{low:.8g}</td>'
                            f'<td>{high:.8g}</td><td>{evidence}</td><td>{labels[z.status]}</td></tr>')
        for c in candles:
            color = "#dc2626" if c.close >= c.open else "#059669"
            tooltip = html.escape(f'{c.time.isoformat()} O={c.open:g} H={c.high:g} L={c.low:g} C={c.close:g}')
            svg.append(f'<g><title>{tooltip}</title><path d="M{x(c.time):.2f} {y(c.high):.2f} '
                       f'V{y(c.low):.2f}" stroke="{color}"/><rect x="{x(c.time)-2:.2f}" '
                       f'y="{min(y(c.open), y(c.close)):.2f}" width="4" '
                       f'height="{max(1, abs(y(c.open)-y(c.close))):.2f}" fill="{color}"/></g>')
        for block in result["blocks"]:
            breaks = direct_break_marks(block)
            for i, s in enumerate(block["strokes"]):
                dash = ' stroke-dasharray="5 4"' if s.provisional else ''
                stroke_width = 3.2 if i in breaks else 1.5
                svg.append(f'<path d="M{x(s.start.time):.2f} {y(s.start.price):.2f} '
                           f'L{x(s.end.time):.2f} {y(s.end.price):.2f}" '
                           f'stroke="#2563eb" stroke-width="{stroke_width}"{dash}/>')
            for seg in block.get("segments", []) + ([block["pending_segment"]] if block.get("pending_segment") else []):
                dash = '' if seg.confirmed else ' stroke-dasharray="7 5"'
                label = html.escape(f"线段 {seg.first_stroke}..{seg.last_stroke} {seg.confirmation}")
                svg.append(f'<path d="M{x(seg.start.time):.2f} {y(seg.start.price):.2f} '
                           f'L{x(seg.end.time):.2f} {y(seg.end.price):.2f}" '
                           f'stroke="#0f172a" stroke-width="3"{dash}><title>{label}</title></path>')
        for i in sorted(set(round(j * (len(candles)-1) / 6) for j in range(7))):
            c = candles[i]
            svg.append(f'<text x="{x(c.time):.2f}" y="430" text-anchor="middle" '
                       f'font-size="10">{c.time:%m-%d %H:%M}</text>')
        svg.append('</svg>')
        table = ('<table><tr><th>层级</th><th>起点 UTC</th><th>终点 UTC</th><th>ZD</th><th>ZG</th>'
                 '<th>波动下沿</th><th>波动上沿</th><th>证据时刻 UTC</th><th>状态</th></tr>' + ''.join(rows) + '</table>')
        method = "线段与递归中枢" if result["method"] == "feature_sequence_recursive" else "笔中枢近似"
        theory_rows, signal_rows = [], []
        classification_names = {"insufficient": "结构不足", "consolidation": "盘整",
                                "uptrend": "上涨结构", "downtrend": "下跌结构",
                                "higher_level_consolidation": "扩展为高层盘整", "unresolved": "方向待定"}
        certainty_names = {"structural": "结构条件成立", "macd_candidate": "MACD 辅助候选",
                           "legacy_structural": "旧分解视角对照（不进入交易候选）",
                           "dependent_candidate": "依赖一类点候选",
                           "policy_confirmed": "结构＋均线力度策略确认",
                           "policy_dependent": "依赖均线力度一类点"}
        for block in result["blocks"]:
            for layer in block.get("theory", {}).get("levels", []):
                classification = classification_names[layer["classification"]]
                for z in layer["centers"]:
                    theory_rows.append(f'<tr><td>L{z.level}</td><td>{z.start.isoformat()}</td>'
                                       f'<td>{z.zd:.8g}</td><td>{z.zg:.8g}</td><td>{z.dd:.8g}</td>'
                                       f'<td>{z.gg:.8g}</td><td>{labels[z.status]}</td><td>{classification}</td></tr>')
                for s in layer["signals"]:
                    signal_rows.append(f'<tr><td>L{s.level}</td><td>{s.label}</td><td>{s.time.isoformat()}</td>'
                                       f'<td>{s.price:.8g}</td><td>{s.evidence_at.isoformat()}</td>'
                                       f'<td>{certainty_names[s.certainty]}</td></tr>')
            for layer in block.get("theory", {}).get("strict_divergence", {}).get("levels", []):
                for s in layer["signals"]:
                    signal_rows.append(f'<tr><td>L{s.level}</td><td>{s.label}</td><td>{s.time.isoformat()}</td>'
                                       f'<td>{s.price:.8g}</td><td>{s.evidence_at.isoformat()}</td>'
                                       f'<td>{certainty_names[s.certainty]}</td></tr>')
        theory_html = ('<details><summary>中枢延伸、扩展与买卖点证据</summary>'
                       '<p>上图为最早三段分解。完成走势递归及逐项背驰审计见下方；均线策略确认与 MACD 辅助分别标记。</p>'
                       '<table><tr><th>层级</th><th>中枢起点</th><th>ZD</th><th>ZG</th><th>DD</th>'
                       '<th>GG</th><th>状态</th><th>当前结构</th></tr>' + ''.join(theory_rows) + '</table>'
                       '<table><tr><th>层级</th><th>点位类型</th><th>端点 UTC</th><th>价格</th>'
                       '<th>证据时刻 UTC</th><th>证据类别</th></tr>' + ''.join(signal_rows) + '</table></details>')
        audit_rows, movement_rows = [], []
        for block in result["blocks"]:
            for layer in block.get("theory", {}).get("type_hierarchy", {}).get("levels", []):
                for m in layer["movements"] + ([layer["active_movement"]] if layer["active_movement"] else []):
                    movement_rows.append(f'<tr><td>L{m.level}</td><td>{m.kind}</td><td>{m.status}</td>'
                                         f'<td>{m.start.time.isoformat()}</td><td>{m.end.time.isoformat()}</td>'
                                         f'<td>{m.evidence_at.isoformat()}</td><td>{m.completion or "等待完成证据"}</td></tr>')
            for layer in block.get("theory", {}).get("strict_divergence", {}).get("levels", []):
                for d in layer["audits"]:
                    reasons = ", ".join(x["rule"] + ":" + x["state"] for x in d["checks"] if x["state"] != "pass")
                    audit_rows.append(f'<tr><td>L{d["level"]}</td><td>{d["side"]}</td><td>{d["state"]}</td>'
                                      f'<td>{d["end"].isoformat()}</td><td>{html.escape(reasons or "全部规则通过")}</td></tr>')
        theory_html += ('<details><summary>已完成走势递归与背驰规则审计</summary>'
                        '<p>confirmed_under_policy 仅指指定结构规则及完整均线面积策略通过。未完成走势不向上递归。</p>'
                        '<table><tr><th>层级</th><th>走势</th><th>状态</th><th>起点</th><th>终点</th>'
                        '<th>证据时刻</th><th>完成原因</th></tr>' + ''.join(movement_rows) + '</table>'
                        '<table><tr><th>层级</th><th>方向</th><th>审计状态</th><th>端点</th><th>未通过条件</th></tr>'
                        + ''.join(audit_rows) + '</table></details>')
        sections.append(f'<h2>{period} · {method} · {len(candles)} 根 K 线 · {len(rows)} 个中枢</h2>'
                        '<div class="chart">' + ''.join(svg) + '</div>' + (table if rows else '<p>未检出中枢，末端结构仍可能待确认。</p>')
                        + theory_html)
    path.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                    '<title>缠论线段与中枢分析</title><style>body{font:14px system-ui;margin:24px;color:#0f172a}'
                    '.chart{overflow-x:auto;border:1px solid #cbd5e1}table{border-collapse:collapse;margin:16px 0}'
                    'td,th{padding:8px;border:1px solid #cbd5e1}h2{margin-top:32px}</style>'
                    '<h1>多周期线段与中枢分析</h1><p>蓝线：笔；黑色粗线：线段；'
                    '虚线：候选结构，不参与递归；橙/紫/青色框：L1/L2/L3 中枢。红涨绿跌，时间为 UTC。</p>'
                    '<p>递归采用同级别最早三段分解，L 层级不等于 K 线周期。'
                    '这是当前选笔下的结构快照；端点时间与证据时间分开，后续数据可能修订选笔及相关划分。</p>'
                    + ''.join(sections) + '</html>', encoding='utf-8')


def demo_candles() -> list[Candle]:
    """多层合成行情，每笔 144 小时，使日线也有足够的去包含 K 线。"""
    begin = datetime(2022, 1, 1, tzinfo=UTC)
    anchors = [130.0, 100.0]
    for i in range(40):
        a = anchors[-1]
        b = (140 if i % 2 == 0 else 100) + 2 * math.sin(i * 0.8)
        anchors.extend([a + (b - a) * 0.65, a + (b - a) * 0.35, b])
    result = [Candle(begin, anchors[0], anchors[0] + 0.1, anchors[0] - 0.1, anchors[0], 100)]
    for a, b in zip(anchors, anchors[1:]):
        for j in range(1, 145):
            i = len(result)
            close, previous = a + (b - a) * j / 144, result[-1].close
            result.append(Candle(begin + timedelta(hours=i), previous,
                                 max(previous, close) + 0.1, min(previous, close) - 0.1, close, 100 + i % 37))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="分析 1m～1d 缠论结构；1m 输入默认使用统一递归树")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="OHLCV CSV 文件")
    source.add_argument("--demo", action="store_true", help="运行合成行情示例")
    parser.add_argument("--source-period", choices=PERIODS, default="1h")
    parser.add_argument("--periods", nargs="+", choices=PERIODS, help="默认分析可由输入周期整倍数合成的周期")
    parser.add_argument("--as-of", help="分析截止时刻 ISO 8601；默认当前 UTC 时间")
    parser.add_argument("--start", help="1m 统一模式的目标窗口起点；此前数据仍用于递归")
    parser.add_argument("--context-csv", help="1m 统一模式的前置行情文件")
    parser.add_argument("--min-context-bars", type=int, default=1440)
    parser.add_argument("--required-level", type=int, default=2)
    parser.add_argument("--require-history", action="store_true", help="统一模式历史检查未通过时返回 2")
    parser.add_argument("--independent-periods", action="store_true", help="显式使用旧的各周期独立分析，不构造共享树")
    parser.add_argument("--entry-plan", action="store_true", help="1m输入生成通用多周期开单候选")
    parser.add_argument("--entry-profile", choices=("combined", "matrix", "nested_1h"), default="combined",
                        help="默认同时分析4h/2h执行矩阵与1h逐级定位；也可分别单独运行")
    parser.add_argument("--previous-entry-plan", type=Path, help="前次entry_plan.json；仅在观察后记录触发")
    parser.add_argument("--entry-trigger-buffer", type=float, default=0.0, help="开单触发价缓冲，单位同报价")
    parser.add_argument("--entry-stop-buffer", type=float, default=0.0, help="开单止损参考缓冲，单位同报价")
    parser.add_argument("--entry-max-wait", type=int, default=60, help="开单计划最长等待分钟数")
    parser.add_argument("--anchor-hour", type=int, default=0, help="时间桶 UTC 起点小时，默认 0")
    parser.add_argument("--min-span", type=int, default=4, help="笔两端分型中心最小索引差，至少 4")
    parser.add_argument("--initial-direction", choices=("up", "down"), default="up")
    parser.add_argument("--mode", choices=("strict", "bi"), default="strict", help="默认特征序列与递归中枢；bi 为旧三笔近似")
    parser.add_argument("--max-levels", type=int, default=5, help="最高递归结构层级，1..20，默认 5")
    parser.add_argument("--zero-axis-ratio", type=float, default=0.2, help="MACD 回零辅助阈值；非原文精确定义")
    parser.add_argument("--strength-fast", type=int, default=5, help="吻间面积的短期 SMA 窗口，默认 5")
    parser.add_argument("--strength-slow", type=int, default=10, help="吻间面积的长期 SMA 窗口，默认 10")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    args = parser.parse_args()
    try:
        if args.previous_entry_plan and not args.entry_plan:
            raise ValueError("--previous-entry-plan 需要 --entry-plan")
        if args.entry_plan:
            from entry_plan import EntryPolicy, analyze_entries, write_entry_outputs
            from unified import merge_sources, minute_demo
            if args.source_period != "1m" or args.mode != "strict":
                raise ValueError("--entry-plan 需要 --source-period 1m --mode strict")
            if args.periods or args.start or args.require_history or args.anchor_hour:
                raise ValueError("开单计划周期由entry-profile确定且使用UTC整点；不接受periods/start/require-history/anchor-hour")
            if args.demo and args.context_csv:
                raise ValueError("合成演示不能拼接真实前置行情")
            candles = minute_demo() if args.demo else read_csv(args.csv)
            if args.context_csv:
                candles, _ = merge_sources(candles, read_csv(args.context_csv))
            as_of = parse_time(args.as_of) if args.as_of else (candles[-1].time + timedelta(minutes=1) if args.demo else datetime.now(UTC))
            previous = json.loads(args.previous_entry_plan.read_text()) if args.previous_entry_plan else None
            policy = AnalysisPolicy(args.min_span, args.initial_direction, args.mode, args.max_levels,
                                    args.zero_axis_ratio, args.strength_fast, args.strength_slow)
            report = analyze_entries(candles, as_of, previous, policy,
                                     EntryPolicy(args.entry_trigger_buffer, args.entry_stop_buffer, args.entry_max_wait, args.entry_profile))
            report["data_origin"] = "synthetic_demo" if args.demo else {"csv": args.csv, "context_csv": args.context_csv}
            write_entry_outputs(report, args.output_dir)
            print(f"主级别候选 {len(report['candidates'])} 个；可执行触发 {sum(p['actionable'] for p in report['plans'])} 个")
            print(f"开单计划：{args.output_dir / 'entry_plan.json'}\n报告：{args.output_dir / 'entry_plan.md'}")
            return
        if args.source_period == "1m" and not args.independent_periods:
            from unified import HistoryPolicy, analyze_unified, minute_demo, write_outputs
            if args.demo and args.context_csv:
                raise ValueError("合成演示不能拼接真实前置行情")
            candles = minute_demo() if args.demo else read_csv(args.csv)
            context = read_csv(args.context_csv) if args.context_csv else []
            as_of = parse_time(args.as_of) if args.as_of else (candles[-1].time+period_delta(PERIODS["1m"]) if args.demo else datetime.now(UTC))
            policy = AnalysisPolicy(args.min_span, args.initial_direction, args.mode, args.max_levels,
                                    args.zero_axis_ratio, args.strength_fast, args.strength_slow)
            report = analyze_unified(candles, as_of, parse_time(args.start) if args.start else None,
                                     args.periods, context, args.anchor_hour, policy,
                                     HistoryPolicy(args.min_context_bars, args.required_level))
            report["data_origin"] = "synthetic_demo" if args.demo else {"csv": args.csv, "context_csv": args.context_csv}
            write_outputs(report, args.output_dir)
            print(f"1m 统一递归树；{len(report['views'])} 个投影视图；历史状态：{report['history']['status']}")
            print(f"结果：{args.output_dir / 'analysis.json'}\n图表：{args.output_dir / 'chart.html'}")
            if args.require_history and report["history"]["status"] != "usable_under_checks":
                raise SystemExit(2)
            return
        if args.start or args.context_csv or args.require_history:
            raise ValueError("前置历史检查参数需要 --source-period 1m 的统一模式")
        if args.demo and args.source_period != "1h":
            raise ValueError("演示数据固定为 1h")
        candles = demo_candles() if args.demo else read_csv(args.csv)
        as_of = parse_time(args.as_of) if args.as_of else datetime.now(UTC)
        source_hours = PERIODS[args.source_period]
        periods = list(dict.fromkeys(args.periods or compatible_periods(source_hours)))
        results, series = {}, {}
        for period in periods:
            bars, quality = resample(candles, source_hours, PERIODS[period], as_of, args.anchor_hour)
            results[period] = analyze(bars, PERIODS[period], args.min_span, args.initial_direction,
                                      args.mode, args.max_levels, args.zero_axis_ratio,
                                      args.strength_fast, args.strength_slow)
            results[period]["data_quality"] = quality
            series[period] = bars
        report = {"schema_version": 4, "as_of": as_of, "source_period": args.source_period,
                  "source": "synthetic_demo" if args.demo else str(args.csv),
                  "parameters": {"min_span": args.min_span, "anchor_hour_utc": args.anchor_hour,
                                 "initial_direction": args.initial_direction, "mode": args.mode,
                                 "max_levels": args.max_levels, "zero_axis_ratio": args.zero_axis_ratio,
                                 "strength_policy": "cross_kiss_sma", "strength_fast": args.strength_fast,
                                 "strength_slow": args.strength_slow},
                  "scope": ("特征序列线段、已完成走势递归/重新分解、背驰结构审计及指定均线力度策略确认；MACD 辅助另列"
                            if args.mode == "strict" else "旧三笔重叠近似"),
                  "results": results}
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "analysis.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=json_default, allow_nan=False), encoding="utf-8")
        write_chart(args.output_dir / "chart.html", results, series)
        if args.demo:
            with (args.output_dir / "demo_1h.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
                for c in candles:
                    writer.writerow([c.time.isoformat(), c.open, c.high, c.low, c.close, c.volume])
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print("识别口径：特征序列线段 + 同级别递归中枢" if args.mode == "strict" else "识别口径：旧三笔重叠近似")
    for period, result in results.items():
        centers = [z for b in result["blocks"] for z in b["centers"]]
        segment_count = sum(len(b.get("segments", [])) for b in result["blocks"])
        print(f"{period}: {result['bar_count']} 根 K 线，{segment_count} 个确认线段，{len(centers)} 个中枢，{result['gap_count']} 处数据间断")
        for z in centers:
            level = f"L{z.level}" if hasattr(z, "level") else "BI"
            print(f"  {z.start.isoformat()} ~ {z.end.isoformat()}  "
                  f"{level} ZD={z.zd:.8g}, ZG={z.zg:.8g}, {z.status}")
        q = result["data_quality"]
        print(f"  排除未收盘输入 {q['excluded_unclosed_source_bars']} 根，丢弃不完整时间桶 {q['dropped_incomplete_buckets']} 个")
    print(f"结果：{args.output_dir / 'analysis.json'}\n图表：{args.output_dir / 'chart.html'}")


if __name__ == "__main__":
    main()
