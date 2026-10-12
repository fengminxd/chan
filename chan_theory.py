"""缠论中枢演化、走势分类、背驰辅助与买卖点证据。

结构规则与 MACD 辅助规则分别标记。这里不把 MACD 减面积等同于严格背驰，
不把同级别分解中的盘整单位等同于最大延伸走势。参见 THEORY.md。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
import math
from typing import Sequence

from chan_structure import Movement


def direction(move: Movement) -> str:
    delta = move.end.price - move.start.price
    return "up" if delta > 0 else "down" if delta < 0 else "flat"


def validate_moves(moves: Sequence[Movement]) -> None:
    for i, m in enumerate(moves):
        if (not all(math.isfinite(x) for x in (m.low, m.high, m.start.price, m.end.price))
                or not m.low <= min(m.start.price, m.end.price) <= max(m.start.price, m.end.price) <= m.high):
            raise ValueError("走势价格范围无效")
        if m.start.time >= m.end.time or m.evidence_at < m.end.time:
            raise ValueError("走势时间或证据时刻无效")
        if i and (moves[i - 1].end != m.start or moves[i - 1].level != m.level):
            raise ValueError("子走势必须同级别、连续且首尾相连")


@dataclass
class EvolvingCenter:
    id: str
    level: int
    first_index: int
    last_index: int
    start: datetime
    end: datetime
    formed_at: datetime
    evidence_at: datetime
    updated_at: datetime
    zd: float
    zg: float
    dd: float
    gg: float
    d: float
    g: float
    range_low: float
    range_high: float
    seed_ids: tuple[str, ...]
    child_ids: list[str]
    z_ids: list[str]
    z_direction: str
    directional_z_valid: bool
    status: str = "extending"
    departure_index: int | None = None
    return_index: int | None = None
    closed_at: datetime | None = None


@dataclass(frozen=True)
class Signal:
    id: str
    label: str  # B1/S1/B2/S2/B3/S3
    level: int
    time: datetime
    price: float
    evidence_at: datetime
    certainty: str  # structural / macd_candidate / dependent_candidate
    child_index: int
    center_id: str
    source_ids: tuple[str, ...]
    details: dict = field(default_factory=dict)


def _update_range(z: EvolvingCenter, moves: Sequence[Movement], *, incremental: bool = True) -> None:
    # 常见路径只新增一个延伸成员；不反复扫描整个延伸中枢。关闭中枢时
    # 可能移除离开段，或首次三段成枢，仍走下面的完整重算路径。
    if (incremental and z.child_ids and z.z_ids
            and len(z.child_ids) == z.last_index - z.first_index
            and z.child_ids[-1] == moves[z.last_index - 1].id):
        move = moves[z.last_index]
        side = direction(move)
        opposite = "down" if z.z_direction == "up" else "up"
        expected = z.z_direction if (z.last_index - z.first_index) % 2 == 0 else opposite
        z.directional_z_valid = z.directional_z_valid and side == expected
        z.child_ids.append(move.id)
        z.end = move.end.time
        z.range_low, z.range_high = min(z.range_low, move.low), max(z.range_high, move.high)
        z.updated_at = max(z.updated_at, move.evidence_at)
        if side == z.z_direction:
            z.z_ids.append(move.id)
            z.dd, z.gg = min(z.dd, move.low), max(z.gg, move.high)
            z.d, z.g = max(z.d, move.low), min(z.g, move.high)
        return
    members = moves[z.first_index:z.last_index + 1]
    opposite = "down" if z.z_direction == "up" else "up"
    z.directional_z_valid = z.z_direction != "flat" and all(
        direction(m) == (z.z_direction if i % 2 == 0 else opposite) for i, m in enumerate(members))
    z_members = [m for m in members if direction(m) == z.z_direction]
    if not z_members:
        z_members = list(members[::2])
    z.end = members[-1].end.time
    z.child_ids = [m.id for m in members]
    z.z_ids = [m.id for m in z_members]
    z.dd, z.gg = min(m.low for m in z_members), max(m.high for m in z_members)
    z.d, z.g = max(m.low for m in z_members), min(m.high for m in z_members)
    z.range_low, z.range_high = min(m.low for m in members), max(m.high for m in members)
    z.updated_at = max(m.evidence_at for m in members)


def center_relation(a: EvolvingCenter, b: EvolvingCenter) -> str:
    if a.level != b.level:
        raise ValueError("只能比较同级别中枢")
    if not a.directional_z_valid or not b.directional_z_valid:
        return "unresolved_direction"
    if b.dd > a.gg:
        return "up"
    if b.gg < a.dd:
        return "down"
    return "expansion"  # 包括边界接触；不是仅比较中枢核心 ZD/ZG。


def third_point_label(z, departure_side, departure_price, return_side, return_low, return_high):
    """一次离开尝试的首次回试；回枢失败后可由下一对走势重新尝试。"""
    if departure_side == "up" and return_side == "down" and departure_price > z.zg and return_low > z.zg:
        return "B3"
    if departure_side == "down" and return_side == "up" and departure_price < z.zd and return_high < z.zd:
        return "S3"
    return None


def evolving_centers(moves: Sequence[Movement], level: int, *, legacy: bool = False) -> dict:
    """非同级别中枢观察视角：保留进入段，前三段定核，等待离开后的首次回试。

    DD/GG/D/G 只统计与首段同向的 Z 走势；离开段确认后从延伸部分移除，
    但不能删掉初始三段中构成中枢的成员。最后的候选走势不能传入本函数。
    """
    validate_moves(moves)
    if level < 1 or any(m.level != level - 1 for m in moves):
        raise ValueError("中枢输入必须是直接次级别走势")
    allowed = ("completed", "decomposition_unit") if legacy else ("completed",)
    if any(m.status not in allowed for m in moves):
        raise ValueError("中枢与三类点只接受已完成走势；旧分解单位须显式选择 legacy")
    centers: list[EvolvingCenter] = []
    signals: list[Signal] = []
    events = []
    active = None
    seed_start = 1  # 第 0 条作为进入走势；左截断部分不臆造进入方向。
    for i, move in enumerate(moves):
        if active is not None:
            prior = moves[i - 1]
            label = third_point_label(active, direction(prior), prior.end.price,
                                      direction(move), move.low, move.high)
            if label:
                active.status = "left_up" if label == "B3" else "left_down"
                active.departure_index, active.return_index = i - 1, i
                active.closed_at = max(active.updated_at, prior.evidence_at, move.evidence_at)
                active.last_index = max(active.first_index + 2, i - 2)
                _update_range(active, moves)
                sig = Signal(f"{active.id}:{label}", label, level, move.end.time, move.end.price,
                             active.closed_at, "legacy_structural" if legacy else "structural",
                             i, active.id, (prior.id, move.id),
                             {"zd": active.zd, "zg": active.zg, "return_low": move.low,
                              "return_high": move.high, "boundary_policy": "touch_is_return"})
                signals.append(sig)
                events.append({"kind": active.status, "center_id": active.id,
                               "evidence_at": active.closed_at, "departure_id": prior.id, "return_id": move.id})
                seed_start, active = i, None
            else:
                active.last_index = i
                _update_range(active, moves)
                events.append({"kind": "extension", "center_id": active.id, "child_id": move.id,
                               "evidence_at": active.updated_at})
                continue
        if i - seed_start < 2:
            continue
        triple = moves[i - 2:i + 1]
        zd, zg = max(x.low for x in triple), min(x.high for x in triple)
        if zd > zg:
            continue
        directions = [direction(m) for m in triple]
        z_valid = directions[0] == directions[2] != "flat" and directions[1] not in (directions[0], "flat")
        evidence = max(m.evidence_at for m in triple)
        active = EvolvingCenter(f"L{level}:E{len(centers)}", level, i - 2, i,
                                triple[0].start.time, move.end.time, move.end.time, evidence, evidence,
                                zd, zg, 0, 0, 0, 0, 0, 0, tuple(m.id for m in triple), [], [],
                                directions[0], z_valid)
        _update_range(active, moves)
        centers.append(active)
        events.append({"kind": "formation", "center_id": active.id, "evidence_at": evidence,
                       "seed_ids": active.seed_ids})
    relations = []
    trend_runs = []
    for a, b in zip(centers, centers[1:]):
        kind = center_relation(a, b)
        relation = {"from": a.id, "to": b.id, "kind": kind,
                    "evidence_at": max(a.updated_at, b.updated_at),
                    "target_level": level + 1 if kind == "expansion" else None}
        relations.append(relation)
        if kind in ("up", "down"):
            if trend_runs and trend_runs[-1]["direction"] == kind and trend_runs[-1]["center_ids"][-1] == a.id:
                trend_runs[-1]["center_ids"].append(b.id)
                trend_runs[-1]["evidence_at"] = relation["evidence_at"]
            else:
                trend_runs.append({"direction": kind, "center_ids": [a.id, b.id],
                                   "evidence_at": relation["evidence_at"], "completion": "not_inferred"})
        if kind == "expansion":
            events.append({"kind": "expansion", "center_ids": [a.id, b.id],
                           "target_level": level + 1, "evidence_at": relation["evidence_at"],
                           "note": "波动区间重叠判据；高层具体中枢边界由递归视角单独给出"})
    classification = ("insufficient" if not centers else "consolidation" if not relations else
                      {"up": "uptrend", "down": "downtrend", "expansion": "higher_level_consolidation",
                       "unresolved_direction": "unresolved"}[relations[-1]["kind"]])
    return {"level": level, "centers": centers, "events": events, "relations": relations,
            "trend_runs": trend_runs, "classification": classification, "signals": signals,
            "view": "extension", "entry_prefix_count": min(len(moves), 1)}


def canonical_center_view(moves: Sequence[Movement], centers: Sequence[EvolvingCenter], level: int) -> dict:
    """引用完成走势树的成枢实体；只计算它们的离开/回试，不重新寻找中枢。

    完成父走势的切分可能早于其三类点证据。回试因此在全层已完成子走势
    中查找，最迟到下一已登记中枢的形成；不把旧中枢无限延伸到新中枢之后。
    """
    validate_moves(moves)
    if any(m.status != "completed" or m.level != level - 1 for m in moves):
        raise ValueError("统一中枢视图只接受已完成的直接次级走势")
    indices = {m.id: i for i, m in enumerate(moves)}
    if len(indices) != len(moves) or len({z.id for z in centers}) != len(centers):
        raise ValueError("统一结构 ID 不能重复")
    canonical = []
    for z in centers:
        seed = [indices[c] for c in z.seed_ids]
        if z.level != level or len(seed) != 3 or seed != list(range(seed[0], seed[0] + 3)):
            raise ValueError("统一中枢须引用三个连续的直接次级走势")
        canonical.append(replace(z, first_index=seed[0], last_index=seed[-1],
                                 child_ids=[], z_ids=[],
                                 departure_index=None, return_index=None, closed_at=None,
                                 status="extending"))
    canonical.sort(key=lambda z: z.first_index)
    signals = []
    for zi, z in enumerate(canonical):
        # 观察范围从成枢三段重放，不能沿用父走势切分时的尾部统计。
        # replace 后列表独立；不得修改父走势内部中枢的成员归属。
        _update_range(z, moves, incremental=False)
        stop = canonical[zi + 1].first_index + 3 if zi + 1 < len(canonical) else len(moves)
        for i in range(z.first_index + 3, stop):
            prior, move = moves[i - 1:i + 1]
            label = third_point_label(z, direction(prior), prior.end.price,
                                      direction(move), move.low, move.high)
            if not label:
                z.last_index = i
                _update_range(z, moves)
                continue
            z.status = "left_up" if label == "B3" else "left_down"
            z.departure_index, z.return_index = i - 1, i
            z.closed_at = max(z.updated_at, prior.evidence_at, move.evidence_at)
            z.last_index = max(z.first_index + 2, i - 2)
            _update_range(z, moves, incremental=False)
            signals.append(Signal(f"{z.id}:{label}", label, level, move.end.time, move.end.price,
                                  z.closed_at, "structural", i, z.id, (prior.id, move.id),
                                  {"zd": z.zd, "zg": z.zg, "return_low": move.low,
                                   "return_high": move.high, "boundary_policy": "touch_is_return",
                                   "structure_source": "completed_type_tree"}))
            break
    return {"level": level, "centers": canonical, "signals": signals,
            "view": "completed_type_tree", "structure_source": "completed_type_tree"}


@dataclass(frozen=True)
class MACDPoint:
    time: datetime
    dif: float
    dea: float
    histogram: float
    warmed_up: bool


def macd(candles: Sequence, fast: int = 12, slow: int = 26, signal: int = 9) -> list[MACDPoint]:
    if not 1 <= fast < slow or signal < 1:
        raise ValueError("MACD 参数须满足 1 <= fast < slow，signal >= 1")
    if not candles:
        return []
    fast_ema = slow_ema = candles[0].close
    dea = 0.0
    result = []
    for i, c in enumerate(candles):
        if not math.isfinite(c.close) or (i and c.time <= candles[i - 1].time):
            raise ValueError("MACD 输入必须是有限价格和严格递增时间")
        fast_ema += 2 / (fast + 1) * (c.close - fast_ema)
        slow_ema += 2 / (slow + 1) * (c.close - slow_ema)
        dif = fast_ema - slow_ema
        dea += 2 / (signal + 1) * (dif - dea)
        result.append(MACDPoint(c.time, dif, dea, 2 * (dif - dea), i >= slow + signal - 2))
    return result


def macd_area(points: Sequence[MACDPoint], start: datetime, end: datetime, side: str) -> dict:
    if side not in ("up", "down") or start >= end:
        raise ValueError("面积需要有效方向和正时间区间")
    # 使用 [start,end)，共享转折 K 线不在 A/C 中重复计数。
    selected = [p for p in points if start <= p.time < end]
    sign = 1 if side == "up" else -1
    area = sum(max(0.0, sign * p.histogram) for p in selected)
    return {"area": area, "count": len(selected), "warmed_up": bool(selected) and all(p.warmed_up for p in selected),
            "dif_peak": max((abs(p.dif) for p in selected), default=0),
            "interval": "[start,end)"}


def divergence_candidates(moves: Sequence[Movement], center_view: dict, points: Sequence[MACDPoint],
                          zero_axis_ratio: float = 0.2) -> list[dict]:
    """第 24 课的 MACD 面积辅助，不能充作严格背驰的证明。"""
    if not math.isfinite(zero_axis_ratio) or not 0 <= zero_axis_ratio <= 1:
        raise ValueError("zero_axis_ratio 必须在 0..1")
    result = []
    centers = center_view["centers"]
    for zi, z in enumerate(centers):
        ai = z.first_index - 1
        a = moves[ai]
        side = direction(a)
        if side == "flat":
            continue
        departures = ([z.departure_index] if z.departure_index is not None else
                      [i for i in range(z.first_index + 2, z.last_index + 1)
                       if direction(moves[i]) == side])
        for ci in departures:
            c = moves[ci]
            if direction(c) != side:
                continue
            extreme = (c.end.price == c.high and c.high > a.high if side == "up" else
                       c.end.price == c.low and c.low < a.low)
            if not extreme:
                continue
            am, cm = macd_area(points, a.start.time, a.end.time, side), macd_area(points, c.start.time, c.end.time, side)
            if not am["warmed_up"] or not cm["warmed_up"] or not 0 <= cm["area"] < am["area"]:
                continue
            middle = [p for p in points if a.end.time <= p.time < c.start.time]
            reset = bool(middle) and min(max(abs(p.dif), abs(p.dea)) for p in middle) <= zero_axis_ratio * am["dif_peak"]
            if not reset:
                continue
            trend = zi > 0 and center_relation(centers[zi - 1], z) == side
            # 两中枢相离且方向相同，才允许标为趋势背驰辅助候选。
            kind = "trend" if trend else "consolidation"
            evidence = max(z.evidence_at, a.evidence_at, c.evidence_at)
            if trend:
                evidence = max(evidence, centers[zi - 1].updated_at, z.updated_at)
            result.append({"id": f"{z.id}:D:{ci}", "kind": kind, "level": z.level,
                           "side": side, "center_id": z.id, "a_id": a.id, "c_id": c.id,
                           "a_index": ai, "c_index": ci, "start": c.start.time, "end": c.end.time,
                           "price": c.end.price, "evidence_at": evidence, "a_area": am["area"],
                           "c_area": cm["area"], "area_ratio": cm["area"] / am["area"],
                           "zero_axis_reset": reset, "zero_axis_ratio": zero_axis_ratio,
                           "strength_metric": "same_direction_histogram_sum", "strength_units": "price_bars",
                           "certainty": "macd_auxiliary", "strict_divergence_confirmed": False})
    return result


def first_second_signals(moves: Sequence[Movement], divergences: Sequence[dict]) -> list[Signal]:
    signals = []
    for div in divergences:
        if div["kind"] != "trend":
            continue  # 盘整背驰不冒充第一类点。
        ci = div["c_index"]
        c = moves[ci]
        buy = div["side"] == "down"
        first = "B1" if buy else "S1"
        signals.append(Signal(f"{div['id']}:{first}", first, div["level"], c.end.time, c.end.price,
                              div["evidence_at"], "macd_candidate", ci, div["center_id"], (div["id"],)))
        if ci + 2 >= len(moves):
            continue
        rebound, retest = moves[ci + 1:ci + 3]
        if direction(rebound) != ("up" if buy else "down") or direction(retest) != div["side"]:
            continue
        weak = retest.end.price < c.end.price if buy else retest.end.price > c.end.price
        second = "B2" if buy else "S2"
        signals.append(Signal(f"{div['id']}:{second}", second, div["level"], retest.end.time, retest.end.price,
                              max(div["evidence_at"], rebound.evidence_at, retest.evidence_at),
                              "dependent_candidate", ci + 2, div["center_id"],
                              (f"{div['id']}:{first}", rebound.id, retest.id),
                              {"strength": "weak_new_extreme" if weak else "holds_first",
                               "first_price": c.end.price, "first_signal_is_candidate": True}))
    return signals


def second_retest_positions(moves: Sequence[Movement]) -> list[dict]:
    """独立定位首次反弹/回试端点，不以本级一类点输出作为扫描开关。

    第 53 课的小转大分支需要独立检查。这里只证明连续已完成走势的
    不破极值几何关系；未证明原趋势终结或次级背驰，不能发布确认交易信号。
    跌破前低/突破前高的弱二类点仍由一类点依赖通路处理。
    """
    validate_moves(moves)
    positions = []
    for i in range(2, len(moves)):
        origin, rebound, retest = moves[i - 2:i + 1]
        if any(m.status != "completed" for m in (origin, rebound, retest)):
            continue
        side = direction(origin)
        if side == "flat" or direction(retest) != side or direction(rebound) != (
                "up" if side == "down" else "down"):
            continue
        buy = side == "down"
        holds = (origin.end.price == origin.low and retest.end.price == retest.low
                 and min(rebound.low, retest.low) >= origin.end.price) if buy else (
                 origin.end.price == origin.high and retest.end.price == retest.high
                 and max(rebound.high, retest.high) <= origin.end.price)
        if not holds:
            continue
        positions.append({"label": "B2" if buy else "S2", "level": origin.level + 1,
                          "time": retest.end.time, "price": retest.end.price,
                          "evidence_at": max(m.evidence_at for m in (origin, rebound, retest)),
                          "origin_time": origin.end.time, "origin_price": origin.end.price,
                          "rebound_time": rebound.end.time, "rebound_price": rebound.end.price,
                          "source_ids": [origin.id, rebound.id, retest.id],
                          "certainty": "second_retest_geometry",
                          "first_point_required_for_scan": False,
                          "first_point_confirmed": False,
                          "pending": ["origin_reversal_or_lower_level_divergence"],
                          "scope": "endpoint_location_only_not_confirmed_signal"})
    return positions


def nested_intervals(divergences: Sequence[dict]) -> list[dict]:
    """同一图表结构层级内的区间套候选；相邻层、同向、完整时间包含。"""
    edges = []
    for parent in divergences:
        for child in divergences:
            if (parent["level"] == child["level"] + 1 and parent["side"] == child["side"]
                    and parent["start"] <= child["start"] < child["end"] <= parent["end"]):
                edges.append({"parent_id": parent["id"], "child_id": child["id"],
                              "start": child["start"], "end": child["end"],
                              "evidence_at": max(parent["evidence_at"], child["evidence_at"]),
                              "certainty": "auxiliary_nested_interval"})
    return edges


def analyze_theory(hierarchy: dict, candles: Sequence, zero_axis_ratio: float = 0.2,
                   hours: int = 1, max_levels: int = 5, strength_fast: int = 5,
                   strength_slow: int = 10) -> dict:
    from chan_types import build_type_hierarchy
    from chan_dynamics import analyze_dynamics
    points = macd(candles)
    levels, all_divergences = [], []
    children = hierarchy["base_movements"]
    for layer in hierarchy["levels"]:
        view = evolving_centers(children, layer["level"], legacy=True)
        divergences = divergence_candidates(children, view, points, zero_axis_ratio)
        view["divergences"] = divergences
        view["signals"].extend(first_second_signals(children, divergences))
        view["signals"].sort(key=lambda x: (x.evidence_at, x.time, x.label))
        all_divergences.extend(divergences)
        levels.append(view)
        children = layer["movements"]
    full = build_type_hierarchy(hierarchy["base_movements"], max_levels)
    dynamics = analyze_dynamics(full, candles, hours, strength_fast, strength_slow)
    positions, children = [], full["base_movements"]
    for layer in full["levels"]:
        positions.extend(second_retest_positions(children))
        children = layer["movements"]
    return {"levels": levels, "nested_intervals": nested_intervals(all_divergences),
            "second_retest_positions": positions,
            "type_hierarchy": full, "strict_divergence": dynamics,
            "macd": {"parameters": [12, 26, 9], "histogram_multiplier": 2,
                     "ema_initialization": "first_close", "warmup_bars": 34,
                     "zero_axis_ratio": zero_axis_ratio, "points": points},
            "strict_divergence_implemented": "specified_policy",
            "scope": "完成走势递归、结构审计及指定均线力度策略下的背驰确认；MACD 辅助另列"}
