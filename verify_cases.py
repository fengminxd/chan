"""案例清单验证器：原文价位、重建规则和缺少原始行情的案例分别报告。"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import html
import json
import math
from pathlib import Path

from chan import Candle, Fractal, Stroke, json_default, merge_inclusions, parse_time, read_csv
from chan_structure import Movement, Segment, build_hierarchy, build_segments
from chan_theory import (MACDPoint, divergence_candidates, evolving_centers,
                         first_second_signals, macd, macd_area)

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)
DEFAULT_MANIFEST = Path(__file__).parent / "cases" / "core_cases.json"


def make_strokes(prices):
    rising = prices[1] > prices[0]
    points = [Fractal(i * 4, "bottom" if (i % 2 == 0) == rising else "top", p,
                      BASE + timedelta(hours=i * 4)) for i, p in enumerate(prices)]
    return [Stroke(a, b, confirmed_at=b.time + timedelta(hours=1)) for a, b in zip(points, points[1:])]


def make_moves(prices):
    return [Movement(f"L0:M{i}", 0, "segment", s.start, s.end, s.low, s.high, (), (), s.confirmed_at)
            for i, s in enumerate(make_strokes(prices))]


def _matches(actual, expected) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _matches(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, (list, tuple)) and len(actual) == len(expected) and all(_matches(a, e) for a, e in zip(actual, expected))
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (float, int)):
        return isinstance(actual, (float, int)) and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)
    return actual == expected


def _historical_macd(case, data_dir):
    spec = case["input"]
    csv_path, labels_path = data_dir / spec["csv"], data_dir / spec["labels"]
    if not csv_path.is_file() or not labels_path.is_file():
        return None, "缺少原始 CSV 或 C 段终点标注；不能以合成行情替代历史案例"
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    data = read_csv(csv_path)
    a0, a1, c0, c1 = (parse_time(spec["a_start"]), parse_time(spec["a_end"]),
                      parse_time(spec["c_start"]), parse_time(labels["c_end"]))
    if not a0 < a1 <= c0 < c1 or data[0].time > a0 or data[-1].time < c1:
        raise ValueError("案例标注时间无序或行情不覆盖 A/C 段")
    points = macd(data)
    a, c = macd_area(points, a0, a1, "up"), macd_area(points, c0, c1, "up")
    if not a["warmed_up"] or not c["warmed_up"]:
        return None, "MACD 预热行情不足，不能核对面积"
    ah = max(b.high for b in data if a0 <= b.time < a1)
    ch = max(b.high for b in data if c0 <= b.time < c1)
    return {"c_area_smaller": c["area"] < a["area"], "new_high": ch > ah,
            "a_area": a["area"], "c_area": c["area"]}, "仅核对 MACD/价格，不等同于完整历史线段复现"


def evaluate(case: dict, data_dir: Path) -> tuple[dict | None, str]:
    spec, kind = case["input"], case["kind"]
    if kind == "historical_macd":
        return _historical_macd(case, data_dir)
    if kind == "inclusion":
        bars = [Candle(BASE + timedelta(hours=i), lo, hi, lo, hi)
                for i, (lo, hi) in enumerate(spec["ranges"])]
        return {"ranges": [[b.low, b.high] for b in merge_inclusions(bars)]}, "标准化规则重建"
    prices = spec["prices"]
    if kind == "segments":
        r = build_segments(make_strokes(prices))
        return {"end_prices": [s.end.price for s in r.segments],
                "confirmation": [s.confirmation for s in r.segments],
                "evidence_indices": [s.evidence_stroke for s in r.segments],
                "pending_end": r.pending.end.price if r.pending else None}, "规则重建；非原图逐点行情"
    if kind == "evolution":
        r = evolving_centers(make_moves(prices), 1)
        return {"centers": [{k: getattr(z, k) for k in ("zd", "zg", "dd", "gg", "status")} for z in r["centers"]],
                "signals": [s.label for s in r["signals"]], "relations": [x["kind"] for x in r["relations"]],
                "classification": r["classification"]}, "已确认次级别走势的规则重建"
    if kind == "hierarchy":
        segments = [Segment(s.start, s.end, i * 3, i * 3 + 2, True, "no_gap", i * 3 + 5, s.confirmed_at)
                    for i, s in enumerate(make_strokes(prices))]
        h = build_hierarchy(segments)
        z = h["levels"][-1]["centers"][-1]
        return {"counts": [len(layer["centers"]) for layer in h["levels"]],
                "highest_core": [z.zd, z.zg]}, "以人工确认线段为输入，验证递归组合"
    if kind == "weak_second":
        moves = make_moves(prices)
        points = [MACDPoint(BASE + timedelta(hours=i), 1 if 16 <= i < 20 else 0, 0,
                            5 if 16 <= i < 20 else 1 if 32 <= i < 36 else 0, True) for i in range(49)]
        ds = divergence_candidates(moves, evolving_centers(moves, 1), points)
        signals = first_second_signals(moves, ds)
        return {"signals": [s.label for s in signals], "second_price": signals[1].price,
                "strength": signals[1].details["strength"]}, "人为构造 MACD 输入；仅验证弱二类点的条件"
    if kind == "price_legs":
        ranges = [(min(a, b), max(a, b)) for a, b in zip(prices, prices[1:])]
        return {"core": [max(x[0] for x in ranges), min(x[1] for x in ranges)],
                "first_decline": prices[0] - prices[1], "third_decline": prices[2] - prices[3],
                "new_low": prices[3] < prices[1]}, "原文明确价格的算术/区间核对；无法由四价推导 MACD"
    raise ValueError(f"未知案例类型：{kind}")


def verify(manifest: Path = DEFAULT_MANIFEST, data_dir: Path | None = None) -> dict:
    catalog = json.loads(manifest.read_text(encoding="utf-8"))
    entries = []
    ids = [c["id"] for c in catalog["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("案例 ID 重复")
    for case in catalog["cases"]:
        entry = {k: case[k] for k in ("id", "title", "source", "provenance", "expected")}
        entry["unverified"] = case.get("unverified", [])
        try:
            actual, note = evaluate(case, data_dir or manifest.parent / "local")
            status = ("unverified" if actual is None else "failed" if not _matches(actual, case["expected"]) else
                      "passed" if case["provenance"] == "reconstructed_rule" else "partial_pass")
            entry.update(actual=actual, status=status, note=note)
        except (ValueError, OSError, KeyError, IndexError, TypeError) as exc:
            entry.update(actual=None, status="failed", note=f"案例输入或验证失败：{exc}")
        entries.append(entry)
    return {"schema_version": 1, "summary": dict(Counter(e["status"] for e in entries)),
            "historical_fully_reproduced": 0,
            "scope": "规则重建通过不代表历史案例复现；partial_pass 只代表列出的可核对部分", "cases": entries}


def write_report(report: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "case_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
    rows = []
    names = {"passed": "重建规则通过", "partial_pass": "部分核对通过", "unverified": "待原始数据", "failed": "失败"}
    for c in report["cases"]:
        source = html.escape(c["source"], quote=True)
        actual = html.escape(json.dumps(c["actual"], ensure_ascii=False, indent=2))
        expected = html.escape(json.dumps(c["expected"], ensure_ascii=False, indent=2))
        rows.append(f'<tr><td><a href="{source}">{html.escape(c["title"])}</a></td>'
                    f'<td>{names[c["status"]]}</td><td>{html.escape(c["note"])}<br>'
                    f'{html.escape("；".join(c["unverified"]))}</td>'
                    f'<td><details><summary>预期与实际</summary><pre>{expected}</pre><pre>{actual}</pre></details></td></tr>')
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>缠论案例核对</title>'
            '<style>body{font:15px system-ui;margin:24px}table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:10px}</style>'
            '<h1>缠论案例核对</h1><p>' + html.escape(report["scope"]) + '</p><p>完整历史复现数：0</p>'
            '<table><tr><th>来源与案例</th><th>状态</th><th>核对范围</th><th>结果</th></tr>' + ''.join(rows) + '</table></html>')
    (output / "case_report.html").write_text(page, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("output/cases"))
    parser.add_argument("--require-historical", action="store_true", help="存在未完全验证的原文案例时返回非零")
    args = parser.parse_args()
    try:
        report = verify(args.manifest, args.data_dir)
        write_report(report, args.output_dir)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"报告：{args.output_dir / 'case_report.html'}；完整历史复现：0")
    if report["summary"].get("failed"):
        raise SystemExit(1)
    if args.require_historical and any(e["provenance"] != "reconstructed_rule" and e["status"] != "passed" for e in report["cases"]):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
