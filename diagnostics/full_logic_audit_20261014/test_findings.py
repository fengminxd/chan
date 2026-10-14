"""Specification tests for the original audit findings (now fixed in v10).

Run: python3 -m unittest discover -s diagnostics/full_logic_audit_20261014 -p test_findings.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import unittest
from dataclasses import replace
from datetime import timedelta

from chan_dynamics import audit_divergence
from chan_theory import MACDPoint, divergence_candidates, evolving_centers
from test_chan import BASE
from test_dynamics import pattern
from test_theory import movements


class AuditFindings(unittest.TestCase):
    def test_changed_divergence_classification_carries_new_evidence(self):
        # Only the final down move changes this candidate from trend to
        # consolidation by expanding the current center's Z range.
        prices = [0,12,6,10,7,15,13,18,14,16,11]
        seq = movements(prices)
        points = [MACDPoint(BASE+timedelta(hours=i), 1 if i%4 else 0, 0,
                            (1 if i%2 else -1)/(i+1), True) for i in range(41)]
        results = []
        for stop in (9, 10):
            ds = divergence_candidates(seq[:stop], evolving_centers(seq[:stop], 1), points[:4*stop])
            results.append(next(d for d in ds if d['c_index'] == 8))
        self.assertEqual([d['kind'] for d in results], ['trend', 'consolidation'])
        self.assertGreaterEqual(results[-1]['evidence_at'], seq[-1].evidence_at)

    def test_internal_center_must_finish_inside_c(self):
        _, args = pattern()
        a, A, b, B, c, inner, third, waves = args
        late = [replace(z, end=c.end.time+timedelta(hours=20),
                        updated_at=c.evidence_at+timedelta(hours=20)) for z in inner]
        result = audit_divergence(a, A, b, B, c, late, third, waves)
        self.assertFalse(result['strict_divergence_confirmed'])

    def test_internal_center_price_range_must_be_inside_c(self):
        _, args = pattern()
        a, A, b, B, c, inner, third, waves = args
        outside = [replace(z, **{key:getattr(z,key)+1000 for key in
                   ('zd','zg','dd','gg','d','g','range_low','range_high')}) for z in inner]
        result = audit_divergence(a, A, b, B, c, outside, third, waves)
        self.assertFalse(result['strict_divergence_confirmed'])

    def test_internal_centers_must_match_c_declared_lineage(self):
        _, args = pattern()
        a, A, b, B, c, inner, third, waves = args
        detached = [replace(z, id='UNRELATED:'+z.id) for z in inner]
        result = audit_divergence(a, A, b, B, c, detached, third, waves)
        self.assertFalse(result['strict_divergence_confirmed'])


if __name__ == '__main__':
    unittest.main()
