"""通用多周期候选与开单计划；只使用截止时刻已收盘的分钟数据。

默认同时运行4h/2h执行矩阵与1h -> 15m -> 5m -> 1m逐级定位。
matrix与nested_1h可分别单独运行，各主级别独立保留候选及触发状态。
候选不冒充严格确认买卖点，触发不回填历史成交；此模块不发送订单。
"""
from dataclasses import asdict, dataclass
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path

from chan import analyze, find_fractals, json_default, merge_inclusions, parse_time, resample
from chan_policy import AnalysisPolicy, candle_digest
from chan_structure import Movement
from chan_theory import center_relation, evolving_centers, macd_area, third_point_label
from chan_time import PERIODS, period_delta

CHAIN = ("1h", "15m", "5m", "1m")
MINUTE = timedelta(minutes=1)
VERSION = "entry-plan-v5"


@dataclass(frozen=True)
class EntryPolicy:
    # 价格缓冲使用报价单位；0 表示严格越过，不假定任何交易所 tick size。
    trigger_buffer: float = 0.0
    stop_buffer: float = 0.0
    max_wait_minutes: int = 60
    profile: str = "combined"

    def __post_init__(self):
        if self.profile not in ("combined", "matrix", "nested_1h"):
            raise ValueError("开单规则仅支持combined、matrix或nested_1h")
        if self.profile == "matrix" and self.trigger_buffer:
            raise ValueError("矩阵按买卖点证据触发，不使用突破价缓冲")
        for value in (self.trigger_buffer, self.stop_buffer):
            if not math.isfinite(value) or value < 0:
                raise ValueError("价格缓冲须为非负有限数")
        if type(self.max_wait_minutes) is not int or self.max_wait_minutes < 1:
            raise ValueError("max_wait_minutes 须为正整数")


def _key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=json_default,
                                    allow_nan=False).encode()).hexdigest()[:24]


def _time(value):
    return parse_time(value) if isinstance(value, str) else value


def observation_legs(block, minutes, as_of):
    """笔只用于候选位置；未确认末笔可随已经收盘的分钟极值延伸。"""
    legs = []
    for i, stroke in enumerate(block["strokes"]):
        end, price = stroke.end.time, stroke.end.price
        finish = min(as_of, end + period_delta(PERIODS[block["period"]]))
        if stroke.provisional:
            tail = [c for c in minutes if c.time >= stroke.start.time]
            if tail:
                extreme = (min(tail, key=lambda c: (c.low, -c.time.timestamp()))
                           if stroke.direction == "down" else
                           max(tail, key=lambda c: (c.high, c.time.timestamp())))
                end = extreme.time
                price = extreme.low if stroke.direction == "down" else extreme.high
                width = period_delta(PERIODS[block["period"]])
                midnight = end.replace(hour=0, minute=0, second=0, microsecond=0)
                finish = min(as_of, midnight + ((end-midnight) // width + 1) * width)
        if end <= stroke.start.time:
            continue
        raw = [c for c in minutes if stroke.start.time <= c.time < finish]
        if not raw:
            continue
        focus = (min(raw, key=lambda c: (c.low, -c.time.timestamp())) if stroke.direction == "down" else
                 max(raw, key=lambda c: (c.high, c.time.timestamp())))
        legs.append(dict(index=i, start=stroke.start.time, end=end, finish=finish,
                         start_price=stroke.start.price, price=price,
                         low=min(c.low for c in raw), high=max(c.high for c in raw),
                         direction=stroke.direction, provisional=stroke.provisional,
                         focus_time=focus.time, focus_price=focus.low if stroke.direction == "down" else focus.high,
                         evidence_at=stroke.confirmed_at,
                         native_structure=dict(
                             kind="stroke", index=i, start_time=stroke.start.time,
                             start_price=stroke.start.price, start_confirmed=True,
                             endpoint_time=stroke.end.time,
                             endpoint_price=stroke.end.price,
                             # analyze只用稳定分型选笔；末笔未完成不等于端点分型未确认。
                             observed_at=as_of,
                             focus_matches_endpoint=(
                                 (focus.low if stroke.direction == "down" else focus.high) == stroke.end.price
                                 and stroke.end.time <= focus.time <
                                 stroke.end.time + period_delta(PERIODS[block["period"]])))))
    return legs


def reverse_structure(last, as_of):
    """只有本周期已有稳定端点，才可作为本周期反向发展段的确认起点。"""
    source = last.get("native_structure", {})
    anchored = (source.get("kind") == "stroke"
                and source.get("focus_matches_endpoint", False)
                and source.get("observed_at") is not None
                and _time(source["observed_at"]) <= as_of)
    return dict(kind="developing_from_native_endpoint" if anchored else "minute_reverse",
                start_time=last["focus_time"], start_price=last["focus_price"],
                start_confirmed=anchored, observed_at=as_of,
                origin_endpoint_time=source.get("endpoint_time") if anchored else None)


def developing_reverse(legs, minutes, as_of, period):
    """从已观察极值展开反向候选，不等待本周期出现下一个端点分型。

    只给候选区间，不把这段分钟价格路径当作已确认笔或已完成走势。
    排除极值所在分钟，避免用同一分钟的高低价臆测先后顺序。
    """
    if not legs:
        return None
    last = legs[-1]
    start, price = last["focus_time"], last["focus_price"]
    raw = [c for c in minutes if c.time > start]
    if not raw:
        return None
    down = last["direction"] == "up"
    extreme = (min(raw, key=lambda c: (c.low, -c.time.timestamp())) if down else
               max(raw, key=lambda c: (c.high, c.time.timestamp())))
    end_price = extreme.low if down else extreme.high
    if (down and end_price >= price) or (not down and end_price <= price):
        return None
    width = period_delta(PERIODS[period])
    midnight = extreme.time.replace(hour=0, minute=0, second=0, microsecond=0)
    finish = min(as_of, midnight + ((extreme.time-midnight) // width + 1) * width)
    # 区间范围保守包含起点整根分钟；不假定该分钟内先高后低。
    part = [c for c in minutes if start <= c.time < finish]
    if (down and min(c.low for c in part) < end_price) or (not down and max(c.high for c in part) > end_price):
        return None
    return dict(index=last["index"]+1, start=start, end=extreme.time, finish=finish,
                start_price=price, price=end_price, low=min(price, *(c.low for c in part)),
                high=max(price, *(c.high for c in part)), direction="down" if down else "up",
                focus_time=extreme.time, focus_price=end_price, provisional=True,
                live_reverse=True, evidence_at=None,
                native_structure=reverse_structure(last, as_of))


def period_candidates(block, legs, as_of, period):
    """B1/S1: 两观察中枢+末段减弱；B2/S2: 首次回试几何；B3/S3: 首次不回枢。

    全部是笔观察候选。中枢与笔的来源自成一层，不混入严格完成走势递归。
    弱二类点在本执行政策中暂不启用。
    """
    if not legs:
        return []
    strokes = block["strokes"]
    completed = [Movement(f"P{i}", 0, "stroke_proxy", s.start, s.end, s.low, s.high,
                          (), (), s.confirmed_at)
                 for i, s in enumerate(strokes) if not s.provisional]
    view = evolving_centers(completed, 1)
    tail = legs[-1]
    buy = tail["direction"] == "down"
    side, prefix = ("long", "B") if buy else ("short", "S")
    result = []

    def add(kind, reference, invalidation, details):
        result.append(dict(
            id=f"{period}:{prefix}{kind}:{reference}", label=f"{prefix}{kind}", side=side,
            period=period, main_period=period, state="candidate", higher_point_confirmed=False,
            basis=f"{period}_stroke_observation_not_completed_type_signal",
            developing_leg=tail.get("live_reverse", False),
            observed_at=as_of, interval=dict(start=tail["start"], end=tail["finish"],
                                            low=tail["low"], high=tail["high"],
                                            focus_time=tail["focus_time"], focus_price=tail["focus_price"]),
            candidate_price=tail["price"], candidate_time=tail["end"],
            parent_invalidation=invalidation,
            details={**details, "missing": details["missing"].replace("1h", period)}))

    if len(legs) >= 3:
        origin, rebound, retest = legs[-3:]
        holds = (min(rebound["low"], retest["low"]) >= origin["price"] if buy else
                 max(rebound["high"], retest["high"]) <= origin["price"])
        if (origin["direction"] == retest["direction"] != rebound["direction"] and holds):
            add(2, origin["end"].isoformat(),
                dict(price=origin["price"], comparison="lt" if buy else "gt"),
                dict(first_point_confirmed=False, origin_time=origin["end"],
                     origin_price=origin["price"], policy="first_retest_holds_origin",
                     missing="1h转折性质待证；不破极值只是二类候选位置"))

    centers = view["centers"]
    # 已经完成离开/首次回试的中枢不能再发布同一中枢的三类候选。
    # signals校验同时防止状态与已消费首次回试的记录不一致。
    if (centers and centers[-1].status == "extending"
            and centers[-1].return_index is None
            and not any(s.center_id == centers[-1].id for s in view["signals"])):
        z = centers[-1]
        # 回枢失败的尝试已结束；检查每次离开的紧邻回试。
        # 首次成功后即消费中枢，不得把后面的回试当成新三类点。
        first_pair = None
        for a, b in zip(legs, legs[1:]):
            # 初始三段的最后一段也可能同时是离开段，和evolving_centers一致。
            if a["index"] < z.first_index + 2:
                continue
            if third_point_label(z, a["direction"], a["price"], b["direction"], b["low"], b["high"]):
                first_pair = (a, b)
                break
        if first_pair and first_pair[1] is tail:
            boundary = z.zg if buy else z.zd
            holds = tail["low"] > boundary if buy else tail["high"] < boundary
            if holds:
                add(3, z.start.isoformat(),
                    dict(price=boundary, comparison="le" if buy else "ge"),
                    dict(zd=z.zd, zg=z.zg, center_id=z.id, boundary_policy="touch_is_return",
                         departure_start=first_pair[0]["start"],
                         missing="1h首次回试尚未按完成走势确认"))

    if len(centers) >= 2:
        prior, z = centers[-2:]
        trend = center_relation(prior, z) == tail["direction"]
        # 找当前中枢的进入笔，与尚在发展的同向末笔比较；只发布辅助候选。
        incoming = next((x for x in legs if x["index"] == z.first_index - 1), None)
        if trend and incoming and tail["index"] > z.first_index + 2:
            new_extreme = tail["price"] < incoming["low"] if buy else tail["price"] > incoming["high"]
            points = block["theory"]["macd"]["points"]
            a = macd_area(points, incoming["start"], incoming["end"], tail["direction"])
            c = macd_area(points, tail["start"], tail["end"], tail["direction"])
            if new_extreme and a["warmed_up"] and c["warmed_up"] and 0 < c["area"] < a["area"]:
                add(1, z.start.isoformat(),
                    dict(price=tail["price"], comparison="lt" if buy else "gt"),
                    dict(center_id=z.id, area_ratio=c["area"] / a["area"],
                         missing="发展中末段MACD面积仅为辅助，1h趋势背驰未确认"))
    return result


def hourly_candidates(block, legs, as_of):
    return period_candidates(block, legs, as_of, "1h")


def narrow_interval(parent, legs, side, as_of=None):
    """子区间须有本周期稳定结构起点，且实际路径缩小，不能仅缩短桶尾。

    中间级别允许未完成笔/从稳定端点展开的反向段，不要求每级的目标极值
    先有分型；只有最终执行级别要求稳定分型。纯分钟极值不能充当结构起点。
    """
    direction = "down" if side == "long" else "up"
    def native_leg(x):
        source = x.get("native_structure", {})
        return (source.get("kind") in ("stroke", "developing_from_native_endpoint")
                and source.get("start_confirmed", False)
                and source.get("start_time") == x["start"]
                and (source["kind"] != "stroke" or not x.get("live_reverse", False))
                and (x.get("provisional", False) or source.get("focus_matches_endpoint", False))
                and source.get("observed_at") is not None
                and (as_of is None or _time(source["observed_at"]) <= as_of))

    choices = [x for x in legs if native_leg(x) and x["direction"] == direction and
               parent["start"] <= x["start"] < x["finish"] <= parent["end"] and
               x["start"] <= parent["focus_time"] < x["finish"] and
               x["focus_price"] == parent["focus_price"] and
               parent["low"] <= x["low"] <= x["high"] <= parent["high"] and
               (x["start"] > parent["start"] or x["low"] > parent["low"]
                or x["high"] < parent["high"])]
    if not choices:
        return None
    leg = max(choices, key=lambda x: (x["end"], x["start"]))
    return dict(start=leg["start"], end=leg["finish"], low=leg["low"], high=leg["high"],
                focus_time=parent["focus_time"], focus_price=parent["focus_price"],
                native_structure=leg["native_structure"])


def execution_setups(bars, interval, side, period, as_of, policy, initial_direction="up"):
    """稳定顶/底分型是执行结构，不等于该周期的一类买卖点。

    额外保留一个已收盘去包含柱，避免把尚会合并的最右柱用于分型确认。
    触发为后续一分钟收盘越过分型三柱的反向边界。
    """
    merged = merge_inclusions(bars, initial_direction)
    kind = "bottom" if side == "long" else "top"
    setups = []
    for f in find_fractals(merged[:-1]):
        if f.kind != kind or not interval["start"] <= f.time < interval["end"]:
            continue
        if (f.price != interval["focus_price"] or
                not f.time <= interval["focus_time"] < f.time + period_delta(PERIODS[period])):
            continue
        triple = merged[f.index - 1:f.index + 2]
        evidence = merged[f.index + 2].end + period_delta(PERIODS[period])
        if evidence > as_of or triple[0].start < interval["start"]:
            continue
        trigger = (max(b.high for b in triple) + policy.trigger_buffer if side == "long" else
                   min(b.low for b in triple) - policy.trigger_buffer)
        stop = f.price - policy.stop_buffer if side == "long" else f.price + policy.stop_buffer
        if (side == "long" and trigger <= stop) or (side == "short" and trigger >= stop):
            continue
        setups.append(dict(period=period, anchor_time=f.time, anchor_price=f.price,
                           structure="stable_bottom_fractal" if side == "long" else "stable_top_fractal",
                           evidence_at=evidence, trigger_price=trigger, stop_price=stop,
                           trigger_rule="1m_close_gt" if side == "long" else "1m_close_lt",
                           id=_key([period, side, f.time, f.price, trigger, stop])))
    return sorted(setups, key=lambda s: s["anchor_time"], reverse=True)


def locate(candidate, frames, as_of, policy, previous=None):
    scope = candidate["interval"]
    path = [dict(period="1h", **scope)]
    rejected = []
    for period in CHAIN[1:]:
        frame = frames[period]
        narrowed = narrow_interval(scope, frame["legs"], candidate["side"], as_of)
        if narrowed is None:
            return dict(path=path, state="waiting_descent", waiting_period=period,
                        reason="缺少本周期稳定起点支持且实际路径缩小的同向子区间；纯分钟反向路径或仅桶尾缩短不可用", setup=None,
                        rejected_setups=rejected)
        path.append(dict(period=period, **narrowed))
        setups = execution_setups(frame["bars"], narrowed, candidate["side"], period, as_of, policy,
                                  frame.get("initial_direction", "up"))
        for setup in setups:
            same = previous and previous.get("id") == _key([candidate["id"], setup["id"]])
            if same or setup_status(setup, candidate, frames["1m"]["bars"]) == "armed":
                return dict(path=path, state="setup_found", setup=setup, rejected_setups=rejected)
            rejected.append(dict(setup=setup, path=list(path),
                                 state=setup_status(setup, candidate, frames["1m"]["bars"])))
        scope = narrowed
    if rejected:
        last = rejected[-1]
        return dict(path=last["path"], searched_path=path, state=last["state"],
                    setup=last["setup"], rejected_setups=rejected,
                    reason="较低周期也无可用新结构；保留已错过或失效价位供审阅，不追入")
    return dict(path=path, state="waiting_structure", waiting_period="1m",
                reason="已缩小至1m，尚无可用的稳定反向分型", setup=None,
                rejected_setups=rejected)


def _breached(price, rule):
    value = rule["price"]
    return {"lt": price < value, "le": price <= value,
            "gt": price > value, "ge": price >= value}[rule["comparison"]]


def setup_status(setup, candidate, minutes):
    side = candidate["side"]
    since = [c for c in minutes if c.time > setup["anchor_time"]]
    invalid = any((c.low < setup["anchor_price"] if side == "long" else
                   c.high > setup["anchor_price"]) or
                  _breached(c.low if side == "long" else c.high, candidate["parent_invalidation"])
                  for c in since)
    crossed = any(c.time + MINUTE >= setup["evidence_at"] and
                  (c.close > setup["trigger_price"] if side == "long" else c.close < setup["trigger_price"])
                  for c in minutes)
    return "invalidated" if invalid else "missed" if crossed else "armed"


def advance_plan(plan, bars, as_of, policy):
    """观察时刻之后逐分钟评估；同柱触发与止损按失效优先，绝不回填成交。"""
    result = dict(plan)
    if result["state"] != "armed":
        return result
    after = _time(result["evaluated_at"])
    expires = _time(result["armed_at"]) + timedelta(minutes=policy.max_wait_minutes)
    side = result["side"]
    selected = [c for c in bars if c.time >= after and c.time + MINUTE <= as_of]
    if selected and selected[0].time != after:
        return {**result, "state": "data_gap", "reason": "观察后分钟行情不连续"}
    for i, c in enumerate(selected):
        if i and c.time != selected[i-1].time + MINUTE:
            return {**result, "state": "data_gap", "reason": "观察后分钟行情不连续"}
        when = c.time + MINUTE
        if when > expires:
            return {**result, "state": "expired", "evaluated_at": when}
        extreme = c.low if side == "long" else c.high
        stopped = extreme <= result["stop_price"] if side == "long" else extreme >= result["stop_price"]
        if stopped or _breached(extreme, result["parent_invalidation"]):
            return {**result, "state": "invalidated", "evaluated_at": when,
                    "reason": "execution_stop" if stopped else "parent_candidate_failed"}
        crossed = c.close > result["trigger_price"] if side == "long" else c.close < result["trigger_price"]
        if crossed:
            return {**result, "state": "triggered", "triggered_at": when,
                    "trigger_observed_close": c.close, "evaluated_at": when,
                    "execution_note": "收盘后才可下单；触发价不是成交价，未模拟成交"}
        result["evaluated_at"] = when
    if as_of >= expires:
        result.update(state="expired", evaluated_at=as_of)
    return result


def make_plan(candidate, location, minutes, as_of, policy, previous=None):
    base = {**candidate, **location, "evaluated_at": as_of}
    setup = location["setup"]
    if not setup:
        return base
    base.update(setup)
    base["candidate_id"] = candidate["id"]
    base["id"] = _key([candidate["id"], setup["id"]])
    if previous and previous["id"] == base["id"]:
        return advance_plan(previous, minutes, as_of, policy)
    base.update(state=setup_status(setup, candidate, minutes),
                armed_at=as_of, first_observed_at=as_of,
                reason="首次观察仅建立计划；不能把此前的突破补记为实时触发")
    return base


def analyze_entries(candles, as_of, previous=None, analysis_policy=None, entry_policy=None):
    """同一份1m行情构造所选规则的各周期，检查完成证据及来源对应关系。"""
    analysis_policy = analysis_policy or AnalysisPolicy()
    policy = entry_policy or EntryPolicy()
    from entry_matrix import MATRIX_PERIODS, ROUTES, locate_matrix, make_matrix_plan
    periods = CHAIN if policy.profile == "nested_1h" else MATRIX_PERIODS
    main_periods = (("4h", "2h", "1h") if policy.profile == "combined" else
                    ("4h", "2h") if policy.profile == "matrix" else ("1h",))
    if analysis_policy.mode != "strict":
        raise ValueError("开单计划需要 strict 结构分析")
    if as_of.second or as_of.microsecond:
        as_of = as_of.replace(second=0, microsecond=0)
    minutes, quality = resample(candles, PERIODS["1m"], PERIODS["1m"], as_of)
    if not minutes:
        raise ValueError("截止时刻没有已收盘1m行情")
    if len(minutes) > 1 and not any(b.time-a.time == MINUTE for a, b in zip(minutes, minutes[1:])):
        raise ValueError("必须输入真实1m行情，不得把1h数据当成分钟数据")
    manifest = dict(version=VERSION, analysis=analysis_policy.manifest(), entry=asdict(policy),
                    periods=periods, main_periods=main_periods,
                    routes={p: CHAIN if p == "1h" else ROUTES[p] for p in main_periods})
    fingerprint = _key(manifest)
    if previous:
        if previous.get("fingerprint") != fingerprint:
            raise ValueError("前次计划规则不一致，不能延续观察状态")
        if _time(previous["as_of"]) > as_of:
            raise ValueError("不能使用未来快照作为前次计划")
        prior_bars = [c for c in minutes if c.time + MINUTE <= _time(previous["as_of"])]
        if candle_digest(prior_bars) != previous["input_digest"]:
            raise ValueError("前次观察的行情已修订或历史被截断；请重新建立计划")
    # 仅使用末个连续区段。间断不能让上下级结构跨区段串联。
    begin = 0
    for i in range(1, len(minutes)):
        if minutes[i].time-minutes[i-1].time != MINUTE:
            begin = i
    contiguous = minutes[begin:]
    fresh = minutes[-1].time + MINUTE == as_of
    frames, summaries = {}, {}
    for period in periods:
        bars, q = resample(contiguous, PERIODS["1m"], PERIODS[period], as_of)
        report = analyze(bars, PERIODS[period], **analysis_policy.kwargs())
        block = report["blocks"][-1] if report["blocks"] else None
        if block:
            block = {**block, "period": period}
        legs = observation_legs(block, contiguous, as_of) if block else []
        live = developing_reverse(legs, contiguous, as_of, period)
        if live:
            legs.append(live)
        frames[period] = dict(bars=bars, legs=legs, block=block, initial_direction=analysis_policy.initial_direction)
        summaries[period] = dict(bar_count=len(bars), last_closed_at=(bars[-1].time + period_delta(PERIODS[period]) if bars else None),
                                 stroke_count=len(block["strokes"]) if block else 0, data_quality=q)
    roots = []
    for period in main_periods:
        frame = frames[period]
        if not frame["block"]:
            continue
        sequences = [frame["legs"]]
        if frame["legs"] and frame["legs"][-1].get("live_reverse"):
            sequences.append(frame["legs"][:-1])
        for seq in sequences:
            roots.extend(period_candidates(frame["block"], seq, as_of, period) if period != "1h" else
                         hourly_candidates(frame["block"], seq, as_of))
    old = {x.get("candidate_id", x["id"]): x for x in (previous or {}).get("plans", [])}
    plans, withdrawn = [], []
    targets = []
    for c in roots:
        if c["period"] == "1h":
            targets.append({**c, "entry_model": "nested_1h"})
        else:
            targets.extend({**c, "id": c["id"]+":exec:"+p, "main_candidate_id": c["id"],
                            "execution_period": p, "entry_model": "matrix"}
                           for p in ROUTES[c["period"]]["execution_periods"])
    for candidate in targets:
        if candidate["entry_model"] == "matrix":
            location = locate_matrix(candidate, frames, contiguous, as_of, policy)
            plan = make_matrix_plan(candidate, location, contiguous, as_of, old.get(candidate["id"]))
        else:
            location = locate(candidate, frames, as_of, policy, old.get(candidate["id"]))
            plan = make_plan(candidate, location, contiguous, as_of, policy, old.get(candidate["id"]))
        if not fresh:
            plan.update(state="stale_data", reason="最新已收盘分钟未覆盖分析截止时刻")
        plans.append(plan)
    active_ids = {p["id"] for p in plans}
    for p in old.values():
        if p["id"] not in active_ids:
            withdrawn.append({**p, "state": "withdrawn", "withdrawn_at": as_of,
                              "actionable": False, "new_trigger": False,
                              "reason": "候选或执行结构已改变；旧计划不再有效"})
    def fresh_trigger(p):
        return (p["state"] == "triggered" and _time(p["triggered_at"]) == as_of and
                (not previous or _time(p["triggered_at"]) > _time(previous["as_of"])))

    directions = {p["side"] for p in plans if p["state"] == "armed" or fresh_trigger(p)}
    conflict = len(directions) > 1
    for p in plans:
        p["actionable"] = fresh and not conflict and fresh_trigger(p)
        p["new_trigger"] = fresh and fresh_trigger(p)
        p["execution_group_id"] = (p["setup"].get("execution_group_id", p["setup"]["id"]) if p.get("setup") else None)
    by_main_period = {}
    for period in main_periods:
        own = [p for p in plans if p.get("main_period", "1h") == period]
        by_main_period[period] = dict(
            entry_model="nested_1h" if period == "1h" else "matrix",
            candidate_count=sum(c["period"] == period for c in roots),
            plan_count=len(own), new_trigger_count=sum(p["new_trigger"] for p in own),
            actionable_count=sum(p["actionable"] for p in own),
            plan_ids=[p["id"] for p in own],
            state_counts={state: sum(p["state"] == state for p in own) for state in sorted({p["state"] for p in own})})
    return dict(schema=VERSION, as_of=as_of, fingerprint=fingerprint, policy=manifest,
                input_digest=candle_digest(minutes), data_quality={**quality, "contiguous_start": contiguous[0].time,
                    "fresh": fresh, "dropped_before_last_gap": begin}, periods=summaries,
                candidates=roots, plans=plans, withdrawn=withdrawn, direction_conflict=conflict,
                by_main_period=by_main_period,
                orders=[], scope="；".join(
                    "1h笔观察候选：15m→5m→1m逐级包含与稳定分型突破" if p == "1h" else
                    f"{p}笔观察候选：次级别完成走势与指定一二类执行点" for p in main_periods),
                empty_reason=None if roots else "当前主级别观察结构没有符合政策的候选；不等于市场不存在买卖点")


def write_entry_outputs(report, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "entry_plan.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                      default=json_default, allow_nan=False), encoding="utf-8")
    states = dict(armed="待触发", triggered="已触发", missed="已越过，等待新结构", invalidated="已失效",
                  waiting_submovement="等待次级别走势完成", waiting_execution_signal="等待指定一/二类执行点",
                  waiting_descent="等待下级区间", waiting_structure="等待1m结构", stale_data="行情已过期",
                  expired="计划超时", withdrawn="已撤回", data_gap="分钟数据间断")
    profile = report["policy"]["entry"]["profile"]
    descriptions = []
    trigger_descriptions = []
    if profile in ("combined", "matrix"):
        descriptions.append("4h候选→1h走势完成→15m一/二类点；2h候选→30m走势完成→5m或15m一/二类点。4h/2h二类候选只接受二类执行点。")
        trigger_descriptions.append("4h/2h触发参考价为组合证据就绪时的1m收盘价，执行点端点价另外列出。")
    if profile in ("combined", "nested_1h"):
        descriptions.append("1h候选沿15m→5m→1m定位；子级须有本周期稳定起点且实际路径缩小，仅桶尾缩短不可用；建立计划后等后续1m收盘突破。")
        trigger_descriptions.append("1h触发价为执行分型三柱边界加减缓冲；首次观察不追入此前的突破。")
    lines = ["# 通用多周期开单候选", "", f"截止时刻（UTC）：{report['as_of'].isoformat()}", "",
             *descriptions, "", *trigger_descriptions,
             "首次观察不补记历史触发；已触发表示条件满足，不代表订单成交。", "",
             "| 主级别 | 候选数 | 执行判断数 | 本次新触发 | 可执行数 |",
             "| --- | --- | --- | --- | --- |"]
    for period, summary in report["by_main_period"].items():
        lines.append(f"| {period} | {summary['candidate_count']} | {summary['plan_count']} | {summary['new_trigger_count']} | {summary['actionable_count']} |")
    lines.extend(["",
             "| 主级别候选 | 方向 | 执行要求 | 定位路径 | 状态 | 触发参考价 | 止损参考 | 主级别失效边界 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    )
    for p in report["plans"]:
        inv = p["parent_invalidation"]
        execution = (f"{p['execution_period']} {'/'.join(p['rule']['execution_labels'])}" if p.get("rule") else "逐级分型突破")
        lines.append(f"| {p.get('main_period', '1h')} {p['label']} | {'多' if p['side']=='long' else '空'} | {execution} | " +
                     "→".join(n["period"] for n in p["path"]) +
                     f" | {states.get(p['state'], p['state'])} | {p.get('trigger_price', '待定')} | {p.get('stop_price', '待定')} | {inv['comparison']} {inv['price']} |")
    if report["empty_reason"]:
        lines.extend(["", report["empty_reason"]])
    if report["direction_conflict"]:
        lines.extend(["", "多空同时具备执行条件，禁止发布可执行方向。"])
    for p in report["plans"]:
        lines.extend(["", f"## {p['label']} · {states.get(p['state'], p['state'])}", "",
                      f"{p.get('main_period', '1h')}候选位置：{p['candidate_price']}；时间：{_time(p['candidate_time']).isoformat()}",
                      f"父级未确认部分：{p['details']['missing']}"])
        if p.get("rule"):
            lines.append(f"执行要求：{p['execution_period']} {'/'.join(p['rule']['execution_labels'])}；次级别检查：{p['submovement_gate']['reason']}")
            if p["submovement_gate"]["state"] == "completed":
                lines.append(f"次级别完成证据：{p['rule']['sub_period']} · {_time(p['submovement_gate']['evidence_at']).isoformat()}")
        if p.get("signal_price") is not None:
            lines.append(f"执行信号：{p['execution_label']}；端点价：{p['signal_price']}")
        if p.get("setup"):
            lines.append(f"执行结构证据：{_time(p['evidence_at']).isoformat()}；首次观察：{_time(p.get('first_observed_at', report['as_of'])).isoformat()}")
        if p.get("triggered_at"):
            lines.append(f"触发观察时刻：{_time(p['triggered_at']).isoformat()}；收盘价：{p['trigger_observed_close']}（非成交价）")
        for rejected in p.get("rejected_setups", []):
            s = rejected["setup"]
            lines.append(f"已排除的{s['period']}位置：触发价{s['trigger_price']}，止损{s['stop_price']}；{states[rejected['state']]}。")
    (folder / "entry_plan.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
