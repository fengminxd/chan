"""用户指定的主级别候选 -> 次级别完成 -> 执行级别一/二类点矩阵。"""
from dataclasses import asdict

from chan_theory import direction
from chan_time import PERIODS, period_delta

ROUTES = {
    "4h": {"sub_period": "1h", "execution_periods": ("15m",)},
    "2h": {"sub_period": "30m", "execution_periods": ("5m", "15m")},
}
MATRIX_PERIODS = ("4h", "2h", "1h", "30m", "15m", "5m", "1m")


def rule_for(period, label):
    if period not in ROUTES or label not in ("B1", "B2", "B3", "S1", "S2", "S3"):
        raise ValueError("矩阵只支持4h/2h的一二三类买卖候选")
    kinds = (2,) if label[1] == "2" else (1, 2)
    return {**ROUTES[period], "main_period": period, "main_label": label,
            "execution_labels": tuple(label[0]+str(k) for k in kinds),
            "sub_requirement": {"1": "trend_completed", "2": "pullback_completed",
                                "3": "first_retest_completed_outside_center"}[label[1]]}


def completed_submovement(candidate, frame, minutes, as_of):
    """使用次级别图表L1已完成走势类型，不用一笔破坏/单线段替代完成证据。"""
    rule = rule_for(candidate["period"], candidate["label"])
    block = frame.get("block")
    levels = block.get("theory", {}).get("type_hierarchy", {}).get("levels", []) if block else []
    layer = next((x for x in levels if x["level"] == 1), None)
    waiting = dict(state="waiting_submovement", reason="尚无对应次级别L1走势完成证据", movement=None)
    if not layer:
        return waiting
    moves = sorted(layer["movements"], key=lambda m: m.start.time)
    scope = candidate["interval"]
    down = candidate["side"] == "long"
    side = "down" if down else "up"
    width = period_delta(PERIODS[rule["sub_period"]])
    first_retest = None
    if candidate["label"][1] == "3":
        boundary = candidate["details"]["zg" if down else "zd"]
        departure_start = candidate["details"]["departure_start"]
        for prior, move in zip(moves, moves[1:]):
            if (prior.start.time >= departure_start and prior.end == move.start and
                    prior.level == 1 and prior.status == "completed" and prior.completion and prior.evidence_at <= as_of and
                    direction(prior) == ("up" if down else "down") and direction(move) == side and
                    (prior.end.price > boundary if down else prior.end.price < boundary)):
                first_retest = move.id
                break
    for move in moves:
        if (move.level != 1 or move.kind not in ("consolidation", side+"trend") or
                move.status != "completed" or not move.completion or move.evidence_at > as_of or
                move.evidence_at < move.end.time or direction(move) != side):
            continue
        if not (scope["start"] <= move.start.time < move.end.time <= scope["focus_time"] < move.end.time+width
                and move.end.time+width <= scope["end"] and move.end.price == scope["focus_price"]):
            continue
        if candidate["label"][1] == "1" and move.kind != side+"trend":
            continue
        raw = [c for c in minutes if move.start.time <= c.time < move.end.time+width]
        if not raw:
            continue
        low, high = min(move.low, min(c.low for c in raw)), max(move.high, max(c.high for c in raw))
        if not scope["low"] <= low <= high <= scope["high"]:
            continue
        if candidate["label"][1] == "3":
            if move.id != first_retest:
                continue
            if (low <= boundary if down else high >= boundary):
                return dict(state="invalidated", reason="次级别首次回试触及或返回主级别原中枢", movement=asdict(move))
        return dict(state="completed", reason=move.completion, period=rule["sub_period"],
                    structural_level=1, evidence_at=move.evidence_at, movement=asdict(move),
                    interval=dict(start=move.start.time, end=move.end.time+width, low=low, high=high))
    return waiting


def confirmed_execution_signals(block, as_of):
    """只接受现有力度政策确认的一类点，或依赖该一类点且回试完成的二类点。

    不升级MACD候选、回试几何或分型。B2/S2保留policy_dependent原始标签，
    并显式审查其一类点和反弹/回试引用，而不是伪造新的严格确认标签。
    """
    if not block:
        return []
    theory = block.get("theory", {})
    tree = theory.get("type_hierarchy", {})
    movements = {m.id: m for m in tree.get("base_movements", [])}
    for layer in tree.get("levels", []):
        movements.update((m.id, m) for m in layer["movements"])
    result = []
    for layer in theory.get("strict_divergence", {}).get("levels", []):
        audits = {a["id"]: a for a in layer["audits"]}
        firsts = {}
        for s in layer["signals"]:
            if s.label not in ("B1", "S1") or s.certainty != "policy_confirmed" or s.evidence_at > as_of:
                continue
            a = next((audits[k] for k in s.source_ids if k in audits), None)
            if not a or not a.get("strict_divergence_confirmed") or a["evidence_at"] > s.evidence_at:
                continue
            if a["end"] != s.time or a["price"] != s.price or a["start"] >= a["end"]:
                continue
            component_ids = a.get("c_components", [])
            if not component_ids or any(k not in movements for k in component_ids):
                continue
            parts = [movements[k] for k in component_ids]
            if any(m.level != s.level-1 or m.status != "completed" or m.evidence_at > s.evidence_at for m in parts):
                continue
            if parts[0].start.time != a["start"] or parts[-1].end.time != a["end"]:
                continue
            if any(x.end != y.start for x, y in zip(parts, parts[1:])):
                continue
            firsts[s.id] = dict(signal=s, start=a["start"], terminal_time=s.time,
                                terminal_price=s.price, evidence_at=s.evidence_at)
            result.append(firsts[s.id])
        for s in layer["signals"]:
            if s.label not in ("B2", "S2") or s.certainty != "policy_dependent" or s.evidence_at > as_of:
                continue
            if len(s.source_ids) != 3 or s.source_ids[0] not in firsts:
                continue
            first = firsts[s.source_ids[0]]
            rebound, retest = (movements.get(k) for k in s.source_ids[1:])
            if rebound is None or retest is None:
                continue
            buy = s.label == "B2"
            if (first["signal"].label != ("B1" if buy else "S1") or first["signal"].level != s.level or
                    rebound.level != s.level-1 or retest.level != s.level-1 or
                    rebound.status != "completed" or retest.status != "completed" or
                    rebound.start.time != first["terminal_time"] or rebound.start.price != first["terminal_price"] or
                    rebound.end != retest.start or retest.end.time != s.time or retest.end.price != s.price or
                    direction(rebound) != ("up" if buy else "down") or direction(retest) != ("down" if buy else "up") or
                    max(first["evidence_at"], rebound.evidence_at, retest.evidence_at) > s.evidence_at):
                continue
            result.append(dict(signal=s, start=first["start"], terminal_time=first["terminal_time"],
                               terminal_price=first["terminal_price"], evidence_at=s.evidence_at,
                               first_signal_id=first["signal"].id))
    return result


def locate_matrix(candidate, frames, minutes, as_of, policy):
    from entry_plan import _key
    rule = rule_for(candidate["period"], candidate["label"])
    execution_period = candidate["execution_period"]
    if execution_period not in rule["execution_periods"]:
        raise ValueError("执行周期不在用户矩阵中")
    gate = completed_submovement(candidate, frames[rule["sub_period"]], minutes, as_of)
    path = [dict(period=candidate["period"], **candidate["interval"])]
    base = dict(rule=rule, path=path, submovement_gate=gate, setup=None, rejected_setups=[])
    if gate["state"] != "completed":
        return {**base, "state": gate["state"], "reason": gate["reason"]}
    path.append(dict(period=rule["sub_period"], **gate["interval"]))
    frame = frames[execution_period]
    signals = confirmed_execution_signals(frame.get("block"), as_of)
    sub = gate["movement"]
    sub_width = period_delta(PERIODS[rule["sub_period"]])
    exec_width = period_delta(PERIODS[execution_period])
    choices = []
    for item in signals:
        s = item["signal"]
        # 二买的起源一买必须定位于这一条次级别走势的末端；不能任取后来的二买。
        if (s.label not in rule["execution_labels"] or item["start"] < sub["start"]["time"] or
                not sub["end"]["time"] <= item["terminal_time"] < sub["end"]["time"]+sub_width or
                item["terminal_price"] != sub["end"]["price"]):
            continue
        available = max(gate["evidence_at"], s.evidence_at)
        choices.append((available, s.time, item))
    if not choices:
        return {**base, "state": "waiting_execution_signal", "reason": "等待指定执行周期的一/二类点及其来源证据"}
    # 两周期各自输出；同周期取最新可用信号，避免旧信号抢占新二类点。
    available, _, item = max(choices, key=lambda x: (x[0], x[1]))
    s = item["signal"]
    path.append(dict(period=execution_period, start=item["start"], end=s.time+exec_width,
                     signal_label=s.label, signal_id=s.id))
    observed = next((c for c in reversed(minutes) if c.time < available), None)
    buy = candidate["side"] == "long"
    setup = dict(id=_key([candidate["period"], rule["sub_period"], sub["id"], sub["end"],
                         execution_period, asdict(s), available]),
                 period=execution_period, structure="confirmed_buy_sell_signal",
                 execution_label=s.label, signal=asdict(s), signal_price=s.price,
                 anchor_time=s.time, anchor_price=s.price, evidence_at=available,
                 trigger_rule="signal_and_submovement_confirmed",
                 trigger_price=observed.close if observed else None,
                 trigger_price_basis="confirmation_minute_close_not_signal_endpoint_or_fill",
                 stop_price=s.price-policy.stop_buffer if buy else s.price+policy.stop_buffer,
                 invalidation_check_from=s.time+exec_width,
                 execution_group_id=_key([candidate["period"], rule["sub_period"], candidate["side"], sub["id"], sub["end"]]))
    return {**base, "state": "setup_found", "setup": setup}


def make_matrix_plan(candidate, location, minutes, as_of, previous=None):
    from entry_plan import MINUTE, _breached, _key
    result = {**candidate, **location, "evaluated_at": as_of}
    setup = location["setup"]
    if not setup:
        return result
    result.update(setup)
    result.update(candidate_id=candidate["id"], main_period=candidate["period"],
                  id=_key([candidate["id"], setup["id"]]))
    invalid = False
    for c in minutes:
        if c.time < setup["invalidation_check_from"]:
            continue
        extreme = c.low if candidate["side"] == "long" else c.high
        if ((extreme <= setup["stop_price"] if candidate["side"] == "long" else extreme >= setup["stop_price"]) or
                _breached(extreme, candidate["parent_invalidation"])):
            invalid = True
            break
    if invalid:
        return {**result, "state": "invalidated", "reason": "执行点或主级别候选已失效"}
    if previous and previous["id"] == result["id"]:
        return {**previous, "evaluated_at": as_of}
    result.update(first_observed_at=as_of, armed_at=as_of)
    if setup["evidence_at"] == as_of and minutes[-1].time+MINUTE == as_of:
        result.update(state="triggered", triggered_at=as_of, trigger_observed_close=minutes[-1].close,
                      reason="次级别完成与指定执行信号在当前截面均已具备；非端点回填成交")
    else:
        result.update(state="missed", reason="组合证据在此前已可用，本次不补记历史开单")
    return result
