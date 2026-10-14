"""First reverse-stroke break rules, checked by interval exits, not fractal code."""
import itertools
import unittest

from chan_structure import _turn_evidence, build_segments
from test_chan import strokes


class FirstReverseBreakTests(unittest.TestCase):
    def test_old_feature_near_boundary_includes_touch(self):
        for sign in (1,-1):
            for end in (7.99,8,8.01):
                with self.subTest(sign=sign,end=end):
                    seq=strokes([sign*p for p in [0,8,4,14,end]])
                    audit={}
                    self.assertIsNone(_turn_evidence(seq,0,3,audit))
                    self.assertEqual(audit['direct_break'],end<=8)
                    self.assertEqual(audit['gap'],end>8)
                    marks=build_segments(seq).direct_breaks
                    self.assertEqual([e['turn_stroke'] for e in marks],[3] if end<=8 else [])

    def test_partial_overlap_cannot_confirm_on_endpoint_touch(self):
        for sign in (1,-1):
            seq=strokes([sign*p for p in [0,8,4,10,6,9,7,8,6]])
            result=build_segments(seq)
            self.assertEqual(result.segments,[])
            self.assertEqual(result.direct_breaks[0]['reason'],'waiting_direct_break_resolution')

    def test_start_cross_invalidates_candidate_and_retains_attacking_mark(self):
        for sign in (1,-1):
            seq=strokes([sign*p for p in [0,8,4,10,6,9,7,11,5]])
            audit={}
            self.assertIsNone(_turn_evidence(seq,0,3,audit))
            self.assertTrue(audit['direct_break'])
            self.assertEqual(audit['reason'],'turn_extreme_exceeded')
            self.assertEqual(audit['evidence_stroke'],6)
            self.assertIn(3,{e['turn_stroke'] for e in build_segments(seq).direct_breaks})

    def test_exhaustive_first_break_resolution_against_interval_exit(self):
        count=0
        # Original up leg: 0->8->4->14. For r<=8 its first reverse stroke
        # breaks the old leg (d3<=g1). A later first strict exit of [r,14]
        # determines confirmation vs invalidation; equality keeps waiting.
        for r in (2,4,6,8):
            for tail in itertools.product(range(0,17,2),repeat=4):
                p=[0,8,4,14,r,*tail]
                if any((b-a)*(1 if i%2==0 else -1)<=0
                       for i,(a,b) in enumerate(zip(p,p[1:]))):
                    continue
                expected=None
                for j in range(5,len(p)):
                    if p[j]>14:break
                    if p[j]<r:
                        expected=j-1
                        break
                for sign in (1,-1):
                    seq=strokes([sign*v for v in p]);audit={}
                    evidence=_turn_evidence(seq,0,3,audit)
                    self.assertTrue(audit['direct_break'],(sign,p))
                    self.assertEqual(evidence[0] if evidence else None,expected,(sign,p))
                    count+=1
        self.assertEqual(count,7290)


if __name__=='__main__':unittest.main()
