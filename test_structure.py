"""带人工预期边界的回归案例；不以实现自身作为 oracle。"""
import random
import unittest
from dataclasses import replace
from datetime import timedelta

from chan import Fractal, analyze, demo_candles, resample
from chan_structure import (Feature, Movement, Segment, build_hierarchy,
                            build_segments, decompose_level, standardize_features)
from test_chan import BASE, strokes


def segment_fixture(prices):
    """递归模块使用已确认线段夹具，划段模块单独验证。"""
    legs = strokes(prices)
    return [Segment(s.start, s.end, 3 * i, 3 * i + 2, True, "no_gap", 3 * i + 5,
                    BASE + timedelta(hours=4 * i + 20)) for i, s in enumerate(legs)]


class SegmentTests(unittest.TestCase):
    def test_equal_boundary_inclusion_confirms_only_after_strict_third_break(self):
        for sign in (1, -1):
            for endpoint in (3.99, 4, 4.01):
                with self.subTest(sign=sign, endpoint=endpoint):
                    seq = strokes([sign*p for p in [0, 8, 4, 10, endpoint, 9, 3]])
                    self.assertEqual(build_segments(seq[:-1]).segments, [])
                    result = build_segments(seq)
                    self.assertEqual([(s.start.price, s.end.price, s.evidence_stroke)
                                      for s in result.segments], [(0, sign*10, 5)])
                    if endpoint == 4:
                        self.assertEqual(result.direct_breaks[0]['turn_stroke'], 3)
                        self.assertEqual(result.direct_breaks[0]['reason'], 'confirmed_direct_break')
            # 第一反向笔触及旧边界已标记，但第三笔仅触及首笔终点仍等待。
            waiting = build_segments(strokes([sign*p for p in [0, 8, 4, 10, 4, 9, 4]]))
            self.assertEqual(waiting.segments, [])
            self.assertEqual(waiting.direct_breaks[0]['turn_stroke'], 3)
            self.assertEqual(waiting.direct_breaks[0]['reason'], 'waiting_direct_break_resolution')

    def test_first_break_cannot_be_reclassified_by_later_containment(self):
        prices = [0, 8, 4, 14, 6, 13, 9, 12, 8, 11, 5]
        for sign in (1, -1):
            with self.subTest(sign=sign):
                seq = strokes([sign*p for p in prices])
                # 14→6 已满足 6<=8，不能因包含后的 [9,14] 改判第二类。
                self.assertEqual(build_segments(seq[:-1]).segments, [])
                result = build_segments(seq)
                audit = next(a for a in result.direct_breaks if a['turn_stroke'] == 3)
                self.assertFalse(audit['gap'])
                self.assertTrue(audit['direct_break'])
                self.assertFalse(audit['reverse_pattern_found'])
                self.assertTrue(audit['reverse_span_ready'])
                self.assertEqual(audit['reason'], 'confirmed_direct_break')
                first = result.segments[0]
                self.assertEqual((first.start.price, first.end.price, first.evidence_stroke),
                                 (0, sign*14, 9))
                self.assertEqual(first.confirmation, 'no_gap')
                self.assertEqual(first.reverse_feature_indices, ())
                # 后来的第二序列不应推迟已经取得的确认证据。
                extended = strokes([sign*p for p in prices + [10, 4, 11, 6, 12]])
                self.assertEqual(build_segments(extended).segments[0], first)

    def test_contained_reverse_extreme_does_not_lock_an_unbuildable_next_segment(self):
        prices = [0,4,2,10,6,12,7,11,9,10,8,15,12,16,11,14,12,17,13,18,14,19]
        for sign in (1, -1):
            seq = strokes([100 + sign*p for p in prices])
            # 12 后的首个反向笔端点 7 尚未被三笔跨度重新触及；表面分型不够。
            short = build_segments(seq[:10])
            self.assertEqual(short.segments, [])
            self.assertTrue(any(a['reason'] == 'waiting_reverse_valid_span'
                                for a in short.tail_diagnostics['candidate_turns']))
            self.assertEqual(build_segments(seq).segments, [])
            # 原方向延续到 19，然后真实三笔回落/回升才确认该转折。
            full = build_segments(strokes([100 + sign*p for p in prices + [10,16,9]]))
            self.assertEqual([(s.start.price, s.end.price) for s in full.segments],
                             [(100, 100 + sign*19)])
            self.assertIsNotNone(full.pending)
            self.assertEqual(full.pending.start, full.segments[-1].end)

    def test_right_containment_keeps_old_direction_until_fractal_forms(self):
        for sign in (1, -1):
            seq = strokes([sign*x for x in [100,80,95,60,75,58,70,61,69,62,76]])
            s = build_segments(seq).segments[0]
            self.assertEqual(s.end.price, sign*58)
            self.assertEqual(s.evidence_stroke, 9)

    def test_direct_break_cannot_be_confirmed_by_a_merged_pseudo_fractal(self):
        for sign in (1, -1):
            seq = strokes([sign*x for x in [0,8,4,10,-2,9,7,8,6,11,5,10,4]])
            self.assertEqual(build_segments(seq[:8]).segments, [])
            result = build_segments(seq)
            self.assertEqual(result.segments[0].end.price, sign*11)
            self.assertEqual((result.segments[0].low,result.segments[0].high),
                             (-2,11) if sign==1 else (-11,2))
            self.assertEqual(result.segments[0].first_stroke,0)

    def test_no_gap_top_and_bottom(self):
        for sign in (1, -1):
            with self.subTest(sign=sign):
                r = build_segments(strokes([sign * x for x in [0, 8, 4, 10, 6, 9, 5]]))
                self.assertEqual(len(r.segments), 1)
                s = r.segments[0]
                self.assertEqual((s.first_stroke, s.last_stroke, s.evidence_stroke), (0, 2, 5))
                self.assertEqual(s.end.price, sign * 10)
                self.assertEqual(s.confirmation, "no_gap")
                self.assertFalse(r.pending.confirmed)

    def test_no_confirmation_before_third_reverse_stroke(self):
        r = build_segments(strokes([0, 8, 4, 10, 6, 9]))
        self.assertEqual(r.segments, [])
        self.assertEqual(r.pending.end.price, 10)

    def test_boundary_inclusion_must_not_swallow_direct_break(self):
        # [2,10] 包含旧特征元素 [4,8]，跨边界合并会误判为一段。
        for sign in (1, -1):
            s = build_segments(strokes([sign * x for x in [0, 8, 4, 10, 2, 7, 1]])).segments[0]
            self.assertEqual((s.end.price, s.evidence_stroke), (sign * 10, 5))

    def test_direct_break_confirmation_reports_ready_reverse_span(self):
        for sign in (1, -1):
            for prices, evidence in (([0, 8, 4, 10, 2, 7, 1], 5),
                                     ([0, 8, 4, 10, 2, 7, 3, 6, 1], 7)):
                seq = strokes([sign * p for p in prices])
                waiting = build_segments(seq[:evidence])
                self.assertEqual(waiting.segments, [])
                self.assertFalse(waiting.direct_breaks[0]['reverse_span_ready'])
                result = build_segments(seq)
                event = result.direct_breaks[0]
                self.assertEqual(event['reason'], 'confirmed_direct_break')
                self.assertEqual(event['evidence_stroke'], evidence)
                self.assertTrue(event['reverse_span_ready'])
                self.assertEqual(result.segments[0].evidence_stroke, evidence)

    def test_touching_first_break_end_does_not_confirm_direct_break(self):
        for sign in (1, -1):
            seq = strokes([sign*p for p in [0, 8, 4, 10, 2, 7, 2]])
            result = build_segments(seq)
            self.assertEqual(result.segments, [])
            self.assertEqual(result.direct_breaks[0]['reason'], 'waiting_direct_break_resolution')

    def test_contained_third_stroke_waits_for_resolution(self):
        base = [0, 8, 4, 10, 2, 7, 3]
        self.assertEqual(build_segments(strokes(base)).segments, [])
        s = build_segments(strokes(base + [6, 1])).segments[0]
        self.assertEqual((s.end.price, s.evidence_stroke), (10, 7))
        # 第三笔缩在第一笔内，先上破原转折点则旧段延续。
        r = build_segments(strokes(base + [11, 5, 9, 4]))
        self.assertEqual(r.segments[0].end.price, 11)

    def test_gap_first_fractal_insufficient(self):
        self.assertEqual(build_segments(strokes([0, 8, 4, 14, 10, 13, 9])).segments, [])

    def test_gap_reverse_fractal_does_not_need_gap_fill(self):
        for sign in (1, -1):
            r = build_segments(strokes([sign * x for x in [0, 8, 4, 14, 10, 13, 9, 12, 10, 13]]))
            self.assertEqual(len(r.segments), 2)
            self.assertEqual([(s.start.price, s.end.price) for s in r.segments],
                             [(0, sign * 14), (sign * 14, sign * 9)])
            self.assertEqual(r.segments[0].confirmation, "gap_reverse_fractal")
            self.assertEqual(r.segments[0].evidence_stroke, 8)
            self.assertEqual(len(r.segments[0].reverse_feature_indices), 3)
            # 回落最低 9 仍高于前特征高点 8，缺口未封闭。

    def test_gap_second_fractal_may_itself_have_gap(self):
        # 反向序列 [12,14], [8,10], [11,13] 的底分型左侧有缺口，仍可确认。
        r = build_segments(strokes([0, 8, 4, 16, 12, 14, 8, 10, 9, 13]))
        self.assertEqual(r.segments[0].confirmation, "gap_reverse_fractal")
        self.assertEqual(r.segments[0].end.price, 16)

    def test_gap_invalidated_before_reverse_fractal(self):
        r = build_segments(strokes([0, 8, 4, 14, 10, 13, 9, 15, 10, 14, 8]))
        self.assertEqual([s.end.price for s in r.segments], [15])

    def test_equal_low_in_reverse_sequence_is_inclusion_not_bottom(self):
        # 81 课更正所强调的等价/包含边界，不把平底直接当成底分型。
        r = build_segments(strokes([0, 8, 4, 14, 10, 13, 9, 12, 9, 13]))
        self.assertEqual(r.segments, [])

    def test_touching_feature_boundary_has_no_gap(self):
        r = build_segments(strokes([0, 8, 4, 12, 8, 10, 7]))
        self.assertEqual(r.segments[0].confirmation, "no_gap")

    def test_last_provisional_stroke_cannot_confirm(self):
        r = build_segments(strokes([0, 8, 4, 10, 6, 9, 5], provisional=True))
        self.assertEqual(r.segments, [])

    def test_evidence_time_is_not_endpoint_time(self):
        seq = strokes([0, 8, 4, 10, 6, 9, 5])
        seq[-1] = replace(seq[-1], confirmed_at=BASE + timedelta(days=10))
        s = build_segments(seq).segments[0]
        self.assertEqual(s.evidence_at, BASE + timedelta(days=10))
        self.assertGreater(s.evidence_at, s.end.time)

    def test_no_overlap_prefix_exposed(self):
        r = build_segments(strokes([10, 20, 0, 5, -2, 4, -3]))
        self.assertEqual(r.skipped_prefix_strokes, 1)
        self.assertTrue(all(s.first_stroke >= 1 for s in r.segments))

    def test_invalid_sequence_rejected(self):
        seq = strokes([0, 8, 4, 10, 6])
        with self.assertRaises(ValueError):
            build_segments([seq[0], seq[2]])
        with self.assertRaises(ValueError):
            build_segments([replace(seq[0], provisional=True), *seq[1:]])

    def test_standardization_preserves_sources_and_order(self):
        f = [Feature(1, 6, (1,)), Feature(2, 5, (3,)), Feature(1.5, 5.5, (5,))]
        result = standardize_features(f, "up")
        self.assertEqual(result, [Feature(2, 6, (1, 3)), Feature(1.5, 5.5, (5,))])
        self.assertEqual(standardize_features(f[:2], "down"), [Feature(1, 5, (1, 3))])

    def test_prefix_stability_on_fixed_completed_strokes(self):
        # 完成笔输入固定时，已给出证据的线段不应因未读到的后缀改变。
        rng = random.Random(67)
        for _ in range(12):
            prices = [100]
            for i in range(48):
                prices.append(prices[-1] + (1 if i % 2 == 0 else -1) * rng.randint(1, 20))
            seq = strokes(prices)
            full = build_segments(seq)
            for stop in range(7, len(seq), 5):
                prefix = build_segments(seq[:stop]).segments
                self.assertEqual(prefix, [s for s in full.segments if s.evidence_stroke < stop])
            for s in full.segments:
                part = seq[s.first_stroke:s.last_stroke + 1]
                self.assertGreaterEqual(len(part), 3)
                self.assertEqual(len(part) % 2, 1)
                self.assertEqual(s.high, max(x.high for x in part))
                self.assertEqual(s.low, min(x.low for x in part))


class RecursiveTests(unittest.TestCase):
    def test_exact_l1_interval(self):
        h = build_hierarchy(segment_fixture([0, 10, 4, 12]))
        z = h["levels"][0]["centers"][0]
        self.assertEqual((z.zd, z.zg, z.level), (4, 10, 1))
        self.assertEqual(z.child_ids, ("L0:M0", "L0:M1", "L0:M2"))
        self.assertEqual(h["levels"][0]["movements"][0].center_ids, (z.id,))

    def test_three_completed_segments_required(self):
        segs = segment_fixture([0, 10, 4, 12])
        self.assertEqual(build_hierarchy(segs[:2])["levels"][0]["centers"], [])
        with self.assertRaises(ValueError):
            build_hierarchy(segs[:2] + [replace(segs[2], confirmed=False)])

    def test_l2_requires_nine_segments_not_three_l1_rectangles(self):
        prices = [0, 10, 4, 12, 3, 11, 5, 13, 2, 14]
        h = build_hierarchy(segment_fixture(prices))
        self.assertEqual([len(l["centers"]) for l in h["levels"]], [3, 1])
        z = h["levels"][1]["centers"][0]
        # 下层走势的完整区间 [0,12], [3,12], [2,14]，非其内部中枢框。
        self.assertEqual((z.zd, z.zg), (3, 12))
        self.assertEqual(z.child_ids, ("L1:M0", "L1:M1", "L1:M2"))
        short = build_hierarchy(segment_fixture(prices[:-1]))
        self.assertEqual(len(short["levels"]), 1)

    def test_l3_and_lineage_have_no_reused_children(self):
        prices = [0] + [10 if i % 2 == 0 else 0 for i in range(27)]
        h = build_hierarchy(segment_fixture(prices))
        self.assertEqual([len(l["centers"]) for l in h["levels"]], [9, 3, 1])
        for layer in h["levels"]:
            ids = [child for m in layer["movements"] for child in m.child_ids]
            self.assertEqual(len(ids), len(set(ids)))
            for m in layer["movements"]:
                self.assertEqual(m.kind, "consolidation")
                self.assertEqual(len(m.center_ids), 1)

    def test_leading_connector_preserves_continuity(self):
        h = build_hierarchy(segment_fixture([10, 20, 0, 5, -2, 6, -1, 7]))
        level = h["levels"][0]
        self.assertEqual(level["centers"][0].child_ids, ("L0:M1", "L0:M2", "L0:M3"))
        self.assertEqual(level["movements"][0].child_ids, ("L0:M0", "L0:M1", "L0:M2", "L0:M3"))
        self.assertEqual(level["movements"][0].end, level["movements"][1].start)

    def test_unassigned_tail_not_promoted(self):
        level = build_hierarchy(segment_fixture([0, 10, 4, 12, 3, 11]))["levels"][0]
        self.assertEqual(level["unassigned_child_ids"], ["L0:M3", "L0:M4"])
        self.assertEqual(len(level["movements"]), 1)

    def test_zero_width_intersection(self):
        # 价格区间允许单点重合；子走势首尾连接，内部波动可超出端点。
        endpoints = [Fractal(i, "bottom", p, BASE + timedelta(hours=i))
                     for i, p in enumerate([0, 1, 2, 3])]
        children = [Movement(f"L1:M{i}", 1, "consolidation", endpoints[i], endpoints[i + 1],
                             lo, hi, (), (), BASE + timedelta(hours=10))
                    for i, (lo, hi) in enumerate([(0, 2), (1, 3), (2, 4)])]
        z = decompose_level(children, 2)["centers"][0]
        self.assertEqual((z.zd, z.zg), (2, 2))

    def test_mixed_levels_and_gaps_rejected(self):
        h = build_hierarchy(segment_fixture([0, 10, 4, 12]))
        children = h["base_movements"]
        with self.assertRaises(ValueError):
            decompose_level([children[0], replace(children[1], level=1), children[2]], 1)
        with self.assertRaises(ValueError):
            decompose_level([children[0], children[2]], 1)

    def test_max_level_and_evidence_propagation(self):
        prices = [0] + [10 if i % 2 == 0 else 0 for i in range(12)]
        segs = segment_fixture(prices)
        h = build_hierarchy(segs, max_levels=1)
        self.assertTrue(h["level_limit_reached"])
        self.assertEqual(len(h["levels"]), 1)
        z = build_hierarchy(segs)["levels"][1]["centers"][0]
        self.assertEqual(z.evidence_at, segs[8].evidence_at)
        with self.assertRaises(ValueError):
            build_hierarchy(segs, 0)

    def test_disjoint_up_moves_do_not_create_higher_center(self):
        points = [Fractal(i, "bottom", i * 10, BASE + timedelta(hours=i)) for i in range(4)]
        children = [Movement(f"L1:M{i}", 1, "consolidation", points[i], points[i + 1],
                             i * 10, (i + 1) * 10, (), (), BASE + timedelta(hours=10))
                    for i in range(3)]
        self.assertEqual(decompose_level(children, 2)["centers"], [])


class IntegrationTests(unittest.TestCase):
    def test_default_strict_and_legacy_explicit(self):
        raw = demo_candles()
        strict = analyze(raw, 1)
        legacy = analyze(raw, 1, mode="bi")
        self.assertEqual(strict["method"], "feature_sequence_recursive")
        self.assertEqual(legacy["method"], "bi_overlap_proxy")
        self.assertIn("hierarchy", strict["blocks"][0])
        self.assertNotIn("hierarchy", legacy["blocks"][0])

    def test_closed_data_evidence_not_after_as_of(self):
        raw = demo_candles()
        cutoff = raw[len(raw) // 2].time
        bars, _ = resample(raw, 1, 4, cutoff)
        result = analyze(bars, 4)
        for block in result["blocks"]:
            for s in block["segments"]:
                self.assertLessEqual(s.evidence_at, cutoff)
            for z in block["centers"]:
                self.assertLessEqual(z.evidence_at, cutoff)


if __name__ == "__main__":
    unittest.main()
