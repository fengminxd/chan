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
    range_low: float | None = None
    range_high: float | None = None
    local_evidence_stroke: int | None = None  # 局部分型证据；发布还须等待前段

    @property
    def direction(self) -> str:
        return "up" if self.end.price > self.start.price else "down"

    @property
    def low(self) -> float:
        return self.range_low if self.range_low is not None else min(self.start.price, self.end.price)

    @property
    def high(self) -> float:
        return self.range_high if self.range_high is not None else max(self.start.price, self.end.price)


@dataclass
class SegmentResult:
    segments: list[Segment] = field(default_factory=list)
    pending: Segment | None = None
    skipped_prefix_strokes: int = 0
    unassigned_from: int = 0
    usable_stroke_count: int = 0
    tail_diagnostics: dict = field(default_factory=dict)
    direct_breaks: list[dict] = field(default_factory=list)


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
    """至少三奇数笔、前三笔重叠、终点为方向极值。

    第 71 课的一笔破坏后原段延续，允许途中越过旧起点；不能额外要求
    起点永远是全段反向极值。内部价格范围由 Segment.range_low/high 保存。
    """
    if stop - start < 3 or (stop - start) % 2 == 0:
        return False
    part = strokes[start:stop]
    a, b = part[0].start.price, part[-1].end.price
    return (_overlap(part[:3]) and
            (b > a and b == max(x.high for x in part) if part[0].direction == "up"
             else b < a and b == min(x.low for x in part)))


def _turn_evidence(strokes, start: int, turn: int, audit: dict | None = None):
    """检验一个假设转折；证据仅取该转折被价格越过之前的前缀。

    左侧标准序列和转折后的序列分别去包含，禁止跨假设边界吞并。
    第一笔直接破坏旧段时，单独处理第 71 课的包含型三笔破坏。
    """
    direction = strokes[start].direction
    reverse = "down" if direction == "up" else "up"
    before = standardize_features([_feature(strokes, i) for i in range(start + 1, turn, 2)], direction)
    left, middle = before[-1], _feature(strokes, turn)
    gap = middle.low > left.high if direction == "up" else middle.high < left.low
    # 第一反向笔触及旧元素边界也属于包含型破坏；后续第三笔必须严格
    # 越过这条破坏笔的终点才确认，见下方 breaks_first，不能一并放宽。
    direct = middle.low <= left.low if direction == "up" else middle.high >= left.high
    if audit is not None:
        audit.update(turn_stroke=turn, gap=gap, direct_break=direct,
                     first_pattern_found=False, reverse_pattern_found=False,
                     reverse_span_ready=False)
    peak = strokes[turn].start.price
    primary: list[Feature] = []
    secondary: list[Feature] = []
    first_pattern = None
    second_pattern = None
    reverse_span_ready = False
    for i in range(turn, len(strokes)):
        s = strokes[i]
        # 包含合并不能隐藏重新越过候选转折点的价格。
        if (direction == "up" and s.high > peak) or (direction == "down" and s.low < peak):
            if audit is not None:
                audit.update(reason="turn_extreme_exceeded", evidence_stroke=i)
            return None
        if (i - turn) % 2 == 0:
            # 特征元素去包含不能替代反向段的真实笔跨度。尤其首个反向
            # 笔已达到最低/最高点、后续三笔尚未成段时，不能提前固定转折。
            if not reverse_span_ready and i >= turn + 2:
                reverse_span_ready = _valid_span(strokes, turn, i + 1)
            # 分型尚未确认时仍按旧段特征序列处理最初的包含。
            # 提前使用反向方向会抹去第二元素的顶/底，导致本应确认的分型消失。
            _append_feature(primary, _feature(strokes, i), direction)
            if not direct:
                # 第 67 课的缺口属于标准特征序列。包含可抬高中元素的低点
                # （或降低高点），不能沿用原始第一反向笔的无缺口结论。
                # 一笔破坏仍单独按原始破坏笔及第三笔处理，避免被合并掩盖。
                gap = primary[0].low > left.high if direction == "up" else primary[0].high < left.low
                if audit is not None:
                    audit['gap'] = gap
            if len(primary) >= 2 and _fractal(left, primary[0], primary[1], direction):
                first_pattern = (left.source_indices, primary[0].source_indices, primary[1].source_indices)
            # 破坏笔包含前一特征元素时不能把两者合并。第三笔缩在第一笔内则等待。
            breaks_first = s.low < middle.low if direction == "up" else s.high > middle.high
            if not gap and direct and reverse_span_ready and breaks_first:
                if audit is not None:
                    # 该分支在下面的通用状态同步之前返回；必须记录本笔刚刚
                    # 建立的反向跨度，不能沿用上一轮的 False。
                    audit.update(reason="confirmed_direct_break", evidence_stroke=i,
                                 first_pattern_found=first_pattern is not None,
                                 reverse_pattern_found=second_pattern is not None,
                                 reverse_span_ready=reverse_span_ready)
                return i, "no_gap", (left.source_indices, middle.source_indices, (i,)), ()
        else:
            _append_feature(secondary, _feature(strokes, i), reverse)
            if len(secondary) >= 3 and _fractal(*secondary[-3:], reverse):
                second_pattern = tuple(f.source_indices for f in secondary[-3:])
        if audit is not None:
            audit.update(first_pattern_found=first_pattern is not None,
                         reverse_pattern_found=second_pattern is not None,
                         reverse_span_ready=reverse_span_ready)
        if first_pattern is not None and reverse_span_ready:
            if not gap:
                # 首笔已直接破坏旧段时，第 71 课的等待规则优先；包含合并
                # 后的表面分型不能代替“后续第三笔越过首笔终点”。
                if not direct:
                    if audit is not None:
                        audit.update(reason="confirmed_no_gap", evidence_stroke=i)
                    return i, "no_gap", first_pattern, ()
            if second_pattern is not None:
                if gap:
                    if audit is not None:
                        audit.update(reason="confirmed_gap", evidence_stroke=i)
                    return i, "gap_reverse_fractal", first_pattern, second_pattern
    if audit is not None:
        audit["reason"] = ("waiting_reverse_valid_span" if first_pattern and not reverse_span_ready else
                           "waiting_direct_break_resolution" if direct and not gap else
                           "waiting_first_feature_fractal" if first_pattern is None else
                           "waiting_reverse_feature_fractal")
    return None


def _turn_diagnostics(strokes, start):
    audits = []
    for turn in range(start + 3, len(strokes), 2):
        if _valid_span(strokes, start, turn):
            audit = {"segment_start_stroke": start}
            _turn_evidence(strokes, start, turn, audit)
            audits.append(audit)
    return audits


def direct_break_marks(block: dict) -> dict[int, dict]:
    """各导出/绘图入口共享的破坏笔标记；兼容尚未缓存事件的内存区段。"""
    events = block.get("direct_breaks")
    if events is None:
        events = build_segments(block["strokes"]).direct_breaks
    return {event["turn_stroke"]: {"direct_break": True,
                                   "direct_break_reason": event["reason"]}
            for event in events if event.get("direct_break")}


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
    # 输入可用时间不必按笔索引排序；前缀证据必须覆盖每一条依赖笔。
    available_at = []
    for s in usable:
        available_at.append(max(s.end.time, s.confirmed_at or s.end.time,
                                available_at[-1] if available_at else s.end.time))
    breaks = {}

    def remember_breaks(audits):
        for audit in audits:
            if audit.get("direct_break"):
                breaks.setdefault(audit["turn_stroke"], audit)

    start = 0
    # 只有前三笔不重叠才能排除此起点。第三笔尚未达到方向极值时，
    # 同一起点仍可延伸为五笔或更多笔的段，不能据此跳过。
    while start + 3 <= len(usable) and not _overlap(usable[start:start + 3]):
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
        local_observed = observed
        if result.segments:
            observed = max(observed, result.segments[-1].evidence_stroke)
        # 保留该起点假设存续期间的破坏事件，包括后来失效、归入段内的转折。
        remember_breaks(_turn_diagnostics(usable[:observed + 1], start))
        result.segments.append(Segment(usable[start].start, usable[turn].start,
                                       start, turn - 1, True, case, observed,
                                       available_at[observed],
                                       primary, secondary,
                                       min(s.low for s in usable[start:turn]),
                                       max(s.high for s in usable[start:turn]), local_observed))
        start = turn
    result.unassigned_from = start
    # 末段只显示截至当前的最远有效端点，不使用候选笔补齐三笔。
    endpoints = [stop for stop in range(start + 3, len(usable) + 1, 2)
                 if _valid_span(usable, start, stop)]
    if endpoints:
        stop = endpoints[-1]
        result.pending = Segment(usable[start].start, usable[stop - 1].end,
                                 start, stop - 1, False, "pending",
                                 range_low=min(s.low for s in usable[start:stop]),
                                 range_high=max(s.high for s in usable[start:stop]))
    rejected = _turn_diagnostics(usable, start)
    remember_breaks(rejected)
    result.direct_breaks = [breaks[i] for i in sorted(breaks)]
    result.tail_diagnostics = {
        "unassigned_completed_strokes": len(usable)-start,
        "candidate_turns": rejected,
        "status": "waiting_confirmation" if rejected else
                  "waiting_future_strokes" if endpoints else "waiting_valid_span",
        "last_completed_stroke_end": usable[-1].end.time if usable else None}
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
