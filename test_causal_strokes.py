"""Connected geometry, honest publication times, revisions and indicator continuity."""
from datetime import timedelta
from pathlib import Path
import random
import unittest
from unittest.mock import patch

from chan import Candle, analyze, build_strokes, merge_inclusions, find_fractals, read_csv, parse_time
from chan_strokes import connected_stroke_runs, causal_stroke_runs
from chan_structure import build_segments
from chan_theory import macd
from test_chan import BASE


def paths(raw, hours=1):
    bars = merge_inclusions(raw)
    return connected_stroke_runs(find_fractals(bars[:-1]), bars, 4, timedelta(hours=hours)), bars


def completed(runs):
    return [s for r in runs for s in r['strokes'] if not s.provisional]


def short_reversal_fixture(second_peak):
    points = [(0,102),(1,100),(7,110),(13,102),(19,108),(25,101),
              (31,107),(33,100),(35,second_peak),(41,95),(47,105),(53,97),(55,101)]
    prices = []
    for (a,p),(b,q) in zip(points, points[1:]):
        prices.extend(p+(q-p)*i/(b-a) for i in range(b-a))
    prices.append(points[-1][1])
    return [Candle(BASE+timedelta(hours=i),p,p,p,p,1) for i,p in enumerate(prices)]


class ConnectedStrokeTests(unittest.TestCase):
    def assert_chain(self, runs, bars, asof):
        self.assertEqual(len(runs), 1)
        seq = runs[0]['strokes']
        for i, s in enumerate(seq):
            self.assertEqual(s.provisional, i == len(seq)-1)
            self.assertGreaterEqual(s.end.index-s.start.index, 4)
            span = bars[s.start.index:s.end.index+1]
            self.assertEqual(s.low, min(b.low for b in span))
            self.assertEqual(s.high, max(b.high for b in span))
            if i:
                self.assertEqual(seq[i-1].end, s.start)
                self.assertNotEqual(seq[i-1].direction, s.direction)
            if not s.provisional:
                self.assertLessEqual(s.confirmed_at, asof)
        structure = build_segments(seq)
        chain = structure.segments + ([structure.pending] if structure.pending else [])
        for a,b in zip(chain, chain[1:]):
            self.assertEqual(a.end, b.start)
            self.assertEqual(a.last_stroke+1, b.first_stroke)

    def test_every_raw_prefix_is_connected_and_every_withdrawal_is_recorded(self):
        for seed in range(20):
            rng = random.Random(seed)
            price, raw = 100, []
            for i in range(140):
                price += rng.randint(-6,6)
                raw.append(Candle(BASE+timedelta(hours=i),price,price+rng.randint(0,3),
                                  price-rng.randint(0,3),price,1))
            prior, previous_events = [], []
            for stop in range(5,len(raw)+1):
                runs,bars = paths(raw[:stop])
                self.assert_chain(runs, bars, raw[stop-1].time+timedelta(hours=1))
                current = completed(runs)
                events = runs[0]['stroke_diagnostics']['revisions']
                self.assertEqual(events[:len(previous_events)], previous_events)
                removed = [s for e in events[len(previous_events):] for s in e['removed_completed']]
                self.assertTrue(all(s in removed for s in prior if s not in current))
                for s in current:
                    if s not in prior and stop > 5:
                        self.assertEqual(s.confirmed_at, raw[stop-1].time+timedelta(hours=1))
                self.assertTrue(all(e['observed_at'] <= raw[stop-1].time+timedelta(hours=1) for e in events))
                # Independent legacy DAG is a geometry cross-check, not the
                # continuity/legality oracle above; evidence follows publication.
                expected = build_strokes(find_fractals(bars[:-1]), bars)
                self.assertEqual([(s.start,s.end) for s in runs[0]['strokes']],
                                 [(s.start,s.end) for s in expected])
                prior,previous_events = current,events

    def test_price_mirror_and_minute_period(self):
        rng = random.Random(88)
        price,raw = 100,[]
        for i in range(120):
            price += rng.randint(-4,4)
            raw.append(Candle(BASE+timedelta(minutes=15*i),price,price+1,price-1,price,1))
        reflected = [Candle(c.time,200-c.open,200-c.low,200-c.high,200-c.close,1) for c in raw]
        a,bars = paths(raw,.25)
        b,_ = paths(reflected,.25)
        signature = lambda rr: [(s.start.index,s.end.index,s.provisional,s.confirmed_at)
                                for s in rr[0]['strokes']]
        self.assertEqual(signature(a), signature(b))
        self.assert_chain(a, bars, raw[-1].time+timedelta(minutes=15))

    def test_short_reversal_revises_connected_suffix_instead_of_restarting(self):
        for peak in (106,109):
            raw = short_reversal_fixture(peak)
            rr,bars = paths(raw)
            self.assert_chain(rr, bars, raw[-1].time+timedelta(hours=1))
            self.assertGreater(rr[0]['stroke_diagnostics']['revision_count'],0)
            self.assertEqual(rr[0]['strokes'][0].start.price,100)
            self.assertEqual(rr[0]['strokes'][-1].end.price,97)
        rr,_ = paths(short_reversal_fixture(109))
        self.assertEqual([(s.start.price,s.end.price) for s in rr[0]['strokes']],
                         [(100,110),(110,95),(95,105),(105,97)])

    def test_indicators_keep_all_continuous_source_history(self):
        raw = short_reversal_fixture(109)
        r = analyze(raw,1)
        self.assertEqual(r['gap_count'],0)
        self.assertEqual(r['structure_break_count'],0)
        self.assertEqual(len(r['blocks']),1)
        b = r['blocks'][0]
        self.assertEqual(b['start'], raw[0].time)
        self.assertEqual(b['bar_count'], len(raw))
        self.assertEqual(b['theory']['macd']['points'],macd(raw))
        # Compatibility callers must also receive continuous results.
        self.assertEqual(analyze(raw,1,stroke_policy='causal'),r)

    def test_real_data_gap_is_the_only_restart_and_resets_indicator(self):
        raw = short_reversal_fixture(109)
        offset = timedelta(hours=len(raw)+5)
        later = [Candle(c.time+offset,c.open,c.high,c.low,c.close,c.volume) for c in raw]
        r = analyze(raw+later,1)
        self.assertEqual(r['gap_count'],1)
        self.assertEqual(r['structure_break_count'],0)
        self.assertEqual(len(r['blocks']),2)
        for b,source in zip(r['blocks'],(raw,later)):
            self.assertEqual(b['theory']['macd']['points'],macd(source))

    def test_accidental_structural_restart_is_rejected(self):
        with patch('chan_strokes.connected_stroke_runs',return_value=[{},{}]):
            with self.assertRaisesRegex(ValueError,'不能拆成'):
                analyze(short_reversal_fixture(109),1)

    def test_btc_september_uses_legal_continuous_path_and_records_lost_pens(self):
        file = Path(__file__).parent/'reports/BTCUSDT_20260606_20261013_five_periods/history_1h.csv'
        if not file.exists():
            self.skipTest('archived BTC source not available')
        raw = read_csv(file)
        begin = parse_time('2026-09-07T07:00:00+08:00')
        end = parse_time('2026-09-11T20:00:00+08:00')
        before,_ = paths([c for c in raw if c.time+timedelta(hours=1) <= end])
        prior = [s for s in completed(before) if s.start.time >= begin]
        self.assertEqual(len(prior),7)
        full,bars = paths(raw)
        self.assert_chain(full,bars,raw[-1].time+timedelta(hours=1))
        events = full[0]['stroke_diagnostics']['revisions']
        self.assertEqual(events[:len(before[0]['stroke_diagnostics']['revisions'])],
                         before[0]['stroke_diagnostics']['revisions'])
        removed = [s for e in events if e['observed_at'] > end for s in e['removed_completed']]
        self.assertTrue(all(s in removed for s in prior))
        long_pen = next(s for s in full[0]['strokes'] if s.start.time == begin)
        self.assertEqual(long_pen.end.time,parse_time('2026-09-16T02:00:00+08:00'))
        # No artificial bridge, relaxed span, or undisclosed preservation of the
        # seven incompatible edges. Their original publication survives in events.
        self.assertEqual(long_pen.end.price,74909.4)


if __name__ == '__main__':
    unittest.main()
