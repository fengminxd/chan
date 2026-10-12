"""独立的力度策略和带逐项证据的趋势背驰检查。

cross_kiss_sma 是第 15 课均线面积思路的明确数值化子集：仅识别均线
相交的吻，不猜测飞吻/唇吻。它不是未公开动力学的数学证明，也不是 MACD。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Sequence

from chan_structure import Movement
from chan_theory import Signal, center_relation, direction, evolving_centers


@dataclass(frozen=True)
class StrengthWave:
    id: str
    side: str
    start: datetime
    end: datetime | None
    evidence_at: datetime
    area: float  # price * hours，零点线性插值后梯形积分
    duration_hours: float
    peak_at: datetime
    peak: float
    left_bounded: bool
    closed: bool


def strength_waves(candles: Sequence, hours: int, fast: int = 5, slow: int = 10) -> list[StrengthWave]:
    if not isinstance(fast, int) or not isinstance(slow, int) or not 1 <= fast < slow:
        raise ValueError("均线窗口须为整数且满足 1 <= fast < slow")
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError("K 线间隔必须为正")
    step = timedelta(hours=hours)
    for i, bar in enumerate(candles):
        if not math.isfinite(bar.close) or i and bar.time - candles[i - 1].time != step:
            raise ValueError("力度输入必须价格有限、连续等间隔；缺口需分块计算")
    closes = [bar.close for bar in candles]
    points = []
    for i in range(slow - 1, len(closes)):
        spread = math.fsum(closes[i - fast + 1:i + 1]) / fast - math.fsum(closes[i - slow + 1:i + 1]) / slow
        # 收盘指标在本根收盘才可用，不能冒用开盘时间。
        points.append((candles[i].time + step, spread))
    return integrate_spread(points)


def integrate_spread(points: Sequence[tuple[datetime, float]]) -> list[StrengthWave]:
    """对均线差的分段线性曲线积分；首个不完整波不参与确认。"""
    for i, (t, v) in enumerate(points):
        if not math.isfinite(v) or i and t <= points[i - 1][0]:
            raise ValueError("均线差必须有限且时间严格递增")
    if not points:
        return []
    waves = []
    active = None

    def begin(t, value, bounded):
        return {"start": t, "sign": 1 if value > 0 else -1, "area": 0.0,
                "peak": abs(value), "peak_at": t, "bounded": bounded}

    def finish(end, evidence, closed):
        duration = (end - active["start"]).total_seconds() / 3600
        waves.append(StrengthWave(f"W{len(waves)}", "up" if active["sign"] > 0 else "down",
                                  active["start"], end if closed else None, evidence,
                                  active["area"], duration, active["peak_at"], active["peak"],
                                  active["bounded"], closed))

    if points[0][1]:
        active = begin(*points[0], False)
    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        dt = (t1 - t0).total_seconds() / 3600
        if not v0:
            if v1:
                active = begin(t0, v1, True)
                active["peak_at"] = t1
                active["area"] = abs(v1) * dt / 2
            continue
        if v0 * v1 > 0:
            active["area"] += (abs(v0) + abs(v1)) * dt / 2
            if abs(v1) >= active["peak"]:
                active["peak"], active["peak_at"] = abs(v1), t1
        else:
            fraction = abs(v0) / (abs(v0) + abs(v1))
            cross = t0 + (t1 - t0) * fraction
            active["area"] += abs(v0) * dt * fraction / 2
            finish(cross, t1, True)
            active = None
            if v1:
                active = begin(cross, v1, True)
                active["peak_at"] = t1
                active["area"] = abs(v1) * dt * (1 - fraction) / 2
    if active is not None:
        finish(points[-1][0], points[-1][0], False)
    return waves


def compare_strength(b: Movement, c: Movement, waves: Sequence[StrengthWave]) -> dict:
    """比较 B 前 b 与 B 后 c 的完整同向吻间面积，不裁剪、不外推未完波。

    将峰时在走势 [start,end] 内的同向波归属该走势；波左界不能越过走势
    起点，波右界允许晚于价格端点，但不得越过走势的结构证据时刻。
    不满足这种严格对应时输出 unknown，避免按任意指标片段拼接结论。
    """
    side = direction(b)
    result = {"policy": "cross_kiss_sma", "state": "unknown", "weaker": None,
              "side": side, "comparison": "b_vs_c", "evidence_at": None}
    if b.level > c.level or direction(c) != side or side == "flat":
        return {**result, "reason": "incomparable_level_or_direction"}
    if b.end.time > c.start.time:
        return {**result, "reason": "overlapping_comparison_intervals"}
    selected = []
    for name, move in (("b", b), ("c", c)):
        part = [w for w in waves if w.side == side and move.start.time <= w.peak_at <= move.end.time]
        # 一段可以有多个同向波；全部必须完整且归属无歧义。
        valid = bool(part) and all(w.left_bounded and w.closed and w.start >= move.start.time
                                  and w.end <= move.evidence_at for w in part)
        result[name] = {"wave_ids": [w.id for w in part], "complete": valid,
                        "area": math.fsum(w.area for w in part),
                        "duration_hours": math.fsum(w.duration_hours for w in part)}
        selected.append(part)
    if not result["b"]["complete"] or not result["c"]["complete"]:
        return {**result, "reason": "missing_open_or_unaligned_strength_wave"}
    if {w.id for w in selected[0]} & {w.id for w in selected[1]}:
        return {**result, "reason": "shared_strength_wave"}
    if max(w.end for w in selected[0]) > c.start.time:
        return {**result, "reason": "strength_crosses_center_boundary"}
    if result["b"]["area"] <= 0:
        return {**result, "reason": "zero_reference_strength"}
    return {**result, "state": "measured", "reason": None,
            "weaker": result["c"]["area"] < result["b"]["area"],
            "ratio": result["c"]["area"] / result["b"]["area"],
            "evidence_at": max(b.evidence_at, c.evidence_at,
                               *(w.evidence_at for part in selected for w in part))}


def audit_divergence(a, A, b, B, c, c_centers, third_point, waves, *,
                     higher_centers=(), lineage_valid=True) -> dict:
    """a+A+b+B+c 的条件审计。缺证据与条件不成立分别返回 pending/rejected。

    此实现确认的是标准的 c 内部同向趋势分支；非标准内部形态不冒认已确认。
    c 为同级走势结合后的离开区间，调用者应保留其实际组成走势 ID。
    """
    side = direction(c)
    checks = []

    def check(name, value, detail=None):
        checks.append({"rule": name, "state": "pending" if value is None else "pass" if value else "fail",
                       "detail": detail})

    check("same_level_centers", A.level == B.level)
    check("same_direction_disjoint_centers", A.level == B.level and side in ("up", "down")
          and center_relation(A, B) == side)
    check("a_A_b_B_c_order", a.end.time <= A.start < A.formed_at <= b.start.time
          < b.end.time <= B.start < B.formed_at <= c.start.time)
    check("c_direct_sublevel", c.level == B.level - 1)
    check("b_level_not_above_c", b.level <= c.level)
    check("same_direction_legs", direction(a) == direction(b) == side != "flat")
    check("completed_comparison_legs", b.status == c.status == "completed")
    check("source_lineage", lineage_valid)
    old_extreme = max(a.high, A.range_high, b.high, B.range_high) if side == "up" else min(a.low, A.range_low, b.low, B.range_low)
    check("new_directional_extreme", (c.end.price == c.high and c.end.price > old_extreme) if side == "up"
          else (side == "down" and c.end.price == c.low and c.end.price < old_extreme))
    check("c_two_internal_centers", len(c_centers) >= 2 if c.level > 0 else None,
          "L0 基底没有内部中枢证据" if c.level == 0 else [z.id for z in c_centers])
    internal_valid = bool(c_centers) and all(z.level == c.level and c.start.time <= z.start
                                           < z.formed_at <= c.end.time for z in c_centers)
    check("c_internal_level_and_containment", internal_valid if c.level > 0 else None)
    check("c_internal_standard_trend", (internal_valid and len(c_centers) >= 2 and all(
        center_relation(x, y) == side for x, y in zip(c_centers, c_centers[1:]))) if c.level > 0 else None,
        "当前自动确认采用内部同向趋势分支")
    check("no_higher_center_inside_c", not any(z.level >= B.level and c.start.time <= z.start
                                               < z.formed_at <= c.end.time for z in higher_centers))
    check("contains_B_third_point", third_point is not None and third_point.center_id == B.id
          and third_point.level == B.level and third_point.certainty == "structural"
          and third_point.label == ("B3" if side == "up" else "S3")
          and c.start.time < third_point.time <= c.end.time
          and third_point.evidence_at <= c.evidence_at
          and (third_point.details.get("return_low", -math.inf) > B.zg if side == "up"
               else third_point.details.get("return_high", math.inf) < B.zd)
          and all(mid in c.child_ids for mid in third_point.source_ids))
    strength = compare_strength(b, c, waves)
    check("strength_comparable", True if strength["state"] == "measured" else None, strength.get("reason"))
    check("strength_decreases", strength["weaker"])
    failed = any(x["state"] == "fail" for x in checks)
    pending = any(x["state"] == "pending" for x in checks)
    state = "rejected" if failed else "pending" if pending else "confirmed_under_policy"
    evidence = max(a.evidence_at, A.updated_at, b.evidence_at, B.updated_at, c.evidence_at,
                   *(z.updated_at for z in c_centers),
                   *([third_point.evidence_at] if third_point else []),
                   *([strength["evidence_at"]] if strength["evidence_at"] else []))
    return {"id": f"{B.id}:STRICT:{c.id}", "kind": "trend", "level": B.level,
            "side": side, "center_id": B.id, "a_id": a.id, "b_id": b.id, "c_id": c.id,
            "pattern": {"a": a.id, "A": A.id, "b": b.id, "B": B.id, "c": c.id},
            "start": c.start.time, "end": c.end.time, "price": c.end.price,
            "evidence_at": evidence, "state": state, "checks": checks, "strength": strength,
            "certainty": "structure_and_cross_kiss_sma",
            "strict_divergence_confirmed": state == "confirmed_under_policy",
            "confirmation_scope": "specified_rules_and_strength_policy; not_a_general_dynamics_proof"}


def analyze_dynamics(hierarchy: dict, candles: Sequence, hours: int,
                     fast: int = 5, slow: int = 10) -> dict:
    """只消费有完成证据的类型树，不把 earliest_triple 单位当作完成走势。"""
    waves = strength_waves(candles, hours, fast, slow)
    center_catalog = {z.id: z for layer in hierarchy["levels"] for z in layer["centers"]}
    internal_catalog = {z.id: z for layer in hierarchy["levels"]
                        for z in layer.get("decomposition_centers", layer["centers"])}
    children = hierarchy["base_movements"]
    layers, confirmed = [], []
    for layer in hierarchy["levels"]:
        view = layer["structural_view"]
        if view["centers"] != layer["centers"]:
            raise ValueError("背驰必须引用完成走势树的同一组中枢")
        audits, signals = [], list(view["signals"])
        centers = view["centers"]
        for ai, (A, B) in enumerate(zip(centers, centers[1:])):
            if A.first_index == 0 or B.first_index == 0 or B.departure_index is None:
                continue
            a, b = children[A.first_index - 1], children[B.first_index - 1]
            first = B.departure_index
            third = next((s for s in view["signals"] if s.center_id == B.id), None)
            # 离开—回试—再离开区间；末个已完成次级走势之后还需一条反向走势。
            limit = centers[ai + 2].first_index + 3 if ai + 2 < len(centers) else len(children)
            for stop in range(B.return_index + 2, min(limit, len(children) - 1) + 1):
                part = children[first:stop]
                side = direction(part[0])
                if direction(part[-1]) != side or direction(children[stop]) == side:
                    continue
                inner_ids = tuple(zid for m in part for zid in m.center_ids)
                # c 内部范围取父走势自身的切分截面，不挪用其后延伸的观察范围。
                internal = [internal_catalog[zid] for zid in inner_ids if zid in internal_catalog]
                inner_valid = len(inner_ids) == len(set(inner_ids)) == len(internal)
                c = Movement(f"C:{part[0].id}:{part[-1].id}", b.level, f"{side}trend",
                             part[0].start, part[-1].end, min(m.low for m in part), max(m.high for m in part),
                             tuple(m.id for m in part), inner_ids,
                             max(m.evidence_at for m in [*part, children[stop]]),
                             "completed", "confirmed_component_boundary")
                audit = audit_divergence(a, A, b, B, c, internal, third, waves,
                                         higher_centers=[*centers, *center_catalog.values()],
                                         lineage_valid=inner_valid and all(m.status == "completed" for m in part))
                audit["c_components"] = list(c.child_ids)
                audit["internal_center_ids"] = list(inner_ids)
                audits.append(audit)
                if audit["strict_divergence_confirmed"]:
                    confirmed.append(audit)
                    label = "S1" if side == "up" else "B1"
                    first_signal = Signal(f"{audit['id']}:{label}", label, layer["level"], c.end.time,
                                          c.end.price, audit["evidence_at"], "policy_confirmed", stop - 1,
                                          B.id, (audit["id"],), {"strength_policy": "cross_kiss_sma"})
                    signals.append(first_signal)
                    if stop + 1 < len(children):
                        rebound, retest = children[stop:stop + 2]
                        if direction(rebound) != side and direction(retest) == side:
                            weak = retest.end.price > c.end.price if side == "up" else retest.end.price < c.end.price
                            signals.append(Signal(f"{audit['id']}:second", "S2" if side == "up" else "B2",
                                                  layer["level"], retest.end.time, retest.end.price,
                                                  max(audit["evidence_at"], rebound.evidence_at, retest.evidence_at),
                                                  "policy_dependent", stop + 1, B.id,
                                                  (first_signal.id, rebound.id, retest.id),
                                                  {"strength": "weak_new_extreme" if weak else "holds_first"}))
        layers.append({"level": layer["level"], "audits": audits, "signals": signals,
                       "evaluation_status": "audited" if audits else "not_evaluated",
                       "waiting_reason": (None if audits else "fewer_than_two_centers" if len(centers) < 2
                                          else "no_completed_departure_return_envelope"),
                       "structural_view": view})
        children = layer["movements"]
    nested = []
    for parent in confirmed:
        for child in confirmed:
            if (parent["level"] == child["level"] + 1 and parent["side"] == child["side"]
                    and parent["start"] <= child["start"] < child["end"] <= parent["end"]):
                nested.append({"parent_id": parent["id"], "child_id": child["id"],
                               "evidence_at": max(parent["evidence_at"], child["evidence_at"]),
                               "certainty": "policy_confirmed_nested_interval"})
    return {"levels": layers, "confirmed": confirmed, "nested_intervals": nested,
            "strength": {"policy": "cross_kiss_sma", "fast": fast, "slow": slow,
                         "units": "price_hours", "integration": "linear_zero_crossing_trapezoids",
                         "kiss_definition": "cross_or_touch_zero_only", "waves": waves},
            "scope": "标准内部趋势分支；指定均线力度策略下确认，不等同完整动力学证明"}
