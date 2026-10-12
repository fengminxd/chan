import unittest
from dataclasses import replace
from datetime import timedelta

from chan import Candle, PERIODS, analyze, compatible_periods, resample
from chan_structure import Movement
from chan_theory import (MACDPoint, canonical_center_view, center_relation, divergence_candidates, evolving_centers,
                         first_second_signals, macd, macd_area, nested_intervals, second_retest_positions)
from test_chan import BASE, candle, strokes


def movements(prices, level=0):
    return [Movement(f"L{level}:M{i}", level, "segment" if level == 0 else "consolidation",
                     s.start, s.end, s.low, s.high, (), (), s.end.time + timedelta(hours=1))
            for i, s in enumerate(strokes(prices))]


TREND = [0, 12, 6, 10, 7, 15, 13, 18, 14, 21, 19, 23, 20]


class SecondRetestPositionTests(unittest.TestCase):
    def test_first_retest_has_exact_endpoint_without_a_first_point_signal(self):
        for sign in (1, -1):
            seq = movements([sign * p for p in [20, 10, 16, 12]])
            points = second_retest_positions(seq)
            self.assertEqual(len(points), 1)
            p = points[0]
            self.assertEqual(p['label'], 'B2' if sign == 1 else 'S2')
            self.assertEqual((p['time'], p['price']), (seq[2].end.time, sign * 12))
            self.assertEqual(p['evidence_at'], seq[2].evidence_at)
            self.assertFalse(p['first_point_confirmed'])
            self.assertEqual(second_retest_positions(seq[:2]), [])

    def test_unfinished_retest_and_internal_break_do_not_pass(self):
        seq = movements([20, 10, 16, 12])
        self.assertEqual(second_retest_positions([*seq[:2], replace(seq[2], status='extending')]), [])
        self.assertEqual(second_retest_positions([*seq[:2], replace(seq[2], low=9)]), [])
        self.assertEqual(second_retest_positions([seq[0], replace(seq[1], low=9), seq[2]]), [])
        self.assertEqual(second_retest_positions(movements([20, 10, 16, 9])), [])

    def test_evidence_includes_delayed_origin_and_disconnected_input_is_rejected(self):
        seq = movements([20, 10, 16, 12])
        delayed = replace(seq[0], evidence_at=seq[-1].evidence_at + timedelta(days=1))
        self.assertEqual(second_retest_positions([delayed, *seq[1:]])[0]['evidence_at'], delayed.evidence_at)
        with self.assertRaises(ValueError):
            second_retest_positions([seq[0], seq[2]])


class CompletionContractTests(unittest.TestCase):
    def test_incremental_center_range_matches_full_recalculation(self):
        import random
        import chan_theory
        from unittest.mock import patch
        rng = random.Random(71)
        prices = [100]
        for i in range(180):
            prices.append(prices[-1] + (1 if i % 2 == 0 else -1) * rng.randint(1, 20))
        original = chan_theory._update_range
        # 同时覆盖普通交替输入，以及同级分解中可能存在的同向/平端点单位。
        for seq in (movements(prices), movements([10,12,12,11,13,13,10,12,9,14])):
            fast = evolving_centers(seq, 1)
            with patch('chan_theory._update_range', side_effect=lambda z, moves: original(z, moves, incremental=False)):
                reference = evolving_centers(seq, 1)
            self.assertEqual(fast, reference)

    def test_unfinished_inputs_cannot_generate_structural_third_points(self):
        for state in ('extending', 'decomposition_unit'):
            seq = movements(TREND)
            seq[5] = replace(seq[5], status=state)
            with self.assertRaises(ValueError):
                evolving_centers(seq, 1)
        seq = [replace(m, status='decomposition_unit') for m in movements(TREND)]
        view = evolving_centers(seq, 1, legacy=True)
        self.assertTrue(view['signals'])
        self.assertTrue(all(s.certainty == 'legacy_structural' for s in view['signals']))
        with self.assertRaises(ValueError):
            canonical_center_view(seq, view['centers'], 1)



class PeriodTests(unittest.TestCase):
    def test_new_period_names_and_integer_multiples(self):
        self.assertEqual(PERIODS["12h"], 12)
        self.assertEqual(PERIODS["1d"], 24)
        self.assertEqual(compatible_periods(8), ["8h", "1d"])
        self.assertEqual(compatible_periods(12), ["12h", "1d"])
        self.assertEqual(compatible_periods(24), ["1d"])
        self.assertEqual(analyze([], 24)["period"], "1d")

    def test_12h_and_day_ohlcv(self):
        raw = [candle(i, i, i + 2) for i in range(48)]
        for hours in (12, 24):
            bars, _ = resample(raw, 1, hours, BASE + timedelta(hours=48))
            self.assertEqual(len(bars), 48 // hours)
            self.assertEqual((bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume),
                             (0, hours + 1, 0, hours + 1, hours))

    def test_day_unclosed_and_missing_hour(self):
        raw = [candle(i, 1, 2) for i in range(48) if i != 4]
        bars, quality = resample(raw, 1, 24, BASE + timedelta(hours=47))
        self.assertEqual(bars, [])
        self.assertEqual(quality["dropped_incomplete_buckets"], 2)
        self.assertEqual(quality["excluded_unclosed_source_bars"], 1)

    def test_custom_daily_anchor_and_leap_day(self):
        begin = BASE.replace(month=2, day=28, hour=16)
        raw = [Candle(begin + timedelta(hours=i), 1, 2, 0, 1, 1) for i in range(48)]
        bars, _ = resample(raw, 1, 24, begin + timedelta(hours=48), 16)
        self.assertEqual([x.time.day for x in bars], [28, 29])
        self.assertTrue(all(x.time.hour == 16 for x in bars))

    def test_8h_cannot_build_12h_and_day_cannot_build_12h(self):
        for source in (8, 24):
            with self.assertRaises(ValueError):
                resample([], source, 12, BASE)


class EvolutionTests(unittest.TestCase):
    def test_fixed_core_and_z_only_statistics(self):
        result = evolving_centers(movements([0, 12, 6, 10, 7, 14, 8]), 1)
        z = result["centers"][0]
        self.assertEqual((z.zd, z.zg), (7, 10))
        self.assertEqual((z.dd, z.gg, z.d, z.g), (6, 14, 8, 10))
        self.assertEqual(z.z_ids, ["L0:M1", "L0:M3", "L0:M5"])
        self.assertEqual(z.status, "extending")

    def test_third_point_requires_departure_and_completed_return(self):
        for sign in (1, -1):
            seq = movements([sign * x for x in TREND])
            before = evolving_centers(seq[:5], 1)
            self.assertEqual(before["signals"], [])
            after = evolving_centers(seq[:6], 1)
            z, s = after["centers"][0], after["signals"][0]
            self.assertEqual(s.label, "B3" if sign == 1 else "S3")
            self.assertEqual(s.price, sign * 13)
            self.assertEqual(s.certainty, "structural")
            self.assertEqual(z.departure_index, 4)
            self.assertEqual(z.return_index, 5)
            self.assertEqual(z.child_ids, ["L0:M1", "L0:M2", "L0:M3"])

    def test_return_touch_is_not_breakout(self):
        result = evolving_centers(movements([0, 12, 6, 10, 7, 15, 10]), 1)
        self.assertEqual(result["signals"], [])
        self.assertEqual(result["centers"][0].status, "extending")

    def test_disjoint_center_waves_classify_trend(self):
        for sign, kind in ((1, "uptrend"), (-1, "downtrend")):
            result = evolving_centers(movements([sign * x for x in TREND]), 1)
            self.assertEqual(result["classification"], kind)
            self.assertEqual(len(result["trend_runs"]), 1)
            self.assertEqual(len(result["trend_runs"][0]["center_ids"]), 3)

    def test_core_separation_alone_does_not_prove_trend(self):
        zs = evolving_centers(movements(TREND), 1)["centers"]
        self.assertGreater(zs[1].zd, zs[0].zg)
        overlapping_waves = replace(zs[1], dd=zs[0].gg)
        self.assertEqual(center_relation(zs[0], overlapping_waves), "expansion")
        with self.assertRaises(ValueError):
            center_relation(zs[0], replace(zs[1], level=2))

    def test_initial_incoming_leg_is_not_fabricated(self):
        result = evolving_centers(movements([0, 10, 4, 12]), 1)
        self.assertEqual(result["centers"], [])
        self.assertEqual(result["classification"], "insufficient")

    def test_non_alternating_higher_moves_not_forced_into_trend(self):
        seq = movements(TREND)
        result = evolving_centers(seq, 1)
        a, b = result["centers"][:2]
        self.assertEqual(center_relation(replace(a, directional_z_valid=False), b), "unresolved_direction")


class MACDTests(unittest.TestCase):
    def test_constant_price_and_warmup(self):
        data = [candle(i, 2, 2) for i in range(40)]
        points = macd(data)
        self.assertTrue(all(p.histogram == 0 and p.dif == 0 and p.dea == 0 for p in points))
        self.assertFalse(points[32].warmed_up)
        self.assertTrue(points[33].warmed_up)

    def test_ema_reference_calculation(self):
        data = [candle(0, 1, 1), candle(1, 2, 2)]
        points = macd(data, fast=2, slow=3, signal=2)
        self.assertAlmostEqual(points[1].dif, 1 / 6)
        self.assertAlmostEqual(points[1].dea, 1 / 9)
        self.assertAlmostEqual(points[1].histogram, 1 / 9)

    def test_area_uses_direction_and_half_open_interval(self):
        ps = [MACDPoint(BASE + timedelta(hours=i), 1, 0, h, True) for i, h in enumerate([2, -3, 5])]
        self.assertEqual(macd_area(ps, BASE, BASE + timedelta(hours=2), "up")["area"], 2)
        self.assertEqual(macd_area(ps, BASE, BASE + timedelta(hours=2), "down")["area"], 3)

    def test_auxiliary_trend_divergence_and_weak_second_point(self):
        for sign in (1, -1):
            seq = movements([sign * x for x in TREND])
            view = evolving_centers(seq, 1)
            points = [MACDPoint(BASE + timedelta(hours=i), sign if 16 <= i < 20 else 0, 0,
                                sign * (5 if 16 <= i < 20 else 1 if 32 <= i < 36 else 0), True)
                      for i in range(49)]
            divs = divergence_candidates(seq, view, points)
            self.assertEqual(len(divs), 1)
            self.assertEqual(divs[0]["kind"], "trend")
            self.assertFalse(divs[0]["strict_divergence_confirmed"])
            self.assertEqual(divs[0]["area_ratio"], 0.2)
            signals = first_second_signals(seq, divs)
            self.assertEqual([s.label for s in signals], ["S1", "S2"] if sign == 1 else ["B1", "B2"])
            self.assertEqual(signals[1].details["strength"], "weak_new_extreme")
            self.assertTrue(all(s.certainty != "structural" for s in signals))
            self.assertEqual(divergence_candidates(seq, view, [replace(p, warmed_up=False) for p in points]), [])
            # 没有回零，不能省掉趋势背景条件后仍报背驰。
            self.assertEqual(divergence_candidates(seq, view, [replace(p, dif=10, dea=10) for p in points]), [])

    def test_consolidation_divergence_does_not_become_first_point(self):
        self.assertEqual(first_second_signals([], [{"kind": "consolidation"}]), [])

    def test_nesting_requires_adjacent_level_same_side_and_containment(self):
        parent = {"id": "P", "level": 2, "side": "down", "start": BASE,
                  "end": BASE + timedelta(hours=10), "evidence_at": BASE + timedelta(hours=12)}
        child = {**parent, "id": "C", "level": 1, "start": BASE + timedelta(hours=2),
                 "end": BASE + timedelta(hours=7)}
        self.assertEqual(len(nested_intervals([parent, child])), 1)
        for bad in ({**child, "side": "up"}, {**child, "level": 0},
                    {**child, "end": BASE + timedelta(hours=11)}):
            self.assertEqual(nested_intervals([parent, bad]), [])


if __name__ == "__main__":
    unittest.main()
