import unittest
from dataclasses import replace
from datetime import timedelta

from chart_buy_points import stroke_buy_annotations
from test_chan import strokes
from test_theory import TREND


def block(prices):
    return {'strokes': [replace(s, confirmed_at=s.end.time+timedelta(hours=4))
                        for s in strokes(prices)]}


def reference(b):
    s = b['strokes'][0]
    return {'time': s.end.time, 'price': s.end.price, 'source': 'test_reference', 'note': 'reference only'}


class BuyAnnotationTests(unittest.TestCase):
    def test_reference_first_and_exact_first_retest_keep_provenance(self):
        b = block([20, 10, 16, 12, 18, 14])
        before = b['strokes'][:]
        points, _ = stroke_buy_annotations(b, 0, [reference(b)])
        self.assertEqual([p['label'] for p in points], ['B1', 'B2'])
        self.assertEqual([p['price'] for p in points], [10, 12])
        self.assertEqual(points[0]['certainty'], 'reference_annotation')
        self.assertEqual(points[1]['certainty'], 'reference_dependent')
        self.assertEqual(points[1]['time'], before[2].end.time)
        self.assertEqual(points[1]['evidence_at'], before[2].confirmed_at)
        self.assertTrue(all(not p['strict_divergence_confirmed'] for p in points))
        self.assertEqual(b['strokes'], before)

    def test_no_reference_does_not_invent_first_or_second(self):
        points, _ = stroke_buy_annotations(block([20, 10, 16, 12]), 0)
        self.assertEqual(points, [])

    def test_unfinished_retest_is_not_published(self):
        b = block([20, 10, 16, 12])
        b['strokes'][-1] = replace(b['strokes'][-1], provisional=True)
        points, _ = stroke_buy_annotations(b, 0, [reference(b)])
        self.assertEqual([p['label'] for p in points], ['B1'])

    def test_weak_first_retest_is_pending_not_skipped_for_a_later_low(self):
        b = block([20, 10, 16, 9, 18, 13])
        points, _ = stroke_buy_annotations(b, 0, [reference(b)])
        second = next(p for p in points if p['label'] == 'B2')
        self.assertEqual((second['price'], second['certainty']), (9, 'weak_retest_pending'))

    def test_third_uses_original_center_and_needs_completed_return(self):
        b = block(TREND)
        points, centers = stroke_buy_annotations(b, 0)
        third = points[0]
        self.assertEqual((third['label'], third['price']), ('B3', 13))
        self.assertEqual(third['certainty'], 'stroke_structural')
        self.assertEqual(centers[third['center_id']]['zg'], 10)
        self.assertGreater(third['details']['return_low'], third['details']['zg'])
        partial = {'strokes': b['strokes'][:6]}
        partial['strokes'][-1] = replace(partial['strokes'][-1], provisional=True)
        self.assertEqual(stroke_buy_annotations(partial, 0)[0], [])

    def test_price_or_time_mismatch_never_invents_reference_endpoint(self):
        b = block([20, 10, 16, 12])
        ref = {**reference(b), 'price': 9}
        self.assertEqual(stroke_buy_annotations(b, 0, [ref])[0], [])
