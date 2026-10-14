"""2026-10-11 审计反例：因果证据、可延伸首段、破坏事件与显示链路。"""
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import random
import re
import tempfile
import unittest
from xml.etree import ElementTree

from chan import analyze, json_default, write_chart
from chan_structure import build_segments, direct_break_marks
from chart_fallback import static_chart
from report_btc_4h_auto import structure_lines
from test_chan import BASE, candle, strokes
from unified import build_graph
from unified_viewer import write_unified_viewer


def make_block(seq):
    r = build_segments(seq)
    return dict(start=seq[0].start.time, end=seq[-1].end.time, bar_count=len(seq)*4+1,
                strokes=seq, segments=r.segments, pending_segment=r.pending,
                direct_breaks=r.direct_breaks, centers=[],
                segment_diagnostics={'tail': r.tail_diagnostics},
                theory={'type_hierarchy': {'levels': [], 'next_level_center_seeds': [],
                                          'level_limit_reached': False}})


class CausalStructureTests(unittest.TestCase):
    def test_later_segment_waits_for_its_start_boundary(self):
        for sign in (1, -1):
            # 首反向上笔终点 99>=92 已破坏，后续到 103 才严格突破 99。
            # 第二段局部证据更早，但必须等待这条前段的发布。
            seq = strokes([sign*x for x in [125,101,102,92,105,83,99,98,99,87,89,84,103]])
            full = build_segments(seq).segments
            self.assertEqual(build_segments(seq[:-1]).segments, [])
            self.assertEqual([(s.first_stroke,s.last_stroke,s.evidence_stroke) for s in full],
                             [(0,4,11), (5,7,11)])
            self.assertEqual(full[1].local_evidence_stroke, 10)
            self.assertEqual(full[0].evidence_at, full[1].evidence_at)

    def test_first_break_and_genuine_gap_keep_distinct_confirmation_requirements(self):
        for sign in (1, -1):
            for old_low in (89, 90, 92, 100):
                with self.subTest(sign=sign, old_low=old_low):
                    seq = strokes([sign*x for x in
                                   [125,101,102,old_low,105,83,99,98,99,87,89,84,103]])
                    result = build_segments(seq)
                    if old_low <= 99:  # 原始首反向笔已进入旧元素区间。
                        self.assertEqual([(s.first_stroke,s.last_stroke,s.evidence_stroke)
                                          for s in result.segments], [(0,4,11),(5,7,11)])
                        self.assertEqual(build_segments(seq[:-1]).segments, [])
                    else:
                        self.assertEqual(result.segments, [])
                        audit = next(a for a in result.tail_diagnostics['candidate_turns']
                                     if a['turn_stroke'] == 5)
                        self.assertTrue(audit['gap'])
                        self.assertFalse(audit['reverse_pattern_found'])
                        self.assertEqual(audit['reason'], 'waiting_reverse_feature_fractal')

    def test_extendable_origin_survives_non_extreme_third_stroke(self):
        for sign in (1, -1):
            seq = strokes([sign*x for x in [0,10,4,8,2,12,6,11,5]])
            for stop in range(3, len(seq)+1):
                result = build_segments(seq[:stop])
                self.assertEqual(result.skipped_prefix_strokes, 0)
                if stop < 8:
                    self.assertEqual(result.segments, [])
            segment = result.segments[0]
            self.assertEqual((segment.first_stroke,segment.last_stroke,segment.evidence_stroke), (0,4,7))

    def test_all_used_stroke_times_propagate_but_future_times_do_not(self):
        seq = strokes([0,8,4,10,6,9,5,8])
        for i in range(6):
            changed = list(seq)
            changed[i] = replace(seq[i], confirmed_at=BASE+timedelta(days=10))
            changed[-1] = replace(seq[-1], confirmed_at=BASE+timedelta(days=20))
            s = build_segments(changed).segments[0]
            self.assertEqual(s.evidence_stroke, 5)
            self.assertEqual(s.evidence_at, BASE+timedelta(days=10))

    def test_containment_does_not_move_existing_confirmation_time(self):
        raw = [candle(i,p,p+.5) for i,p in enumerate([1,0,2,4,6,8,10,8,6,4,2,0,2,4,6,8,10,8,6])]
        a = analyze(raw, 1)['blocks'][0]['strokes']
        b = analyze(raw+[candle(19,6.1,6.4),candle(20,6.2,6.3)], 1)['blocks'][0]['strokes']
        self.assertEqual(a, b)
        self.assertEqual(a[1].confirmed_at, BASE+timedelta(hours=19))

    def test_random_fixed_strokes_have_causal_prefixes_and_persistent_marks(self):
        rng = random.Random(20261011)
        for case in range(500):
            prices = [100]
            for i in range(40):
                prices.append(prices[-1]+(1 if i%2 == 0 else -1)*rng.randint(1,25))
            seq = strokes(prices)
            full = build_segments(seq)
            for stop in sorted({s.evidence_stroke+1 for s in full.segments}):
                self.assertEqual(build_segments(seq[:stop]).segments,
                                 [s for s in full.segments if s.evidence_stroke < stop], (case,stop))
            if case < 50:
                previous = set()
                for stop in range(4,len(seq)+1):
                    marks = {e['turn_stroke'] for e in build_segments(seq[:stop]).direct_breaks}
                    self.assertLessEqual(previous,marks,(case,stop))
                    previous = marks


class BreakDisplayTests(unittest.TestCase):
    def test_partial_overlap_marks_only_attacking_stroke_before_confirmation(self):
        for sign in (1, -1):
            block = make_block(strokes([sign*p for p in [0,8,4,10,6,9,6]]))
            self.assertEqual(block['segments'], [])
            self.assertEqual(set(direct_break_marks(block)), {3})
            nodes = structure_lines(block, 'equal')
            self.assertEqual({n['id'] for n in nodes if n.get('direct_break')},
                             {'equal:stroke:3'})

    def test_historical_attacking_stroke_survives_extension_and_export(self):
        for sign in (1,-1):
            seq = strokes([sign*x for x in [0,8,4,10,-2,9,7,8,6,11,5,10,4]])
            short, full = make_block(seq[:7]), make_block(seq)
            self.assertEqual(short['segments'], [])
            self.assertIn(3,direct_break_marks(short))
            self.assertTrue({3,9} <= direct_break_marks(full).keys())
            nodes = structure_lines(full,'b')
            self.assertEqual({n['id'] for n in nodes if n.get('direct_break')}, {'b:stroke:3','b:stroke:9'})
            legacy = {k:v for k,v in full.items() if k != 'direct_breaks'}
            self.assertEqual(direct_break_marks(legacy),direct_break_marks(full))

    def test_svg_and_fallback_keep_color_dash_and_only_bold_attacker(self):
        for provisional in (False,True):
            block = make_block(strokes([0,8,4,10,6,7,5]))
            if provisional:
                # 显示状态独立于事件；验证虚线不会被加粗逻辑覆盖。
                block['strokes'][3] = replace(block['strokes'][3],provisional=True)
            with tempfile.TemporaryDirectory() as folder:
                path = Path(folder)/'chart.html'
                bars = [candle(i,-1,12) for i in range(25)]
                write_chart(path,{'1h':dict(blocks=[block],method='feature_sequence_recursive')},{'1h':bars})
                svg = ElementTree.fromstring(re.search(r'<svg .*?</svg>',path.read_text(),re.S).group())
                blue = [p for p in svg.iter('{http://www.w3.org/2000/svg}path') if p.get('stroke') == '#2563eb']
                self.assertEqual([p.get('stroke-width') for p in blue],['1.5','1.5','1.5','3.2','1.5','1.5'])
                self.assertEqual(blue[3].get('stroke-dasharray'),'5 4' if provisional else None)
                payload = dict(start=BASE,asof=BASE+timedelta(hours=25),bars=[
                    dict(time=c.time,open=c.open,high=c.high,low=c.low,close=c.close) for c in bars],
                    nodes=structure_lines(block,'b'), signals=[],summary={})
                fallback = ElementTree.fromstring(static_chart(json.loads(json.dumps(payload,default=json_default))))
                lines = [p for p in fallback.iter('{http://www.w3.org/2000/svg}path') if p.get('data-kind') == 'stroke']
                self.assertEqual(lines[3].get('stroke-width'),'3.2')
                self.assertEqual(lines[3].get('stroke'),'#237be5')
                self.assertEqual(lines[3].get('stroke-dasharray'),'6 4' if provisional else None)
                self.assertTrue(all(p.get('stroke-width') != '3.2' for p in fallback.iter()
                                    if p.get('data-kind') == 'segment'))

    def test_unified_tree_carries_mark_into_viewer_payload(self):
        block = make_block([replace(s,confirmed_at=s.end.time)
                            for s in strokes([0,8,4,10,2,7,1])])
        tree = build_graph({'blocks':[block]},BASE+timedelta(days=10))
        marks = [n for n in tree['nodes'].values() if n.get('direct_break')]
        self.assertEqual(len(marks),1)
        self.assertEqual((marks[0]['kind'],marks[0]['start_price'],marks[0]['end_price']),('stroke',10,2))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'unified.html'
            write_unified_viewer(path,dict(tree=tree,history={},views={},as_of=BASE,source_period='1m'))
            page = path.read_text()
            data = json.loads(re.search(r'id="data">(.*?)</script>',page,re.S).group(1))
            self.assertTrue(data['tree']['nodes'][marks[0]['id']]['direct_break'])
            self.assertIn('value="stroke"',page)
            self.assertIn("n.kind==='stroke'&&n.direct_break?3.2:1.8",page)


if __name__ == '__main__':
    unittest.main()
