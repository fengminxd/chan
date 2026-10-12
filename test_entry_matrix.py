import json
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch

from chan import Fractal, json_default
from chan_structure import Movement
from chan_theory import Signal
from chan_time import period_name
from entry_matrix import (ROUTES, completed_submovement, confirmed_execution_signals,
                          locate_matrix, make_matrix_plan, rule_for)
from entry_plan import EntryPolicy, analyze_entries, write_entry_outputs
from test_entry_plan import BASE, M, bar


def move(mid, start, end, a, b, evidence, kind="consolidation", level=1):
    up = b > a
    return Movement(mid, level, kind,
                    Fractal(start, "bottom" if up else "top", a, BASE+start*M),
                    Fractal(end, "top" if up else "bottom", b, BASE+end*M),
                    min(a, b), max(a, b), (), (), BASE+evidence*M,
                    "completed", "opposite_third_point")


def fixture(main="4h", label="B1", execution="15m"):
    sign = 1 if label.startswith("B") else -1
    # 对称价格转换围绕200，保持价格为正。
    price = lambda p: p if sign == 1 else 400-p
    interval = dict(start=BASE+30*M, end=BASE+180*M,
                    low=100 if sign == 1 else 250, high=150 if sign == 1 else 300,
                    focus_time=BASE+120*M, focus_price=price(100))
    c = dict(id=f"{main}:{label}:test:exec:{execution}", main_candidate_id=f"{main}:{label}:test",
             period=main, main_period=main, label=label, side="long" if sign == 1 else "short",
             interval=interval, candidate_time=BASE+120*M, candidate_price=price(100),
             higher_point_confirmed=False, execution_period=execution,
             parent_invalidation=dict(price=price(95), comparison="le" if sign == 1 else "ge"),
             details=dict(zg=95 if sign == 1 else 310, zd=90 if sign == 1 else 305,
                          departure_start=BASE, missing="主级别仍为候选"))
    prior = move("prior", 0, 30, price(80), price(140), 180, "uptrend" if sign == 1 else "downtrend")
    sub = move("sub", 30, 120, price(140), price(100), 240, "downtrend" if sign == 1 else "uptrend")
    sub_block = dict(theory=dict(type_hierarchy=dict(levels=[dict(level=1, movements=[prior, sub])], base_movements=[])))
    falling = move("falling", 60, 120, price(130), price(100), 150)
    rebound = move("rebound", 120, 150, price(100), price(125), 180)
    retest = move("retest", 150, 165, price(125), price(110), 210)
    first = Signal("first", label[0]+"1", 2, BASE+120*M, price(100), BASE+150*M,
                   "policy_confirmed", 0, "center", ("audit",))
    second = Signal("second", label[0]+"2", 2, BASE+165*M, price(110), BASE+210*M,
                    "policy_dependent", 2, "center", ("first", "rebound", "retest"))
    audit = dict(id="audit", start=BASE+60*M, end=BASE+120*M, price=price(100),
                 evidence_at=BASE+150*M, strict_divergence_confirmed=True, c_components=["falling"])
    execution_block = dict(theory=dict(type_hierarchy=dict(base_movements=[], levels=[dict(
        level=1, movements=[falling, rebound, retest])]), strict_divergence=dict(levels=[dict(
            level=2, audits=[audit], signals=[first, second])]),
        second_retest_positions=[dict(label="B2", certainty="second_retest_geometry")]))
    raw = [bar(i, 112, 114, 113) for i in range(241)]
    raw[120] = bar(120, 100, 114, 113)
    if sign == -1:
        raw = [replace(x, open=400-x.open, high=400-x.low, low=400-x.high, close=400-x.close) for x in raw]
    frames = {rule_for(main, label)["sub_period"]: dict(block=sub_block), execution: dict(block=execution_block)}
    return c, frames, raw, sub, execution_block


class MatrixRulesTests(unittest.TestCase):
    def test_all_twelve_mirrored_routes(self):
        for main in ROUTES:
            for prefix in ("B", "S"):
                for kind in (1, 2, 3):
                    r = rule_for(main, prefix+str(kind))
                    self.assertEqual(r["sub_period"], "1h" if main == "4h" else "30m")
                    self.assertEqual(r["execution_periods"], ("15m",) if main == "4h" else ("5m", "15m"))
                    self.assertEqual(r["execution_labels"], (prefix+"2",) if kind == 2 else (prefix+"1", prefix+"2"))

    def test_completed_movement_is_required_not_a_fractal_or_segment(self):
        c, frames, raw, sub, _ = fixture()
        frame = frames["1h"]
        for replacement in (replace(sub, status="extending"), replace(sub, completion=None),
                            replace(sub, evidence_at=BASE+241*M), replace(sub, kind="consolidation"),
                            replace(sub, level=0, kind="segment")):
            frame["block"]["theory"]["type_hierarchy"]["levels"][0]["movements"] = [replacement]
            self.assertEqual(completed_submovement(c, frame, raw[:240], BASE+240*M)["state"], "waiting_submovement")
        frame["block"]["theory"]["type_hierarchy"] = dict(levels=[], base_movements=[sub])
        self.assertEqual(completed_submovement(c, frame, raw[:240], BASE+240*M)["state"], "waiting_submovement")

    def test_first_and_third_accept_first_or_second_but_second_only_second(self):
        for main in ROUTES:
            for execution in ROUTES[main]["execution_periods"]:
                for prefix in ("B", "S"):
                    for kind in (1, 2, 3):
                        c, frames, raw, _, block = fixture(main, prefix+str(kind), execution)
                        signals = block["theory"]["strict_divergence"]["levels"][0]["signals"]
                        both = locate_matrix(c, frames, raw[:240], BASE+240*M, EntryPolicy())
                        self.assertEqual(both["setup"]["execution_label"], prefix+"2")
                        signals.pop()
                        only_first = locate_matrix(c, frames, raw[:240], BASE+240*M, EntryPolicy())
                        if kind == 2:
                            self.assertEqual(only_first["state"], "waiting_execution_signal")
                        else:
                            self.assertEqual(only_first["setup"]["execution_label"], prefix+"1")

    def test_third_needs_first_return_outside_original_center(self):
        for label in ("B3", "S3"):
            c, frames, raw, sub, _ = fixture(label=label)
            good = completed_submovement(c, frames["1h"], raw[:240], BASE+240*M)
            self.assertEqual(good["state"], "completed")
            boundary_name = "zg" if label == "B3" else "zd"
            c["details"][boundary_name] = sub.end.price
            self.assertEqual(completed_submovement(c, frames["1h"], raw[:240], BASE+240*M)["state"], "invalidated")
        c, frames, raw, sub, _ = fixture(label="B3")
        moves = frames["1h"]["block"]["theory"]["type_hierarchy"]["levels"][0]["movements"]
        # 更早已有一次回试；后面的同价回试不能冒充第一次。
        moves[:0] = [move("old_up", 0, 10, 80, 130, 20), move("old_return", 10, 20, 130, 90, 30)]
        self.assertNotEqual(completed_submovement(c, frames["1h"], raw[:240], BASE+240*M)["state"], "completed")

    def test_wrong_signal_provenance_and_future_evidence_are_rejected(self):
        c, frames, raw, _, block = fixture(label="B2")
        layer = block["theory"]["strict_divergence"]["levels"][0]
        original = layer["signals"][:]
        for first in (replace(original[0], certainty="macd_candidate"), replace(original[0], evidence_at=BASE+241*M)):
            layer["signals"] = [first, original[1]]
            self.assertEqual(confirmed_execution_signals(block, BASE+240*M), [])
        layer["signals"] = [original[0], replace(original[1], source_ids=("absent", "rebound", "retest"))]
        self.assertEqual([s["signal"].label for s in confirmed_execution_signals(block, BASE+240*M)], ["B1"])
        self.assertEqual(locate_matrix(c, frames, raw[:240], BASE+240*M, EntryPolicy())["state"], "waiting_execution_signal")

    def test_execution_signal_must_belong_to_this_submovement_terminal(self):
        c, frames, raw, sub, _ = fixture()
        c["interval"]["focus_price"] = 101
        c["interval"]["low"] = 99
        m = replace(sub, end=replace(sub.end, price=101), low=99)
        frames["1h"]["block"]["theory"]["type_hierarchy"]["levels"][0]["movements"][-1] = m
        self.assertEqual(locate_matrix(c, frames, raw[:240], BASE+240*M, EntryPolicy())["state"], "waiting_execution_signal")


class MatrixLifecycleTests(unittest.TestCase):
    def test_full_report_resume_keeps_confirmation_event_but_does_not_repeat_action(self):
        c, frames, raw, _, _ = fixture(label="B2")
        root = {**c, "id": c["main_candidate_id"]}

        def analysis(bars, hours, **kwargs):
            frame = frames.get(period_name(hours), {})
            return dict(blocks=[{**frame.get("block", {}), "strokes": []}])

        def roots(block, legs, as_of, period):
            return [root] if period == "4h" else []

        with patch("entry_plan.analyze", side_effect=analysis), patch("entry_plan.period_candidates", side_effect=roots):
            r = analyze_entries(raw, BASE+240*M)
            self.assertTrue(r["plans"][0]["actionable"])
            prior = json.loads(json.dumps(r, default=json_default))
            updated = analyze_entries(raw, BASE+241*M, prior)
            self.assertFalse(updated["plans"][0]["actionable"])
            self.assertEqual(updated["plans"][0]["triggered_at"], prior["plans"][0]["triggered_at"])
            with tempfile.TemporaryDirectory() as td:
                write_entry_outputs(updated, td)

    def test_confirmation_triggers_at_observable_close_not_historical_endpoint(self):
        for label in ("B2", "S2"):
            c, frames, raw, _, _ = fixture(label=label)
            loc = locate_matrix(c, frames, raw[:240], BASE+240*M, EntryPolicy())
            p = make_matrix_plan(c, loc, raw[:240], BASE+240*M)
            self.assertEqual(p["state"], "triggered")
            self.assertEqual(p["trigger_price"], raw[239].close)
            self.assertNotEqual(p["trigger_price"], p["signal_price"])
            late = make_matrix_plan(c, loc, raw[:241], BASE+241*M)
            self.assertEqual(late["state"], "missed")
            prior = json.loads(json.dumps(p, default=json_default))
            again = make_matrix_plan(c, loc, raw[:241], BASE+241*M, prior)
            self.assertEqual(again["first_observed_at"], prior["first_observed_at"])

    def test_stop_invalidation_wins_over_confirmation(self):
        c, frames, raw, _, _ = fixture(label="B2")
        loc = locate_matrix(c, frames, raw[:240], BASE+240*M, EntryPolicy())
        raw[239] = bar(239, 109, 114, 113)
        self.assertEqual(make_matrix_plan(c, loc, raw[:240], BASE+240*M)["state"], "invalidated")

    def test_default_profile_and_no_future_candles(self):
        raw = [bar(i, 100, 101) for i in range(481)]
        r = analyze_entries(raw, BASE+480*M)
        self.assertEqual(r["policy"]["entry"]["profile"], "combined")
        self.assertEqual(list(r["by_main_period"]), ["4h", "2h", "1h"])
        self.assertEqual(set(r["periods"]), {"4h", "2h", "1h", "30m", "15m", "5m", "1m"})
        self.assertEqual(r["plans"], [])
        short = analyze_entries(raw[:480], BASE+480*M)
        self.assertEqual(short["input_digest"], r["input_digest"])
        with tempfile.TemporaryDirectory() as td:
            write_entry_outputs(r, td)


if __name__ == "__main__":
    unittest.main()
