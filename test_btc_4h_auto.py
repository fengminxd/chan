import json
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

from chan import Candle, analyze, demo_candles, json_default
from chart_fallback import static_chart
from chan_theory import evolving_centers
from report_btc_4h_auto import (auto_layer, automatic_views, fetch_history,
                               pending_stroke_candidates, unique_signals)
from test_chan import BASE
from test_theory import movements, TREND


class PendingStrokeCandidateTests(unittest.TestCase):
    def fixture(self, points):
        """Real OHLC input with spaced turns and a stable right neighbour."""
        side = 1 if points[1] > points[0] else -1
        values = [points[0]+2*side, points[0]+side]
        for a,b in zip(points,points[1:]):
            values.extend(a+(b-a)*i/5 for i in range(5))
        side = 1 if points[-1] > points[-2] else -1
        values += [points[-1],points[-1]-side,points[-1]-2*side]
        bars = [Candle(BASE+timedelta(hours=i),p,p,p,p,1) for i,p in enumerate(values)]
        report = analyze(bars,1)
        return bars,report,bars[-1].time+timedelta(hours=1)

    def candidates(self, bars, report, end):
        return pending_stroke_candidates(report['blocks'][-1],bars,end,'1h:block:0:strokes',1)

    def test_stable_tail_exports_both_classes_without_completing_structure(self):
        for mirror,prefix in ((False,'S'),(True,'B')):
            points = [10,20,12,18,8,11]
            if mirror:points = [100-p for p in points]
            bars,report,end = self.fixture(points)
            original = deepcopy(report)
            result = automatic_views(report,bars,bars[0].time,end,period_hours=1)
            tail = report['blocks'][-1]['strokes'][-1]
            found = [s for s in result['views']['strokes']['signals'] if s['certainty']=='pending_stroke_candidate']
            self.assertEqual({s['label'] for s in found},{prefix+'2',prefix+'3'})
            for s in found:
                self.assertEqual((s['time'],s['price']),(tail.end.time,tail.end.price))
                self.assertGreater(s['evidence_at'],s['time'])
                self.assertLessEqual(s['evidence_at'],end)
                self.assertFalse(s['higher_point_confirmed'])
                self.assertFalse(s['actionable'])
                self.assertFalse(s['details']['stroke_completed'])
                if s['center_id']:self.assertIn(s['center_id'],result['views']['strokes']['centers'])
            self.assertEqual(report,original)
            self.assertTrue(tail.provisional)
            # Display-only augmentation cannot change centers, lines, or completed-type signals.
            with patch('report_btc_4h_auto.pending_stroke_candidates',return_value=[]):
                prior = automatic_views(report,bars,bars[0].time,end,period_hours=1)
            self.assertEqual(result['views']['types'],prior['views']['types'])
            self.assertEqual(result['views']['strokes']['nodes'],prior['views']['strokes']['nodes'])
            self.assertEqual(result['views']['strokes']['centers'],prior['views']['strokes']['centers'])

    def test_touching_boundary_is_not_third_candidate(self):
        for mirror in (False,True):
            for last in (12,13):
                points=[10,20,12,18,8,last]
                if mirror:points=[100-p for p in points]
                bars,report,end=self.fixture(points)
                signals=self.candidates(bars,report,end)
                self.assertTrue(any(s['label'].endswith('2') for s in signals))
                self.assertFalse(any(s['label'].endswith('3') for s in signals))

    def test_completed_third_consumes_center_but_failed_attempt_allows_retry(self):
        for mirror in (False,True):
            for points,want_third in (([0,20,10,18,5,8,2,4],False),
                                      ([0,12,6,10,7,15,9,16,13],True)):
                points=[100+(-p if mirror else p) for p in points]
                bars,report,end=self.fixture(points)
                self.assertEqual(any(s['label'].endswith('3') for s in self.candidates(bars,report,end)),want_third)

    def test_stability_and_dependency_evidence_cannot_be_backdated(self):
        bars,report,end=self.fixture([10,20,12,18,8,11])
        self.assertEqual(self.candidates(bars,report,end-timedelta(hours=1)),[])
        block=report['blocks'][-1]
        block['strokes'][-2]=replace(block['strokes'][-2],confirmed_at=end+timedelta(hours=1))
        self.assertEqual(self.candidates(bars,report,end),[])

    def test_new_extreme_withdraws_old_tail_candidate_and_unclosed_bar_is_ignored(self):
        bars,report,end=self.fixture([10,20,12,18,8,11])
        future=Candle(end,12,12,12,12,1)
        self.assertEqual(self.candidates(bars+[future],report,end),self.candidates(bars,report,end))
        self.assertEqual(self.candidates(bars+[future],report,end+timedelta(hours=1)),[])

    def test_second_candidate_rejects_raw_range_break_and_is_not_duplicated_when_completed(self):
        bars,report,end=self.fixture([10,20,12,16])
        at=report['blocks'][-1]['strokes'][-1].start.time+timedelta(hours=1)
        changed=[replace(c,high=21) if c.time==at else c for c in bars]
        self.assertFalse(any(s['label']=='S2' for s in self.candidates(changed,report,end)))
        block=report['blocks'][-1]
        block['strokes'][-1]=replace(block['strokes'][-1],provisional=False,confirmed_at=end)
        self.assertEqual(self.candidates(bars,report,end),[])

    def test_pending_candidate_never_replaces_confirmed_signal(self):
        point=dict(label='S3',level=1,time=BASE,price=10)
        for order in (['pending_stroke_candidate','stroke_structural'],['stroke_structural','pending_stroke_candidate']):
            self.assertEqual(unique_signals([{**point,'certainty':c} for c in order])[0]['certainty'],'stroke_structural')

    def test_static_fallback_shows_hollow_question_mark_candidates(self):
        bars,report,end=self.fixture([10,20,12,18,8,11])
        result=automatic_views(report,bars,bars[0].time,end,period_hours=1)
        signals=[s for s in result['views']['strokes']['signals'] if s['certainty']=='pending_stroke_candidate']
        payload=dict(start=bars[0].time,asof=end,bars=result['bars'],
                     nodes=result['views']['strokes']['nodes'],signals=signals,summary={})
        svg=ElementTree.fromstring(static_chart(json.loads(json.dumps(payload,default=json_default))))
        markers=[n for n in svg.iter() if n.get('data-kind')=='signal']
        self.assertEqual(len(markers),2)
        self.assertTrue(all(n.get('fill')=='white' for n in markers))
        self.assertTrue({'L1 S2?','L1 S3?'} <= {n.text for n in svg.iter()})


class AutoReportTests(unittest.TestCase):
    def test_one_hour_view_uses_its_own_period_and_left_overlapping_bar(self):
        candles = demo_candles()
        report = analyze(candles, 1)
        start = candles[1].time+timedelta(minutes=30)
        end = candles[-1].time+timedelta(hours=1)
        result = automatic_views(report,candles,start,end,period_hours=1)
        self.assertEqual(result['analysis_period'],'1h')
        self.assertEqual(result['bars'][0]['time'],candles[1].time)
        self.assertTrue(all(n['id'].startswith('1h:') for v in result['views'].values() for n in v['nodes']))
        self.assertTrue(all(s['basis'].startswith('1h') for v in result['views'].values() for s in v['signals']))

    def test_retest_raw_price_check_does_not_include_later_periods(self):
        seq=movements([20,10,16,12]);view=evolving_centers(seq,1)
        end=seq[-1].end.time
        bars=[Candle(end,13,14,12,13,1),Candle(end+timedelta(hours=2),13,14,9,10,1)]
        _,hourly,_=auto_layer(seq,view,[],'one','1h完成走势',candles=bars,period_hours=1)
        _,four_hourly,_=auto_layer(seq,view,[],'four','4h完成走势',candles=bars,period_hours=4)
        self.assertEqual([s['label'] for s in hourly],['B2'])
        self.assertEqual(four_hourly,[])

    def test_manual_free_views_keep_same_strokes_and_closed_inputs(self):
        candles = demo_candles()
        # Demo samples are hourly; the engine duration here follows that dataset.
        report = analyze(candles, 1)
        end = candles[-1].time + timedelta(hours=4)
        partial = dict(time=end, open=1, high=999999, low=1, close=2, volume=1, partial=True)
        first = automatic_views(report, candles, candles[0].time, end)
        second = automatic_views(report, candles, candles[0].time, end, partial)
        self.assertEqual(first['views'], second['views'])
        self.assertEqual(second['manual_annotations'], [])
        a, b = first['views']['strokes'], first['views']['types']
        self.assertEqual([n for n in a['nodes'] if n['kind'] != 'center'],
                         [n for n in b['nodes'] if n['kind'] != 'center'])
        for view in first['views'].values():
            for s in view['signals']:
                self.assertNotIn('reference', s['certainty'])
                if s.get('center_id'):
                    self.assertIn(s['center_id'], view['centers'])

    def test_macd_position_exports_even_without_strict_confirmation(self):
        seq = movements(TREND)
        view = evolving_centers(seq, 1)
        d = dict(id='test:D', kind='trend', level=1, side='up', center_id=view['centers'][1].id,
                 a_id=seq[4].id, c_id=seq[8].id, c_index=8, end=seq[8].end.time,
                 price=seq[8].end.price, evidence_at=seq[8].evidence_at, area_ratio=.5,
                 certainty='macd_auxiliary', strict_divergence_confirmed=False)
        with patch('report_btc_4h_auto.divergence_candidates', return_value=[d]):
            centers, signals, divs = auto_layer(seq, view, [], 'test', '4h笔级')
        first = next(s for s in signals if s['label'] == 'S1')
        self.assertEqual(first['certainty'], 'macd_candidate')
        self.assertEqual((divs[0]['time'], divs[0]['price']), (d['end'], d['price']))
        self.assertFalse(divs[0]['strict_divergence_confirmed'])
        self.assertIn(divs[0]['center_id'], {z['id'] for z in centers})

    def test_duplicate_geometric_candidate_does_not_replace_confirmed_point(self):
        base = dict(label='B2', level=1, time=BASE, price=10)
        result = unique_signals([{**base, 'certainty': 'policy_dependent'},
                                 {**base, 'certainty': 'second_retest_geometry'}])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['certainty'], 'policy_dependent')

    def test_fetch_splits_closed_four_hour_bars_and_partial_minutes(self):
        cutoff = int((BASE+timedelta(hours=14, minutes=7)).timestamp()*1000)
        class Response:
            headers = {}
            def __init__(self, data): self.data = json.dumps(data).encode()
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return self.data
        def fetch(url, timeout):
            if url.endswith('/time'):
                return Response({'serverTime': cutoff})
            q = parse_qs(urlparse(url).query)
            start, end = int(q['startTime'][0]), int(q['endTime'][0])+1
            step = 60000 if q['interval'][0] == '1m' else 14400000
            return Response([[t, '10', '12', '9', '11', '2', t+step-1, '0', 1, '0', '0', '0']
                             for t in range(start, end, step)])
        with tempfile.TemporaryDirectory() as td, patch('report_btc_4h_auto.urlopen', side_effect=fetch):
            folder = Path(td)
            fetch_history(folder, BASE)
            source = json.loads((folder/'source.json').read_text())
            partial = json.loads((folder/'partial_4h.json').read_text())
            self.assertEqual(source['closed_4h_count'], 3)
            self.assertEqual(source['partial_4h_minutes'], 127)
            self.assertEqual(partial['volume'], 254)
            self.assertEqual(partial['time'], (BASE+timedelta(hours=12)).isoformat())


if __name__ == '__main__':
    unittest.main()
