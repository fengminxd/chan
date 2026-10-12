import math
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from chan import (UTC, Candle, Fractal, Stroke, analyze, build_strokes,
                  demo_candles, find_centers, find_fractals, merge_inclusions,
                  parse_time, read_csv, resample, validate_candles, write_chart)

BASE = datetime(2024, 1, 1, tzinfo=UTC)


def candle(i, low, high):
    return Candle(BASE + timedelta(hours=i), low, high, low, high, 1)


def strokes(prices, provisional=False):
    first_bottom = prices[1] > prices[0]
    points = [Fractal(i * 4, "bottom" if (i % 2 == 0) == first_bottom else "top",
                      p, BASE + timedelta(hours=i * 4)) for i, p in enumerate(prices)]
    return [Stroke(a, b, provisional and i == len(points) - 2)
            for i, (a, b) in enumerate(zip(points, points[1:]))]


class DataTests(unittest.TestCase):
    def test_timezone(self):
        self.assertEqual(parse_time("2024-01-01T08:00:00+08:00"), BASE)
        self.assertEqual(parse_time("2024-01-01 00:00:00"), BASE)

    def test_ohlcv_aggregation(self):
        raw = [candle(i, i, i + 2) for i in range(8)]
        bars, quality = resample(raw, 1, 4, BASE + timedelta(hours=8))
        self.assertEqual(len(bars), 2)
        self.assertEqual((bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].volume),
                         (0, 5, 0, 5, 4))
        self.assertEqual(quality["dropped_incomplete_buckets"], 0)
        bars8, _ = resample(raw, 1, 8, BASE + timedelta(hours=8))
        self.assertEqual((len(bars8), bars8[0].close), (1, 9))

    def test_unclosed_and_missing(self):
        raw = [candle(i, 1, 2) for i in range(12) if i != 5]
        bars, quality = resample(raw, 1, 4, BASE + timedelta(hours=11, minutes=30))
        self.assertEqual(len(bars), 1)
        self.assertEqual(quality["dropped_incomplete_buckets"], 2)
        self.assertEqual(quality["excluded_unclosed_source_bars"], 1)

    def test_missing_first_bar_not_accepted(self):
        bars, _ = resample([candle(i, 1, 2) for i in range(1, 8)], 1, 4, BASE + timedelta(hours=8))
        self.assertEqual([c.time for c in bars], [BASE + timedelta(hours=4)])

    def test_custom_anchor(self):
        bars, _ = resample([candle(i, 1, 2) for i in range(1, 9)], 1, 8,
                           BASE + timedelta(hours=9), anchor_hour=1)
        self.assertEqual(bars[0].time, BASE + timedelta(hours=1))

    def test_no_downsampling_or_misaligned_input(self):
        with self.assertRaises(ValueError):
            resample([], 4, 1, BASE)
        with self.assertRaises(ValueError):
            resample([candle(1, 1, 2)], 4, 8, BASE + timedelta(days=1))

    def test_bad_data(self):
        for data in ([candle(0, 1, 2)] * 2,
                     [candle(1, 1, 2), candle(0, 1, 2)],
                     [Candle(BASE, 1, 2, 0, math.nan)],
                     [Candle(BASE, 3, 2, 0, 1)],
                     [Candle(BASE, 1, 2, 0, 1, -1)]):
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_candles(data)

    def test_csv_optional_volume_and_error(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "test.csv"
            p.write_text("timestamp,open,high,low,close\n2024-01-01T00:00:00Z,1,2,0,1\n")
            self.assertEqual(read_csv(p)[0].volume, 0)
            p.write_text("timestamp,open,high,low,close\nbad,1,2,0,1\n")
            with self.assertRaisesRegex(ValueError, "第 2 行"):
                read_csv(p)


class StructureTests(unittest.TestCase):
    def test_up_inclusion_and_extreme_time(self):
        merged = merge_inclusions([candle(0, 0, 2), candle(1, 1, 4), candle(2, 2, 3)])
        self.assertEqual(len(merged), 2)
        self.assertEqual((merged[-1].low, merged[-1].high), (2, 4))
        self.assertEqual(merged[-1].high_time, BASE + timedelta(hours=1))
        self.assertEqual(merged[-1].low_time, BASE + timedelta(hours=2))

    def test_down_inclusion(self):
        merged = merge_inclusions([candle(0, 3, 6), candle(1, 1, 4), candle(2, 2, 3)])
        self.assertEqual((merged[-1].low, merged[-1].high), (1, 3))

    def test_inclusion_uses_merged_not_original_predecessor(self):
        merged = merge_inclusions([candle(0, 0, 1), candle(1, 1, 5), candle(2, 2, 4), candle(3, 1.5, 4.5)])
        self.assertEqual(len(merged), 3)
        self.assertEqual((merged[1].low, merged[1].high), (2, 5))
        self.assertEqual((merged[2].low, merged[2].high), (1.5, 4.5))

    def test_top_bottom_fractals(self):
        merged = merge_inclusions([candle(i, x, x + 1) for i, x in enumerate([0, 2, 0, -2, 0])])
        f = find_fractals(merged)
        self.assertEqual([(x.index, x.kind) for x in f], [(1, "top"), (3, "bottom")])
        self.assertEqual(build_strokes(f, merged), [])  # 间距不足

    def test_flat_or_monotonic_no_center(self):
        for raw in ([candle(i, 1, 2) for i in range(50)],
                    [candle(i, i, i + 1) for i in range(50)]):
            result = analyze(raw, 1)
            self.assertFalse(result["has_center"])

    def test_skipped_extreme_does_not_permanently_freeze_stroke_tail(self):
        prices = [0,-2,-1,1,-1,2,1,-1,2,5,4,1,2,0,-3,-1,2,5,4,5,6,8,10,12,
                  11,13,10,12,14,11,10,8,7,8,7]
        for sign in (1, -1):
            bars = merge_inclusions([candle(i, sign*p, sign*p+.25) for i,p in enumerate(prices)])
            result = build_strokes(find_fractals(bars[:-1]), bars)
            self.assertEqual(result[-1].end.index, 32)
            self.assertEqual(result[0].start.index, 1)  # 不能靠删到第 28 根伪装成追上行情。
            for s in result:
                span = bars[s.start.index:s.end.index+1]
                self.assertEqual(s.low, min(b.low for b in span))
                self.assertEqual(s.high, max(b.high for b in span))
                self.assertGreaterEqual(s.end.index-s.start.index, 4)

    def test_known_center_from_candles(self):
        # 各转折间隔 6 根，前三笔范围依次 [0,12]、[4,12]、[4,10]。
        prices = [0, 12, 4, 10, 2, 13, 3, 11]
        raw = [candle(0, 1, 1.4)]
        for k, p in enumerate(prices[:-1]):
            for j in range(6):
                mid = p + (prices[k + 1] - p) * j / 6
                raw.append(candle(len(raw), mid, mid + 0.4))
        raw.extend([candle(len(raw), 11, 11.4)])
        raw.extend([candle(len(raw), 9, 9.4), candle(len(raw) + 1, 7, 7.4),
                    candle(len(raw) + 2, 5, 5.4)])
        result = analyze(raw, 1, mode="bi")
        z = result["blocks"][0]["centers"][0]
        self.assertAlmostEqual(z.zd, 4)
        self.assertAlmostEqual(z.zg, 10.4)
        self.assertTrue(result["blocks"][0]["strokes"][-1].provisional)

    def test_last_merged_bar_not_used_to_confirm_fractal(self):
        raw = [candle(i, p, p + 1) for i, p in enumerate([0, 2, 0])]
        self.assertEqual(analyze(raw, 1)["blocks"][0]["fractals"], [])

    def test_gaps_are_separate_blocks(self):
        raw = [candle(i, 1, 2) for i in [0, 1, 5, 6]]
        result = analyze(raw, 1)
        self.assertEqual(result["gap_count"], 1)
        self.assertEqual([b["bar_count"] for b in result["blocks"]], [2, 2])


class CenterTests(unittest.TestCase):
    def test_three_stroke_overlap(self):
        z = find_centers(strokes([0, 10, 4, 12]))[0]
        self.assertEqual((z.zd, z.zg, z.dd, z.gg, z.stroke_count), (4, 10, 0, 12, 3))

    def test_no_overlap_or_only_touch(self):
        self.assertEqual(find_centers(strokes([0, 5, 4, 8, 7])[:2]), [])
        self.assertEqual(find_centers(strokes([0, 5, 5, 8])), [])

    def test_core_does_not_shrink_on_extension(self):
        z = find_centers(strokes([0, 10, 4, 12, 6, 15]))[0]
        self.assertEqual((z.zd, z.zg, z.dd, z.gg, z.stroke_count), (4, 10, 0, 15, 5))
        self.assertEqual(z.status, "extending")

    def test_leave_requires_non_returning_reverse_stroke(self):
        seq = strokes([0, 10, 4, 12, 6, 15, 11])
        self.assertEqual(find_centers(seq[:-1])[0].status, "extending")
        z = find_centers(seq)[0]
        self.assertEqual((z.status, z.break_stroke, z.last_stroke), ("left_up", 5, 4))

    def test_leave_down(self):
        z = find_centers(strokes([12, 2, 8, 0, 6, -2, 1]))[0]
        self.assertEqual((z.zd, z.zg, z.status), (2, 8, "left_down"))

    def test_touch_boundary_returns_to_center(self):
        self.assertEqual(find_centers(strokes([0, 10, 4, 12, 10]))[0].status, "extending")

    def test_provisional_stroke_excluded(self):
        self.assertEqual(find_centers(strokes([0, 10, 4, 12], provisional=True)), [])
        seq = strokes([0, 10, 4, 12, 6, 15, 11], provisional=True)
        self.assertEqual(find_centers(seq)[0].status, "extending")

    def test_second_center_after_break(self):
        zs = find_centers(strokes([0, 10, 4, 12, 6, 15, 11, 17, 12]))
        self.assertEqual(len(zs), 2)
        self.assertEqual((zs[1].zd, zs[1].zg), (11, 15))
        self.assertGreater(zs[1].zd, zs[0].zg)

    def test_disconnected_strokes_rejected(self):
        seq = strokes([0, 10, 4, 12, 6])
        with self.assertRaises(ValueError):
            find_centers([seq[0], seq[2]])


class EndToEndTests(unittest.TestCase):
    def test_demo_all_periods_and_chart(self):
        raw = demo_candles()
        results, series = {}, {}
        for period in (1, 4, 8):
            bars, _ = resample(raw, 1, period, BASE + timedelta(hours=len(raw)))
            result = analyze(bars, period)
            self.assertTrue(result["has_center"], f"{period}h 应产生中枢")
            self.assertEqual(result["bar_count"], len(raw) // period)
            results[f"{period}h"], series[f"{period}h"] = result, bars
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chart.html"
            write_chart(path, results, series)
            content = path.read_text()
            self.assertEqual(content.count("<svg "), 3)
            self.assertIn("ZD", content)

    def test_empty_input_result(self):
        result = analyze([], 1)
        self.assertEqual(result["bar_count"], 0)
        self.assertIsNone(result["last_close_position"])


if __name__ == "__main__":
    unittest.main()
