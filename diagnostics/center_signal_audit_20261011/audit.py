"""中枢及买卖点只读反例审计；退出 1 表示严格一致性检查存在未解决项。

运行：python3 diagnostics/center_signal_audit_20261011/audit.py
仅写同目录 current_results.json，保留修复前 results.json。
输入为人工声明完成的走势，验证算法行为，不代表真实 OHLC 历史复现。
"""
from dataclasses import asdict, replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chan import json_default
from chan_structure import build_segments, direct_break_marks
from chan_theory import evolving_centers, first_second_signals
from chan_types import build_type_hierarchy
from entry_plan import hourly_candidates
from test_chan import strokes
from test_theory import movements


def main():
    checks = []

    def record(name, passed, expected, actual, category="implementation"):
        checks.append(dict(name=name, passed=passed, category=category,
                           expected=expected, actual=actual))

    for sign in (1, -1):
        # 成枢实体保留父走势截面，三类点却继续扫描到该截面之后。
        prices = [128, 130, 109, 128, 78, 79, 63, 85, 61, 85, 66, 89, 81, 97, 89]
        seq = movements([sign * p for p in prices])
        centers = build_type_hierarchy(seq, 1)["levels"][0]["centers"]
        z = next(z for z in centers if z.first_index == 6)
        stop = max(z.first_index + 2, z.return_index - 2)
        members = seq[z.first_index:stop + 1]
        z_members = [m for m in members if (m.end.price > m.start.price) == (z.z_direction == "up")]
        expected = dict(child_ids=[m.id for m in members],
                        dd=min(m.low for m in z_members), gg=max(m.high for m in z_members),
                        range_low=min(m.low for m in members), range_high=max(m.high for m in members))
        actual = {key: getattr(z, key) for key in expected}
        record(f"closed_center_range_{sign}", actual == expected, expected,
               dict(prices=[sign*p for p in prices], center=asdict(z)), "range_semantics")

        # 排除底层笔重绘：固定完成走势，仅追加最后一条。
        prices = [196, 215, 214, 226, 203, 210, 195, 218, 213, 218, 199]
        seq = movements([sign * p for p in prices])
        old = build_type_hierarchy(seq[:-1], 1)["levels"][0]
        new = build_type_hierarchy(seq, 1)["levels"][0]
        key = lambda s: (s.label, s.level, s.time, s.price)
        current = {key(s) for s in new["structural_view"]["signals"]}
        lost = [s for s in old["structural_view"]["signals"] if key(s) not in current]
        record(f"structural_signal_persistence_{sign}", not lost,
               "若 structural 用作稳定历史确认，固定完成输入追加后不得丢失已发布三类点",
               dict(prices=[sign*p for p in prices], lost=[asdict(s) for s in lost],
                    old_centers=[asdict(z) for z in old["centers"]],
                    new_centers=[asdict(z) for z in new["centers"]]), "lifecycle_risk")

        # 同一中心第一次回试入枢后，再次离开且回试不回枢。
        prices = [sign*p for p in [0, 12, 6, 10, 7, 15, 9, 16, 13]]
        seq = [replace(s, confirmed_at=s.end.time + timedelta(hours=1))
               for s in strokes(prices, provisional=True)]
        legs = [dict(index=i, start=s.start.time, end=s.end.time,
                     finish=s.end.time+timedelta(hours=1), start_price=s.start.price,
                     price=s.end.price, low=s.low, high=s.high, direction=s.direction,
                     focus_time=s.end.time, focus_price=s.end.price) for i, s in enumerate(seq)]
        candidates = hourly_candidates(dict(strokes=seq, theory=dict(macd=dict(points=[]))),
                                       legs, legs[-1]["finish"])
        structural = evolving_centers(movements(prices), 1)["signals"]
        label = "B3" if sign == 1 else "S3"
        candidate_has = any(c["label"] == label for c in candidates)
        engine_has = any(s.label == label and s.price == prices[-1] for s in structural)
        record(f"third_retry_policy_alignment_{sign}", candidate_has == engine_has,
               "候选与完成结构对回枢后再次离开的可接受性一致（完成时间可不同）",
               dict(prices=prices, candidate_labels=[c["label"] for c in candidates],
                    completed_labels=[s.label for s in structural]), "policy_mismatch")

        # 沿用既有 direct_break：标记攻击笔不等于确认旧段结束。
        prices = [sign*p for p in [0, 8, 4, 10, -2, 9, 7, 8, 6, 11, 5, 10, 4]]
        seq = strokes(prices)
        early, full = build_segments(seq[:7]), build_segments(seq)
        early_marks = direct_break_marks(dict(strokes=seq[:7], direct_breaks=early.direct_breaks))
        full_marks = direct_break_marks(dict(strokes=seq, direct_breaks=full.direct_breaks))
        record(f"break_without_segment_end_{sign}", 3 in early_marks and not early.segments,
               "第3号笔已破坏，旧段仍未确认结束", dict(marks=list(early_marks), segments=len(early.segments)))
        record(f"break_survives_extension_{sign}", {3, 9} <= full_marks.keys(),
               [3, 9], list(full_marks))

        # 二类点允许弱于一类点；不把依赖候选升级为确认。
        seq = movements([sign*p for p in [20, 10, 16, 9]])
        div = dict(id="D", kind="trend", c_index=0, side="down" if sign == 1 else "up",
                   level=1, center_id="Z", evidence_at=seq[0].evidence_at)
        signals = first_second_signals(seq, [div])
        first, second = signals
        record(f"weak_second_is_dependent_{sign}",
               first.certainty == "macd_candidate" and second.certainty == "dependent_candidate"
               and second.details["strength"] == "weak_new_extreme"
               and second.evidence_at == max(m.evidence_at for m in seq),
               "弱二类点保留为依赖候选且等待反弹/回试完成", [asdict(s) for s in signals])
        record(f"consolidation_not_first_point_{sign}",
               not first_second_signals(seq, [{**div, "kind": "consolidation"}]),
               "盘整背驰不输出一类点", "no first point")

        touched = movements([sign*p for p in [0, 12, 6, 10, 7, 15, 10]])
        record(f"third_touch_boundary_waits_{sign}", not evolving_centers(touched, 1)["signals"],
               "沿用 touch_is_return 政策，接触边界不确认三类点", "no third point")

    sources = ["chan_theory.py", "chan_types.py", "chan_structure.py", "chan_dynamics.py", "entry_plan.py"]
    result = dict(scope="synthetic completed inputs; audit only; no production rules changed",
                  source_sha256={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources},
                  passed=sum(c["passed"] for c in checks), failed=sum(not c["passed"] for c in checks),
                  checks=checks)
    (Path(__file__).parent / "current_results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=json_default)+"\n", encoding="utf-8")
    for c in checks:
        print(f"{'PASS' if c['passed'] else 'FAIL'} {c['name']} [{c['category']}]")
    print(f"{result['passed']} passed; {result['failed']} unresolved checks")
    return int(result["failed"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
