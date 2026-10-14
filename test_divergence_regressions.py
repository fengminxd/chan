"""背驰分类证据、内部中枢包含及注册表归属的独立反例回归。"""
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from chan_dynamics import analyze_dynamics, audit_divergence
from chan_theory import MACDPoint, divergence_candidates, evolving_centers, first_second_signals
from test_chan import BASE
from test_dynamics import comparison_waves, pattern
from test_theory import TREND, movements


def indicator_points(count, sign=1):
    # 单独控制面积递减和回零，使测试只针对结构及时间传播。
    return [MACDPoint(BASE+timedelta(hours=i), sign if i % 4 else 0, 0,
                      sign*(1 if i % 2 else -1)/(i+1), True) for i in range(count)]


class ClassificationEvidenceTests(unittest.TestCase):
    def test_downgrade_in_both_directions_includes_extension_evidence(self):
        for sign in (1, -1):
            with self.subTest(sign=sign):
                seq = movements([sign*p for p in [0,12,6,10,7,15,13,18,14,16,11]])
                results = []
                for stop in (9, 10):
                    view = evolving_centers(seq[:stop], 1)
                    ds = divergence_candidates(seq[:stop], view, indicator_points(4*stop, sign))
                    results.append(next(d for d in ds if d['c_index'] == 8))
                self.assertEqual([d['kind'] for d in results], ['trend', 'consolidation'])
                self.assertEqual(results[0]['evidence_at'], seq[8].evidence_at)
                self.assertEqual(results[1]['evidence_at'], seq[9].evidence_at)

    def test_closed_center_range_recalculation_keeps_closure_evidence(self):
        for sign in (1, -1):
            for prices, ci, kind in (([0,12,6,10,7,15,13], 4, 'consolidation'),
                                     (TREND[:11], 8, 'trend')):
                with self.subTest(sign=sign, kind=kind):
                    seq = movements([sign*p for p in prices])
                    view = evolving_centers(seq, 1)
                    z = view['centers'][-1]
                    self.assertLess(z.updated_at, z.closed_at)
                    ds = divergence_candidates(seq, view, indicator_points(4*len(seq), sign))
                    candidate = next(d for d in ds if d['c_index'] == ci)
                    self.assertEqual(candidate['kind'], kind)
                    self.assertEqual(candidate['evidence_at'], z.closed_at)
                    self.assertEqual(candidate['evidence_at'], seq[-1].evidence_at)
                    for signal in first_second_signals(seq, [candidate]):
                        self.assertGreaterEqual(signal.evidence_at, z.closed_at)


class InternalCenterTests(unittest.TestCase):
    def test_full_time_and_price_containment_in_both_directions(self):
        for sign in (1, -1):
            _, args = pattern(sign)
            a, A, b, B, c, inner, third, waves = args
            bad = {
                'late_end': replace(inner[0], end=c.end.time+timedelta(hours=20),
                                    updated_at=c.evidence_at+timedelta(hours=20)),
                'end_before_formation': replace(inner[0], end=inner[0].start),
                'outside_price': replace(inner[0], **{key:getattr(inner[0], key)+1000 for key in
                                        ('zd','zg','dd','gg','d','g','range_low','range_high')}),
                'uncontained_range': replace(inner[0], range_low=c.low-1),
                'uncontained_z_statistic': replace(inner[0], gg=c.high+1),
                'nonfinite_price': replace(inner[0], range_high=float('nan')),
            }
            for name, center in bad.items():
                with self.subTest(sign=sign, case=name):
                    audit = audit_divergence(a,A,b,B,c,[center,*inner[1:]],third,waves)
                    states = {x['rule']:x['state'] for x in audit['checks']}
                    self.assertEqual(states['c_internal_level_and_containment'], 'fail')
                    self.assertEqual(audit['state'], 'rejected')

    def test_declared_ids_are_ordered_unique_and_cannot_be_overridden(self):
        _, args = pattern()
        a, A, b, B, c, inner, third, waves = args
        cases = [
            ('detached', c, [replace(z,id='UNRELATED:'+z.id) for z in inner]),
            ('omitted', c, inner[:-1]),
            ('reordered', c, list(reversed(inner))),
            ('undeclared', replace(c,center_ids=()), inner),
            ('duplicate', replace(c,center_ids=tuple(z.id for z in [*inner,inner[-1]])),
             [*inner,inner[-1]]),
        ]
        for name, move, centers in cases:
            with self.subTest(case=name):
                audit = audit_divergence(a,A,b,B,move,centers,third,waves,lineage_valid=True)
                states = {x['rule']:x['state'] for x in audit['checks']}
                self.assertEqual(states['source_lineage'], 'fail')
                self.assertEqual(audit['state'], 'rejected')

    def test_complete_boundary_containment_remains_valid(self):
        _, args = pattern()
        a, A, b, B, c, inner, third, waves = args
        # 中枢边界与 c 接触仍属于包含，不能使用严格不等号误拒绝。
        inner = [replace(inner[0],start=c.start.time,range_low=c.low),
                 inner[1], replace(inner[2],end=c.end.time,range_high=c.high)]
        audit = audit_divergence(a,A,b,B,c,inner,third,waves)
        self.assertEqual(audit['state'], 'confirmed_under_policy')

    def test_detector_checks_owner_component_not_only_combined_c_envelope(self):
        seq, args = pattern()
        inner = [replace(z,departure_index=None,return_index=None) for z in args[5]]
        # 仍在整个 c 内，但已经越出声明拥有该中枢的第一条组成走势。
        inner[0] = replace(inner[0],end=seq[9].end.time,updated_at=seq[9].evidence_at)
        view = evolving_centers(seq, 2)
        hierarchy = {'base_movements': [], 'levels': [
            {'level':1, 'centers':inner, 'movements':seq,
             'structural_view':{'centers':inner,'signals':[]}},
            {'level':2, 'centers':view['centers'], 'movements':[], 'structural_view':view}]}
        with patch('chan_dynamics.strength_waves',return_value=comparison_waves()):
            result = analyze_dynamics(hierarchy, [], 1)
        self.assertEqual(result['confirmed'], [])
        self.assertTrue(result['levels'][1]['audits'])
        for audit in result['levels'][1]['audits']:
            states = {x['rule']:x['state'] for x in audit['checks']}
            self.assertEqual(states['source_lineage'], 'fail')


if __name__ == '__main__':
    unittest.main()
