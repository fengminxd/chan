from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import random
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from backtest import ExecutionCosts, TradingPolicy, backtest
from chan import Candle, Fractal, Stroke, analyze, resample, write_chart
from chan_dynamics import analyze_dynamics, strength_waves
from chan_signals import decision_layers
from chan_theory import evolving_centers
from chan_policy import AnalysisPolicy
from chan_structure import Segment
from chan_time import PERIODS, PERIOD_MINUTES, compatible_periods, period_delta, period_minutes
from chan_types import build_type_hierarchy
from test_chan import BASE
from test_theory import TREND, movements
from unified import (HistoryPolicy, analyze_unified, build_graph, merge_sources, minute_demo,
                     prepare_minutes, project_views, replay_unified, unified_snapshot, validate_graph)


def minute_bars(n, start=BASE):
    return [Candle(start+timedelta(minutes=i), 100+i%10, 101+i%10, 99+i%10, 100+i%10, 1) for i in range(n)]


def stub_report(prices):
    seq = movements(prices)
    strokes, segments = [], []
    for m in seq:
        a, b = m.start, m.end
        delta = b.price-a.price
        inside = [Fractal(0, "top" if delta > 0 else "bottom", a.price+delta*.6, a.time+(b.time-a.time)/3),
                  Fractal(1, "bottom" if delta > 0 else "top", a.price+delta*.4, a.time+2*(b.time-a.time)/3)]
        points = [a, *inside, b]
        first = len(strokes)
        strokes.extend(Stroke(x, y, False, m.evidence_at) for x, y in zip(points, points[1:]))
        segments.append(Segment(a, b, first, first+2, True, "fixture", evidence_at=m.evidence_at,
                                range_low=m.low, range_high=m.high))
    return {"blocks": [{"start": seq[0].start.time, "end": seq[-1].end.time, "bar_count": len(seq)*12,
                        "strokes": strokes, "segments": segments, "pending_segment": None,
                        "segment_diagnostics": {}, "theory": {"type_hierarchy": build_type_hierarchy(seq)}}]}


class MinutePeriodTests(unittest.TestCase):
    def test_all_ten_periods_and_exact_integer_conversion(self):
        self.assertEqual(list(PERIODS), ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d"])
        for name, n in PERIOD_MINUTES.items():
            self.assertEqual(period_minutes(PERIODS[name]), n)
            self.assertEqual(period_delta(PERIODS[name]), timedelta(minutes=n))
        self.assertEqual(compatible_periods(PERIODS["1m"]), list(PERIODS))
        self.assertIn("2h", compatible_periods(PERIODS["5m"]))
        self.assertNotIn("12h", compatible_periods(8))
        for bad in (True, float("nan"), 1/7, 0, -1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                period_minutes(bad)

    def test_1m_to_5m_15m_30m_2h_ohlcv(self):
        raw = minute_bars(1440)
        for name in PERIODS:
            out, q = resample(raw, PERIODS["1m"], PERIODS[name], BASE+timedelta(days=1))
            n = PERIOD_MINUTES[name]
            self.assertEqual(len(out), 1440//n)
            first = raw[:n]
            self.assertEqual((out[0].open, out[0].close, out[0].volume), (first[0].open, first[-1].close, n))
            self.assertEqual((out[0].low, out[0].high), (min(c.low for c in first), max(c.high for c in first)))
            self.assertEqual(q["dropped_incomplete_buckets"], 0)

    def test_two_stage_aggregation_matches_direct(self):
        raw = minute_bars(1440)
        cutoff = BASE+timedelta(days=1)
        five, _ = resample(raw, PERIODS["1m"], PERIODS["5m"], cutoff)
        direct, _ = resample(raw, PERIODS["1m"], 2, cutoff)
        via, _ = resample(five, PERIODS["5m"], 2, cutoff)
        self.assertEqual(direct, via)

    def test_missing_minute_unclosed_and_alignment(self):
        raw = minute_bars(16)
        out, q = resample(raw[:3]+raw[4:], PERIODS["1m"], PERIODS["5m"], BASE+timedelta(minutes=14, seconds=30))
        self.assertEqual([c.time for c in out], [BASE+timedelta(minutes=5)])
        self.assertEqual(q["dropped_incomplete_buckets"], 2)
        with self.assertRaises(ValueError):
            resample([replace(raw[0], time=BASE+timedelta(seconds=1))], PERIODS["1m"], 1, BASE+timedelta(days=1))
        with self.assertRaises(ValueError):
            resample([], 2, PERIODS["30m"], BASE)

    def test_utc8_daily_anchor_from_minutes(self):
        start = BASE+timedelta(hours=16)
        raw = minute_bars(1440, start)
        daily, _ = resample(raw, PERIODS["1m"], 24, start+timedelta(days=1), 16)
        self.assertEqual(daily[0].time, start)
        self.assertEqual(daily[0].volume, 1440)

    def test_minute_strength_uses_hours_units_and_close_time(self):
        raw = minute_bars(100)
        hourly = [replace(c, time=BASE+timedelta(hours=i)) for i, c in enumerate(raw)]
        a, b = strength_waves(raw, PERIODS["1m"]), strength_waves(hourly, 1)
        self.assertEqual(len(a), len(b))
        for x, y in zip(a, b):
            self.assertAlmostEqual(x.area*60, y.area)
        self.assertTrue(all(x.evidence_at <= BASE+timedelta(minutes=100) for x in a))

    def test_minute_constant_funding_grid_and_next_minute_open(self):
        from test_research import frame
        raw = [replace(c, open=100, high=101, low=99, close=100) for c in minute_bars(10)]
        row = frame(1/60, period="1m")
        result = backtest(raw, [row], PERIODS["1m"], TradingPolicy(hold_bars=2), ExecutionCosts(0, 0, .0001))
        self.assertEqual(result["trades"][0]["entry_time"], BASE+timedelta(minutes=1))
        self.assertEqual(result["trades"][0]["exit_time"], BASE+timedelta(minutes=3))


class UnifiedGraphTests(unittest.TestCase):
    def signal_fixture(self):
        report = stub_report(TREND + [25,18,22,16,20,14])
        block = report['blocks'][0]
        hierarchy = block['theory']['type_hierarchy']
        block['theory']['strict_divergence'] = analyze_dynamics(hierarchy, [], 1)
        legacy = evolving_centers(hierarchy['base_movements'], 1, legacy=True)
        legacy['divergences'] = []
        block['theory']['levels'] = [legacy]
        return report

    def test_centers_and_signals_share_one_registry_and_only_canonical_signals_trade(self):
        report = self.signal_fixture()
        theory = report['blocks'][0]['theory']
        for layer, dynamic in zip(theory['type_hierarchy']['levels'], theory['strict_divergence']['levels']):
            self.assertIs(layer['centers'], dynamic['structural_view']['centers'])
        tree = build_graph(report, BASE + timedelta(days=30))
        self.assertTrue(tree['signals'])
        self.assertTrue(tree['audits'])
        expected = [s for l in theory['strict_divergence']['levels'] for s in l['signals']]
        self.assertEqual(len(tree['signals']), len(expected))
        snapshot = unified_snapshot(tree)
        self.assertEqual(sum(v['type'] == 'policy_signal' for v in snapshot.values()), len(expected))
        candidates = decision_layers(report['blocks'])['trade_candidates']
        old = [c for c in candidates if ':signal:' in c['id']]
        self.assertTrue(old)
        self.assertFalse(any(c['eligible_under_default_policy'] for c in old))
        self.assertTrue(any(c['eligible_under_default_policy'] for c in candidates if ':policy_signal:' in c['id']))
        from replay import object_timing, snapshot_objects
        # 旧视角即使有 evidence_at，也不能在回放生命周期中冒充已确认信号。
        report['blocks'][0]['centers'] = []
        old_signals = [x for x in snapshot_objects(report).values() if x['type'] == 'legacy_signal']
        self.assertTrue(old_signals)
        self.assertTrue(all(not object_timing(x)[2] for x in old_signals))

    def test_signal_and_audit_references_are_validated_with_the_tree(self):
        tree = build_graph(self.signal_fixture(), BASE + timedelta(days=30))
        uid = next(iter(tree['signals']))
        for field, value in [('center_id', 'missing'), ('source_node_ids', ['missing']),
                             ('price', -999), ('evidence_at', BASE + timedelta(days=99))]:
            changed = deepcopy(tree)
            changed['signals'][uid][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_graph(changed, BASE + timedelta(days=30))
        changed = deepcopy(tree)
        next(iter(changed['audits'].values()))['node_refs']['B'] = 'missing'
        with self.assertRaises(ValueError):
            validate_graph(changed, BASE + timedelta(days=30))

    def test_complete_and_pending_movements_keep_parent_child_evidence(self):
        report = stub_report([0, 12, 6, 10, 7, 14, 4, 6])
        cutoff = BASE+timedelta(days=10)
        tree = build_graph(report, cutoff)
        parents = [n for n in tree["nodes"].values() if n["kind"] == "movement" and n["state"] == "completed"]
        self.assertEqual(len(parents), 1)
        p = parents[0]
        self.assertEqual(p["completion"], "opposite_third_point")
        self.assertEqual(p["end_price"], 14)
        self.assertGreater(p["evidence_at"], p["end"])
        for c in p["child_ids"]:
            child = tree["nodes"][c]
            self.assertEqual(child["parent_id"], p["id"])
            self.assertEqual(child["level"], p["level"]-1)
            self.assertLessEqual(child["evidence_at"], p["evidence_at"])

    def test_missing_completion_cannot_promote(self):
        report = stub_report([0, 12, 6, 10, 7])
        tree = build_graph(report, BASE+timedelta(days=10))
        active = [n for n in tree["nodes"].values() if n["kind"] == "movement"]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["state"], "extending")
        self.assertFalse(active[0]["promotion_ready"])
        self.assertIsNone(active[0]["parent_id"])
        self.assertFalse(any(n["level"] > 1 for n in tree["nodes"].values()))

    def test_two_levels_have_real_completed_children(self):
        r = random.Random(0)
        prices = [100]
        for i in range(64):
            prices.append(prices[-1]+(1 if i%2 == 0 else -1)*r.randint(2, 20))
        tree = build_graph(stub_report(prices), BASE+timedelta(days=30))
        centers = [n for n in tree["nodes"].values() if n["kind"] == "center" and n["level"] == 2]
        self.assertTrue(centers)
        for center in centers:
            kids = [tree["nodes"][c] for c in center["child_ids"]]
            self.assertEqual(len(kids), 3)
            self.assertTrue(all(c["state"] == "completed" and c["level"] == 1 for c in kids))
            self.assertEqual(center["zd"], max(c["low"] for c in kids))
            self.assertEqual(center["zg"], min(c["high"] for c in kids))

    def test_future_incomplete_cross_gap_or_duplicate_child_rejected(self):
        tree = build_graph(stub_report([0, 12, 6, 10, 7, 14, 4, 6]), BASE+timedelta(days=10))
        parent = next(n for n in tree["nodes"].values() if n["kind"] == "movement")
        for field, value in (("state", "extending"), ("block_id", "other"),
                             ("evidence_at", BASE+timedelta(days=99))):
            changed = deepcopy(tree)
            changed["nodes"][parent["child_ids"][0]][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_graph(changed, BASE+timedelta(days=10))
        changed = deepcopy(tree)
        changed["nodes"][parent["id"]]["child_ids"][1] = parent["child_ids"][0]
        with self.assertRaises(ValueError):
            validate_graph(changed, BASE+timedelta(days=10))

    def test_all_views_reference_same_tree_without_analyzing_again(self):
        raw = minute_demo()
        end = raw[-1].time+timedelta(minutes=1)
        report = analyze_unified(raw, end, raw[3000].time, history_policy=HistoryPolicy(100, 2, False))
        self.assertEqual(list(report["views"]), list(PERIODS))
        ids = [{p["node_id"] for p in v["projections"]} for v in report["views"].values()]
        self.assertTrue(ids[0])
        self.assertTrue(all(s == ids[0] for s in ids))
        self.assertTrue(all(uid in report["tree"]["nodes"] for uid in ids[0]))
        # 从真实 OHLC 形式的合成输入走完整条链，不注入预先完成的笔/段/走势。
        self.assertEqual({s['label'] for s in report['tree']['signals'].values()}, {'B3', 'S3'})
        self.assertTrue(all(s['center_id'] in report['tree']['nodes']
                            for s in report['tree']['signals'].values()))
        with tempfile.TemporaryDirectory() as folder:
            chart = Path(folder) / 'independent.html'
            write_chart(chart, {'1m': report['base_analysis']}, {'1m': raw})
            self.assertIn('旧分解视角对照（不进入交易候选）', chart.read_text())
        with patch("unified.analyze", side_effect=AssertionError("projection must not reanalyze")):
            projected = project_views(raw, report["tree"], ["1m", "5m", "2h"], end, raw[3000].time, 0)
        self.assertEqual(projected["2h"]["structure_source"], "shared_1m_tree")

    def test_projection_does_not_require_complete_large_candle(self):
        raw = minute_demo()[:1000]
        report = analyze_unified(raw, raw[-1].time+timedelta(minutes=1), periods=["1m", "1d"], history_policy=HistoryPolicy(0, 1, False))
        self.assertEqual(report["views"]["1d"]["candles"], [])
        self.assertTrue(report["views"]["1d"]["projections"])
        self.assertTrue(all(p["first_bar"] is None for p in report["views"]["1d"]["projections"]))

    def test_projection_endpoint_inside_missing_bucket_is_not_mapped(self):
        raw = minute_bars(15)
        raw = raw[:7]+raw[8:]
        node = {"kind": "segment", "level": 0, "start": BASE+timedelta(minutes=6),
                "end": BASE+timedelta(minutes=9)}
        view = project_views(raw, {"nodes": {"fixture": node}}, ["5m"],
                             BASE+timedelta(minutes=15), BASE, 0)["5m"]
        self.assertEqual(len(view["candles"]), 2)
        projection = view["projections"][0]
        self.assertIsNone(projection["first_bar"])
        self.assertFalse(projection["endpoint_in_closed_view"])

    def test_three_completed_children_form_seed_without_completed_parent(self):
        prices = [100,109,89,108,102,115,98,118,114,116,99,109,90,99,91,108,89,
                  108,91,105,99,108,102,120,106,108,104,111,91,94,83,85,75]
        tree = build_graph(stub_report(prices), BASE+timedelta(days=10))
        seeds = [n for n in tree["nodes"].values() if n["kind"] == "center_seed"]
        self.assertEqual(len(seeds), 1)
        seed = seeds[0]
        self.assertEqual((seed["level"], seed["zd"], seed["zg"]), (2, 89, 118))
        self.assertEqual(seed["state"], "formed_parent_uncompleted")
        self.assertFalse(seed["promotion_ready"])
        self.assertFalse(any(n["kind"] == "movement" and n["level"] == 2 for n in tree["nodes"].values()))

    def test_formed_center_replay_confirmation_does_not_complete_parent(self):
        from replay import LifecycleTracker
        tree = build_graph(stub_report([0, 12, 6, 10, 7]), BASE+timedelta(days=10))
        events = LifecycleTracker().update(unified_snapshot(tree), BASE+timedelta(days=10))
        center = next(e for e in events if e["structure"]["value"]["kind"] == "center")
        parent = next(e for e in events if e["structure"]["value"]["kind"] == "movement")
        self.assertTrue(center["structure"]["value"]["confirmed"])
        self.assertEqual(center["lifecycle"]["status"], "confirmed_in_snapshot")
        self.assertFalse(center["structure"]["value"]["promotion_ready"])
        self.assertEqual(parent["lifecycle"]["status"], "candidate")
        self.assertFalse(parent["structure"]["value"]["promotion_ready"])


class ContextTests(unittest.TestCase):
    def test_context_merge_identical_overlap_and_conflict(self):
        raw = minute_bars(100)
        merged, quality = merge_sources(raw[40:], raw[:60])
        self.assertEqual(merged, raw)
        self.assertEqual(quality["identical_duplicates_removed"], 20)
        with self.assertRaises(ValueError):
            merge_sources(raw[40:], [*raw[:40], replace(raw[40], volume=2)])

    def test_hourly_cannot_be_silently_relabelled_minutes(self):
        raw = [replace(c, time=BASE+timedelta(hours=i)) for i, c in enumerate(minute_bars(10))]
        with self.assertRaisesRegex(ValueError, "真实 1m"):
            prepare_minutes(raw, BASE+timedelta(days=1))

    def test_input_context_same_tree_as_joined_and_future_not_used(self):
        raw = minute_bars(120)
        policy = HistoryPolicy(20, 1)
        cutoff = BASE+timedelta(minutes=100)
        a = analyze_unified(raw, cutoff, BASE+timedelta(minutes=50), history_policy=policy)
        b = analyze_unified(raw[50:], cutoff, BASE+timedelta(minutes=50), context=raw[:60], history_policy=policy)
        self.assertEqual(a["tree"], b["tree"])
        altered = raw[:100]+[replace(c, volume=999) for c in raw[100:]]
        c = analyze_unified(altered, cutoff, BASE+timedelta(minutes=50), history_policy=policy)
        self.assertEqual(a["tree"], c["tree"])
        self.assertEqual(a["views"], c["views"])

    def test_gap_splits_tree_and_history_floor_is_not_proof(self):
        raw = minute_demo()[:2000]
        raw = raw[:900]+raw[901:]
        end = raw[-1].time+timedelta(minutes=1)
        report = analyze_unified(raw, end, raw[500].time, history_policy=HistoryPolicy(100, 1, False))
        self.assertEqual(len(report["tree"]["blocks"]), 2)
        self.assertIn("target_window_has_gaps", report["history"]["reasons"])
        self.assertFalse(report["history"]["history_sufficiency_proven"])
        for n in report["tree"]["nodes"].values():
            self.assertTrue(all(report["tree"]["nodes"][c]["block_id"] == n["block_id"] for c in n["child_ids"]))

    def test_history_checks_do_not_mark_empty_target_as_sufficient(self):
        raw = [replace(c, open=100, high=100, low=100, close=100) for c in minute_bars(100)]
        report = analyze_unified(raw, BASE+timedelta(minutes=100), BASE+timedelta(minutes=60), history_policy=HistoryPolicy(20, 1))
        self.assertEqual(report["history"]["stability"]["status"], "no_target_structure")
        self.assertEqual(report["history"]["status"], "needs_more_evidence")
        self.assertIn("requested_level_has_no_completed_movement_in_target", report["history"]["reasons"])

    def test_detects_missing_start_end_and_invalid_config(self):
        raw = minute_bars(20, BASE+timedelta(minutes=10))
        report = analyze_unified(raw, BASE+timedelta(minutes=40), BASE, history_policy=HistoryPolicy(0, 1))
        self.assertIn("target_start_not_covered", report["history"]["reasons"])
        self.assertIn("target_end_not_covered", report["history"]["reasons"])
        with self.assertRaises(ValueError):
            analyze_unified(raw, BASE+timedelta(minutes=40), policy=AnalysisPolicy(max_levels=1))
        with self.assertRaises(ValueError):
            analyze_unified(raw, BASE+timedelta(minutes=40), policy=AnalysisPolicy(mode="bi"))


class UnifiedReplayAndCliTests(unittest.TestCase):
    def test_replay_prefix_invariant_and_final_tree_matches_batch(self):
        raw = minute_demo()[:1000]
        end = raw[-1].time+timedelta(minutes=1)
        rows = list(replay_unified(raw, end, step=100))
        current = {}
        for row in rows:
            self.assertEqual(row["period"], "1m")
            for e in row["events"]:
                self.assertLessEqual(e["first_observed_at"], row["as_of"])
                if e["action"] == "retracted":
                    current.pop(e["id"], None)
                else:
                    current[e["id"]] = e["structure"]
        tree = build_graph(analyze(raw, PERIODS["1m"]), end)
        self.assertEqual({k: v for k, v in current.items() if not k.startswith("base_theory:")}, unified_snapshot(tree))
        altered = raw[:500]+[replace(c, volume=999) for c in raw[500:]]
        stop = raw[499].time+timedelta(minutes=1)
        self.assertEqual(list(replay_unified(raw, stop, step=50)), list(replay_unified(altered, stop, step=50)))

    def test_cli_default_minute_path_and_history_failure_output(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            raw = minute_bars(120)
            csv = p/"minute.csv"
            csv.write_text("timestamp,open,high,low,close,volume\n"+"\n".join(
                f"{c.time.isoformat()},{c.open},{c.high},{c.low},{c.close},{c.volume}" for c in raw))
            end = (BASE+timedelta(minutes=120)).isoformat()
            args = ["--csv", str(csv), "--as-of", end, "--start", (BASE+timedelta(minutes=60)).isoformat(),
                    "--periods", "1m", "5m", "15m", "30m", "2h", "1d", "--output-dir", str(p/"out")]
            run = subprocess.run(["python3", "chan.py", "--source-period", "1m", *args], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads((p/"out/analysis.json").read_text())
            self.assertEqual(report["schema"], "chan-unified-v1")
            self.assertTrue((p/"out/chart.html").exists())
            run = subprocess.run(["python3", "unified.py", *args, "--require-history"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertTrue((p/"out/report.md").exists())

    def test_minute_research_runs_one_event_stream(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            raw = minute_bars(80)
            csv = p/"minute.csv"
            csv.write_text("timestamp,open,high,low,close,volume\n"+"\n".join(
                f"{c.time.isoformat()},{c.open},{c.high},{c.low},{c.close},{c.volume}" for c in raw))
            run = subprocess.run(["python3", "research.py", "--csv", str(csv), "--source-period", "1m",
                                  "--as-of", (BASE+timedelta(minutes=80)).isoformat(), "--periods", "1m", "5m", "2h",
                                  "--train-bars", "30", "--test-bars", "20", "--output-dir", str(p/"out")],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads((p/"out/research.json").read_text())
            self.assertEqual(report["signal_periods"], ["1m"])
            self.assertEqual(report["structure_mode"], "shared_1m_tree")
            self.assertEqual(len(list((p/"out").glob("replay_*.jsonl"))), 1)


if __name__ == "__main__":
    unittest.main()
