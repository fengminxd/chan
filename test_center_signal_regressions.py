"""2026-10-11 中枢范围、三类点历史及候选政策的反例回归。"""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import random
import unittest

from chan_dynamics import analyze_dynamics
from chan_types import build_type_hierarchy
from report_btc_4h_auto import auto_layer
from test_chan import BASE
from test_theory import movements
from test_unified import stub_report
from unified import build_graph, validate_graph


RANGE = [128,130,109,128,78,79,63,85,61,85,66,89,81,97,89]
REPARTITION = [196,215,214,226,203,210,195,218,213,218,199]


class CenterSignalRegressionTests(unittest.TestCase):
    def test_extension_range_and_parent_members_are_separate_in_both_directions(self):
        for sign in (1, -1):
            seq = movements([sign*p for p in RANGE])
            layer = build_type_hierarchy(seq, 1)['levels'][0]
            z = next(z for z in layer['centers'] if z.first_index == 6)
            original = next(c for c in layer['decomposition_centers'] if c.id == z.id)
            self.assertEqual(z.child_ids, [m.id for m in seq[6:12]])
            self.assertEqual(original.child_ids, [m.id for m in seq[6:10]])
            self.assertEqual((z.dd, z.gg), (61, 89) if sign == 1 else (-89, -61))
            self.assertEqual((original.dd, original.gg), (61, 85) if sign == 1 else (-85, -61))
            owner = next(m for m in layer['movements'] if z.id in m.center_ids)
            self.assertNotIn(seq[10].id, owner.child_ids)
            assigned = [cid for m in layer['movements'] for cid in m.child_ids]
            self.assertEqual(assigned+layer['unassigned_child_ids'], [m.id for m in seq])
            self.assertIsNot(z.child_ids, original.child_ids)

    def test_departure_is_removed_but_seed_members_are_retained(self):
        prices = [135,150,136,149,122,130,107,131,106,118,102,104,90,123]
        for sign in (1, -1):
            seq = movements([sign*p for p in prices])
            z = next(z for z in build_type_hierarchy(seq, 1)['levels'][0]['centers']
                     if z.first_index == 6)
            self.assertEqual(z.child_ids, [m.id for m in seq[6:9]])
            self.assertEqual((z.range_low, z.range_high), (106,131) if sign == 1 else (-131,-106))
            self.assertNotIn(seq[z.departure_index].id, z.child_ids)
            self.assertLessEqual(set(z.seed_ids), set(z.child_ids))

    def test_repartition_keeps_signal_identity_evidence_and_original_center(self):
        for sign in (1, -1):
            seq = movements([sign*p for p in REPARTITION])
            old = build_type_hierarchy(seq[:-1], 1)['levels'][0]
            new = build_type_hierarchy(seq, 1)['levels'][0]
            signal = next(s for s in old['structural_view']['signals'] if s.price == sign*213)
            self.assertIn(signal, new['structural_view']['signals'])
            old_center = next(z for z in old['centers'] if z.id == signal.center_id)
            self.assertIn(old_center, new['historical_centers'])
            self.assertNotIn(old_center.id, {z.id for z in new['centers']})
            self.assertTrue(all(old_center.id not in m.center_ids for m in new['movements']))

    def test_fixed_completed_prefixes_preserve_all_published_signals(self):
        rng = random.Random(20261012)
        saw_signals = saw_upper = False
        for _ in range(12):
            prices = [100]
            for i in range(60):
                prices.append(prices[-1]+(1 if i % 2 == 0 else -1)*rng.randint(1,25))
            seq = movements(prices)
            previous = {}
            for stop in range(4, len(seq)+1):
                hierarchy = build_type_hierarchy(seq[:stop], 3)
                saw_upper |= len(hierarchy['levels']) > 1
                current = {s.id:s for layer in hierarchy['levels'] for s in layer['structural_view']['signals']}
                for sid, signal in previous.items():
                    self.assertEqual(current.get(sid), signal, (prices, stop, sid))
                for signal in current.values():
                    self.assertLessEqual(signal.evidence_at, seq[stop-1].evidence_at)
                previous = current
                saw_signals |= bool(current)
        self.assertTrue(saw_signals)
        self.assertTrue(saw_upper)

    def test_delayed_input_does_not_backdate_signal_publication(self):
        seq = movements(REPARTITION)
        delayed = BASE+timedelta(days=20)
        seq[0] = replace(seq[0], evidence_at=delayed)
        layer = build_type_hierarchy(seq, 1)['levels'][0]
        self.assertTrue(layer['structural_view']['signals'])
        self.assertTrue(all(s.evidence_at >= delayed for s in layer['structural_view']['signals']))

    def test_center_discovered_by_repartition_waits_for_discovery_evidence(self):
        seq = movements(REPARTITION)
        before = build_type_hierarchy(seq[:-1], 1)['levels'][0]
        after = build_type_hierarchy(seq, 1)['levels'][0]
        self.assertFalse(any(z.first_index == 6 for z in before['centers']))
        new = next(z for z in after['centers'] if z.first_index == 6)
        self.assertLess(new.formed_at, seq[-1].end.time)
        self.assertEqual(new.evidence_at, seq[-1].evidence_at)

    def test_historical_signals_have_real_graph_and_report_sources(self):
        for sign in (1, -1):
            prices = [sign*p for p in REPARTITION]
            report = stub_report(prices)
            block = report['blocks'][0]
            hierarchy = block['theory']['type_hierarchy']
            block['theory']['strict_divergence'] = analyze_dynamics(hierarchy, [], 1)
            tree = build_graph(report, BASE+timedelta(days=30))
            signals = [s for s in tree['signals'].values() if s['price'] == sign*213]
            self.assertEqual(len(signals), 1)
            z = tree['nodes'][signals[0]['center_id']]
            self.assertTrue(z['historical'])
            self.assertIsNone(z['owner_movement_id'])
            layer = hierarchy['levels'][0]
            historical = {z.id for z in layer['historical_centers']}
            for audit in block['theory']['strict_divergence']['levels'][0]['audits']:
                self.assertNotIn(audit['pattern']['A'], historical)
                self.assertNotIn(audit['pattern']['B'], historical)
            nodes, output, _ = auto_layer(hierarchy['base_movements'], layer['structural_view'], [], 'audit', '1h完成走势')
            ids = {n['id'] for n in nodes}
            self.assertTrue(all(s['center_id'] in ids for s in output if s.get('center_id')))
            self.assertTrue(any(n['historical'] for n in nodes))
            bad = deepcopy(tree)
            bad['nodes'][z['id']]['low'] -= 1
            with self.assertRaises(ValueError):
                validate_graph(bad, BASE+timedelta(days=30))


if __name__ == '__main__':
    unittest.main()
