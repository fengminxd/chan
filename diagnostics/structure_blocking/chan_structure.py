"""特征序列线段及同级别分解递归中枢。

依据第 67/71/81 课的转折假设与双特征序列确认；递归采用第 38 课的
最早三段同级别分解策略。与非同级别的中枢延伸/扩展视角分开。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chan import Fractal, Stroke


@dataclass(frozen=True)
class Feature:
    low: float
    high: float
    source_indices: tuple[int, ...]


@dataclass(frozen=True)
class Segment:
    start: Fractal
    end: Fractal
    first_stroke: int
    last_stroke: int
    confirmed: bool
    confirmation: str  # no_gap / gap_reverse_fractal / pending
    evidence_stroke: int | None = None
    evidence_at: datetime | None = None
    feature_indices: tuple[tuple[int, ...], ...] = ()
    reverse_feature_indices: tuple[tuple[int, ...], ...] = ()

    @property
    def direction(self) -> str:
        return "up" if self.end.price > self.start.price else "down"

    @property
    def low(self) -> float:
        return min(self.start.price, self.end.price)

    @property
    def high(self) -> float:
        return max(self.start.price, self.end.price)


@dataclass
class SegmentResult:
    segments: list[Segment] = field(default_factory=list)
    pending: Segment | None = None
    skipped_prefix_strokes: int = 0
    unassigned_from: int = 0
    usable_stroke_count: int = 0


def _contains(a: Feature, b: Feature) -> bool:
    return ((a.low <= b.low and a.high >= b.high)
            or (b.low <= a.low and b.high >= a.high))


def _append_feature(seq: list[Feature], new: Feature, initial: str) -> None:
    if not seq or not _contains(seq[-1], new):
        seq.append(new)
        return
    old = seq[-1]
    direction = initial
    if len(seq) > 1:
        direction = "up" if old.high > seq[-2].high else "down"
    select = max if direction == "up" else min
    seq[-1] = Feature(select(old.low, new.low), select(old.high, new.high),
                      old.source_indices + new.source_indices)


def standardize_features(features: list[Feature], direction: str) -> list[Feature]:
    if direction not in ("up", "down"):
        raise ValueError("特征序列初始方向必须为 up/down")
    seq: list[Feature] = []
    for f in features:
        _append_feature(seq, f, direction)
    return seq


def _fractal(a: Feature, b: Feature, c: Feature, direction: str) -> bool:
    if direction == "up":
        return b.high > max(a.high, c.high) and b.low > max(a.low, c.low)
    return b.low < min(a.low, c.low) and b.high < min(a.high, c.high)


def _feature(strokes, i: int) -> Feature:
    return Feature(strokes[i].low, strokes[i].high, (i,))


def _overlap(items) -> bool:
    return max(x.low for x in items) <= min(x.high for x in items)


def _valid_span(strokes, start: int, stop: int) -> bool:
    """stop 为下一段第一笔索引：至少三笔、奇数笔、前三笔重叠、端点为全段极值。"""
    if stop - start < 3 or (stop - start) % 2 == 0:
        return False
    part = strokes[start:stop]
    a, b = part[0].start.price, part[-1].end.price
    return (a != b and _overlap(part[:3])
            and min(a, b) == min(x.low for x in part)
            and max(a, b) == max(x.high for x in part))


def _turn_evidence(strokes, start: int, turn: int):
    """检验一个假设转折；证据仅取该转折被价格越过之前的前缀。

    左侧标准序列和转折后的序列分别去包含，禁止跨假设边界吞并。
    第一笔直接破坏旧段时，单独处理第 71 课的包含型三笔破坏。
    """
    direction = strokes[start].direction
    reverse = "down" if direction == "up" else "up"
    before = standardize_features([_feature(strokes, i) for i in range(start + 1, turn, 2)], direction)
    left, middle = before[-1], _feature(strokes, turn)
    gap = middle.low > left.high if direction == "up" else middle.high < left.low
    direct = middle.low < left.low if direction == "up" else middle.high > left.high
    peak = strokes[turn].start.price
    primary: list[Feature] = []
    secondary: list[Feature] = []
    first_pattern = None
    second_pattern = None
    for i in range(turn, len(strokes)):
        s = strokes[i]
        # 包含合并不能隐藏重新越过候选转折点的价格。
        if (direction == "up" and s.high > peak) or (direction == "down" and s.low < peak):
            return None
        if (i - turn) % 2 == 0:
            _append_feature(primary, _feature(strokes, i), reverse)
            if len(primary) >= 2 and _fractal(left, primary[0], primary[1], direction):
                first_pattern = (left.source_indices, primary[0].source_indices, primary[1].source_indices)
            # 破坏笔包含前一特征元素时不能把两者合并。第三笔缩在第一笔内则等待。
            breaks_first = s.low < middle.low if direction == "up" else s.high > middle.high
            if not gap and direct and i >= turn + 2 and breaks_first:
                return i, "no_gap", (left.source_indices, middle.source_indices, (i,)), ()
        else:
            _append_feature(secondary, _feature(strokes, i), reverse)
            if len(secondary) >= 3 and _fractal(*secondary[-3:], reverse):
                second_pattern = tuple(f.source_indices for f in secondary[-3:])
        if first_pattern is not None:
            if not gap:
                return i, "no_gap", first_pattern, ()
            if second_pattern is not None:
                return i, "gap_reverse_fractal", first_pattern, second_pattern
    return None


def build_segments(strokes: list[Stroke]) -> SegmentResult:
    """对已完成笔前缀划分线段；末段不强行确认，也不参与中枢递归。"""
    usable = []
    provisional_seen = False
    for i, s in enumerate(strokes):
        if s.start.time >= s.end.time or s.start.price == s.end.price:
            raise ValueError("笔必须有正时间跨度和非零价差")
        expected = ("bottom", "top") if s.direction == "up" else ("top", "bottom")
        if (s.start.kind, s.end.kind) != expected:
            raise ValueError("笔方向与端点分型不符")
        if i and (strokes[i - 1].end != s.start or strokes[i - 1].direction == s.direction):
            raise ValueError("笔必须首尾相连、方向交替")
        if s.provisional:
            provisional_seen = True
        elif provisional_seen:
            raise ValueError("候选笔之后不能再有已完成笔")
        else:
            usable.append(s)
    result = SegmentResult(usable_stroke_count=len(usable))
    start = 0
    # 左截断样本允许存在不构成段的前缀；透明报告，不把它们伪装成段。
    while start + 3 <= len(usable) and not _valid_span(usable, start, start + 3):
        start += 1
    result.skipped_prefix_strokes = start
    while start + 3 <= len(usable):
        candidates = []
        for turn in range(start + 3, len(usable), 2):
            if candidates and turn + 2 > min(c[0] for c in candidates):
                break  # 新假设至少还需要两笔，已不可能更早得到确认。
            if not _valid_span(usable, start, turn):
                continue
            evidence = _turn_evidence(usable, start, turn)
            if evidence is not None:
                candidates.append((evidence[0], turn, evidence))
        if not candidates:
            break
        # 取最先得到证据的转折，避免尚待第二序列确认的旧假设抢占后来已确认的转折。
        _, turn, (observed, case, primary, secondary) = min(candidates)
        result.segments.append(Segment(usable[start].start, usable[turn].start,
                                       start, turn - 1, True, case, observed,
                                       usable[observed].confirmed_at or usable[observed].end.time,
                                       primary, secondary))
        start = turn
    result.unassigned_from = start
    # 末段只显示截至当前的最远有效端点，不使用候选笔补齐三笔。
    endpoints = [stop for stop in range(start + 3, len(usable) + 1, 2)
                 if _valid_span(usable, start, stop)]
    if endpoints:
        stop = endpoints[-1]
        result.pending = Segment(usable[start].start, usable[stop - 1].end,
                                 start, stop - 1, False, "pending")
    return result


@dataclass(frozen=True)
class Movement:
    id: str
    level: int
    kind: str  # segment / consolidation / uptrend / downtrend
    start: Fractal
    end: Fractal
    low: float  # 全部子走势经过的价格范围，不能只用首尾价格
    high: float
    child_ids: tuple[str, ...]
    center_ids: tuple[str, ...]
    evidence_at: datetime
    status: str = "completed"
    completion: str | None = None


@dataclass(frozen=True)
class RecursiveCenter:
    id: str
    level: int
    start: datetime
    end: datetime
    formed_at: datetime
    evidence_at: datetime
    zd: float
    zg: float
    range_low: float
    range_high: float
    child_ids: tuple[str, str, str]
    movement_id: str
    status: str = "formed"


def decompose_level(children: list[Movement], level: int) -> dict:
    """确定的同级别最早三段分解：6 段可为两个盘整，余尾不提升。

    找到最早连续三子走势重叠后，前方尚未归属的连接部分按 a+B 结合入该
    盘整走势。中枢本身只引用实际的三子走势，不能把前缀充作中枢的组成段。
    """
    if level < 1:
        raise ValueError("中枢层级从 L1 开始")
    for i, child in enumerate(children):
        if child.level != level - 1:
            raise ValueError("递归只接受同一直接次级别的走势")
        if i and children[i - 1].end != child.start:
            raise ValueError("递归子走势必须时间及端点连续，不能跳段")
    if len({c.id for c in children}) != len(children):
        raise ValueError("子走势 ID 不能重复")
    movements, centers, relations = [], [], []
    begin = 0
    for stop in range(3, len(children) + 1):
        if stop - begin < 3:
            continue
        triple = children[stop - 3:stop]
        zd, zg = max(m.low for m in triple), min(m.high for m in triple)
        if zd > zg:  # 严格区间交集包含单点；绘图以水平带显示
            continue
        selected = children[begin:stop]
        mid = f"L{level}:M{len(movements)}"
        zid = f"L{level}:Z{len(centers)}"
        evidence = max(m.evidence_at for m in selected)
        z = RecursiveCenter(zid, level, triple[0].start.time, triple[-1].end.time,
                            triple[-1].end.time, max(m.evidence_at for m in triple), zd, zg,
                            min(m.low for m in triple), max(m.high for m in triple),
                            tuple(m.id for m in triple), mid)
        centers.append(z)
        movements.append(Movement(mid, level, "consolidation", selected[0].start, selected[-1].end,
                                  min(m.low for m in selected), max(m.high for m in selected),
                                  tuple(m.id for m in selected), (zid,), evidence,
                                  "decomposition_unit", "earliest_triple"))
        if len(centers) > 1:
            previous = centers[-2]
            relation = ("up_disjoint" if z.range_low > previous.range_high else
                        "down_disjoint" if z.range_high < previous.range_low else "ranges_overlap")
            relations.append({"previous": previous.id, "current": z.id, "relation": relation})
        begin = stop
    return {"level": level, "centers": centers, "movements": movements,
            "relations": relations, "unassigned_child_ids": [m.id for m in children[begin:]]}


def build_hierarchy(segments: list[Segment], max_levels: int = 5) -> dict:
    """L0=确认线段；L1=三线段中枢；L2+=三下层走势类型重叠。"""
    if not 1 <= max_levels <= 20:
        raise ValueError("max_levels 必须在 1..20")
    base = []
    previous = None
    for i, segment in enumerate(segments):
        if not segment.confirmed or segment.evidence_at is None:
            raise ValueError("未确认线段不能进入中枢递归")
        if segment.last_stroke - segment.first_stroke + 1 < 3:
            raise ValueError("线段至少包含三笔")
        if previous and (previous.end != segment.start or
                         previous.last_stroke + 1 != segment.first_stroke or
                         previous.direction == segment.direction):
            raise ValueError("线段必须端点、笔索引连续且方向交替")
        base.append(Movement(f"L0:M{i}", 0, "segment", segment.start, segment.end,
                             segment.low, segment.high,
                             tuple(f"BI:{j}" for j in range(segment.first_stroke, segment.last_stroke + 1)),
                             (), segment.evidence_at))
        previous = segment
    levels = []
    children = base
    for level in range(1, max_levels + 1):
        current = decompose_level(children, level)
        levels.append(current)
        children = current["movements"]
        if len(children) < 3:
            break
    return {"policy": "same_level_earliest_triple", "base_movements": base,
            "levels": levels, "level_limit_reached": len(children) >= 3 and len(levels) == max_levels}
