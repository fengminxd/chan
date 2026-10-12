import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chan import json_default
from entry_plan import EntryPolicy, analyze_entries, write_entry_outputs
from test_entry_plan import BASE, M, bar, candidate, location


class CombinedEntryTests(unittest.TestCase):
    def roots(self, block, legs, as_of, period):
        return [{**candidate(), "id": f"{period}:B2:test", "period": period, "main_period": period}]

    def test_all_three_main_periods_use_their_own_engines_and_one_hour_can_trigger_alone(self):
        bars = [bar(i, 12, 13) for i in range(240)]
        with patch("entry_plan.period_candidates", side_effect=self.roots), patch("entry_plan.locate", return_value=location()) as nested:
            first = analyze_entries(bars, BASE+240*M)
            self.assertEqual([c["period"] for c in first["candidates"]], ["4h", "2h", "1h"])
            self.assertEqual([first["by_main_period"][p]["plan_count"] for p in ("4h", "2h", "1h")], [1, 2, 1])
            self.assertEqual(nested.call_count, 1)
            high = [p for p in first["plans"] if p["main_period"] != "1h"]
            self.assertTrue(all(p["entry_model"] == "matrix" and p["state"] == "waiting_submovement" for p in high))
            low = next(p for p in first["plans"] if p["main_period"] == "1h")
            self.assertEqual((low["entry_model"], low["state"]), ("nested_1h", "armed"))

            # 与高周期尚无完成证据并存，1h仍可单独触发；跨快照序列化保留观察时刻。
            prior = json.loads(json.dumps(first, default=json_default))
            bars.append(bar(240, 12, 16, 15))
            second = analyze_entries(bars, BASE+241*M, prior)
            self.assertEqual(second["by_main_period"]["1h"]["new_trigger_count"], 1)
            self.assertEqual(second["by_main_period"]["1h"]["actionable_count"], 1)
            self.assertEqual(second["by_main_period"]["4h"]["actionable_count"], 0)
            same = analyze_entries(bars, BASE+241*M, second)
            self.assertTrue(all(not p["actionable"] and not p["new_trigger"] for p in same["plans"]))
            self.assertEqual(same["withdrawn"], [])
            with tempfile.TemporaryDirectory() as td:
                write_entry_outputs(second, td)
                page = (Path(td)/"entry_plan.md").read_text()
                for p in ("4h", "2h", "1h"):
                    self.assertIn(f"| {p} |", page)
                    self.assertIn(f"| {p} B2 |", page)
            self.assertIn("1h候选沿15m→5m→1m", page)
            self.assertIn("子级须有本周期稳定起点且实际路径缩小", page)

    def test_one_hour_retraction_preserves_higher_period_candidates(self):
        bars = [bar(i, 12, 13) for i in range(240)]
        with patch("entry_plan.period_candidates", side_effect=self.roots), patch("entry_plan.locate", return_value=location()):
            first = analyze_entries(bars, BASE+240*M)
        def remaining(block, legs, as_of, period):
            return [] if period == "1h" else self.roots(block, legs, as_of, period)
        with patch("entry_plan.period_candidates", side_effect=remaining):
            second = analyze_entries(bars, BASE+240*M, first)
        self.assertEqual(len(second["plans"]), 3)
        self.assertEqual(second["by_main_period"]["1h"]["candidate_count"], 0)
        self.assertEqual([p["main_period"] for p in second["withdrawn"]], ["1h"])
        self.assertFalse(second["withdrawn"][0]["new_trigger"])

    def test_individual_profiles_and_buffer_scope_remain_available(self):
        bars = [bar(i, 12, 13) for i in range(240)]
        with patch("entry_plan.period_candidates", side_effect=self.roots), patch("entry_plan.locate", return_value=location()):
            for profile, expected in (("combined", ["4h", "2h", "1h"]), ("matrix", ["4h", "2h"]), ("nested_1h", ["1h"])):
                r = analyze_entries(bars, BASE+240*M, entry_policy=EntryPolicy(profile=profile))
                self.assertEqual(list(r["by_main_period"]), expected)
            combined = analyze_entries(bars, BASE+240*M, entry_policy=EntryPolicy(trigger_buffer=1))
            with self.assertRaises(ValueError):
                analyze_entries(bars, BASE+240*M, combined, entry_policy=EntryPolicy(profile="matrix"))
        with self.assertRaises(ValueError):
            EntryPolicy(profile="matrix", trigger_buffer=1)


if __name__ == "__main__":
    unittest.main()
