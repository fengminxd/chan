"""汇总真实 BTC 分钟统一树快照；不把快照信号冒充逐根回放结果。"""
from collections import Counter
import csv
from datetime import timedelta, timezone
import hashlib
import json
from pathlib import Path

from chan import parse_time
from research import source_fingerprint

FOLDER = Path("output/btc_unified")
DATA = Path("reports/BTCUSDT_perpetual_1m_20260606_20260819")
TZ = timezone(timedelta(hours=8))


def local(value):
    return parse_time(value).astimezone(TZ).strftime("%Y-%m-%d %H:%M") if value else "等待"


def main():
    report = json.loads((FOLDER/"analysis.json").read_text())
    source = json.loads((DATA/"source.json").read_text())
    quality = json.loads((DATA/"data_validation.json").read_text())
    assert quality["status"] == "passed"
    start, end = parse_time(report["target_start"]), parse_time(report["as_of"])
    nodes = report["tree"]["nodes"]
    target = [n for n in nodes.values() if parse_time(n["end"]) >= start]
    centers = sorted((n for n in target if n["kind"] in ("center", "center_seed")),
                     key=lambda n: (n["level"], n["start"]))
    stats = []
    for row in report["history"]["levels"]:
        level = row["level"]
        z = [n for n in centers if n["level"] == level]
        moves = [n for n in target if n["kind"] == "movement" and n["level"] == level]
        stats.append({**row, "target_overlapping_centers": len(z),
                      "center_evidence_in_window": sum(parse_time(n["evidence_at"]) >= start for n in z),
                      "target_extending_movements": sum(n["state"] != "completed" for n in moves)})
    fields = ["id", "level", "kind", "state", "zd", "zg", "start_utc8", "end_utc8",
              "evidence_at_utc8", "owner_state", "promotion_ready"]
    with (FOLDER/"centers.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for n in centers:
            row = {k: n[k] for k in ("id", "level", "kind", "state", "zd", "zg", "promotion_ready")}
            row.update(start_utc8=local(n["start"]), end_utc8=local(n["end"]),
                       evidence_at_utc8=local(n["evidence_at"]),
                       owner_state=nodes.get(n.get("owner_movement_id"), {}).get("state", "no_parent_yet"))
            writer.writerow(row)
    audits, auxiliary, signals, diagnostics, recursive_third = [], [], [], [], []
    for block in report["base_analysis"]["blocks"]:
        theory = block["theory"]
        diagnostics.append({"start": block["start"], "end": block["end"],
                            "strokes": len(block["strokes"]), "confirmed_segments": len(block["segments"]),
                            "last_confirmed_segment": block["segments"][-1] if block["segments"] else None,
                            "last_completed_movement_by_level": {
                                str(l["level"]): l["movements"][-1] if l["movements"] else None
                                for l in theory["type_hierarchy"]["levels"]},
                            "stroke_diagnostics": block["stroke_diagnostics"],
                            "segment_diagnostics": block["segment_diagnostics"]})
        for layer in theory["levels"]:
            auxiliary.extend(d for d in layer["divergences"] if start <= parse_time(d["end"]) < end)
            signals.extend({"origin": "existing_base_theory", **s} for s in layer["signals"]
                           if start <= parse_time(s["time"]) < end)
        for layer in theory["strict_divergence"]["levels"]:
            audits.extend(d for d in layer["audits"] if start <= parse_time(d["end"]) < end)
            signals.extend({"origin": "completed_type_strict_policy", **s} for s in layer["signals"]
                           if start <= parse_time(s["time"]) < end)
            recursive_third.extend({"origin": "completed_children_evolution", **s}
                                   for s in layer["structural_view"]["signals"]
                                   if start <= parse_time(s["time"]) < end)
    failed = Counter(c["rule"] for a in audits for c in a["checks"] if c["state"] == "fail")
    pending = Counter(c["rule"] for a in audits for c in a["checks"] if c["state"] == "pending")
    signal_counts = Counter(f"{s['origin']}:{s['certainty']}:{s['label']}" for s in signals)
    for s in [*signals, *recursive_third]:
        assert parse_time(s["evidence_at"]) <= end
    views = report["views"]
    ids = [{p["node_id"] for p in v["projections"]} for v in views.values()]
    assert all(s == ids[0] for s in ids), "多视图引用集合不一致"
    summary = {"symbol": source["symbol"], "market": source["market"], "price_type": source["price_type"],
               "target_start": report["target_start"], "as_of": report["as_of"], "data_validation": quality,
               "history": report["history"], "levels": stats,
               "view_bar_counts": {p: len(v["candles"]) for p, v in views.items()},
               "shared_projection_ids_verified": True, "structure_diagnostics": diagnostics,
               "strict_audit_states": dict(Counter(a["state"] for a in audits)),
               "strict_failed_checks": dict(failed), "strict_pending_checks": dict(pending),
               "macd_auxiliary_candidates": len(auxiliary), "snapshot_signal_counts": dict(signal_counts),
               "recursive_third_point_counts": dict(Counter(s["label"] for s in recursive_third)),
               "scope": "as_of_snapshot; structural_endpoints_not_first_observation_or_trade_times; no_replay"}
    (FOLDER/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (FOLDER/"signals.json").write_text(json.dumps({"base_snapshot_signals": signals,
        "completed_children_third_points": recursive_third, "macd_auxiliary": auxiliary,
        "strict_audits": audits}, ensure_ascii=False, indent=2))
    with (FOLDER/"signals.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["origin", "label", "level", "certainty", "endpoint_utc8", "price", "evidence_utc8", "id"])
        for s in sorted([*signals, *recursive_third], key=lambda x: (x["time"], x["origin"])):
            writer.writerow([s["origin"], s["label"], s["level"], s["certainty"], local(s["time"]),
                             s["price"], local(s["evidence_at"]), s["id"]])
    strict_count = summary["strict_audit_states"].get("confirmed_under_policy", 0)
    lines = ["# BTCUSDT 永续：真实 1m 统一递归分析", "",
             f"目标区间（UTC+8，左闭右开）：{local(report['target_start'])} 至 {local(report['as_of'])}。",
             "Binance USDT 本位永续成交价；前置区间为 2026-02-06 00:00 至 2026-06-06 00:00（UTC+8）。",
             "目标 106,560 根 + 前置 172,800 根 = 279,360 根真实分钟线；无缺失、无重复。",
             "分钟聚合的 4,656 根小时 OHLCV 与此前数据精确一致。原始响应、请求与校验值见数据目录。", "",
             "**行情覆盖完整，但当前规则的结构确认链没有覆盖到区间末端。以下数量是现有程序识别结果，不能作为完整市场结构或全区间不存在背驰的结论。**", "",
             "## 统一树", "", "十个图表周期共享一棵 1m 基底递归树。下表按结构层级计数，不把投影重复计为十套中枢。", "",
             "| 层级 | 全历史中枢 | 与目标区间相交中枢 | 相交已完成走势 | 相交未完成走势 |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for r in stats:
        lines.append(f"| L{r['level']} | {r['formed_centers']} | {r['target_overlapping_centers']} | {r['target_completed_movements']} | {r['target_extending_movements']} |")
    lines += ["", "相交按结构结束时刻不早于目标起点统计；结构可从前置行情延续而来，不代表全部在窗口内新生。",
              "完整目标区间中枢明细见 [centers.csv](centers.csv)，精确父子引用见 [analysis.json](analysis.json)。", "",
              "## 各层最近中枢（UTC+8）", "",
              "| 层级 | ZD–ZG（USDT） | 结构起点 | 结构终点 | 成枢证据 | 所属走势状态 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for level in sorted({n["level"] for n in centers}):
        n = max((x for x in centers if x["level"] == level), key=lambda x: x["start"])
        owner = nodes.get(n.get("owner_movement_id"), {}).get("state", "no_parent_yet")
        lines.append(f"| L{level} | {n['zd']:,.1f}–{n['zg']:,.1f} | {local(n['start'])} | {local(n['end'])} | {local(n['evidence_at'])} | {owner} |")
    lines += ["", "## 前置行情与完成证据", "",
              f"历史检查：`{report['history']['status']}`；前置敏感性：`{report['history']['stability']['status']}`。",
              f"原因：{', '.join(report['history']['reasons']) or '当前有限检查通过'}。",
              "程序对比 120 天与缩短为 60 天的前置窗口。有限窗口稳定性不证明历史绝对充分，未完成走势仍不能升级。", ""]
    for d in diagnostics:
        last = d["last_confirmed_segment"]
        if last:
            lines += [f"- 共 {d['strokes']:,} 笔、{d['confirmed_segments']:,} 条确认线段。最后确认线段结束于 **{local(last['end']['time'])}**，证据时刻 {local(last['evidence_at'])}（UTC+8）。",
                      f"- 其后有 **{d['segment_diagnostics']['tail'].get('unassigned_completed_strokes', 0):,} 条已完成笔未被划入线段**；诊断为 `{d['segment_diagnostics']['tail'].get('status')}`。这部分已有行情，仍被当前线段规则阻断。"]
        for level, move in d["last_completed_movement_by_level"].items():
            if move:
                lines.append(f"- L{level} 最近一个完成走势结束于 {local(move['end']['time'])}；证据时刻 {local(move['evidence_at'])}（UTC+8）。")
    lines += ["", "因此，7 月 2 日之后未输出新确认线段/中枢，不能解读为该段行情客观不存在这些结构。本次未修改分析判据。", "",
              "## 背驰与买卖点", "",
              f"目标区间端点的严格政策背驰确认 {strict_count} 个；全部审计状态：`{summary['strict_audit_states']}`。",
              f"原有分析视角的 MACD 辅助候选 {len(auxiliary)} 个；MACD 候选不等于严格政策确认。",
              f"完成次级走势演化视角的第三类点：`{summary['recursive_third_point_counts']}`。", "",
              "| 原有信号来源 / 确认性质 / 标签 | 数量 |", "| --- | ---: |"]
    for label, count in sorted(signal_counts.items()):
        lines.append(f"| {label} | {count} |")
    lines += ["", "原有信号与完成子走势演化视角的三类点分开保存，不合并累加。严格背驰使用结构审计和完整 SMA 吻间面积；MACD 独立保留为辅助。",
              "信号详细时间、价格和确认性质见 [signals.csv](signals.csv)；完整检查与来源见 [signals.json](signals.json)。",
              "这是截止时刻的结构快照，未执行逐分钟回放或交易回测。表中端点与证据时间不等于当时首次可交易时间。", "",
              "### 完成次级走势演化视角的三买（UTC+8）", "",
              "| 层级 | 结构端点 | 价格（USDT） | 证据时刻 | 对应中枢 ZD–ZG |",
              "| --- | --- | ---: | --- | --- |"]
    for s in recursive_third:
        if s["label"] == "B3":
            d = s["details"]
            lines.append(f"| L{s['level']} | {local(s['time'])} | {s['price']:,.1f} | {local(s['evidence_at'])} | {d['zd']:,.1f}–{d['zg']:,.1f} |")
    lines += ["", "全部 22 条严格背驰审计均因 `c_internal_standard_trend` 不通过；L0 基底没有内部中枢证据。还存在方向、创新极值或力度条件不满足，逐项结果已保留。",
              "这里的 B3 是结构信号，不自动等于趋势背驰或可执行交易。", "",
              "## 复核文件", "",
              "- [交互图表](chart.html)：切换十个观察周期，按结构层筛选并沿父子节点检查。",
              "- [统计与诊断](summary.json)：选笔尾部、线段状态、历史检查和背驰阻断项。",
              "- [真实分钟数据与下载说明](../../reports/BTCUSDT_perpetual_1m_20260606_20260819/README.md)。",
              "- [数据校验](../../reports/BTCUSDT_perpetual_1m_20260606_20260819/data_validation.json)。",
              "- 数据字段依据：[Binance 官方 USD-M K 线说明](https://github.com/binance/binance-public-data#futures)。"]
    (FOLDER/"btc_report.md").write_text("\n".join(lines)+"\n")
    manifest = {"source_sha256": source_fingerprint(), "analysis_policy": report["rule_manifest"],
                "input_sha256": source["sha256"], "as_of": report["as_of"],
                "artifacts_sha256": {name: hashlib.sha256((FOLDER/name).read_bytes()).hexdigest()
                                     for name in ("analysis.json", "summary.json", "centers.csv", "signals.csv", "btc_report.md")}}
    (FOLDER/"run_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(json.dumps({"levels": stats, "strict": summary["strict_audit_states"],
                      "macd_auxiliary": len(auxiliary), "signal_counts": dict(signal_counts),
                      "recursive_third": summary["recursive_third_point_counts"],
                      "history": report["history"]["status"], "history_reasons": report["history"]["reasons"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
