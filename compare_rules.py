"""同一行情的规则对照及外部引擎交换格式；定位最早的结构差异。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from chan import PERIODS, analyze, json_default, parse_time, read_csv, resample
from chan_policy import candle_digest, load_policy


def comparison_snapshot(candles, hours, as_of, anchor_hour, policy):
    report = analyze(candles, hours, **policy.kwargs())
    rows = {k: [] for k in ("fractals", "strokes", "segments", "centers", "completed_movements")}
    timing = {k: [] for k in ("strokes", "segments", "centers")}
    diagnostics = []
    for block in report["blocks"]:
        for f in block["fractals"]:
            rows["fractals"].append({"time": f.time, "kind": f.kind, "price": f.price})
        for kind in ("strokes", "segments"):
            for item in block.get(kind, []):
                confirmed = not item.provisional if kind == "strokes" else item.confirmed
                rows[kind].append({"start": item.start.time, "end": item.end.time,
                                   "start_price": item.start.price, "end_price": item.end.price,
                                   "confirmed": confirmed})
                timing[kind].append({"start": item.start.time, "end": item.end.time,
                                     "evidence_at": item.confirmed_at if kind == "strokes" else item.evidence_at})
        for z in block["centers"]:
            rows["centers"].append({"start": z.start, "end": z.end, "level": getattr(z, "level", 1),
                                   "zd": z.zd, "zg": z.zg})
            timing["centers"].append({"start": z.start, "end": z.end, "level": getattr(z, "level", 1),
                                      "evidence_at": getattr(z, "evidence_at", None)})
        for layer in block.get("theory", {}).get("type_hierarchy", {}).get("levels", []):
            for m in layer["movements"]:
                rows["completed_movements"].append({"start": m.start.time, "end": m.end.time, "level": m.level,
                                                    "kind": m.kind, "low": m.low, "high": m.high,
                                                    "evidence_at": m.evidence_at})
        diagnostics.append({"start": block["start"], "stroke": block["stroke_diagnostics"],
                            "segment": block.get("segment_diagnostics"),
                            "completion": [{"level": x["level"], "events": x.get("events", []),
                                            "diagnostics": x.get("diagnostics")}
                                           for x in block.get("theory", {}).get("type_hierarchy", {}).get("levels", [])]})
    doc = {"format": "chan-comparison-v1", "engine": "local-chan", "engine_version": "rules-v1",
           "input": {"ohlcv_sha256": candle_digest(candles), "bar_count": len(candles),
                     "period_hours": hours, "anchor_hour_utc": anchor_hour, "as_of": as_of,
                     "start": candles[0].time if candles else None, "end": candles[-1].time if candles else None},
           "rules": policy.manifest(), "structures": rows, "confirmation_timing": timing,
           "diagnostics": diagnostics}
    return json.loads(json.dumps(doc, default=json_default, allow_nan=False))


def compare_snapshots(left, right):
    for doc in (left, right):
        if (doc.get("format") != "chan-comparison-v1" or not isinstance(doc.get("engine"), str)
                or not doc["engine"] or not doc.get("engine_version") or not isinstance(doc.get("rules"), dict)
                or not doc["rules"]):
            raise ValueError("对照文件必须带 format、engine、engine_version 和规则对象 rules")
        required_input = {"ohlcv_sha256", "bar_count", "period_hours", "anchor_hour_utc", "as_of", "start", "end"}
        if not isinstance(doc.get("input"), dict) or required_input-doc["input"].keys() or not doc["input"]["ohlcv_sha256"]:
            raise ValueError("对照文件缺少完整输入元数据或行情指纹")
    if left["input"] != right["input"]:
        raise ValueError("行情、截止时刻、周期或时间桶不一致，不能进行规则对照")
    results = []
    fields = {"fractals": ("time", "kind", "price"),
              "strokes": ("start", "end", "start_price", "end_price", "confirmed"),
              "segments": ("start", "end", "start_price", "end_price", "confirmed"),
              "centers": ("start", "end", "level", "zd", "zg"),
              "completed_movements": ("start", "end", "level", "kind", "low", "high", "evidence_at")}
    for kind, required in fields.items():
        a, b = left.get("structures", {}).get(kind), right.get("structures", {}).get(kind)
        if a is None or b is None:
            results.append({"stage": kind, "status": "not_comparable", "reason": "engine_did_not_export_stage"})
            continue
        normalized = []
        for values in (a, b):
            if not isinstance(values, list) or any(not isinstance(x, dict) or set(required)-x.keys() for x in values):
                raise ValueError(f"{kind} 不符合交换格式")
            selected = [{k: x[k] for k in required} for x in values]
            for row in selected:
                for field in ("time", "start", "end", "evidence_at"):
                    if field in row and row[field] is not None:
                        row[field] = parse_time(row[field]).isoformat()
            normalized.append(sorted(selected, key=lambda x: (x.get("start", x.get("time")), x.get("level", 0))))
        a, b = normalized
        first = next((i for i in range(max(len(a), len(b)))
                      if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None)), None)
        results.append({"stage": kind, "status": "equal" if first is None else "different",
                        "left_count": len(a), "right_count": len(b), "first_difference": None if first is None else
                        {"index": first, "left": a[first] if first < len(a) else None,
                         "right": b[first] if first < len(b) else None}})
    earliest = next((x for x in results if x["status"] == "different"), None)
    hints = {"fractals": "检查包含方向、稳定右邻和分型平价规则",
             "strokes": "检查分型间距、全跨度极值、选笔路径和末笔确认",
             "segments": "检查特征序列包含、缺口、直接破坏等待与证据时刻",
             "centers": "检查子走势范围、递归层级和结合律",
             "completed_movements": "检查反向三类点、不相容中枢及走势完成口径"}
    timing = []
    for stage in ("strokes", "segments", "centers"):
        a = left.get("confirmation_timing", {}).get(stage)
        b = right.get("confirmation_timing", {}).get(stage)
        status = "not_comparable" if a is None or b is None else "equal" if a == b else "different"
        first = next((i for i in range(max(len(a), len(b))) if
                      (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None)), None) if a is not None and b is not None else None
        timing.append({"stage": stage, "status": status, "first_difference": None if first is None else
                       {"index": first, "left": a[first] if first < len(a) else None,
                        "right": b[first] if first < len(b) else None}})
    return {"input": left["input"], "left_engine": left["engine"], "right_engine": right["engine"],
            "independent_engines": left["engine"] != right["engine"],
            "left_rules": left["rules"], "right_rules": right["rules"], "stages": results,
            "confirmation_timing": timing,
            "first_differing_stage": earliest["stage"] if earliest else None,
            "review_hint": hints[earliest["stage"]] if earliest else None,
            "scope": "difference_localization_not_correctness_vote; exact_prices_no_tolerance"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--csv", required=True)
    p.add_argument("--source-period", choices=PERIODS, default="1h")
    p.add_argument("--period", choices=PERIODS, default="1h")
    p.add_argument("--as-of")
    p.add_argument("--anchor-hour", type=int, default=0)
    p.add_argument("--left-policy", type=Path)
    right = p.add_mutually_exclusive_group()
    right.add_argument("--right-policy", type=Path)
    right.add_argument("--external", type=Path, help="外部引擎按 chan-comparison-v1 导出的 JSON")
    p.add_argument("--output-dir", type=Path, default=Path("output/comparison"))
    args = p.parse_args()
    try:
        as_of = parse_time(args.as_of) if args.as_of else datetime.now(timezone.utc)
        candles, _ = resample(read_csv(args.csv), PERIODS[args.source_period], PERIODS[args.period], as_of, args.anchor_hour)
        left = comparison_snapshot(candles, PERIODS[args.period], as_of, args.anchor_hour, load_policy(args.left_policy))
        other = json.loads(args.external.read_text()) if args.external else comparison_snapshot(
            candles, PERIODS[args.period], as_of, args.anchor_hour, load_policy(args.right_policy))
        result = compare_snapshots(left, other)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, doc in (("left.json", left), ("right.json", other), ("comparison.json", result)):
            (args.output_dir / name).write_text(json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    except (ValueError, OSError, TypeError) as exc:
        p.error(str(exc))
    print(f"首个差异阶段：{result['first_differing_stage']}；输出：{args.output_dir}")


if __name__ == "__main__":
    main()
