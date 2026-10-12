from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from backtest import ExecutionCosts, Funding, TradingPolicy, backtest, read_funding
from chan import Candle, analyze, json_default
from chan_policy import AnalysisPolicy, candle_digest, load_policy
from chan_signals import decision_layers, volume_price
from chan_theory import Signal
from compare_rules import compare_snapshots, comparison_snapshot
from replay import LifecycleTracker, replay
from research import assess_overfitting, probability_of_overfitting, walk_forward
from replay_viewer import write_viewer
from test_chan import BASE


def bars(prices):
    return [Candle(BASE+timedelta(hours=i), x, x+1, x-1, x, 100) for i, x in enumerate(prices)]


def frame(hour, label="B3", key="entry", action="added", certainty="structural", period="1h", evidence=None):
    time = BASE+timedelta(hours=hour)
    item = {"type": "signal", "value": {"label": label, "certainty": certainty, "level": 1,
                                         "time": BASE, "price": 100, "evidence_at": evidence or time}}
    ev = {"action": action, "id": key, "observed_at": time, "first_observed_at": time}
    ev["previous_structure" if action == "retracted" else "structure"] = item
    return {"as_of": time, "period": period, "events": [ev], "sampling_step_source_bars": 1,
            "volume_price": {"status": "measured", "tags": [], "available_at": time}}


class PolicyComparisonTests(unittest.TestCase):
    def test_manifest_and_input_hash_reproducible(self):
        a = AnalysisPolicy()
        self.assertEqual(a.manifest(), AnalysisPolicy().manifest())
        self.assertNotEqual(a.manifest()["fingerprint"], AnalysisPolicy(min_span=5).manifest()["fingerprint"])
        raw = bars([100, 101])
        self.assertNotEqual(candle_digest(raw), candle_digest([*raw[:1], replace(raw[1], volume=101)]))
        report = analyze(raw, 1)
        self.assertEqual(report["rule_manifest"], a.manifest())
        self.assertEqual(report["decision_layers"]["orders"], [])

    def test_invalid_policy_even_with_empty_data(self):
        for kw in ({"min_span": 4.5}, {"initial_direction": "bad"}, {"strength_fast": True}, {"zero_axis_ratio": float("nan")}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                AnalysisPolicy(**kw)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"policy.json"
            path.write_text('{"unknown": 1}')
            with self.assertRaises(ValueError):
                load_policy(path)

    def test_first_difference_and_input_rejection(self):
        raw = bars([100+i%10 for i in range(100)])
        left = comparison_snapshot(raw, 1, BASE+timedelta(hours=100), 0, AnalysisPolicy())
        right = deepcopy(left)
        self.assertIsNone(compare_snapshots(left, right)["first_differing_stage"])
        right["structures"]["fractals"][0]["price"] += 1
        result = compare_snapshots(left, right)
        self.assertEqual(result["first_differing_stage"], "fractals")
        self.assertFalse(result["independent_engines"])
        right["input"]["ohlcv_sha256"] = "different"
        with self.assertRaises(ValueError):
            compare_snapshots(left, right)

    def test_missing_external_stage_not_equal(self):
        doc = comparison_snapshot(bars([100, 101]), 1, BASE+timedelta(hours=2), 0, AnalysisPolicy())
        external = deepcopy(doc)
        external["engine"] = "external-test"
        external["structures"]["segments"] = None
        result = compare_snapshots(doc, external)
        self.assertEqual(result["stages"][2]["status"], "not_comparable")

    def test_same_shape_different_confirmation_is_reported(self):
        doc = comparison_snapshot(bars([100+i%10 for i in range(100)]), 1,
                                  BASE+timedelta(hours=100), 0, AnalysisPolicy())
        other = deepcopy(doc)
        other["confirmation_timing"]["strokes"][0]["evidence_at"] = BASE.isoformat()
        result = compare_snapshots(doc, other)
        self.assertIsNone(result["first_differing_stage"])
        self.assertEqual(result["confirmation_timing"][0]["status"], "different")


class LifecycleAndSignalsTests(unittest.TestCase):
    def test_confirmation_revision_retraction_reappearance(self):
        tracker = LifecycleTracker()
        t = BASE+timedelta(hours=3)
        pending = {"type": "stroke", "value": {"end": {"time": BASE}, "provisional": True, "confirmed_at": None}}
        confirmed = {"type": "stroke", "value": {"end": {"time": BASE}, "provisional": False, "confirmed_at": t}}
        first = tracker.update({"x": pending}, BASE)[0]
        self.assertIsNone(first["lifecycle"]["first_confirmed_observed_at"])
        second = tracker.update({"x": confirmed}, t)[0]
        self.assertEqual(second["lifecycle"]["first_confirmed_observed_at"], t)
        self.assertEqual(second["lifecycle"]["revision"], 2)
        gone = tracker.update({}, t+timedelta(hours=1))[0]
        self.assertEqual(gone["lifecycle"]["status"], "retracted")
        again = tracker.update({"x": confirmed}, t+timedelta(hours=2))[0]
        self.assertTrue(again["lifecycle"]["reappeared"])
        self.assertEqual(again["first_observed_at"], BASE)
        self.assertEqual(again["lifecycle"]["current_revision_confirmed_at"], t+timedelta(hours=2))

    def test_future_evidence_rejected(self):
        item = {"type": "segment", "value": {"confirmed": True, "end": {"time": BASE},
                                              "evidence_at": BASE+timedelta(hours=1)}}
        with self.assertRaises(ValueError):
            LifecycleTracker().update({"x": item}, BASE)

    def test_macd_candidate_does_not_become_trade(self):
        signal = Signal("x", "B1", 1, BASE, 100, BASE, "macd_candidate", 0, "z", ())
        block = {"start": BASE, "theory": {"levels": [{"signals": [signal], "divergences": []}]}}
        output = decision_layers([block])
        self.assertEqual(output["trade_candidates"][0]["state"], "blocked")
        self.assertFalse(output["trade_candidates"][0]["eligible_under_default_policy"])

    def test_volume_price_warmup_missing_and_effort(self):
        raw = bars([100+i%2 for i in range(22)])
        raw[-1] = Candle(raw[-1].time, 100, 101, 99, 100.1, 300)
        result = volume_price(raw)
        self.assertEqual(result["tags"], ["high_effort_low_result"])
        self.assertEqual(result["available_at"], BASE+timedelta(hours=22))
        self.assertEqual(volume_price(raw[:10])["status"], "insufficient_history")
        self.assertEqual(volume_price([replace(c, volume=0) for c in raw])["status"], "volume_unavailable")
        self.assertEqual(volume_price(raw[:3]+raw[4:]+[replace(raw[-1], time=BASE+timedelta(hours=22))])["status"], "data_gap")

    def test_replay_policy_and_future_suffix(self):
        raw = bars([100+i%10 for i in range(100)])
        policy = AnalysisPolicy(min_span=5)
        cutoff = BASE+timedelta(hours=60)
        a = list(replay(raw, target_hours=4, step=1, as_of=cutoff, policy=policy))
        b = list(replay(raw[:60]+[replace(c, volume=500) for c in raw[60:]], target_hours=4, as_of=cutoff, step=1, policy=policy))
        self.assertEqual(a, b)
        self.assertTrue(all(x["rule_fingerprint"] == policy.manifest()["fingerprint"] for x in a))


class ExecutionTests(unittest.TestCase):
    def test_entry_observation_not_endpoint_and_long_funding(self):
        raw = bars([90, 100, 110, 120, 120])
        fees = [Funding(BASE+timedelta(hours=2), .01, 110)]
        result = backtest(raw, [frame(1)], policy=TradingPolicy(hold_bars=2),
                          costs=ExecutionCosts(0, 0), funding=fees)
        trade = result["trades"][0]
        self.assertEqual(trade["entry_time"], BASE+timedelta(hours=1))
        self.assertEqual(trade["entry_price"], 100)
        self.assertAlmostEqual(trade["net_pnl"], 189)
        self.assertAlmostEqual(result["metrics"]["final_equity"], 10189)
        self.assertEqual(result["metrics"]["funding_pnl"], -11)

    def test_short_receives_positive_funding(self):
        result = backtest(bars([100, 100, 90, 80]), [frame(1, "S3")], policy=TradingPolicy(hold_bars=2),
                          costs=ExecutionCosts(0, 0), funding=[Funding(BASE+timedelta(hours=2), .01, 90)])
        self.assertAlmostEqual(result["metrics"]["final_equity"], 10209)
        self.assertEqual(result["metrics"]["funding_pnl"], 9)

    def test_fees_and_adverse_slippage_both_sides(self):
        result = backtest(bars([100]*4), [frame(1)], policy=TradingPolicy(hold_bars=1), costs=ExecutionCosts(10, 10, 0))
        t = result["trades"][0]
        self.assertAlmostEqual(t["entry_price"], 100.1)
        self.assertAlmostEqual(t["exit_price"], 99.9)
        expected = (1000/100.1)*(-.2)-1-(1000/100.1)*99.9*.001
        self.assertAlmostEqual(t["net_pnl"], expected)
        self.assertAlmostEqual(result["metrics"]["final_equity"], 10000+expected)

    def test_retraction_closes_at_observation_not_past(self):
        result = backtest(bars([100, 100, 110, 200]), [frame(1), frame(2, action="retracted")], costs=ExecutionCosts(0, 0, 0))
        t = result["trades"][0]
        self.assertEqual(t["exit_reason"], "signal_retracted")
        self.assertEqual(t["exit_price"], 110)
        self.assertEqual(t["exit_time"], BASE+timedelta(hours=2))

    def test_updated_signal_not_reentered_and_unconfirmed_filtered(self):
        rows = [frame(1), frame(2, action="updated"), frame(3, key="aux", label="B1", certainty="macd_candidate")]
        result = backtest(bars([100]*6), rows, policy=TradingPolicy(hold_bars=1), costs=ExecutionCosts(0, 0, 0))
        self.assertEqual(result["metrics"]["trade_count"], 1)
        self.assertEqual(result["skipped"]["unconfirmed_or_filtered_signal"], 1)

    def test_signal_on_final_close_cannot_fill(self):
        result = backtest(bars([100]*4), [frame(4)], costs=ExecutionCosts(0, 0, 0))
        self.assertEqual(result["trades"], [])

    def test_conflict_and_higher_timeframe_visibility(self):
        rows = [frame(1), frame(1, "S3", key="other", period="4h")]
        result = backtest(bars([100]*6), rows)
        self.assertEqual(result["trades"], [])
        self.assertEqual(result["skipped"]["simultaneous_direction_conflict"], 1)
        policy = TradingPolicy(periods=("1h",), confirmation_period="4h")
        result = backtest(bars([100]*6), [frame(1), frame(2, period="4h")], policy=policy)
        self.assertEqual(result["trades"], [])
        result = backtest(bars([100]*6), [frame(1), frame(1, period="4h")], policy=policy)
        self.assertEqual(result["metrics"]["trade_count"], 1)

    def test_volume_tag_is_optional_separate_filter(self):
        policy = TradingPolicy(require_volume_tag="high_effort_low_result")
        result = backtest(bars([100]*4), [frame(1)], policy=policy)
        self.assertFalse(result["trades"])
        row = frame(1)
        row["volume_price"]["tags"] = ["high_effort_low_result"]
        self.assertEqual(backtest(bars([100]*4), [row], policy=policy)["metrics"]["trade_count"], 1)

    def test_reject_sampled_or_future_or_gapped_inputs(self):
        row = frame(1)
        row["sampling_step_source_bars"] = 4
        for data, rows in ((bars([100]*5), [row]), (bars([100]*5), [frame(1, evidence=BASE+timedelta(hours=2))]),
                           (bars([100]*5)[::2], [])):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                backtest(data, rows)

    def test_unknown_funding_not_misreported_as_full_cost(self):
        result = backtest(bars([100]*4), [frame(1)])
        self.assertEqual(result["cost_status"], "partial_funding_missing")
        self.assertEqual(result["funding_mode"], "missing")

    def test_funding_csv_validation_and_intrabar_settlement(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"funding.csv"
            path.write_text("timestamp,rate,mark_price\n2024-01-01T06:00:00Z,0.01,100\n")
            funding = read_funding(path)
            result = backtest(bars([100]*12)[::4], [frame(4)], source_hours=4,
                              policy=TradingPolicy(hold_bars=1), costs=ExecutionCosts(0, 0), funding=funding)
            self.assertEqual(result["metrics"]["funding_pnl"], -10)
            self.assertEqual(result["metrics"]["final_equity"], 9990)
            path.write_text("timestamp,rate,mark_price\n2024-01-01T06:00:00Z,NaN,100\n")
            with self.assertRaises(ValueError):
                read_funding(path)

    def test_future_volume_and_incompatible_funding_grid_rejected(self):
        row = frame(1)
        row["volume_price"]["available_at"] = BASE+timedelta(hours=2)
        with self.assertRaises(ValueError):
            backtest(bars([100]*4), [row])
        with self.assertRaises(ValueError):
            backtest(bars([100]*24)[::12], [], source_hours=12, costs=ExecutionCosts(funding_rate=.0001))

    def test_funding_boundary_precedes_open_and_terminal_costs(self):
        funds = [Funding(BASE+timedelta(hours=i), .01, 100) for i in (1, 2, 4)]
        result = backtest(bars([100]*4), [frame(1)], costs=ExecutionCosts(0, 0), funding=funds)
        self.assertEqual(result["metrics"]["funding_pnl"], -20)
        self.assertEqual(result["metrics"]["final_equity"], 9980)
        self.assertEqual(result["equity_curve"][-1]["equity"], 9980)

    def test_prefix_execution_invariant_and_no_pre_window_entry(self):
        raw = bars([100]*10)
        stop = BASE+timedelta(hours=5)
        rows = [frame(1), frame(7, "S3", key="later")]
        a = backtest(raw, rows, end=stop)
        altered = raw[:5]+[replace(c, open=200, high=201, low=199, close=200) for c in raw[5:]]
        self.assertEqual(a, backtest(altered, rows, end=stop))
        self.assertEqual(backtest(raw, rows, start=BASE+timedelta(hours=2), end=stop)["trades"], [])


class ValidationTests(unittest.TestCase):
    def test_html_embedded_payload_cannot_close_script(self):
        row = frame(1, key="</script><script>alert(1)</script>")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"viewer.html"
            write_viewer(path, [row])
            content = path.read_text()
            self.assertNotIn("</script><script>alert(1)", content)
            self.assertIn("\\u003c/script>", content)

    def test_walk_forward_insufficient_trades_is_cash(self):
        result = walk_forward(bars([100]*30), [], [TradingPolicy()], train_bars=10, test_bars=5, min_train_trades=1)
        self.assertEqual(result["tested_folds"], 0)
        self.assertEqual(result["status"], "insufficient_training_trades")
        self.assertIsNone(result["oos_compounded_return"])
        self.assertTrue(all(x["status"] == "cash_insufficient_training_trades" for x in result["folds"]))

    def test_oos_changes_do_not_select_training_winner(self):
        raw = bars([100, 100, 101, 102, 103, 105, 104, 103, 102, 101, 100, 99, 98, 97, 96, 95])
        rows = [frame(i, key=str(i)) for i in (1, 4, 10, 12)]
        candidates = [TradingPolicy(hold_bars=x) for x in (1, 3)]
        kwargs = dict(train_bars=8, test_bars=4, embargo_bars=1, min_train_trades=1, costs=ExecutionCosts(0, 0, 0))
        a = walk_forward(raw, rows, candidates, **kwargs)
        changed = raw[:9]+[replace(c, open=500, high=501, low=499, close=500) for c in raw[9:]]
        b = walk_forward(changed, rows, candidates, **kwargs)
        self.assertEqual(a["folds"][0]["selected_candidate"], b["folds"][0]["selected_candidate"])
        self.assertEqual(a["folds"][0]["training_scores"], b["folds"][0]["training_scores"])
        self.assertLess(a["folds"][0]["train_end"], a["folds"][0]["test_start"])

    def test_cscv_hand_computed_ranks_and_ties(self):
        self.assertEqual(probability_of_overfitting([[1, 0]]*8, 4)["pbo"], 0)
        reverse = probability_of_overfitting([[1, 0]]*4+[[0, 1]]*4, 4)
        self.assertEqual(reverse["pbo"], 1)
        self.assertEqual(reverse["skipped_tied_splits"], 4)
        self.assertEqual(probability_of_overfitting([[0, 0]]*8, 4)["status"], "uninformative_ties")
        self.assertIsNone(probability_of_overfitting([[1]]*8, 4)["pbo"])

    def test_cash_candidates_and_two_trades_do_not_claim_good_pbo(self):
        runs = [{"metrics": {"trade_count": n}} for n in (2, 0, 0)]
        result = assess_overfitting(runs)
        self.assertEqual(result["status"], "insufficient_trading_evidence")
        self.assertIsNone(result["pbo"])
        with self.assertRaises(ValueError):
            assess_overfitting(runs, partitions=5)

    def test_cli_writes_reviewable_artifacts(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            csv = path/"bars.csv"
            csv.write_text("timestamp,open,high,low,close,volume\n"+"\n".join(
                f"{c.time.isoformat()},{c.open},{c.high},{c.low},{c.close},{c.volume}" for c in bars([100+i%10 for i in range(80)])))
            run = subprocess.run(["python3", "research.py", "--csv", str(csv), "--periods", "1h", "4h", "12h", "1d",
                                  "--as-of", (BASE+timedelta(hours=80)).isoformat(), "--funding-rate", "0",
                                  "--train-bars", "30", "--test-bars", "20", "--output-dir", str(path/"out")],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            doc = json.loads((path/"out/research.json").read_text())
            self.assertEqual(doc["overfitting"]["status"], "insufficient_trading_evidence")
            self.assertTrue((path/"out/report.md").exists())
            self.assertTrue((path/"out/replay_12h.jsonl").exists())
            self.assertIn("backtest.py", doc["source_sha256"])


if __name__ == "__main__":
    unittest.main()
