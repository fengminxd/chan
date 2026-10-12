import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from chan import Candle, json_default
from entry_plan import (EntryPolicy, advance_plan, analyze_entries, execution_setups,
                        developing_reverse, hourly_candidates, locate, make_plan, narrow_interval,
                        observation_legs, write_entry_outputs)
from chan_theory import MACDPoint
from test_chan import BASE, strokes

M = timedelta(minutes=1)


def bar(i, low, high, close=None):
    close = (low + high) / 2 if close is None else close
    return Candle(BASE + i*M, close, high, low, close, 10)


def scope(start, end, low=10, high=30):
    return dict(start=BASE + start*M, end=BASE + end*M, low=low, high=high,
                focus_time=BASE+2*M, focus_price=10)


def leg(start, end, low=10, high=30):
    s = scope(start, end, low, high)
    return {**s, "finish": s["end"], "direction": "down",
            "native_structure": dict(kind="stroke", index=0, start_time=s["start"],
                                     start_confirmed=True, start_price=high, endpoint_time=s["focus_time"],
                                     endpoint_price=s["focus_price"], observed_at=BASE,
                                     focus_matches_endpoint=True)}


def candidate():
    return dict(id="1h:B2:test", label="B2", period="1h", side="long",
                state="candidate", interval=scope(-60, 60), higher_point_confirmed=False,
                candidate_price=10, candidate_time=BASE+2*M,
                parent_invalidation=dict(price=8, comparison="lt"), details=dict(missing="1h待证"))


def turn_bars():
    return [bar(0, 14, 16), bar(1, 12, 14), bar(2, 10, 12), bar(3, 11, 13), bar(4, 11.5, 13.5)]


def setup():
    return execution_setups(turn_bars(), scope(1, 3, 10, 14), "long", "1m", BASE+5*M, EntryPolicy())[0]


def location():
    return dict(path=[dict(period="1h", **scope(-60, 60)), dict(period="1m", **scope(1, 3, 10, 14))],
                state="setup_found", setup=setup())


class NarrowingTests(unittest.TestCase):
    def test_bucket_end_only_shrink_cannot_create_nested_structure(self):
        # #28: 四个周期同一反弹，只有11:31/11:30/11:25/11:22桶尾不同。
        for side, focus in (("short", 1385.51), ("long", 1366.43)):
            parent = {**scope(0, 40, 1366.43, 1385.51), "focus_price": focus}
            child = {**leg(0, 39, 1366.43, 1385.51),
                     "direction": "up" if side == "short" else "down", "focus_price": focus}
            child["native_structure"]["endpoint_price"] = focus
            self.assertIsNone(narrow_interval(parent, [child], side, BASE+60*M))

    def test_child_requires_native_origin_but_intermediate_endpoint_can_be_pending(self):
        parent, child = scope(-60, 60), leg(-20, 20, 10, 20)
        for bad in ({**child, "live_reverse": True},
                    {k: v for k, v in child.items() if k != "native_structure"},
                    {**child, "native_structure": {**child["native_structure"], "focus_matches_endpoint": False}},
                    {**child, "native_structure": {**child["native_structure"], "observed_at": BASE+61*M}}):
            self.assertIsNone(narrow_interval(parent, [bad], "long", BASE+60*M))
        # 中间周期只要求稳定起点；目标极值可继续发展，让更低级别确认执行。
        pending = {**child, "provisional": True,
                   "native_structure": {**child["native_structure"], "focus_matches_endpoint": False}}
        found = narrow_interval(parent, [pending], "long", BASE+60*M)
        self.assertEqual(found["native_structure"], pending["native_structure"])
        reverse = {**pending, "live_reverse": True,
                   "native_structure": {**pending["native_structure"], "kind": "developing_from_native_endpoint"}}
        self.assertIsNotNone(narrow_interval(parent, [reverse], "long", BASE+60*M))
        reverse["native_structure"]["start_confirmed"] = False
        self.assertIsNone(narrow_interval(parent, [reverse], "long", BASE+60*M))

    def test_rejected_child_does_not_reuse_an_existing_armed_plan(self):
        bad = {**leg(-45, 45, 10, 25), "live_reverse": True}
        frames = {"15m": dict(legs=[bad], bars=[])}
        old = make_plan(candidate(), location(), turn_bars(), BASE+5*M, EntryPolicy())
        found = locate(candidate(), frames, BASE+6*M, EntryPolicy(), old)
        self.assertEqual(found["state"], "waiting_descent")
        self.assertIsNone(found["setup"])

    def test_time_price_direction_and_same_extreme_all_required(self):
        parent = scope(-60, 60)
        good = leg(-20, 20, 10, 20)
        self.assertIsNotNone(narrow_interval(parent, [good], "long"))
        for bad in ({**good, "start": BASE-61*M}, {**good, "high": 31},
                    {**good, "direction": "up"}, {**good, "focus_price": 11},
                    {**good, "finish": BASE+M}):
            self.assertIsNone(narrow_interval(parent, [bad], "long"))

    def test_descends_to_one_minute_and_never_skips_missing_fifteen(self):
        frames = {"15m": dict(legs=[leg(-45, 45, 10, 25)], bars=[]),
                  "5m": dict(legs=[leg(-10, 10, 10, 20)], bars=[]),
                  "1m": dict(legs=[leg(1, 3, 10, 14)], bars=turn_bars())}
        found = locate(candidate(), frames, BASE+5*M, EntryPolicy())
        self.assertEqual([x["period"] for x in found["path"]], ["1h", "15m", "5m", "1m"])
        self.assertEqual(found["setup"]["trigger_price"], 14)
        frames["1m"]["bars"].append(bar(5, 12, 16, 15))
        missed = locate(candidate(), frames, BASE+6*M, EntryPolicy())
        self.assertEqual(missed["state"], "missed")
        self.assertEqual(missed["setup"]["trigger_price"], 14)
        frames["15m"]["legs"] = []
        missing = locate(candidate(), frames, BASE+5*M, EntryPolicy())
        self.assertEqual(missing["state"], "waiting_descent")
        self.assertIsNone(missing["setup"])

    def test_unconfirmed_intermediate_endpoints_can_still_reach_one_minute(self):
        frames = {"15m": dict(legs=[leg(-45, 45, 10, 25)], bars=[]),
                  "5m": dict(legs=[leg(-10, 10, 10, 20)], bars=[]),
                  "1m": dict(legs=[leg(1, 3, 10, 14)], bars=turn_bars())}
        for p in ("15m", "5m"):
            frames[p]["legs"][0]["provisional"] = True
            frames[p]["legs"][0]["native_structure"]["focus_matches_endpoint"] = False
        found = locate(candidate(), frames, BASE+5*M, EntryPolicy())
        self.assertEqual(found["setup"]["period"], "1m")

    def test_stop_at_first_period_with_valid_confirmed_structure(self):
        # 与1m相同形状的15m柱，证据在75分钟才可见。
        bars = [replace(c, time=BASE+15*i*M) for i, c in enumerate(turn_bars())]
        c = candidate()
        c["interval"] = {**scope(-60, 90), "focus_time": BASE+30*M}
        l = {**leg(15, 45, 10, 14), "focus_time": BASE+30*M}
        frames = {"15m": dict(legs=[l], bars=bars), "1m": dict(bars=[bar(74, 12, 13)])}
        found = locate(c, frames, BASE+75*M, EntryPolicy())
        self.assertEqual(found["setup"]["period"], "15m")
        self.assertEqual(len(found["path"]), 2)

    def test_fractal_requires_stable_right_neighbor_and_is_not_b1(self):
        args = (scope(1, 3, 10, 14), "long", "1m", BASE+5*M, EntryPolicy())
        self.assertEqual(execution_setups(turn_bars()[:-1], *args), [])
        s = execution_setups(turn_bars(), *args)[0]
        self.assertEqual(s["structure"], "stable_bottom_fractal")
        self.assertEqual(s["evidence_at"], BASE+5*M)
        self.assertEqual((s["trigger_price"], s["stop_price"]), (14, 10))


class ObservationTests(unittest.TestCase):
    def test_first_snapshot_cannot_claim_prior_breakout(self):
        plan = make_plan(candidate(), location(), turn_bars(), BASE+5*M, EntryPolicy())
        self.assertEqual(plan["state"], "armed")
        jumped = turn_bars()+[bar(5, 12, 16, 15)]
        first_late = make_plan(candidate(), location(), jumped, BASE+6*M, EntryPolicy())
        self.assertEqual(first_late["state"], "missed")
        after = make_plan(candidate(), location(), jumped, BASE+6*M, EntryPolicy(), plan)
        self.assertEqual(after["state"], "triggered")
        self.assertEqual(after["triggered_at"], BASE+6*M)
        self.assertNotIn("fill_price", after)

    def test_touch_is_not_close_break_and_same_bar_stop_has_priority(self):
        plan = make_plan(candidate(), location(), turn_bars(), BASE+5*M, EntryPolicy())
        for new, expected in ((bar(5, 12, 16, 14), "armed"),
                              (bar(5, 9, 16, 15), "invalidated")):
            self.assertEqual(advance_plan(plan, [new], BASE+6*M, EntryPolicy())["state"], expected)

    def test_gap_timeout_and_parent_failure(self):
        plan = make_plan(candidate(), location(), turn_bars(), BASE+5*M, EntryPolicy())
        self.assertEqual(advance_plan(plan, [bar(6, 12, 16, 15)], BASE+7*M, EntryPolicy())["state"], "data_gap")
        self.assertEqual(advance_plan(plan, [], BASE+66*M, EntryPolicy())["state"], "expired")
        plan = {**plan, "parent_invalidation": dict(price=12, comparison="le")}
        failed = advance_plan(plan, [bar(5, 12, 16, 15)], BASE+6*M, EntryPolicy())
        self.assertEqual(failed["reason"], "parent_candidate_failed")

    def test_short_is_symmetric(self):
        bars = [replace(c, open=30-c.open, high=30-c.low, low=30-c.high, close=30-c.close) for c in turn_bars()]
        interval = {**scope(1, 3, 16, 20), "focus_price": 20}
        s = execution_setups(bars, interval, "short", "1m", BASE+5*M, EntryPolicy())[0]
        c = {**candidate(), "side": "short", "parent_invalidation": dict(price=22, comparison="gt")}
        loc = {**location(), "setup": s}
        plan = make_plan(c, loc, bars, BASE+5*M, EntryPolicy())
        self.assertEqual((plan["trigger_price"], plan["stop_price"]), (16, 20))
        triggered = advance_plan(plan, [bar(5, 14, 18, 15)], BASE+6*M, EntryPolicy())
        self.assertEqual(triggered["state"], "triggered")


class CandidateTests(unittest.TestCase):
    def fixture(self, prices):
        seq = [replace(s, confirmed_at=s.end.time+timedelta(hours=1))
               for s in strokes(prices, provisional=True)]
        legs = [dict(index=i, start=s.start.time, end=s.end.time, finish=s.end.time+timedelta(hours=1),
                     start_price=s.start.price, price=s.end.price, low=s.low, high=s.high,
                     direction=s.direction, focus_time=s.end.time, focus_price=s.end.price)
                for i, s in enumerate(seq)]
        return dict(strokes=seq, theory=dict(macd=dict(points=[]))), legs

    def test_second_candidate_does_not_require_confirmed_hourly_first(self):
        for prices, label in (([20, 10, 16, 12], "B2"), ([10, 20, 14, 18], "S2")):
            block, legs = self.fixture(prices)
            cs = hourly_candidates(block, legs, legs[-1]["finish"])
            self.assertEqual(cs[0]["label"], label)
            self.assertFalse(cs[0]["higher_point_confirmed"])
            self.assertFalse(cs[0]["details"]["first_point_confirmed"])
        block, legs = self.fixture([20, 10, 16, 9])
        self.assertFalse(hourly_candidates(block, legs, legs[-1]["finish"]))

    def test_third_requires_first_retest_and_strictly_outside_boundary(self):
        block, legs = self.fixture([0, 12, 6, 10, 7, 15, 13])
        cs = hourly_candidates(block, legs, legs[-1]["finish"])
        self.assertIn("B3", [c["label"] for c in cs])
        for values in ([0, 12, 6, 10, 7, 15, 10], [0, 12, 6, 10, 7, 15, 9, 16, 10]):
            block, legs = self.fixture(values)
            self.assertNotIn("B3", [c["label"] for c in hourly_candidates(block, legs, legs[-1]["finish"])])

    def test_third_retries_after_failed_return_both_directions(self):
        for sign, label in ((1, "B3"), (-1, "S3")):
            block, legs = self.fixture([sign*p for p in [0,12,6,10,7,15,9,16,13]])
            self.assertIn(label, [c["label"] for c in hourly_candidates(block, legs, legs[-1]["finish"])])

    def test_third_allows_departure_in_seed_last_leg_but_never_reuses_closed_center(self):
        for sign, label in ((1, "S3"), (-1, "B3")):
            # 中枢10~18，构成中枢的第三段18→5同时离开；5→8是首次回试。
            block, legs = self.fixture([sign*p for p in [0, 20, 10, 18, 5, 8]])
            self.assertIn(label, [c["label"] for c in hourly_candidates(block, legs, legs[-1]["finish"])])
            # 已用5→8完成首次回试，后面2→4不能重用同一中枢；#28镜像回归。
            block, legs = self.fixture([sign*p for p in [0, 20, 10, 18, 5, 8, 2, 4]])
            self.assertNotIn(label, [c["label"] for c in hourly_candidates(block, legs, legs[-1]["finish"])])

    def test_native_endpoint_provenance_is_lost_when_minute_extreme_extends(self):
        seq = strokes([20, 10], provisional=True)
        end = seq[0].end.time
        raw = [Candle(end, 11, 12, 10, 11)]
        block = dict(period="1h", strokes=seq)
        before = observation_legs(block, raw, end+M)[0]
        self.assertTrue(before["native_structure"]["focus_matches_endpoint"])
        raw.append(Candle(end+M, 10, 11, 9, 10))
        after = observation_legs(block, raw, end+2*M)[0]
        self.assertEqual(after["focus_price"], 9)
        self.assertEqual(after["native_structure"]["endpoint_price"], 10)
        self.assertFalse(after["native_structure"]["focus_matches_endpoint"])

    def test_developing_hourly_pullback_needs_no_hourly_bottom_fractal(self):
        block, legs = self.fixture([20, 10, 16])
        # 只有下降、反弹两笔；最新小时尚未形成回试笔/底分型。
        t = legs[-1]["end"]
        raw = [replace(bar(i, 14-i*.2, 15-i*.2), time=t+i*M) for i in range(1, 5)]
        live = developing_reverse(legs, raw, t+5*M, "1h")
        self.assertIsNone(live["evidence_at"])
        roots = hourly_candidates(block, [*legs, live], t+5*M)
        self.assertEqual(roots[0]["label"], "B2")
        self.assertTrue(roots[0]["developing_leg"])
        self.assertFalse(roots[0]["higher_point_confirmed"])

    def test_first_candidates_require_trend_context_and_auxiliary_weakening(self):
        values = [0, 12, 6, 10, 7, 15, 13, 18, 14, 21, 19, 23]
        for sign, expected in ((1, "S1"), (-1, "B1")):
            block, legs = self.fixture([sign*p for p in values])
            block["theory"]["macd"]["points"] = [
                MACDPoint(BASE+timedelta(hours=i), sign, 0, sign*(10 if i < 40 else 1), True)
                for i in range(45)]
            roots = hourly_candidates(block, legs, legs[-1]["finish"])
            self.assertIn(expected, [c["label"] for c in roots])
            block["theory"]["macd"]["points"] = [replace(p, histogram=sign*10)
                                                       for p in block["theory"]["macd"]["points"]]
            self.assertNotIn(expected, [c["label"] for c in hourly_candidates(block, legs, legs[-1]["finish"])])


class PipelineTests(unittest.TestCase):
    def analyze_legacy(self, *args):
        return analyze_entries(*args, entry_policy=EntryPolicy(profile="nested_1h"))

    def test_future_data_is_ignored_and_snapshot_roundtrip_is_stable(self):
        bars = [bar(i, 10+i*.01, 11+i*.01) for i in range(125)]
        a = self.analyze_legacy(bars, BASE+120*M)
        b = self.analyze_legacy(bars[:120], BASE+120*M)
        self.assertEqual(a["candidates"], b["candidates"])
        self.assertEqual(a["input_digest"], b["input_digest"])
        previous = json.loads(json.dumps(a, default=json_default))
        with self.assertRaisesRegex(ValueError, "规则不一致"):
            self.analyze_legacy(bars, BASE+125*M, {**previous, "fingerprint": "entry-plan-v3"})
        c = self.analyze_legacy(bars, BASE+125*M, previous)
        self.assertTrue(c["data_quality"]["fresh"])
        with self.assertRaises(ValueError):
            self.analyze_legacy([replace(bars[0], volume=3), *bars[1:]], BASE+125*M, previous)
        with self.assertRaises(ValueError):
            self.analyze_legacy(bars, BASE+119*M, previous)
        with tempfile.TemporaryDirectory() as tmp:
            write_entry_outputs(c, tmp)
            self.assertTrue((Path(tmp)/"entry_plan.md").exists())

    def test_stale_and_gap_data_cannot_be_actionable(self):
        bars = [bar(i, 10, 11) for i in range(60)]
        stale = self.analyze_legacy(bars, BASE+65*M)
        self.assertFalse(stale["data_quality"]["fresh"])
        gap = self.analyze_legacy(bars + [bar(61, 10, 11)], BASE+62*M)
        self.assertEqual(gap["data_quality"]["dropped_before_last_gap"], 60)
        self.assertEqual(gap["plans"], [])

    def test_trigger_is_emitted_once_and_retraction_does_not_reuse_plan(self):
        # 接口层注入已验证的结构夹具，检验跨快照状态与序列化，非重算事后成交。
        bars = [bar(i, 12, 13) for i in range(60)]
        with patch("entry_plan.hourly_candidates", return_value=[candidate()]), patch("entry_plan.locate", return_value=location()):
            a = self.analyze_legacy(bars, BASE+60*M)
            self.assertEqual(a["plans"][0]["state"], "armed")
            prior = json.loads(json.dumps(a, default=json_default))
            bars.append(bar(60, 12, 16, 15))
            b = self.analyze_legacy(bars, BASE+61*M, prior)
            self.assertTrue(b["plans"][0]["actionable"])
            with tempfile.TemporaryDirectory() as tmp:
                write_entry_outputs(b, tmp)
            same = self.analyze_legacy(bars, BASE+61*M, b)
            self.assertFalse(same["plans"][0]["actionable"])
        with patch("entry_plan.hourly_candidates", return_value=[]):
            c = self.analyze_legacy(bars, BASE+61*M, b)
            self.assertEqual(c["withdrawn"][0]["state"], "withdrawn")
            self.assertFalse(c["withdrawn"][0]["actionable"])


if __name__ == "__main__":
    unittest.main()
