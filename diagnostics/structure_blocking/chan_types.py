"""有完成证据的走势类型递归，与 earliest_triple 的结合律分解分开。

采用保守的形态完成政策：反向三类点，或新中枢使原同向趋势失效。
转折取此前最后一个方向极值；找不到合法分界则等待，不用三段成枢代替完成。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Sequence

from chan_structure import Movement
from chan_theory import center_relation, direction, evolving_centers, validate_moves


def _named_view(children, level, namespace):
    view = evolving_centers(children, level)
    names = {z.id: f"{namespace}:Z{i}" for i, z in enumerate(view["centers"])}
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


def _boundary(children, view):
    """返回 (切分位置, 原因, 证据时刻)，极值同价时取最后一次。"""
    centers = view["centers"]
    if not centers:
        return None
    side = direction(children[0])
    if side == "flat":
        return None
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
        # 切在新中枢之前，或反向离开之前的方向极值，保证新分解不重复子走势。
        eligible = range(centers[0].first_index + 3, limit + 1)
        candidates = list(eligible)
        if not candidates:
            continue
        key = lambda stop: ((children[stop - 1].end.price if side == "up"
                             else -children[stop - 1].end.price), stop)
        stop = max(candidates, key=key)
        chosen = children[:stop]
        extreme = max(m.high for m in chosen) if side == "up" else min(m.low for m in chosen)
        if chosen[-1].end.price != extreme or direction(chosen[-1]) != side:
            continue
        prefix = evolving_centers(chosen, view["level"])
        rels = [center_relation(a, b) for a, b in zip(prefix["centers"], prefix["centers"][1:])]
        if prefix["centers"] and all(r == side for r in rels):
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
    # 前缀扫描确保完成证据到达之前不使用后面的走势决定分界。
    for visible in range(4, len(children) + 1):
        while visible - begin >= 4:
            tail = children[begin:visible]
            view = evolving_centers(tail, level)
            boundary = _boundary(tail, view)
            if boundary is None:
                break
            stop, reason, evidence = boundary
            selected = tail[:stop]
            mid = f"T{level}:M{len(completed)}"
            own_view = _named_view(selected, level, mid)
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
    tail = children[begin:]
    active = None
    if tail:
        mid = f"T{level}:M{len(completed)}"
        view = _named_view(tail, level, mid)
        if view["centers"]:
            active = _typed(tail, level, mid, view, "extending",
                            max(m.evidence_at for m in tail))
            centers.extend(view["centers"])
            views.append({"movement_id": mid, **view})
            events.append({"kind": "continuation" if active.kind.endswith("trend") else "extension",
                           "movement_id": mid, "center_ids": list(active.center_ids),
                           "evidence_at": active.evidence_at})
    return {"level": level, "movements": completed, "active_movement": active,
            "centers": centers, "views": views, "events": events,
            "unassigned_child_ids": [m.id for m in tail]}


def build_type_hierarchy(base: Sequence[Movement], max_levels: int = 5) -> dict:
    if not 1 <= max_levels <= 20:
        raise ValueError("max_levels 必须在 1..20")
    if any(m.level != 0 for m in base):
        raise ValueError("递归基底必须是 L0 已确认线段")
    layers, children = [], list(base)
    for level in range(1, max_levels + 1):
        layer = decompose_types(children, level)
        layers.append(layer)
        children = layer["movements"]
        if len(children) < 4:
            break
    return {"policy": "confirmed_type_repartition_v1", "base_movements": list(base),
            "levels": layers, "level_limit_reached": len(children) >= 4 and len(layers) == max_levels,
            "completion_policy": "opposite_third_point_or_incompatible_center",
            "left_boundary_policy": "first_child_is_entry; incomplete_left_context_is_not_invented"}
