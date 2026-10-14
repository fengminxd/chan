"""合成单元输入检验规则与证据时刻；不运行原文历史案例验证器。"""
import json
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from chan import analyze, json_default
from chan_dynamics import (analyze_dynamics, audit_divergence, compare_strength,
                           integrate_spread, strength_waves)
from chan_structure import Movement
from chan_theory import evolving_centers
from chan_types import build_type_hierarchy, decompose_types
from test_chan import BASE, candle
from test_theory import TREND, movements


def t(hour):
    return BASE + timedelta(hours=hour)


def comparison_waves(sign=1):
    return integrate_spread([(t(i), sign * v) for i, v in
                             [(16, 0), (18, 4), (20, 0), (32, 0), (38, 1), (44, 0)]])


def pattern(sign=1):
    seq = movements([sign * x for x in TREND], 1)
    view = evolving_centers(seq, 2)
    A, B = view["centers"][:2]
    inner = []
    for i, (lo, hi) in zip((8, 9, 10), ((16, 17), (19.25, 20), (21, 22))):
        lo, hi = sorted((sign * lo, sign * hi))
        z = replace(A, id=f"INNER:{i}", level=1, first_index=1, last_index=3,
                    start=t(4 * i + 1), end=t(4 * i + 3), formed_at=t(4 * i + 3),
                    evidence_at=t(4 * i + 4), updated_at=t(4 * i + 4),
                    zd=lo, zg=hi, dd=lo, gg=hi, d=lo, g=hi, range_low=lo, range_high=hi)
        inner.append(z)
        seq[i] = replace(seq[i], center_ids=(z.id,))
    part = seq[8:11]
    c = Movement("c", 1, "uptrend" if sign == 1 else "downtrend", part[0].start, part[-1].end,
                 min(m.low for m in part), max(m.high for m in part), tuple(m.id for m in part),
                 tuple(z.id for z in inner), t(49), "completed", "confirmed_component_boundary")
    third = next(s for s in view["signals"] if s.center_id == B.id)
    return seq, (seq[0], A, seq[4], B, c, inner, third, comparison_waves(sign))


class StrengthTests(unittest.TestCase):
    def test_exact_piecewise_linear_area_crossings_and_evidence(self):
        waves = integrate_spread([(t(0), 0), (t(1), 2), (t(2), -2), (t(3), 0)])
        self.assertEqual(len(waves), 2)
        self.assertEqual(waves[0].end, t(1.5))
        self.assertEqual(waves[0].evidence_at, t(2))
        self.assertEqual([w.area for w in waves], [1.5, 1.5])
        self.assertTrue(all(w.closed and w.left_bounded for w in waves))

    def test_truncated_start_and_unclosed_tail(self):
        waves = integrate_spread([(t(0), 2), (t(1), -2), (t(2), -3)])
        self.assertFalse(waves[0].left_bounded)
        self.assertTrue(waves[0].closed)
        self.assertFalse(waves[1].closed)
        self.assertIsNone(waves[1].end)

    def test_sma_uses_close_time_and_full_warmup(self):
        bars = [candle(i, p, p) for i, p in enumerate([1, 1, 1, 4, 4, 1, 1, 1])]
        waves = strength_waves(bars, 1, 2, 3)
        self.assertEqual(waves[0].start, t(3))
        self.assertTrue(waves[0].left_bounded)
        self.assertTrue(all(w.evidence_at <= t(8) for w in waves))
        self.assertEqual(strength_waves(bars[:2], 1, 2, 3), [])
        self.assertEqual(strength_waves([candle(i, 1, 1) for i in range(20)], 1), [])

    def test_no_gap_bridge_or_invalid_parameters(self):
        with self.assertRaises(ValueError):
            strength_waves([candle(0, 1, 1), candle(2, 2, 2)], 1)
        for fast, slow in ((0, 10), (10, 5), (2.5, 10)):
            with self.assertRaises(ValueError):
                strength_waves([], 1, fast, slow)

    def test_b_c_comparison_and_unknown_states(self):
        _, args = pattern()
        b, c, waves = args[2], args[4], args[-1]
        result = compare_strength(b, c, waves)
        self.assertEqual(result["comparison"], "b_vs_c")
        self.assertEqual(result["ratio"], 0.75)  # 手算三角形面积 b=8, c=6
        self.assertTrue(result["weaker"])
        self.assertEqual(result["evidence_at"], t(49))
        self.assertIsNone(compare_strength(b, c, [waves[0], replace(waves[1], closed=False, end=None)])["weaker"])
        self.assertFalse(compare_strength(b, c, [waves[0], replace(waves[1], area=8)])["weaker"])
        self.assertEqual(compare_strength(replace(b, level=2), c, waves)["state"], "unknown")
        self.assertTrue(compare_strength(replace(b, level=0), c, waves)["weaker"])

    def test_indicator_prefix_does_not_close_future_wave(self):
        ps = [(t(0), 0), (t(1), 2), (t(2), 1), (t(3), -1)]
        self.assertFalse(integrate_spread(ps[:3])[-1].closed)
        self.assertTrue(integrate_spread(ps)[0].closed)


class MovementTypeTests(unittest.TestCase):
    def test_incompatible_extreme_falls_back_to_legal_boundary(self):
        # 首个反向三类点的可选终点方向不符；后来的扩展事件提供更大
        # 搜索范围，但其最高/最低点之前已有扩展中枢。较早单中枢分界
        # 仍合法，不能因为首选极值失败就把后面所有走势永久积压。
        for sign in (1, -1):
            seq = movements([sign*x for x in [0, 12, 6, 10, 4, 5, 3, 13, 8, 14, 9]])
            result = decompose_types(seq, 1)
            self.assertEqual(len(result['movements']), 1)
            move = result['movements'][0]
            self.assertEqual((move.kind, move.end.price, move.completion),
                             ('consolidation', sign*5, 'center_expansion'))
            self.assertEqual(move.child_ids, tuple(m.id for m in seq[:5]))
            self.assertEqual(move.evidence_at, seq[-1].evidence_at)
            assigned = [cid for m in result['movements'] for cid in m.child_ids]
            self.assertEqual(assigned + result['unassigned_child_ids'], [m.id for m in seq])

    def test_fallback_waits_for_event_and_preserves_completed_prefix(self):
        for sign in (1, -1):
            seq = movements([sign*x for x in
                             [0, 12, 6, 10, 4, 5, 3, 13, 8, 14, 9, 16, 7, 11, 5, 8]])
            full = decompose_types(seq, 1)['movements']
            self.assertTrue(full)
            # 已有较早几何分界并不等于已有完成证据。
            for stop in range(4, 10):
                self.assertEqual(decompose_types(seq[:stop], 1)['movements'], [])
            for stop in range(4, len(seq)+1):
                prefix = decompose_types(seq[:stop], 1)['movements']
                self.assertEqual(prefix, [m for m in full if m.evidence_at <= seq[stop-1].evidence_at])

    def test_boundary_cache_preserves_uncached_results_and_evidence(self):
        import random
        import chan_types
        rng = random.Random(37)
        prices = [100]
        for i in range(120):
            prices.append(prices[-1] + (1 if i % 2 == 0 else -1) * rng.randint(1, 20))
        seq = movements(prices)
        original = chan_types._boundary
        for stop in (40, 80, 120):
            cached = decompose_types(seq[:stop], 1)
            with patch('chan_types._boundary', side_effect=lambda children, view, cache: original(children, view)):
                uncached = decompose_types(seq[:stop], 1)
            self.assertEqual(cached, uncached)

    def test_three_completed_types_expose_center_without_completing_parent(self):
        completed = movements([0,10,4,12],level=1)
        # 本测试隔离递归门槛；三个子走势完成条件由前面的完成规则测试覆盖。
        layer = {"level":1,"movements":completed}
        with patch("chan_types.decompose_types",return_value=layer):
            result = build_type_hierarchy([],max_levels=2)
        seed = result["next_level_center_seeds"][0]
        self.assertEqual((seed["level"],seed["zd"],seed["zg"]),(2,4,10))
        self.assertEqual(seed["state"],"center_formed_parent_movement_uncompleted")
        self.assertEqual(len(result["levels"]),1)

    def test_consolidation_can_finish_at_a_lower_top_or_higher_bottom(self):
        for sign in (1,-1):
            seq = movements([sign*x for x in [0,12,6,10,7,9,5,6]])
            result = decompose_types(seq,1)
            self.assertEqual(len(result["movements"]),1)
            m = result["movements"][0]
            self.assertEqual((m.kind,m.end.price,m.completion),
                             ("consolidation",sign*9,"opposite_third_point"))
            self.assertEqual((m.low,m.high),(0,12) if sign==1 else (-12,0))
            self.assertEqual(m.evidence_at,seq[-1].evidence_at)

    def test_three_or_four_legs_are_not_a_completed_movement(self):
        for prices in ([0, 12, 6, 10], [0, 12, 6, 10, 7]):
            layer = decompose_types(movements(prices), 1)
            self.assertEqual(layer["movements"], [])
        self.assertEqual(layer["active_movement"].kind, "consolidation")
        self.assertEqual(layer["active_movement"].status, "extending")

    def test_same_direction_centers_continue_without_forced_completion(self):
        for sign, kind in ((1, "uptrend"), (-1, "downtrend")):
            h = build_type_hierarchy(movements([sign * x for x in TREND]))
            self.assertEqual(len(h["levels"]), 1)
            self.assertEqual(h["levels"][0]["movements"], [])
            self.assertEqual(h["levels"][0]["active_movement"].kind, kind)

    def test_opposite_third_point_completes_consolidation(self):
        for sign in (1, -1):
            seq = movements([sign * x for x in [0, 12, 6, 10, 7, 14, 4, 6]])
            result = decompose_types(seq, 1)
            m = result["movements"][0]
            self.assertEqual(m.end.price, sign * 14)
            self.assertEqual(m.completion, "opposite_third_point")
            self.assertEqual(m.evidence_at, seq[-1].evidence_at)
            self.assertGreater(m.evidence_at, m.end.time)
            self.assertEqual(decompose_types(seq[:-1], 1)["movements"], [])

    def test_expansion_repartitions_at_prior_extreme_and_keeps_all_children(self):
        seq = movements(TREND + [25, 18, 22, 16, 20, 14])
        result = decompose_types(seq, 1)
        m = result["movements"][0]
        self.assertEqual((m.kind, m.end.price, m.completion), ("uptrend", 21, "center_expansion"))
        assigned = [cid for m in result["movements"] for cid in m.child_ids]
        self.assertEqual(assigned + result["unassigned_child_ids"], [m.id for m in seq])
        self.assertTrue(any(e["kind"] == "repartition" for e in result["events"]))

    def test_completed_output_prefix_stability_and_lineage(self):
        seq = movements(TREND + [25, 18, 22, 16, 20, 14, 18, 12, 16, 10, 14, 8])
        full = decompose_types(seq, 1)["movements"]
        for stop in range(4, len(seq) + 1):
            result = decompose_types(seq[:stop], 1)["movements"]
            self.assertEqual(result, [m for m in full if m.evidence_at <= seq[stop - 1].evidence_at])
        with self.assertRaises(ValueError):
            decompose_types([replace(seq[0], status="extending"), *seq[1:]], 1)

    def test_upper_layer_uses_only_completed_types_and_propagates_evidence(self):
        prices = [100,129,114,143,114,129,117,136,112,142,124,148,133,149,127,135,124,
                  151,128,155,149,178,148,168,159,184,154,170,160,170,144,167,140,
                  157,139,156,139,160,140,159,145,155,131,138,122,139,127,157,135,
                  155,126,129,103,130,119,143,142,172,145,152,128,132,130,149,128,
                  130,121,140,132,154,125,155,151,176,159,164,136,145,137,164,157,
                  186,184,198,169,192,167,169,167,179,167,173,165,187,186,189,185,
                  188,187,189,165]
        h = build_type_hierarchy(movements(prices))
        self.assertGreaterEqual(len(h["levels"]), 2)
        lower, upper = h["levels"][:2]
        known = {m.id: m for m in lower["movements"]}
        self.assertTrue(upper["centers"])
        for z in upper["centers"]:
            self.assertTrue(all(known[cid].status == "completed" for cid in z.child_ids))
            self.assertGreaterEqual(z.updated_at, max(known[cid].evidence_at for cid in z.child_ids))
            if lower["active_movement"]:
                self.assertNotIn(lower["active_movement"].id, z.child_ids)


class DivergenceAuditTests(unittest.TestCase):
    def test_positive_standard_branch_both_directions(self):
        for sign in (1, -1):
            _, args = pattern(sign)
            audit = audit_divergence(*args)
            self.assertEqual(audit["state"], "confirmed_under_policy")
            self.assertTrue(all(c["state"] == "pass" for c in audit["checks"]))
            self.assertEqual(audit["strength"]["ratio"], 0.75)

    def test_each_structural_requirement_blocks_confirmation(self):
        _, original = pattern()
        bad_values = [(1, replace(original[1], level=3), "same_level_centers"),
                      (2, replace(original[2], level=2), "b_level_not_above_c"),
                      (4, replace(original[4], status="extending"), "completed_comparison_legs"),
                      (4, replace(original[4], level=0), "c_direct_sublevel"),
                      (5, original[5][:1], "c_two_internal_centers"),
                      (6, None, "contains_B_third_point"),
                      (6, replace(original[6], center_id="wrong"), "contains_B_third_point")]
        for index, value, rule in bad_values:
            with self.subTest(rule=rule):
                args = list(original)
                args[index] = value
                audit = audit_divergence(*args)
                self.assertFalse(audit["strict_divergence_confirmed"])
                self.assertIn(rule, [x["rule"] for x in audit["checks"] if x["state"] != "pass"])

    def test_missing_strength_is_pending_and_equal_strength_rejected(self):
        _, args = pattern()
        self.assertEqual(audit_divergence(*args[:-1], [])["state"], "pending")
        waves = [args[-1][0], replace(args[-1][1], area=8)]
        self.assertEqual(audit_divergence(*args[:-1], waves)["state"], "rejected")
        self.assertFalse(audit_divergence(*args, lineage_valid=False)["strict_divergence_confirmed"])

    def test_base_internal_evidence_is_pending_not_a_failed_trend(self):
        for sign in (1, -1):
            _, original = pattern(sign)
            a, A, b, B, c, inner, third, waves = original
            result = audit_divergence(replace(a, level=0), replace(A, level=1),
                                      replace(b, level=0), replace(B, level=1),
                                      replace(c, level=0, center_ids=()), [], replace(third, level=1), waves)
            self.assertEqual(result['state'], 'pending')
            self.assertEqual([x['rule'] for x in result['checks'] if x['state'] == 'fail'], [])
            self.assertEqual(sum(x['state'] == 'pending' for x in result['checks']), 3)

    def test_higher_center_inside_c_blocks_false_divergence(self):
        _, args = pattern()
        higher = replace(args[5][0], level=2)
        audit = audit_divergence(*args, higher_centers=[higher])
        self.assertFalse(audit["strict_divergence_confirmed"])

    def test_touching_extreme_return_and_external_internal_center_rejected(self):
        _, args = pattern()
        c = replace(args[4], end=replace(args[4].end, price=18), high=18)
        self.assertFalse(audit_divergence(*args[:4], c, *args[5:])["strict_divergence_confirmed"])
        third = replace(args[6], details={**args[6].details, "return_low": args[3].zg})
        self.assertFalse(audit_divergence(*args[:6], third, args[7])["strict_divergence_confirmed"])
        inner = [replace(args[5][0], start=t(31)), *args[5][1:]]
        self.assertFalse(audit_divergence(*args[:5], inner, *args[6:])["strict_divergence_confirmed"])

    def test_automatic_detector_consumes_lineage_and_emits_first_point(self):
        seq, args = pattern()
        # 明确提供同一中心注册表；检测器不得另算一套外部中枢。
        inner = [replace(z, departure_index=None, return_index=None) for z in args[5]]
        view = evolving_centers(seq, 2)
        hierarchy = {"base_movements": [], "levels": [
            {"level": 1, "centers": inner, "movements": seq,
             "structural_view": {"centers": inner, "signals": []}},
            {"level": 2, "centers": view['centers'], "movements": [], "structural_view": view}]}
        # 力度积分在前面用独立手算验证，此处隔离检测器的结构装配。
        with patch("chan_dynamics.strength_waves", return_value=comparison_waves()):
            result = analyze_dynamics(hierarchy, [], 1)
        self.assertEqual(len(result["confirmed"]), 1)
        signals = result["levels"][1]["signals"]
        first = [s for s in signals if s.label == 'S1']
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].certainty, "policy_confirmed")
        self.assertEqual([s for s in signals if s.label == 'B3'], view['signals'])
        self.assertIs(result['levels'][1]['structural_view'], view)

    def test_main_pipeline_all_periods_and_json_serialization(self):
        for hours in (1, 4, 8, 12, 24):
            bars = [replace(candle(i * hours, 10, 10), time=t(i * hours)) for i in range(40)]
            report = analyze(bars, hours)
            theory = report["blocks"][0]["theory"]
            self.assertIn("type_hierarchy", theory)
            self.assertEqual(theory["strict_divergence"]["strength"]["policy"], "cross_kiss_sma")
            self.assertEqual(theory["strict_divergence"]["confirmed"], [])
            json.dumps(report, default=json_default, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
