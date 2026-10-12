from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from chan import analyze, demo_candles, resample
from replay import replay, snapshot_objects
from test_chan import BASE, candle
from verify_cases import DEFAULT_MANIFEST, verify, write_report


class CaseTests(unittest.TestCase):
    def test_manifest_cases_and_honest_missing_data(self):
        report = verify()
        self.assertEqual(report["summary"], {"passed": 11, "partial_pass": 1, "unverified": 1})
        self.assertEqual(report["historical_fully_reproduced"], 0)
        vanke = next(x for x in report["cases"] if x["id"] == "lesson27_vanke_quarterly_points")
        self.assertEqual(vanke["status"], "partial_pass")
        self.assertEqual(vanke["actual"]["core"], [3.2, 15.99])
        self.assertIn("原文背驰结论", vanke["unverified"])

    def test_expected_mismatch_fails_and_report_is_written(self):
        fixture = json.loads(DEFAULT_MANIFEST.read_text())
        fixture["cases"] = fixture["cases"][:1]
        fixture["cases"][0]["expected"]["ranges"] = [[999, 1000]]
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / "cases.json").write_text(json.dumps(fixture))
            result = verify(p / "cases.json")
            self.assertEqual(result["summary"], {"failed": 1})
            write_report(result, p / "reports")
            self.assertIn("失败", (p / "reports/case_report.html").read_text())


class ReplayTests(unittest.TestCase):
    def test_future_suffix_does_not_change_past_snapshots(self):
        raw = [candle(i, i % 7, i % 7 + 2) for i in range(70)]
        as_of = BASE + timedelta(hours=40)
        a = list(replay(raw, step=5, as_of=as_of))
        altered = raw[:40] + [replace(c, open=c.open + 1000, high=c.high + 1000,
                                      low=c.low + 1000, close=c.close + 1000) for c in raw[40:]]
        b = list(replay(altered, step=5, as_of=as_of))
        self.assertEqual(a, b)
        self.assertEqual(a[-1]["source_bar_count"], 40)
        self.assertTrue(all(row["as_of"] <= as_of for row in a))

    def test_final_sample_included_and_matches_batch(self):
        raw = demo_candles()
        # 每日输入将测试回放规模压到几百个点，仍覆盖真正的线段/中枢。
        daily, _ = resample(raw, 1, 24, raw[-1].time + timedelta(hours=1))
        rows = list(replay(daily, 24, 24, step=150, min_bars=150))
        current = {}
        for row in rows:
            for event in row["events"]:
                self.assertLessEqual(event["first_observed_at"], event["observed_at"])
                if event["action"] == "retracted":
                    current.pop(event["id"])
                else:
                    current[event["id"]] = event["structure"]
        self.assertEqual(rows[-1]["source_bar_count"], len(daily))
        self.assertEqual(current, snapshot_objects(analyze(daily, 24)))
        self.assertTrue(current)

    def test_all_five_periods_have_recursive_demo_centers(self):
        raw = demo_candles()
        for hours in (1, 4, 8, 12, 24):
            bars, _ = resample(raw, 1, hours, raw[-1].time + timedelta(hours=1))
            result = analyze(bars, hours)
            self.assertTrue(result["has_center"], hours)
            self.assertEqual([layer["level"] for layer in result["blocks"][0]["hierarchy"]["levels"]], [1, 2, 3])
            self.assertIn("theory", result["blocks"][0])

    def test_invalid_step_rejected(self):
        with self.assertRaises(ValueError):
            list(replay([], step=0))


if __name__ == "__main__":
    unittest.main()
