"""有完成证据的走势类型递归，与 earliest_triple 的结合律分解分开。

采用保守的形态完成政策：反向三类点，或新中枢使原同向趋势失效。
转折优先取此前方向极值，失败时按极值优先检查其他合法分界；
所有候选都失败才等待，不用三段成枢代替完成。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from chan_structure import Movement
from chan_theory import (canonical_center_view, center_relation, direction,
                         evolving_centers, validate_moves)


def _named_view(children, level):
    view = evolving_centers(children, level)
    # 成枢三段的首个子走势决定身份，父走势重分解不得改变或复用它。
    names = {z.id: f"T{level}:Z:{z.seed_ids[0]}" for z in view["centers"]}
    for z in view["centers"]:
        z.id = names[z.id]
    view["signals"] = [replace(s, id=f"{names[s.center_id]}:{s.label}",
                               center_id=names[s.center_id]) for s in view["signals"]]
    for r in view["relations"]:
        r["from"], r["to"] = names[r["from"]], names[r["to"]]
    for e in view["events"]:
        if "center_id" in e:
            e["center_id"] = names[e["center_id"]]
        if "center_ids" in e:
            e["center_ids"] = [names[x] for x in e["center_ids"]]
    for r in view["trend_runs"]:
        r["center_ids"] = [names[x] for x in r["center_ids"]]
    return view


def _typed(children, level, mid, view, status, evidence, reason=None):
    centers = view["centers"]
    relations = [center_relation(a, b) for a, b in zip(centers, centers[1:])]
    kind = ("consolidation" if len(centers) == 1 else
            "uptrend" if relations and all(x == "up" for x in relations) else
            "downtrend" if relations and all(x == "down" for x in relations) else "unresolved")
    return Movement(mid, level, kind, children[0].start, children[-1].end,
                    min(m.low for m in children), max(m.high for m in children),
                    tuple(m.id for m in children), tuple(z.id for z in centers),
                    evidence, status, reason)


def _boundary(children, view, boundary_cache=None):
    """返回 (切分位置, 原因, 证据时刻)，极值同价时取最后一次。"""
    centers = view["centers"]
    if not centers:
        return None
    side = direction(children[0])
    if side == "flat":
        return None
    cache = boundary_cache if boundary_cache is not None else {}
    events = []
    opposite = "left_down" if side == "up" else "left_up"
    for z in centers:
        if z.status == opposite:
            events.append((z.return_index, "opposite_third_point", z.closed_at))
    for a, b in zip(centers, centers[1:]):
        rel = center_relation(a, b)
        if rel == "expansion" or rel in ("up", "down") and rel != side:
            events.append((b.first_index, "center_expansion" if rel == "expansion"
                           else "opposite_center", max(a.updated_at, b.updated_at)))
    for limit, reason, evidence in sorted(events, key=lambda x: x[2]):
        # 同一 begin 下 children 的历史前缀不变；仅缓存几何分界，证据时间
        # 与事件顺序仍使用本次可见视图，避免缓存将来确认时间。
        cache_key = (view["level"], centers[0].first_index, limit, side)
        if cache_key in cache:
            if cache[cache_key] is not None:
                return cache[cache_key], reason, evidence
            continue
        cache[cache_key] = None
        # 切在新中枢之前，或反向离开之前的方向极值，保证新分解不重复子走势。
        eligible = range(centers[0].first_index + 3, limit + 1)
        candidates = list(eligible)
        if not candidates:
            continue
        key = lambda stop: ((children[stop - 1].end.price if side == "up"
                             else -children[stop - 1].end.price), stop)
        # 先保留原来的极值优先、同价取最后一次政策；若该极值之前已
        # 混入扩展/反向中枢，继续检查较早合法分界。只试一个极值会
        # 把冲突中枢永久留在尾部，后续完成事件也就无法推动递归。
        for stop in sorted(candidates, key=key, reverse=True):
            chosen = children[:stop]
            if direction(chosen[-1]) != side:
                continue
            prefix = evolving_centers(chosen, view["level"])
            rels = [center_relation(a, b) for a, b in zip(prefix["centers"], prefix["centers"][1:])]
            if prefix["centers"] and all(r == side for r in rels):
                cache[cache_key] = stop
                return stop, reason, evidence
    return None


def decompose_types(children: Sequence[Movement], level: int) -> dict:
    validate_moves(children)
    if level < 1 or any(m.level != level - 1 or m.status != "completed" for m in children):
        raise ValueError("走势类型递归只接收已完成的直接次级别走势")
    if len({m.id for m in children}) != len(children):
        raise ValueError("走势 ID 不能重复")
    completed, centers, events, views = [], [], [], []
    begin = 0
    boundary_cache = {}
    published, first_seen = {}, {}
    available = []
    for m in children:
        available.append(max(m.evidence_at, available[-1] if available else m.evidence_at))
    structural = canonical_center_view([], [], level)
    # 前缀扫描确保完成证据到达之前不使用后面的走势决定分界。
    for visible in range(4, len(children) + 1):
        while visible - begin >= 4:
            tail = children[begin:visible]
            view = evolving_centers(tail, level)
            boundary = _boundary(tail, view, boundary_cache)
            if boundary is None:
                break
            stop, reason, evidence = boundary
            selected = tail[:stop]
            mid = f"T{level}:M{len(completed)}"
            own_view = _named_view(selected, level)
            evidence = max(evidence, *(m.evidence_at for m in tail))
            move = _typed(selected, level, mid, own_view, "completed", evidence, reason)
            completed.append(move)
            centers.extend(own_view["centers"])
            views.append({"movement_id": mid, **own_view})
            events.append({"kind": "completion", "movement_id": mid, "reason": reason,
                           "endpoint": move.end.time, "evidence_at": evidence})
            events.append({"kind": "repartition", "reason": reason, "movement_id": mid,
                           "previous_child_ids": [m.id for m in tail],
                           "completed_child_ids": list(move.child_ids),
                           "remaining_child_ids": [m.id for m in tail[stop:]],
                           "evidence_at": evidence})
            begin += stop
            boundary_cache.clear()
        # 只观察这个前缀最终采用的分解，不发布 while 中的临时假设。
        current = _named_view(children[begin:visible], level)
        structural = canonical_center_view(children[:visible], [*centers, *current["centers"]], level)
        for z in structural["centers"]:
            first_seen.setdefault(z.id, available[visible - 1])
            z.evidence_at = max(z.evidence_at, first_seen[z.id])
            z.updated_at = max(z.updated_at, z.evidence_at)
        by_id = {z.id: z for z in structural["centers"]}
        for signal in structural["signals"]:
            if signal.center_id not in published:
                # 重分解后才可见的中心/信号不得倒填到其几何端点。
                signal = replace(signal, evidence_at=max(signal.evidence_at, available[visible - 1]))
                z = by_id[signal.center_id]
                z.closed_at = signal.evidence_at
                published[z.id] = (signal, z)
    tail = children[begin:]
    active = None
    waiting = "fewer_than_four_children_in_entry_preserving_policy"
    if tail:
        mid = f"T{level}:M{len(completed)}"
        view = _named_view(tail, level)
        if view["centers"]:
            active = _typed(tail, level, mid, view, "extending",
                            max(m.evidence_at for m in tail))
            centers.extend(view["centers"])
            views.append({"movement_id": mid, **view})
            events.append({"kind": "continuation" if active.kind.endswith("trend") else "extension",
                           "movement_id": mid, "center_ids": list(active.center_ids),
                           "evidence_at": active.evidence_at})
            waiting = ("waiting_center_departure" if view["centers"][-1].status == "extending" else
                       "waiting_next_center_or_legal_turn")
        elif len(tail) >= 4:
            waiting = "no_overlapping_center_seed"
    # 当前中枢用于新的相离/背驰判断；历史事件保留自己的成枢实体，
    # 不把已退出当前分解的中心混入新的趋势比较，也不增加父走势成员。
    structural["centers"] = [published[z.id][1] if z.id in published else z
                             for z in structural["centers"]]
    current_ids = {z.id for z in structural["centers"]}
    historical = [z for zid, (_, z) in published.items() if zid not in current_ids]
    structural["historical_centers"] = historical
    structural["signals"] = sorted((s for s, _ in published.values()),
                                   key=lambda s: (s.evidence_at, s.time, s.id))
    return {"level": level, "movements": completed, "active_movement": active,
            "centers": structural["centers"], "decomposition_centers": centers,
            "historical_centers": historical, "structural_view": structural,
            "views": views, "events": events,
            "unassigned_child_ids": [m.id for m in tail],
            "diagnostics": {"completed_children": len(children), "completed_types": len(completed),
                            "unassigned_children": len(tail), "waiting_reason": waiting,
                            "next_level_minimum_children": 4,
                            "next_level_gate": "ready" if len(completed) >= 4 else "insufficient_completed_types",
                            "policy_note": "保留一条进入走势，另需三子走势成枢；这是本分解政策，非所有中枢都至少四段"}}


def build_type_hierarchy(base: Sequence[Movement], max_levels: int = 5) -> dict:
    if not 1 <= max_levels <= 20:
        raise ValueError("max_levels 必须在 1..20")
    if any(m.level != 0 for m in base):
        raise ValueError("递归基底必须是 L0 已确认线段")
    layers, children, seeds = [], list(base), []
    for level in range(1, max_levels + 1):
        layer = decompose_types(children, level)
        layers.append(layer)
        children = layer["movements"]
        if len(children) < 4:
            # 三个完成子走势已经可以成枢；显式进入段政策只阻止把它们
            # 当成已完成父走势，不能把已经存在的三段交集也隐藏掉。
            if len(children) == 3 and level < max_levels:
                zd, zg = max(m.low for m in children), min(m.high for m in children)
                if zd <= zg:
                    seeds.append({"level": level+1, "zd": zd, "zg": zg,
                                  "start": children[0].start.time, "end": children[-1].end.time,
                                  "child_ids": [m.id for m in children],
                                  "evidence_at": max(m.evidence_at for m in children),
                                  "state": "center_formed_parent_movement_uncompleted"})
            break
    return {"policy": "confirmed_type_repartition_v4", "base_movements": list(base),
            "levels": layers, "next_level_center_seeds": seeds,
            "level_limit_reached": len(children) >= 4 and len(layers) == max_levels,
            "completion_policy": "opposite_third_point_or_incompatible_center",
            "boundary_policy": "directional_extreme_ranked_legal_prefix; latest_equal_extreme",
            "left_boundary_policy": "first_child_is_entry; incomplete_left_context_is_not_invented"}
