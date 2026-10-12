"""单品种线性永续研究回测：可见信号、下一开盘成交、费用和资金费。"""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import csv
import math

from chan import PERIODS, parse_time, validate_candles
from chan_time import period_delta, period_minutes


@dataclass(frozen=True)
class TradingPolicy:
    labels: tuple = ("B1", "S1", "B3", "S3")
    levels: tuple = (1,)
    periods: tuple = tuple(PERIODS)
    confirmation_period: str | None = None
    max_signal_age_hours: int = 72
    require_volume_tag: str | None = None
    hold_bars: int = 48
    allocation: float = 0.1
    exit_on_retraction: bool = True

    def __post_init__(self):
        if not self.labels or set(self.labels) - {"B1", "S1", "B3", "S3"}:
            raise ValueError("交易只接受策略确认 B1/S1 和结构确认 B3/S3")
        if not self.levels or any(type(x) is not int or x < 1 for x in self.levels):
            raise ValueError("交易结构层必须为正整数")
        if not self.periods or set(self.periods)-PERIODS.keys():
            raise ValueError("交易周期无效")
        if self.confirmation_period is not None and self.confirmation_period not in PERIODS:
            raise ValueError("确认周期无效")
        if self.require_volume_tag not in (None, "high_effort_low_result", "high_effort_high_result"):
            raise ValueError("未知量价标签")
        if (type(self.hold_bars) is not int or self.hold_bars < 1 or
                type(self.max_signal_age_hours) is not int or self.max_signal_age_hours < 1):
            raise ValueError("持仓根数和信号有效小时数必须为正整数")
        if not math.isfinite(self.allocation) or not 0 < self.allocation <= 1:
            raise ValueError("allocation 必须在 (0,1]；本研究模型不使用杠杆")
        if type(self.exit_on_retraction) is not bool:
            raise ValueError("exit_on_retraction 必须为布尔值")


@dataclass(frozen=True)
class ExecutionCosts:
    fee_bps: float = 5.0
    slippage_bps: float = 2.0
    funding_rate: float | None = None
    funding_interval_hours: int = 8

    def __post_init__(self):
        if any(not math.isfinite(x) or not 0 <= x < 10000 for x in (self.fee_bps, self.slippage_bps)):
            raise ValueError("费用/滑点须为有限数，范围 [0,10000) bps")
        if self.funding_rate is not None and (not math.isfinite(self.funding_rate) or abs(self.funding_rate) > 1):
            raise ValueError("资金费率须为有限小数且绝对值不超过 1")
        if type(self.funding_interval_hours) is not int or self.funding_interval_hours < 1:
            raise ValueError("资金费间隔须为正整数")


@dataclass(frozen=True)
class Funding:
    time: datetime
    rate: float
    mark_price: float


def read_funding(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not {"timestamp", "rate", "mark_price"}.issubset(reader.fieldnames or []):
            raise ValueError("资金费 CSV 需要 timestamp,rate,mark_price")
        rows = [Funding(parse_time(x["timestamp"]), float(x["rate"]), float(x["mark_price"])) for x in reader]
    validate_funding(rows)
    return rows


def validate_funding(rows):
    for i, row in enumerate(rows):
        if row.time.tzinfo is None or (i and row.time <= rows[i-1].time):
            raise ValueError("资金费时间必须带时区、递增且不重复")
        if not math.isfinite(row.rate) or abs(row.rate) > 1 or not math.isfinite(row.mark_price) or row.mark_price <= 0:
            raise ValueError("资金费率或标记价格无效")


def signal_eligible(value, policy):
    label, certainty = value.get("label"), value.get("certainty")
    return (label in policy.labels and value.get("level") in policy.levels and
            ((label in ("B3", "S3") and certainty == "structural") or
             (label in ("B1", "S1") and certainty == "policy_confirmed")))


def backtest(candles, frames, source_hours=1, policy=None, costs=None, funding=None,
             initial_equity=10000.0, start=None, end=None):
    """时间范围 [start,end)，只在当时观察后成交，不按结构端点回填订单。

    funding=None 且未设常数费率时仅报告部分成本估计。资金费列表是实际结算输入，
    列表完整性由调用者负责，不根据交易所当前规则猜测历史结算间隔。
    """
    policy, costs = policy or TradingPolicy(), costs or ExecutionCosts()
    validate_candles(candles)
    if source_hours not in PERIODS.values() or not math.isfinite(initial_equity) or initial_equity <= 0:
        raise ValueError("源周期或初始权益无效")
    width = period_delta(source_hours)
    if costs.funding_rate is not None:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        if (costs.funding_interval_hours * 60 % period_minutes(source_hours) or
                any((c.time-epoch) % width != timedelta(0) for c in candles)):
            raise ValueError("常数资金费场景要求源周期整除结算间隔且对齐 UTC；否则请提供实际 CSV")
    if any(b.time-a.time != width for a, b in zip(candles, candles[1:])):
        raise ValueError("交易回测要求连续源行情，不能跨缺失 K 线估算成交和收益")
    if any(c.low <= 0 for c in candles):
        raise ValueError("永续回测需要正价格")
    if funding is not None and costs.funding_rate is not None:
        raise ValueError("实际资金费与常数费率场景不能同时指定")
    funding_rows = list(funding or [])
    validate_funding(funding_rows)
    bars = [c for c in candles if (start is None or c.time >= start) and
            (end is None or c.time + width <= end)]
    frames = sorted(frames, key=lambda x: (x["as_of"], x["period"]))
    for row in frames:
        if row["as_of"].tzinfo is None or row["period"] not in PERIODS or row.get("sampling_step_source_bars", 1) != 1:
            raise ValueError("交易回测只接受带时区的逐根回放，step 必须为 1")
        for event in row["events"]:
            if event["observed_at"] != row["as_of"] or event["first_observed_at"] > row["as_of"]:
                raise ValueError("回放观察时间不一致")
            if event["action"] != "retracted":
                value = event["structure"]["value"]
                evidence = value.get("evidence_at", value.get("confirmed_at"))
                if evidence is not None and evidence > row["as_of"]:
                    raise ValueError("信号引用未来证据")
        auxiliary = row.get("volume_price", {})
        if auxiliary.get("available_at") is not None and auxiliary["available_at"] > row["as_of"]:
            raise ValueError("量价标签引用未来数据")
    cash, position, pointer, funding_pointer = initial_equity, None, 0, 0
    active, volume, acted = {}, {}, set()
    trades, executions, curve, decisions, cashflows = [], [], [], [], []
    skipped = Counter()
    peak, max_dd = initial_equity, 0.0
    total_fees, total_funding = 0.0, 0.0

    def settle(row):
        nonlocal cash, total_funding
        if position is None:
            return
        flow = -position["quantity"] * row.mark_price * row.rate
        cash += flow
        total_funding += flow
        position["funding_pnl"] += flow
        cashflows.append({"time": row.time, "rate": row.rate, "mark_price": row.mark_price, "cashflow": flow})

    def close(price, time, reason):
        nonlocal position, cash, total_fees
        qty = position["quantity"]
        fill = price * (1 - math.copysign(costs.slippage_bps / 10000, qty))
        fee = abs(qty) * fill * costs.fee_bps / 10000
        gross = qty * (fill-position["entry_price"])
        cash += gross-fee
        total_fees += fee
        executions.append({"time": time, "side": "sell" if qty > 0 else "buy", "price": fill,
                           "quantity": abs(qty), "fee": fee, "reason": reason})
        trades.append({**position, "exit_time": time, "exit_price": fill, "exit_fee": fee,
                       "gross_pnl_after_slippage": gross,
                       "net_pnl": gross-fee-position["entry_fee"]+position["funding_pnl"], "exit_reason": reason})
        position = None

    def enter(side, price, time, index, signals):
        nonlocal position, cash, total_fees
        fill = price * (1 + side * costs.slippage_bps / 10000)
        notional = cash * policy.allocation
        fee = notional * costs.fee_bps / 10000
        cash -= fee
        total_fees += fee
        position = {"entry_time": time, "entry_price": fill, "quantity": side*notional/fill,
                    "entry_index": index, "entry_fee": fee, "funding_pnl": 0.0,
                    "signal_ids": [x["key"] for x in signals],
                    "signal_observed_at": max(x["available_at"] for x in signals)}
        executions.append({"time": time, "side": "buy" if side > 0 else "sell", "price": fill,
                           "quantity": abs(position["quantity"]), "fee": fee, "reason": "observed_signal"})

    def equity_point(time, mark):
        nonlocal peak, max_dd
        equity = cash + (position["quantity"]*(mark-position["entry_price"]) if position else 0)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak-equity)/peak)
        curve.append({"time": time, "equity": equity})
        return equity

    begin = bars[0].time if bars else None
    if begin is not None:
        while funding_pointer < len(funding_rows) and funding_rows[funding_pointer].time < begin:
            funding_pointer += 1
    for i, c in enumerate(bars):
        # 结算时点先向已有仓位收付资金费，再处理该开盘边界的订单。
        while funding_pointer < len(funding_rows) and funding_rows[funding_pointer].time <= c.time:
            settle(funding_rows[funding_pointer])
            funding_pointer += 1
        if costs.funding_rate is not None:
            epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
            if (c.time-epoch) % timedelta(hours=costs.funding_interval_hours) == timedelta(0):
                settle(Funding(c.time, costs.funding_rate, c.open))
        fresh, invalidated = [], set()
        while pointer < len(frames) and frames[pointer]["as_of"] <= c.time:
            row = frames[pointer]
            pointer += 1
            volume[row["period"]] = row.get("volume_price", {})
            for ev in row["events"]:
                key = f"{row['period']}:{ev['id']}"
                if ev["action"] == "retracted":
                    active.pop(key, None)
                    invalidated.add(key)
                    continue
                if ev["structure"]["type"] not in ("signal", "policy_signal"):
                    continue
                v = ev["structure"]["value"]
                if v["time"] > row["as_of"]:
                    raise ValueError("信号端点晚于观察时间")
                if not signal_eligible(v, policy):
                    active.pop(key, None)
                    invalidated.add(key)
                    if row["as_of"] >= begin:
                        skipped["unconfirmed_or_filtered_signal"] += 1
                    continue
                item = {"key": key, "period": row["period"], "signal": v,
                        "side": 1 if v["label"].startswith("B") else -1,
                        "available_at": active[key]["available_at"] if key in active else row["as_of"]}
                newly_eligible = key not in active
                active[key] = item
                if newly_eligible and row["as_of"] >= begin and key not in acted:
                    fresh.append(item)
                    acted.add(key)
        accepted = []
        for item in fresh:
            reason = None
            if item["key"] not in active:
                reason = "retracted_before_execution"
            elif item["period"] not in policy.periods:
                reason = "entry_period_filtered"
            elif c.time-item["available_at"] > timedelta(hours=policy.max_signal_age_hours):
                reason = "stale_signal"
            elif policy.require_volume_tag and policy.require_volume_tag not in volume.get(item["period"], {}).get("tags", []):
                reason = "volume_filter_not_met"
            elif policy.confirmation_period:
                confirmations = [v for v in active.values() if v["period"] == policy.confirmation_period and
                                 c.time-v["available_at"] <= timedelta(hours=policy.max_signal_age_hours)]
                latest = max((v["available_at"] for v in confirmations), default=None)
                sides = {v["side"] for v in confirmations if v["available_at"] == latest}
                if PERIODS[policy.confirmation_period] <= PERIODS[item["period"]]:
                    reason = "confirmation_period_not_higher"
                elif sides != {item["side"]}:
                    reason = "higher_period_confirmation_missing_or_conflicting"
            decisions.append({"time": c.time, "signal_id": item["key"], "state": "blocked" if reason else "eligible",
                              "reason": reason, "observed_at": item["available_at"]})
            if reason:
                skipped[reason] += 1
            else:
                accepted.append(item)
        sides = {x["side"] for x in accepted}
        side = next(iter(sides)) if len(sides) == 1 else None
        if len(sides) > 1:
            skipped["simultaneous_direction_conflict"] += 1
        if position:
            reason = ("signal_retracted" if policy.exit_on_retraction and set(position["signal_ids"]) & invalidated else
                      "opposite_signal" if side is not None and side * position["quantity"] < 0 else
                      "holding_limit" if i-position["entry_index"] >= policy.hold_bars else None)
            if reason:
                close(c.open, c.time, reason)
        if position is None and side is not None and cash > 0:
            enter(side, c.open, c.time, i, accepted)
        while funding_pointer < len(funding_rows) and funding_rows[funding_pointer].time < c.time+width:
            settle(funding_rows[funding_pointer])
            funding_pointer += 1
        value = equity_point(c.time+width, c.close)
        if value <= 0 and position:
            close(c.close, c.time+width, "insolvent_research_stop")
            curve[-1]["equity"] = cash
            break
    if bars and position:
        finish = bars[-1].time+width
        while funding_pointer < len(funding_rows) and funding_rows[funding_pointer].time == finish:
            settle(funding_rows[funding_pointer])
            funding_pointer += 1
        if costs.funding_rate is not None:
            epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
            if (finish-epoch) % timedelta(hours=costs.funding_interval_hours) == timedelta(0):
                settle(Funding(finish, costs.funding_rate, bars[-1].close))
        close(bars[-1].close, finish, "end_of_sample_mark_close")
        curve.pop()
        equity_point(finish, bars[-1].close)
    mode = "actual_input" if funding is not None else "constant_rate_scenario" if costs.funding_rate is not None else "missing"
    return {"policy": asdict(policy), "costs": asdict(costs), "funding_mode": mode,
            "funding_coverage": "caller_supplied_not_exchange_verified" if funding is not None else mode,
            "cost_status": "partial_funding_missing" if mode == "missing" else "specified_cost_model",
            "metrics": {"initial_equity": initial_equity, "final_equity": cash,
                        "net_return": cash/initial_equity-1, "max_drawdown": max_dd,
                        "trade_count": len(trades), "win_rate": sum(t["net_pnl"] > 0 for t in trades)/len(trades) if trades else None,
                        "fees": total_fees, "funding_pnl": total_funding},
            "trades": trades, "executions": executions, "equity_curve": curve, "decisions": decisions,
            "funding_cashflows": cashflows, "skipped": dict(skipped),
            "execution_policy": "next_available_source_open_after_observation; zero_latency_at_close_open_boundary",
            "drawdown_sampling": "closed_source_bar_equity; not_intrabar_extremes",
            "scope": "linear_quote_margined_single_position_no_leverage; no_liquidation_or_orderbook_model"}
