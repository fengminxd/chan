"""以真实 1m 为唯一基底的完成走势递归树、历史检查和多周期投影。"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

from chan import (Candle, analyze, json_default, parse_time, read_csv,
                  resample, split_contiguous, validate_candles)
from chan_policy import AnalysisPolicy, candle_digest, load_policy
from chan_structure import direct_break_marks
from chan_time import PERIODS, PERIOD_MINUTES, period_delta

MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class HistoryPolicy:
    min_context_bars: int = 1440
    required_level: int = 2
    check_stability: bool = True

    def __post_init__(self):
        if type(self.min_context_bars) is not int or self.min_context_bars < 0:
            raise ValueError("min_context_bars 必须为非负整数")
        if type(self.required_level) is not int or not 1 <= self.required_level <= 20:
            raise ValueError("required_level 必须在 1..20")
        if type(self.check_stability) is not bool:
            raise ValueError("check_stability 必须为布尔值")


def merge_sources(candles, context=()):
    """允许相同记录重叠；冲突不擅自选择新旧来源。"""
    validate_candles(candles)
    validate_candles(context)
    rows = {}
    duplicates = 0
    for c in [*context, *candles]:
        if c.time in rows:
            if c != rows[c.time]:
                raise ValueError(f"前置行情与目标行情冲突：{c.time.isoformat()}")
            duplicates += 1
        rows[c.time] = c
    merged = sorted(rows.values(), key=lambda c: c.time)
    return merged, {"input_bars": len(candles), "context_input_bars": len(context),
                    "identical_duplicates_removed": duplicates}


def prepare_minutes(candles, as_of, context=(), anchor_hour=0):
    raw, sources = merge_sources(candles, context)
    closed, quality = resample(raw, PERIODS["1m"], PERIODS["1m"], as_of, anchor_hour)
    if len(closed) > 1 and not any(b.time-a.time == MINUTE for a, b in zip(closed, closed[1:])):
        raise ValueError("未发现相邻 1m 记录；本入口需要真实 1m 数据，不能把 1h/5m 数据当作 1m")
    return closed, {**sources, **quality}


def build_graph(base_report, as_of):
    """只发布 completed_type 树；三段近似层级留在 base_analysis 中供独立对照。"""
    nodes, blocks, audits, signals = {}, [], {}, {}
    for block in base_report["blocks"]:
        bid = "1m:" + block["start"].isoformat()
        aliases = {}

        def key(kind, level, start):
            return f"{bid}:{kind}:L{level}:{start.isoformat()}"

        def put(kind, level, start, end, low, high, state, evidence, **fields):
            uid = key(kind, level, start)
            if uid in nodes:
                raise ValueError(f"统一树对象身份冲突：{uid}")
            nodes[uid] = {"id": uid, "block_id": bid, "kind": kind, "level": level,
                          "start": start, "end": end, "low": low, "high": high,
                          "state": state, "evidence_at": evidence, "child_ids": [],
                          "parent_id": None, "center_ids": [], "center_membership_ids": [],
                          "promotion_ready": state == "completed" and kind in ("segment", "movement"),
                          **fields}
            return uid

        breaks = direct_break_marks(block)
        for i, s in enumerate(block["strokes"]):
            uid = put("stroke", -1, s.start.time, s.end.time, s.low, s.high,
                      "candidate" if s.provisional else "completed", s.confirmed_at,
                      start_price=s.start.price, end_price=s.end.price,
                      raw_interval={"start": s.start.time, "end": s.end.time}, details=asdict(s),
                      **breaks.get(i, {}))
            aliases[f"BI:{i}"] = uid
        for i, s in enumerate(block["segments"]):
            uid = put("segment", 0, s.start.time, s.end.time, s.low, s.high, "completed", s.evidence_at,
                      child_ids=[aliases[f"BI:{j}"] for j in range(s.first_stroke, s.last_stroke+1)],
                      start_price=s.start.price, end_price=s.end.price, completion=s.confirmation, details=asdict(s))
            aliases[f"L0:M{i}"] = uid
        pending = block["pending_segment"]
        if pending:
            put("segment", 0, pending.start.time, pending.end.time, pending.low, pending.high,
                "pending", None, start_price=pending.start.price, end_price=pending.end.price,
                child_ids=[aliases[f"BI:{j}"] for j in range(pending.first_stroke, pending.last_stroke+1)],
                waiting=block["segment_diagnostics"], details=asdict(pending))
        hierarchy = block["theory"]["type_hierarchy"]
        for layer in hierarchy["levels"]:
            moves = [*layer["movements"], *([layer["active_movement"]] if layer["active_movement"] else [])]
            for m in moves:
                uid = put("movement", m.level, m.start.time, m.end.time, m.low, m.high,
                          m.status, m.evidence_at, child_ids=[aliases[c] for c in m.child_ids],
                          start_price=m.start.price, end_price=m.end.price, movement_kind=m.kind,
                          completion=m.completion, local_id=m.id)
                aliases[m.id] = uid
            owners = {zid: aliases[m.id] for m in moves for zid in m.center_ids}
            for z in [*layer["centers"], *layer.get("historical_centers", [])]:
                uid = put("center", z.level, z.start, z.end, z.range_low, z.range_high,
                          "formed", z.evidence_at, child_ids=[aliases[c] for c in z.seed_ids],
                          extension_child_ids=[aliases[c] for c in z.child_ids], zd=z.zd, zg=z.zg,
                          updated_at=z.updated_at, formed_at=z.formed_at, evolution=z.status,
                          closed_at=z.closed_at, range_scope="observation",
                          owner_movement_id=owners.get(z.id), local_id=z.id,
                          historical=z in layer.get("historical_centers", []))
                aliases[z.id] = uid
                if z.id in owners:
                    nodes[owners[z.id]]["center_ids"].append(uid)
        for z in hierarchy["next_level_center_seeds"]:
            members = [aliases[c] for c in z["child_ids"]]
            put("center_seed", z["level"], z["start"], z["end"], min(nodes[c]["low"] for c in members),
                max(nodes[c]["high"] for c in members), "formed_parent_uncompleted", z["evidence_at"],
                child_ids=members, zd=z["zd"], zg=z["zg"], owner_movement_id=None,
                formation="three_completed_children_without_entry", formed_at=z["end"])
        # 审计与信号也进入同一注册表；只导出完成走势通路，拒绝悬空旧 ID。
        dynamics = block["theory"].get("strict_divergence", {}).get("levels", [])
        audit_ids = {a["id"]: f"{bid}:audit:{a['id']}" for layer in dynamics for a in layer["audits"]}
        signal_ids = {s.id: f"{bid}:signal:{s.id}" for layer in dynamics for s in layer["signals"]}
        for layer in dynamics:
            for a in layer["audits"]:
                refs = {name: aliases[a["pattern"][name]] for name in ("a", "A", "b", "B")}
                refs["c_components"] = [aliases[c] for c in a["c_components"]]
                refs["internal_centers"] = [aliases[c] for c in a["internal_center_ids"]]
                uid = audit_ids[a["id"]]
                audits[uid] = {**a, "id": uid, "local_id": a["id"], "block_id": bid,
                               "node_refs": refs, "structure_source": "completed_type_tree"}
            for s in layer["signals"]:
                unknown = set(s.source_ids) - aliases.keys() - audit_ids.keys() - signal_ids.keys()
                if unknown:
                    raise ValueError(f"信号引用不在统一注册表中：{sorted(unknown)}")
                source_nodes = [aliases[c] for c in s.source_ids if c in aliases]
                source_audits = [audit_ids[c] for c in s.source_ids if c in audit_ids]
                source_signals = [signal_ids[c] for c in s.source_ids if c in signal_ids]
                uid = signal_ids[s.id]
                signals[uid] = {**asdict(s), "id": uid, "local_id": s.id, "block_id": bid,
                                "center_id": aliases[s.center_id],
                                "source_ids": source_nodes + source_audits + source_signals,
                                "source_node_ids": source_nodes, "source_audit_ids": source_audits,
                                "source_signal_ids": source_signals,
                                "structure_source": "completed_type_tree"}
        blocks.append({"id": bid, "start": block["start"], "end": block["end"],
                       "bar_count": block["bar_count"], "left_boundary": "unknown_before_input_or_gap",
                       "levels": [{"level": x["level"], "completed_movements": len(x["movements"]),
                                   "diagnostics": x["diagnostics"]} for x in hierarchy["levels"]],
                       "level_limit_reached": hierarchy["level_limit_reached"],
                       "segment_waiting": block["segment_diagnostics"]})
    # 实际所属走势和成枢引用分别记录，避免把同一中枢成员当成多个父走势所有权。
    for uid, node in nodes.items():
        if node["kind"] in ("segment", "movement"):
            for cid in node["child_ids"]:
                owner = nodes[cid]["parent_id"]
                if owner is not None and owner != uid:
                    raise ValueError("同一子走势不能同时属于两个父走势")
                nodes[cid]["parent_id"] = uid
        elif node["kind"] in ("center", "center_seed"):
            for cid in node["child_ids"]:
                nodes[cid]["center_membership_ids"].append(uid)
    graph = {"base_period": "1m", "policy": "confirmed_type_repartition_v4",
             "nodes": nodes, "blocks": blocks, "audits": audits, "signals": signals,
             "roots": [uid for uid, n in nodes.items() if n["kind"] in ("segment", "movement") and n["parent_id"] is None],
             "scope": "one_tree_per_contiguous_1m_block; chart_period_does_not_assign_structural_level"}
    validate_graph(graph, as_of)
    return graph


def validate_graph(graph, as_of):
    nodes = graph["nodes"]
    for uid, node in nodes.items():
        if node["end"] > as_of or node["evidence_at"] is not None and node["evidence_at"] > as_of:
            raise ValueError("统一树包含尚未可见的结构或证据")
        children = [nodes[c] for c in node["child_ids"]]
        owner = node.get("parent_id")
        if owner is not None and (owner not in nodes or uid not in nodes[owner]["child_ids"] or
                                  nodes[owner]["level"] != node["level"]+1):
            raise ValueError("父子反向引用不一致")
        for cid in node["center_ids"]:
            if cid not in nodes or nodes[cid].get("owner_movement_id") != uid:
                raise ValueError("中枢与所属走势反向引用不一致")
        if any(c["block_id"] != node["block_id"] for c in children):
            raise ValueError("统一树不能跨缺失行情区段连接")
        if any(c["level"] != node["level"]-1 for c in children):
            raise ValueError("父子必须是直接相邻结构级别")
        if len(set(node["child_ids"])) != len(children):
            raise ValueError("子走势重复")
        for a, b in zip(children, children[1:]):
            if a["end"] != b["start"] or a.get("end_price") != b.get("start_price"):
                raise ValueError("子走势必须端点和价格连续，不能跳段")
        if node["kind"] in ("movement", "center", "center_seed") or node["kind"] == "segment" and node["state"] == "completed":
            if any(c["state"] != "completed" for c in children):
                raise ValueError("未完成的子走势不能成枢或进入上层走势")
            if node["evidence_at"] is None or any(c["evidence_at"] is None or c["evidence_at"] > node["evidence_at"] for c in children):
                raise ValueError("父结构证据不得早于子结构证据")
        if node["kind"] in ("center", "center_seed"):
            if len(children) != 3 or node["zd"] != max(c["low"] for c in children) or node["zg"] != min(c["high"] for c in children) or node["zd"] > node["zg"]:
                raise ValueError("中枢须由连续三个完成子走势的真实价格区间重叠形成")
            owner = node.get("owner_movement_id")
            if owner is not None and (owner not in nodes or not set(node["child_ids"]).issubset(nodes[owner]["child_ids"])):
                raise ValueError("中枢成员必须属于其标记的父走势")
            if node["kind"] == "center":
                members = [nodes[c] for c in node["extension_child_ids"]]
                if (not members or not set(node["child_ids"]).issubset(node["extension_child_ids"])
                        or any(m["block_id"] != node["block_id"] or m["level"] != node["level"]-1
                               or m["state"] != "completed" for m in members)
                        or len({m["id"] for m in members}) != len(members)
                        or any(a["end"] != b["start"] or a["end_price"] != b["start_price"]
                               for a, b in zip(members, members[1:]))
                        or node["start"] != members[0]["start"] or node["end"] != members[-1]["end"]
                        or node["low"] != min(m["low"] for m in members)
                        or node["high"] != max(m["high"] for m in members)
                        or any(m["evidence_at"] is None or m["evidence_at"] > node["updated_at"] for m in members)
                        or node["updated_at"] > as_of
                        or node.get("closed_at") is not None and not node["updated_at"] <= node["closed_at"] <= as_of):
                    raise ValueError("中枢观察范围或延伸成员证据不一致")
        if node["kind"] == "movement" and node["state"] == "completed" and not node.get("completion"):
            raise ValueError("完成走势必须保留完成原因")
        if node["kind"] in ("segment", "movement") and children:
            if node["start"] != children[0]["start"] or node["end"] != children[-1]["end"]:
                raise ValueError("父走势边界必须与其全部子走势相同")
            if node["low"] != min(c["low"] for c in children) or node["high"] != max(c["high"] for c in children):
                raise ValueError("父走势范围必须包含全部子走势波动")
    for audit in graph.get("audits", {}).values():
        refs = audit["node_refs"]
        ids = [refs[k] for k in ("a", "A", "b", "B")] + refs["c_components"] + refs["internal_centers"]
        if any(uid not in nodes for uid in ids):
            raise ValueError("背驰引用不在统一树中")
        if any(nodes[uid]["block_id"] != audit["block_id"] or
               nodes[uid]["evidence_at"] is None or nodes[uid]["evidence_at"] > audit["evidence_at"] for uid in ids):
            raise ValueError("背驰引用跨区段或早于组成证据")
        if audit["evidence_at"] > as_of:
            raise ValueError("背驰包含未来证据")
        if any(nodes[refs[k]]["kind"] != "center" or nodes[refs[k]]["level"] != audit["level"] for k in ("A", "B")):
            raise ValueError("背驰必须引用本层统一中枢")
        parts = [nodes[uid] for uid in refs["c_components"]]
        if not parts or any(p["state"] != "completed" or p["level"] != audit["level"] - 1 for p in parts):
            raise ValueError("背驰离开区间只接受完成的直接次级走势")
        if parts[0]["start"] != audit["start"] or parts[-1]["end"] != audit["end"] or any(
                a["end"] != b["start"] or a["end_price"] != b["start_price"] for a, b in zip(parts, parts[1:])):
            raise ValueError("背驰离开区间必须连续并匹配端点")
    for signal in graph.get("signals", {}).values():
        center = nodes.get(signal["center_id"])
        if center is None or center["kind"] != "center" or center["level"] != signal["level"]:
            raise ValueError("信号必须引用本层统一中枢")
        if not signal["time"] <= signal["evidence_at"] <= as_of:
            raise ValueError("信号时间或证据无效")
        sources = [center]
        for key, catalog in (("source_node_ids", nodes), ("source_audit_ids", graph.get("audits", {})),
                             ("source_signal_ids", graph.get("signals", {}))):
            if any(uid not in catalog for uid in signal[key]):
                raise ValueError("信号依赖不在统一注册表中")
            sources.extend(catalog[uid] for uid in signal[key])
        if any(s["block_id"] != signal["block_id"] or s["evidence_at"] is None or
               s["evidence_at"] > signal["evidence_at"] for s in sources):
            raise ValueError("信号引用跨区段或早于组成证据")
        if signal["label"] in ("B3", "S3"):
            parts = [nodes[uid] for uid in signal["source_node_ids"]]
            if len(parts) != 2 or any(p["state"] != "completed" or p["level"] != signal["level"] - 1 for p in parts):
                raise ValueError("三类点须由已完成的直接次级离开和回试确认")
            prior, retest = parts
            if prior["end"] != retest["start"] or prior["end_price"] != retest["start_price"]:
                raise ValueError("三类点离开和回试必须连续")
            valid = (prior["end_price"] > prior["start_price"] and retest["end_price"] < retest["start_price"]
                     and retest["low"] > center["zg"]) if signal["label"] == "B3" else (
                     prior["end_price"] < prior["start_price"] and retest["end_price"] > retest["start_price"]
                     and retest["high"] < center["zd"])
            if not valid or signal["time"] != retest["end"] or signal["price"] != retest["end_price"]:
                raise ValueError("三类点回试或端点不符合统一结构")
        if signal["label"] in ("B1", "S1") and (not signal["source_audit_ids"] or any(
                graph["audits"][uid]["state"] != "confirmed_under_policy" for uid in signal["source_audit_ids"])):
            raise ValueError("一类点必须依赖策略确认的背驰")
    return True


def target_signature(graph, start):
    """比较语义和端点，不比较因前置区间变化而重新编号的局部 ID。"""
    def identity(n):
        return (n["kind"], n["level"], n["start"], n["end"], n["low"], n["high"], n["state"],
                n["evidence_at"], n.get("zd"), n.get("zg"))
    values = [(identity(n), tuple(identity(graph["nodes"][c]) for c in n["child_ids"]))
              for n in graph["nodes"].values() if n["kind"] in ("segment", "movement", "center", "center_seed") and n["end"] >= start]
    return hashlib.sha256(json.dumps(sorted(values, key=str), default=json_default, separators=(",", ":")).encode()).hexdigest(), len(values)


def inspect_history(candles, graph, start, as_of, policy, history_policy):
    runs = split_contiguous(candles, PERIODS["1m"])
    relevant = [run for run in runs if run[-1].time+MINUTE > start]
    gaps = [{"after": a.time, "next": b.time, "missing_minutes": int((b.time-a.time)/MINUTE)-1}
            for a, b in zip(candles, candles[1:]) if b.time-a.time != MINUTE]
    first_run = relevant[0] if relevant else []
    before = [c for c in first_run if c.time < start]
    reasons = []
    if not candles or not relevant or first_run[0].time > start:
        reasons.append("target_start_not_covered")
    if not before:
        reasons.append("no_contiguous_pre_context")
    if len(before) < history_policy.min_context_bars:
        reasons.append("pre_context_below_configured_floor")
    if any(g["next"] >= start for g in gaps):
        reasons.append("target_window_has_gaps")
    if not candles or candles[-1].time+MINUTE < as_of.replace(second=0, microsecond=0):
        reasons.append("target_end_not_covered")
    signature, count = target_signature(graph, start)
    stability = {"status": "not_checked", "full_signature": signature, "target_structure_count": count,
                 "scope": "finite_context_sensitivity_check_not_proof_of_complete_history"}
    if history_policy.check_stability and len(before) >= 2:
        shortened = first_run[len(before)//2:]
        # 后续间断区段也保留，所有比较在同一 as_of 下进行。
        shortened = [*shortened, *[c for run in relevant[1:] for c in run]]
        comparison = build_graph(analyze(shortened, PERIODS["1m"], **policy.kwargs()), as_of)
        other, other_count = target_signature(comparison, start)
        stable = signature == other
        stability.update(status="stable_on_available_contexts" if stable and count else
                         "no_target_structure" if not count and not other_count else "sensitive_to_context",
                         shorter_start=shortened[0].time, shorter_signature=other,
                         full_context_bars=len(before), shorter_context_bars=len(before)-len(before)//2)
        if not stable:
            reasons.append("target_structure_changes_with_context")
        if stable and not count:
            reasons.append("no_target_structure_to_compare")
    else:
        reasons.append("context_stability_unverified")
    levels = []
    for level in range(1, policy.max_levels+1):
        completed = [n for n in graph["nodes"].values() if n["kind"] == "movement" and n["level"] == level and n["state"] == "completed"]
        centers = [n for n in graph["nodes"].values() if n["kind"] in ("center", "center_seed") and n["level"] == level]
        levels.append({"level": level, "completed_movements": len(completed), "formed_centers": len(centers),
                       "target_completed_movements": sum(n["end"] >= start for n in completed)})
    requested = levels[history_policy.required_level-1]
    if not requested["target_completed_movements"]:
        reasons.append("requested_level_has_no_completed_movement_in_target")
    if any(b["level_limit_reached"] for b in graph["blocks"]):
        reasons.append("configured_recursion_limit_reached")
    return {"status": "usable_under_checks" if not reasons else "needs_more_evidence", "reasons": reasons,
            "policy": asdict(history_policy), "target_start": start, "as_of": as_of,
            "first_available": candles[0].time if candles else None,
            "contiguous_context_bars": len(before), "gaps": gaps, "stability": stability, "levels": levels,
            "history_sufficiency_proven": False,
            "next_action": "可补充更早的真实 1m CSV 后重算稳定性；走势尚未完成则等待新行情，不自动降低完成门槛" if reasons else None}


def project_views(candles, graph, periods, as_of, start, anchor_hour):
    views = {}
    for period in periods:
        bars, quality = resample(candles, PERIODS["1m"], PERIODS[period], as_of, anchor_hour)
        width = period_delta(PERIODS[period])
        bars = [c for c in bars if c.time+width > start]
        times = [c.time for c in bars]
        projections = []
        for uid, n in graph["nodes"].items():
            if n["end"] < start or n["kind"] == "stroke":
                continue
            first = max(0, bisect_right(times, n["start"])-1)
            last = bisect_right(times, n["end"])-1
            if first < len(bars) and bars[first].time+width <= n["start"]:
                first += 1
            count = max(0, last-first+1)
            projections.append({"node_id": uid, "level": n["level"], "first_bar": first if count else None,
                                "last_bar": last if count else None, "mapped_bar_count": count,
                                "exact_start": n["start"], "exact_end": n["end"],
                                "endpoint_in_closed_view": last >= 0 and bars[last].time <= n["end"] < bars[last].time+width,
                                "status": "mapped" if count else "outside_closed_view_bars"})
        views[period] = {"period": period, "period_minutes": PERIOD_MINUTES[period], "candles": bars,
                         "data_quality": quality, "projections": projections,
                         "structure_source": "shared_1m_tree", "independently_recalculated": False,
                         "signal_scope": "projection_is_not_an_independent_confirmation_signal"}
    return views


def analyze_unified(candles, as_of, start=None, periods=None, context=(), anchor_hour=0,
                    policy=None, history_policy=None):
    policy, history_policy = policy or AnalysisPolicy(), history_policy or HistoryPolicy()
    if policy.mode != "strict":
        raise ValueError("统一树需要 strict 模式")
    if history_policy.required_level > policy.max_levels:
        raise ValueError("required_level 不能超过 max_levels")
    periods = list(dict.fromkeys(periods if periods is not None else PERIODS))
    if not periods or set(periods)-PERIODS.keys():
        raise ValueError("视图周期无效")
    closed, quality = prepare_minutes(candles, as_of, context, anchor_hour)
    start = start or (closed[0].time if closed else as_of)
    if start.tzinfo is None or start >= as_of:
        raise ValueError("start 须带时区且早于 as_of")
    base = analyze(closed, PERIODS["1m"], **policy.kwargs())
    graph = build_graph(base, as_of)
    history = inspect_history(closed, graph, start, as_of, policy, history_policy)
    return {"schema": "chan-unified-v1", "as_of": as_of, "target_start": start, "source_period": "1m",
            "input": {"closed_bar_count": len(closed), "ohlcv_sha256": candle_digest(closed), "quality": quality,
                      "anchor_hour_utc": anchor_hour}, "rule_manifest": policy.manifest(),
            "tree": graph, "history": history,
            "views": project_views(closed, graph, periods, as_of, start, anchor_hour),
            "base_analysis": base,
            "scope": "completed_1m_type_tree; legacy_earliest_triple_is_only_in_base_analysis_not_promoted_to_views"}


def unified_snapshot(graph):
    items = {}
    for uid, n in graph["nodes"].items():
        kind = "movement_type" if n["kind"] == "movement" else "segment" if n["kind"] == "segment" else "stroke" if n["kind"] == "stroke" else "recursive_center"
        confirmed = n["state"] == "completed" or (kind == "recursive_center" and n["evidence_at"] is not None)
        value = {**n, "status": n["state"], "confirmed": confirmed,
                 "provisional": not confirmed, "confirmed_at": n["evidence_at"]}
        items[uid] = {"type": kind, "value": value}
    for uid, value in graph.get("signals", {}).items():
        items[uid] = {"type": "policy_signal", "value": value}
    for uid, value in graph.get("audits", {}).items():
        items[uid] = {"type": "divergence_audit", "value": value}
    return items


def replay_unified(candles, as_of, context=(), policy=None, step=1, anchor_hour=0):
    """唯一 1m 事件流；投影视图不得复制为多个独立交易信号。"""
    from replay import LifecycleTracker
    policy = policy or AnalysisPolicy()
    if type(step) is not int or step < 1 or policy.mode != "strict":
        raise ValueError("统一回放需要正整数 step 和 strict 模式")
    closed, quality = prepare_minutes(candles, as_of, context, anchor_hour)
    tracker = LifecycleTracker()
    stops = list(range(1, len(closed)+1, step))
    if closed and (not stops or stops[-1] != len(closed)):
        stops.append(len(closed))
    for stop in stops:
        time = closed[stop-1].time+MINUTE
        base = analyze(closed[:stop], PERIODS["1m"], **policy.kwargs())
        graph = build_graph(base, time)
        current = unified_snapshot(graph)
        yield {"as_of": time, "period": "1m", "source_bar_count": stop, "target_bar_count": stop,
               "sampling_step_source_bars": step, "events": tracker.update(current, time),
               "object_count": len(current), "structure_source": "shared_1m_tree",
               "rule_fingerprint": policy.manifest()["fingerprint"], "volume_price": base["volume_price"],
               "data_quality": {"gaps_seen": base["gap_count"]},
               "signal_origin": "completed_type_tree_registry; no_projection_confirmation"}


def minute_demo():
    """三组确定性正弦波叠加的一周合成分钟行情，与任何真实行情无关。"""
    begin, previous, candles = datetime(2022, 1, 1, tzinfo=timezone.utc), 10000.0, []
    for i in range(10080):
        close = 10000+300*math.sin(i/480)+100*math.sin(i/72)+30*math.sin(i/10)
        candles.append(Candle(begin+i*MINUTE, previous, max(previous, close)+.1,
                              min(previous, close)-.1, close, 100+i%31))
        previous = close
    return candles


def write_outputs(report, folder):
    from unified_viewer import write_unified_viewer
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder/"analysis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                 default=json_default, allow_nan=False), encoding="utf-8")
    write_unified_viewer(folder/"chart.html", report)
    lines = ["# 1m 统一递归树", "", f"截止：{report['as_of'].isoformat()}",
             f"输入尺度：1m；已收盘根数：{report['input']['closed_bar_count']}；来源：{report.get('data_origin', 'caller_supplied')}",
             f"历史检查：{report['history']['status']}；原因：{', '.join(report['history']['reasons']) or '当前检查通过'}", "",
             "所有周期图引用相同 node_id；L 层级不等于分钟/小时周期。", "",
             "| 结构层 | 已完成走势 | 已形成中枢 |", "| --- | ---: | ---: |"]
    for row in report["history"]["levels"]:
        lines.append(f"| L{row['level']} | {row['completed_movements']} | {row['formed_centers']} |")
    lines += ["", "历史数量阈值与有限窗口稳定性均不证明历史绝对充分。未完成走势继续等待，不向上递归。",
              "base_analysis 保留旧最早三段视角和原有指标；tree/views 只使用完成走势递归。"]
    (folder/"report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    data = p.add_mutually_exclusive_group(required=True)
    data.add_argument("--csv", help="真实 1m OHLCV")
    data.add_argument("--demo", action="store_true", help="合成 1m 数据，不是真实行情")
    p.add_argument("--context-csv", help="同一品种更早的真实 1m OHLCV，可与主文件重叠")
    p.add_argument("--start")
    p.add_argument("--as-of")
    p.add_argument("--periods", nargs="+", choices=PERIODS)
    p.add_argument("--anchor-hour", type=int, default=0)
    p.add_argument("--policy", type=Path)
    p.add_argument("--min-context-bars", type=int, default=1440)
    p.add_argument("--required-level", type=int, default=2)
    p.add_argument("--require-history", action="store_true", help="历史/完成证据检查未通过时输出报告并返回 2")
    p.add_argument("--replay-step", type=int, help="额外生成统一回放；1 为逐根，较大值为采样")
    p.add_argument("--output-dir", type=Path, default=Path("output/unified"))
    args = p.parse_args()
    try:
        if args.demo and args.context_csv:
            raise ValueError("合成演示不能拼接真实前置行情")
        candles = minute_demo() if args.demo else read_csv(args.csv)
        context = read_csv(args.context_csv) if args.context_csv else []
        as_of = parse_time(args.as_of) if args.as_of else (candles[-1].time+MINUTE if args.demo else datetime.now(timezone.utc))
        start = parse_time(args.start) if args.start else None
        policy = load_policy(args.policy)
        report = analyze_unified(candles, as_of, start, args.periods, context, args.anchor_hour,
                                 policy, HistoryPolicy(args.min_context_bars, args.required_level))
        report["data_origin"] = "synthetic_demo" if args.demo else {"csv": args.csv, "context_csv": args.context_csv}
        write_outputs(report, args.output_dir)
        if args.replay_step is not None:
            with (args.output_dir/"replay.jsonl").open("w", encoding="utf-8") as f:
                for row in replay_unified(candles, as_of, context, policy, args.replay_step, args.anchor_hour):
                    f.write(json.dumps(row, ensure_ascii=False, default=json_default, allow_nan=False)+"\n")
    except (ValueError, OSError, TypeError) as exc:
        p.error(str(exc))
    print(f"统一树 {len(report['tree']['nodes'])} 个对象，{len(report['views'])} 个投影视图；历史状态：{report['history']['status']}")
    print(args.output_dir/"report.md")
    if args.require_history and report["history"]["status"] != "usable_under_checks":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
