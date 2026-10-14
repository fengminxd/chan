"""Historical counterexamples, with the first-break expectation corrected in v9."""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from test_chan import strokes
from chan_structure import build_segments


class FeatureSpecificationRegressions(unittest.TestCase):
    def test_boundary_inclusion_first_break_then_lower_third_pen_confirms(self):
        # Upward first three pens overlap. The next first reverse element
        # [4,10] contains [4,8], and the third reverse pen ends at 3 < 4.
        # Reflection exercises the bottom-fractal branch as well.
        for sign in (1,-1):
            with self.subTest(sign=sign):
                seq=strokes([sign*p for p in [0,8,4,10,4,9,3]])
                actual=build_segments(seq)
                self.assertEqual([(s.start.price,s.end.price) for s in actual.segments],
                                 [(0,sign*10)])

    def test_first_break_precedes_containment_gap_classification(self):
        # First reverse 14->6 already breaks because 6<=8. Its eventual
        # continuation below 6 resolves the break without a second fractal.
        # The v8 expectation of [] overlooked this prerequisite.
        for sign in (1,-1):
            with self.subTest(sign=sign):
                seq=strokes([sign*p for p in [0,8,4,14,6,13,9,12,8,11,5]])
                self.assertEqual([(s.start.price,s.end.price,s.evidence_stroke)
                                  for s in build_segments(seq).segments],[(0,sign*14,9)])


if __name__=='__main__':unittest.main()
