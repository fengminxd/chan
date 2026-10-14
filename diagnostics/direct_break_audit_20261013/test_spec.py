"""Independent first-break counterexamples: fail against audited v8, pass in v9."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from chan_structure import build_segments
from test_chan import strokes

class FirstPenBreakSpecification(unittest.TestCase):
    def test_first_reverse_overlaps_earlier_forward_stroke(self):
        # d3=6 <= g1=8, j=3 >= i+2; no need to undercut d2=4.
        for sign in (1,-1):
            with self.subTest(sign=sign):
                r=build_segments(strokes([sign*v for v in [0,8,4,10,6]]))
                self.assertEqual({e['turn_stroke'] for e in r.direct_breaks},{3})
                self.assertEqual(r.segments,[])

    def test_touching_first_reverse_endpoint_does_not_confirm(self):
        for sign in (1,-1):
            with self.subTest(sign=sign):
                r=build_segments(strokes([sign*v for v in [0,8,4,10,6,9,7,8,6]]))
                self.assertEqual(r.segments,[])

    def test_first_break_resolves_without_second_feature_fractal(self):
        for sign in (1,-1):
            with self.subTest(sign=sign):
                r=build_segments(strokes([sign*v for v in [0,8,4,14,6,13,9,12,8,11,5]]))
                self.assertEqual([(s.start.price,s.end.price,s.evidence_stroke) for s in r.segments],
                                 [(0,sign*14,9)])

if __name__=='__main__':unittest.main()
