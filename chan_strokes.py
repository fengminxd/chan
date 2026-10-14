"""Connected strict strokes, with causal publication and explicit revisions.

One continuous OHLC run produces one connected path. Completion is a state in
the current decomposition, not an immutable promise: an invalidated suffix is
reselected and its withdrawal is recorded, never hidden by starting a new run.
"""


def connected_stroke_runs(fractals, bars, min_span=4, duration=None):
    from chan import Stroke

    if min_span < 4 or duration is None:
        raise ValueError('connected strokes require min_span >= 4 and a candle duration')
    parents, lengths, roots, points = [], [], [], []
    active_root = None
    selected, strokes, revisions = [], [], []

    for f in fractals:
        if not 0 <= f.index < len(bars) or f.kind not in ('top', 'bottom'):
            raise ValueError('分型索引或类型无效')
        if points and (f.index <= points[-1].index or f.time <= points[-1].time):
            raise ValueError('分型索引及时间必须严格递增')
        if f.price != (bars[f.index].high if f.kind == 'top' else bars[f.index].low):
            raise ValueError('分型价格与去包含 K 线极值不符')
        if f.index+2 >= len(bars):
            continue
        available = bars[f.index+2].start+duration
        low, high, pos = bars[f.index].low, bars[f.index].high, f.index
        candidates = []
        for j in range(len(points)-1, -1, -1):
            a = points[j]
            for k in range(pos-1, a.index-1, -1):
                low, high = min(low, bars[k].low), max(high, bars[k].high)
            pos = a.index
            if (f.kind == 'top' and high > f.price or
                    f.kind == 'bottom' and low < f.price):
                break
            if a.kind == f.kind or f.index-a.index < min_span:
                continue
            if (f.kind == 'top' and a.price == low and a.price < f.price or
                    f.kind == 'bottom' and a.price == high and a.price > f.price):
                candidates.append(j)
        i = len(points)
        parent = min(candidates, key=lambda j: (roots[j], -lengths[j], -j)) if candidates else None
        points.append(f)
        parents.append(parent)
        lengths.append(lengths[parent]+1 if parent is not None else 0)
        roots.append(roots[parent] if parent is not None else i)
        if not lengths[i] or active_root is not None and roots[i] > active_root:
            continue
        active_root = roots[i]
        chain, cursor = [], i
        while cursor is not None:
            chain.append(points[cursor])
            cursor = parents[cursor]
        chain.reverse()

        # Preserve availability only for the unchanged completed prefix. New
        # geometry (including a reappearing stroke) is published NOW, not at
        # the earlier timestamp of its endpoint or a historical witness.
        completed_count = len(chain)-2
        common = 0
        while (common < min(len(strokes)-1, completed_count)
               and strokes[common].start == chain[common]
               and strokes[common].end == chain[common+1]):
            common += 1
        previous_completed = strokes[:-1]
        following = [*strokes[:common],
                     *[Stroke(chain[j], chain[j+1], False, available)
                       for j in range(common, completed_count)],
                     Stroke(chain[-2], chain[-1], True)]
        if common < len(previous_completed):
            revisions.append(dict(
                observed_at=available, reason='strict_connected_path_reselection',
                removed_completed=previous_completed[common:],
                replacement=following[common:]))
        selected, strokes = chain, following

    return [dict(strokes=strokes, stroke_diagnostics=dict(
        policy='connected_strict_revision_events_v1',
        status='tracking' if strokes else 'waiting_valid_stroke',
        fractal_count=len(points), selected_point_count=len(selected),
        selected_start=selected[0].time if selected else None,
        selected_end=selected[-1].time if selected else None,
        unreachable_tail_fractals=sum(p.index > selected[-1].index for p in points) if selected else len(points),
        tail_endpoint_stable_at=bars[selected[-1].index+2].start+duration if selected else None,
        revision_count=len(revisions), revisions=revisions,
        note='connected strict snapshot; completed suffix may revise; withdrawals carry observation times'))]


def causal_stroke_runs(fractals, bars, min_span=4, duration=None):
    """Compatibility name; the rejected v6 split-and-freeze policy is removed."""
    return connected_stroke_runs(fractals, bars, min_span, duration)
