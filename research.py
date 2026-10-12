"""逐根多周期研究、含成本回测、滚动样本外验证及 CSCV 过拟合诊断。"""
import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
from itertools import combinations
import json
import math
from pathlib import Path
from statistics import mean

from backtest import ExecutionCosts, TradingPolicy, backtest, read_funding
from chan import PERIODS, compatible_periods, json_default, parse_time, read_csv, resample
from chan_policy import candle_digest, load_policy
from replay import replay
from replay_viewer import write_viewer


def walk_forward(candles, frames, policies, source_hours=1, costs=None, funding=None,
                 train_bars=240, test_bars=120, embargo_bars=1, min_train_trades=3):
    if any(type(n) is not int or n < 1 for n in (train_bars, test_bars, min_train_trades)):
        raise ValueError("训练/测试长度及最低交易数必须为正整数")
    if type(embargo_bars) is not int or embargo_bars < 0 or not policies:
        raise ValueError("隔离根数非负且至少有一个候选策略")
    frames = list(frames)
    folds = []
    width = timedelta(hours=source_hours)
    for test_start in range(train_bars+embargo_bars, len(candles)-test_bars+1, test_bars):
        train_end = test_start-embargo_bars
        train_start = train_end-train_bars
        scores = []
        for i, policy in enumerate(policies):
            run = backtest(candles, frames, source_hours, policy, costs, funding,
                           start=candles[train_start].time, end=candles[train_end-1].time+width)
            m = run["metrics"]
            scores.append({"candidate": i, "metrics": m,
                           "eligible": m["trade_count"] >= min_train_trades})
        eligible = [x for x in scores if x["eligible"]]
        best = max(eligible, key=lambda x: (x["metrics"]["net_return"], -x["candidate"])) if eligible else None
        test_end = test_start+test_bars
        result = backtest(candles, frames, source_hours, policies[best["candidate"]], costs, funding,
                          start=candles[test_start].time, end=candles[test_end-1].time+width) if best else None
        folds.append({"train_start": candles[train_start].time, "train_end": candles[train_end-1].time+width,
                      "test_start": candles[test_start].time, "test_end": candles[test_end-1].time+width,
                      "selected_candidate": best["candidate"] if best else None,
                      "status": "tested" if best else "cash_insufficient_training_trades",
                      "training_scores": scores, "test_metrics": result["metrics"] if result else None})
    compounded = 1.0
    for fold in folds:
        if fold["test_metrics"]:
            compounded *= 1+fold["test_metrics"]["net_return"]
    tested = sum(x["status"] == "tested" for x in folds)
    return {"status": "evaluated" if tested else "insufficient_training_trades" if folds else "insufficient_history",
            "folds": folds, "oos_compounded_return": compounded-1 if tested else None,
            "cash_path_return": compounded-1 if folds else None, "tested_folds": tested,
            "candidate_count": len(policies), "train_bars": train_bars, "test_bars": test_bars,
            "embargo_bars": embargo_bars, "minimum_training_trades": min_train_trades,
            "selection_metric": "training_net_return; fixed_candidate_order_breaks_ties",
            "boundary_policy": "flat_reset_each_fold; no_pre_window_entry; terminal_costs_included",
            "scope": "chronological_walk_forward_not_full_purged_CV_or_profitability_proof"}


def probability_of_overfitting(returns, partitions=8):
    """CSCV：等长时间块的一半选参，另一半排名；评分为平均逐根收益。

    不平衡剩余行从尾部丢弃并报告；全体同分分割不计为证据。
    """
    if type(partitions) is not int or partitions < 4 or partitions > 12 or partitions % 2:
        raise ValueError("CSCV partitions 必须为 4..12 的偶数")
    n = len(returns)
    candidates = len(returns[0]) if n else 0
    if any(len(row) != candidates or any(not math.isfinite(x) for x in row) for row in returns):
        raise ValueError("CSCV 需要矩形有限收益矩阵")
    base = {"method": "CSCV", "score": "mean_bar_return", "partitions": partitions,
            "candidate_count": candidates, "sample_rows": n,
            "scope": "diagnostic_for_supplied_trials; not_chronological_oos; no_significance_claim"}
    if candidates < 2 or n < partitions:
        return {**base, "status": "insufficient_data", "pbo": None}
    size = n//partitions
    blocks = [[mean(returns[j][i] for j in range(b*size, (b+1)*size))
               for i in range(candidates)] for b in range(partitions)]
    ranks, skipped = [], 0
    for selected in combinations(range(partitions), partitions//2):
        other = sorted(set(range(partitions))-set(selected))
        train = [mean(blocks[b][i] for b in selected) for i in range(candidates)]
        test = [mean(blocks[b][i] for b in other) for i in range(candidates)]
        # 同分没有足够证据判断哪一个样本内赢家，应明确排除。
        if max(train) == min(train) or max(test) == min(test):
            skipped += 1
            continue
        winner = max(range(candidates), key=lambda i: (train[i], -i))
        lower = sum(x < test[winner] for x in test)
        equal = sum(x == test[winner] for x in test)
        rank = (lower+(equal+1)/2)/(candidates+1)
        ranks.append(rank)
    return {**base, "status": "evaluated" if ranks else "uninformative_ties",
            "pbo": sum(x <= 0.5 for x in ranks)/len(ranks) if ranks else None,
            "oos_relative_ranks": ranks, "evaluated_splits": len(ranks), "skipped_tied_splits": skipped,
            "discarded_tail_rows": n-size*partitions,
            "selection_ties": "first_declared_candidate; full_ties_excluded"}


def candidate_returns(runs):
    if not runs:
        return []
    times = [x["time"] for x in runs[0]["equity_curve"]]
    if any([x["time"] for x in r["equity_curve"]] != times for r in runs):
        raise ValueError("候选收益曲线时间不一致，不能计算 CSCV")
    cols = []
    for run in runs:
        previous = run["metrics"]["initial_equity"]
        col = []
        for point in run["equity_curve"]:
            if previous <= 0 or point["equity"] <= 0:
                raise ValueError("破产收益路径不能直接用于该 CSCV 诊断")
            col.append(point["equity"]/previous-1)
            previous = point["equity"]
        cols.append(col)
    return list(map(list, zip(*cols)))


def assess_overfitting(runs, partitions=8, min_trades=3):
    """无交易/极少交易的候选不能因与现金同分而获得看似良好的 PBO。"""
    if type(min_trades) is not int or min_trades < 1:
        raise ValueError("最低有效交易数须为正整数")
    if type(partitions) is not int or partitions < 4 or partitions > 12 or partitions % 2:
        raise ValueError("CSCV partitions 必须为 4..12 的偶数")
    counts = [r["metrics"]["trade_count"] for r in runs]
    eligible = sum(n >= min_trades for n in counts)
    base = {"candidate_trade_counts": counts, "minimum_trades_per_active_candidate": min_trades,
            "active_candidate_count": eligible, "candidate_count": len(runs)}
    if eligible < 2:
        return {**base, "method": "CSCV", "status": "insufficient_trading_evidence", "pbo": None,
                "partitions": partitions, "reason": "fewer_than_two_candidates_with_minimum_trades"}
    return {**probability_of_overfitting(candidate_returns(runs), partitions), **base}


def source_fingerprint():
    files = ("chan.py", "chan_structure.py", "chan_types.py", "chan_theory.py", "chan_dynamics.py",
             "chan_policy.py", "chan_signals.py", "replay.py", "backtest.py", "research.py", "replay_viewer.py",
             "chan_time.py", "unified.py", "unified_viewer.py")
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in files}


def load_candidates(path, periods):
    if path is None:
        return [TradingPolicy(periods=tuple(periods), hold_bars=n) for n in (24, 48, 96)]
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("候选策略 JSON 必须是非空参数对象数组")
    result = []
    for item in data:
        if not isinstance(item, dict) or set(item)-TradingPolicy.__dataclass_fields__.keys():
            raise ValueError("候选策略包含未知参数")
        params = {"periods": tuple(periods), **item}
        for field in ("labels", "levels", "periods"):
            if field in params:
                params[field] = tuple(params[field])
        policy = TradingPolicy(**params)
        if set(policy.periods)-set(periods) or policy.confirmation_period and policy.confirmation_period not in periods:
            raise ValueError("候选策略使用了本次未回放的周期")
        result.append(policy)
    return result


def write_summary(path, report):
    lines = ["# 缠论研究报告", "", f"分析规则：`{report['analysis_rules']['fingerprint']}`", "",
             "逐根回放后按下一源 K 线开盘执行；这是研究模拟，资金费口径见下表。", "",
             "| 候选 | 成交笔数 | 收益率 | 最大回撤 | 手续费 | 资金费损益 | 成本状态 |",
             "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for i, run in enumerate(report["backtests"]):
        m = run["metrics"]
        lines.append(f"| {i} | {m['trade_count']} | {m['net_return']:.4%} | {m['max_drawdown']:.4%} | "
                     f"{m['fees']:.4f} | {m['funding_pnl']:.4f} | {run['cost_status']} |")
    wf, pbo = report["walk_forward"], report["overfitting"]
    lines += ["", f"滚动样本外：{wf['status']}；测试折数 {wf['tested_folds']}；复合收益 {wf['oos_compounded_return']}。",
              f"CSCV：{pbo['status']}；PBO={pbo['pbo']}。交易不足或全同分不作为有效性验证通过。",
              "训练交易不足的折保持现金；未形成可评估交易时，不用零收益代表策略效果。",
              "资金费缺失时，表中收益仅计入指定手续费和滑点；资金费损益列的 0 不代表真实资金费为零。", "",
              "结构周期与递归层级分开。候选 MACD 一二类点默认不交易；量价标签不改变缠论结构。",
              "完整信号来源、观察时间、修订、撤回、订单、资金费现金流和源码指纹见 JSON/JSONL。"]
    path.write_text("\n".join(lines)+"\n", encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True)
    p.add_argument("--context-csv", help="统一 1m 模式的前置行情")
    p.add_argument("--independent-periods", action="store_true", help="显式逐周期研究；1m 默认只有一个统一事件流")
    p.add_argument("--min-context-bars", type=int, default=1440)
    p.add_argument("--required-level", type=int, default=2)
    p.add_argument("--require-history", action="store_true")
    p.add_argument("--source-period", choices=PERIODS, default="1h")
    p.add_argument("--periods", nargs="+", choices=PERIODS)
    p.add_argument("--as-of")
    p.add_argument("--start", help="交易/评估起点；此前行情仅用于结构预热")
    p.add_argument("--anchor-hour", type=int, default=0)
    p.add_argument("--policy", type=Path)
    p.add_argument("--candidates", type=Path)
    p.add_argument("--fee-bps", type=float, default=5)
    p.add_argument("--slippage-bps", type=float, default=2)
    fund = p.add_mutually_exclusive_group()
    fund.add_argument("--funding-csv", type=Path)
    fund.add_argument("--funding-rate", type=float, help="常数资金费场景，小数；不是历史真实费率")
    p.add_argument("--funding-interval-hours", type=int, default=8)
    p.add_argument("--train-bars", type=int, default=240)
    p.add_argument("--test-bars", type=int, default=120)
    p.add_argument("--embargo-bars", type=int, default=1)
    p.add_argument("--min-train-trades", type=int, default=3)
    p.add_argument("--pbo-partitions", type=int, default=8)
    p.add_argument("--output-dir", type=Path, default=Path("output/research"))
    args = p.parse_args()
    try:
        as_of = parse_time(args.as_of) if args.as_of else datetime.now(timezone.utc)
        start = parse_time(args.start) if args.start else None
        source_hours = PERIODS[args.source_period]
        raw = read_csv(args.csv)
        unified_report = None
        if args.source_period == "1m" and not args.independent_periods:
            from unified import prepare_minutes
            closed, quality = prepare_minutes(raw, as_of, read_csv(args.context_csv) if args.context_csv else [], args.anchor_hour)
        else:
            if args.context_csv or args.require_history:
                raise ValueError("前置历史参数需要统一 1m 模式")
            closed, quality = resample(raw, source_hours, source_hours, as_of, args.anchor_hour)
        if not closed:
            raise ValueError("没有已收盘行情")
        policy = load_policy(args.policy)
        periods = list(dict.fromkeys(args.periods or compatible_periods(source_hours)))
        unified_mode = args.source_period == "1m" and not args.independent_periods
        # 投影不是独立信号来源；统一研究仅接受原始 1m 事件，防止伪造多周期共振。
        candidates = load_candidates(args.candidates, ["1m"] if unified_mode else periods)
        costs = ExecutionCosts(args.fee_bps, args.slippage_bps, args.funding_rate, args.funding_interval_hours)
        if min(args.train_bars, args.test_bars, args.min_train_trades) < 1 or args.embargo_bars < 0:
            raise ValueError("训练/测试长度及最低交易数必须为正，隔离根数必须非负")
        if args.pbo_partitions < 4 or args.pbo_partitions > 12 or args.pbo_partitions % 2:
            raise ValueError("CSCV partitions 必须为 4..12 的偶数")
        funding = read_funding(args.funding_csv) if args.funding_csv else None
        for period in periods:
            resample([], source_hours, PERIODS[period], as_of, args.anchor_hour)
        frames = []
        args.output_dir.mkdir(parents=True, exist_ok=True)
        if unified_mode:
            from unified import HistoryPolicy, analyze_unified, replay_unified, write_outputs
            unified_report = analyze_unified(closed, as_of, start, periods, anchor_hour=args.anchor_hour,
                                              policy=policy, history_policy=HistoryPolicy(args.min_context_bars, args.required_level))
            unified_report["data_origin"] = {"csv": args.csv, "context_csv": args.context_csv}
            write_outputs(unified_report, args.output_dir / "unified")
            if args.require_history and unified_report["history"]["status"] != "usable_under_checks":
                print(f"历史或完成证据不足，未执行交易研究；详见 {args.output_dir / 'unified/report.md'}", flush=True)
                raise SystemExit(2)
        for period in (["1m"] if unified_mode else periods):
            print(f"逐根回放 {period}，保留前置行情……", flush=True)
            with (args.output_dir / f"replay_{period}.jsonl").open("w", encoding="utf-8") as f:
                stream = (replay_unified(closed, as_of, policy=policy, anchor_hour=args.anchor_hour) if unified_mode else
                          replay(closed, source_hours, PERIODS[period], step=1, min_bars=1,
                                 as_of=as_of, anchor_hour=args.anchor_hour, policy=policy))
                for row in stream:
                    f.write(json.dumps(row, ensure_ascii=False, default=json_default, allow_nan=False)+"\n")
                    # 交易模拟只消费信号；磁盘仍完整保存结构生命周期。
                    frames.append({**row, "events": [e for e in row["events"] if
                                   e.get("structure", e.get("previous_structure"))["type"] in ("signal", "policy_signal")]})
        evaluation = [c for c in closed if start is None or c.time >= start]
        if not evaluation:
            raise ValueError("交易起点之后没有已收盘行情")
        frames.sort(key=lambda x: (x["as_of"], x["period"]))
        runs = [backtest(evaluation, frames, source_hours, c, costs, funding) for c in candidates]
        wf = walk_forward(evaluation, frames, candidates, source_hours, costs, funding,
                          args.train_bars, args.test_bars, args.embargo_bars, args.min_train_trades)
        report = {"schema": "chan-research-v1", "as_of": as_of, "trading_start": start,
                  "input": {"source": str(args.csv), "closed_ohlcv_sha256": candle_digest(closed),
                            "source_period": args.source_period, "periods": periods,
                            "anchor_hour_utc": args.anchor_hour, "quality": quality},
                  "source_sha256": source_fingerprint(), "analysis_rules": policy.manifest(),
                  "declared_candidates": [asdict(c) for c in candidates], "backtests": runs,
                  "walk_forward": wf, "overfitting": assess_overfitting(runs, args.pbo_partitions, args.min_train_trades),
                  "references": ["https://github.com/Vespa314/chan.py", "https://github.com/waditu/czsc",
                                 "https://github.com/yijixiuxin/chanlun-pro", "https://www.wyckoffanalytics.com/wyckoff-method/",
                                 "https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf"]}
        report["funding_input"] = {"source": str(args.funding_csv) if args.funding_csv else None,
                                   "sha256": hashlib.sha256(args.funding_csv.read_bytes()).hexdigest() if args.funding_csv else None}
        report["structure_mode"] = "shared_1m_tree" if unified_mode else "independent_periods"
        report["history_check"] = unified_report["history"] if unified_report else None
        report["signal_periods"] = ["1m"] if unified_mode else periods
        (args.output_dir / "research.json").write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                                default=json_default, allow_nan=False), encoding="utf-8")
        write_summary(args.output_dir / "report.md", report)
        write_viewer(args.output_dir / "signals.html", frames)
    except (ValueError, OSError, TypeError) as exc:
        p.error(str(exc))
    print(f"研究结果：{args.output_dir / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()
