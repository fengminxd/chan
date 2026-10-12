"""4h 图表的笔级买点标注；参考图位置与自动结构计算分别保留来源。"""
from dataclasses import asdict

from chan_structure import Movement
from chan_theory import evolving_centers


def stroke_buy_annotations(block, block_index, first_references=()):
    """不重新划笔/段，不把参考一买或笔重叠规则升级为严格背驰确认。"""
    strokes = block['strokes']
    complete = []
    for i, s in enumerate(strokes):
        if s.provisional:
            break
        if s.confirmed_at is None:
            raise ValueError('标注必须保存笔端点的结构证据时间')
        complete.append(Movement(f'{block_index}:stroke:{i}', 0, 'stroke_proxy',
                                 s.start, s.end, s.low, s.high, (), (), s.confirmed_at))
    view = evolving_centers(complete, 1)
    centers = {f'{block_index}:{z.id}': {**asdict(z), 'id': f'{block_index}:{z.id}',
               'structure_source': '4h_strokes'} for z in view['centers']}
    annotations = []
    for ref in first_references:
        matches = [i for i, s in enumerate(strokes) if s.end.time == ref['time']
                   and s.end.price == ref['price'] and s.direction == 'down']
        if not matches:
            continue
        i = matches[0]
        if i >= len(complete):
            continue
        first = dict(id=f'{block_index}:stroke:{i}:reference:B1', label='B1', level=1,
                     time=ref['time'], price=ref['price'], evidence_at=complete[i].evidence_at,
                     certainty='reference_annotation', center_id=None, child_index=i,
                     source_ids=[complete[i].id], annotation_basis='4h笔级',
                     structure_source='4h_strokes', strict_divergence_confirmed=False,
                     details={'reference': ref['source'], 'reference_note': ref['note'],
                              'evidence_scope': 'stroke_endpoint_only_not_divergence_confirmation'})
        annotations.append(first)
        if i + 2 >= len(complete):
            continue
        rebound, retest = complete[i+1:i+3]
        # 仅首次两笔；不能跳过破低的第一次回试，去选择更晚的高低点。
        if rebound.end.price <= rebound.start.price or retest.end.price >= retest.start.price:
            continue
        holds = retest.low >= ref['price']
        annotations.append(dict(id=f'{first["id"]}:B2', label='B2', level=1,
                                time=retest.end.time, price=retest.end.price,
                                evidence_at=max(first['evidence_at'], rebound.evidence_at, retest.evidence_at),
                                certainty='reference_dependent' if holds else 'weak_retest_pending',
                                center_id=None, child_index=i+2,
                                source_ids=[first['id'], rebound.id, retest.id],
                                annotation_basis='4h笔级', structure_source='4h_strokes',
                                strict_divergence_confirmed=False,
                                details={'first_signal_id': first['id'], 'first_price': ref['price'],
                                         'rebound_start': rebound.start.time, 'rebound_end': rebound.end.time,
                                         'retest_start': retest.start.time, 'retest_end': retest.end.time,
                                         'holds_first': holds, 'first_is_user_reference': True,
                                         'pending': [] if holds else ['weak_retest_divergence']}))
    for signal in view['signals']:
        if signal.label != 'B3':
            continue
        cid = f'{block_index}:{signal.center_id}'
        annotations.append({**asdict(signal), 'id': f'{block_index}:stroke:{signal.id}',
                            'center_id': cid, 'certainty': 'stroke_structural',
                            'annotation_basis': '4h笔级', 'structure_source': '4h_strokes',
                            'strict_divergence_confirmed': False,
                            'details': {**signal.details, 'center_start': centers[cid]['start'],
                                        'center_end': centers[cid]['end'],
                                        'scope': 'completed_stroke_overlap_not_completed_type_recursion'}})
    return sorted(annotations, key=lambda s: (s['time'], s['label'])), centers
