"""Audit all 44 recorded triggers (31 groups) against causal strict structures.

Uses the existing completed-type / cross_kiss_sma policy. Never upgrades a
fractal, MACD hint, later signal, or unrelated same-direction signal.
"""
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
import hashlib
import json
import sys
import time

P = Path(__file__).resolve().parent
ROOT = P.parents[1]
SOURCE = ROOT / 'reports/ZECUSDT_nested_1h_fixed_20261008'
sys.path.insert(0, str(ROOT))
from chan import analyze, read_csv, resample, parse_time, json_default
from chan_time import PERIODS, period_delta
from entry_matrix import confirmed_execution_signals
import entry_plan as ep


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               default=json_default, allow_nan=False))


def same_endpoint(time, price, focus, target, width):
    return price == target and time <= focus < time + width


def third_signals(block, now):
    """Structural third points only from the completed type tree, never bi proxy."""
    tree = block['theory']['type_hierarchy']
    moves = {m.id: m for m in tree['base_movements']}
    for layer in tree['levels']:
        moves.update((m.id, m) for m in layer['movements'])
    result = []
    for layer in block['theory']['strict_divergence']['levels']:
        for s in layer['signals']:
            if s.label not in ('B3', 'S3') or s.certainty != 'structural' or s.evidence_at > now:
                continue
            parts = [moves.get(k) for k in s.source_ids]
            if len(parts) != 2 or any(m is None or m.status != 'completed'
                                     or m.level != s.level-1 or m.evidence_at > s.evidence_at
                                     for m in parts):
                continue
            a, b = parts
            if a.end != b.start or b.end.time != s.time or b.end.price != s.price:
                continue
            outside = b.low > s.details['zg'] if s.label == 'B3' else b.high < s.details['zd']
            if outside:
                result.append(s)
    return result


def frame_proof(block, period, now, record):
    width = period_delta(PERIODS[period])
    focus, target = parse_time(record['interval']['focus_time']), record['interval']['focus_price']
    match = lambda t, p: same_endpoint(t, p, focus, target, width)
    buy = record['side'] == 'long'
    labels = ('B1', 'B2') if buy else ('S1', 'S2')
    accepted = confirmed_execution_signals(block, now)
    matched = [x for x in accepted if x['signal'].label in labels
               and match(x['signal'].time, x['signal'].price)]
    thirds = [s for s in third_signals(block, now) if s.label == ('B3' if buy else 'S3')
              and match(s.time, s.price)]
    tree = block['theory']['type_hierarchy']
    movements = [*tree['base_movements'], *(m for l in tree['levels'] for m in l['movements'])]
    endpoints = [m for m in movements if match(m.end.time, m.end.price)
                 and m.status == 'completed' and m.evidence_at <= now]
    audits = [a for l in block['theory']['strict_divergence']['levels'] for a in l['audits']
              if match(a['end'], a['price'])]
    fractals = [f for f in block['fractals'] if match(f.time, f.price)
                and f.kind == ('bottom' if buy else 'top')]
    strokes = [s for s in block['strokes'] if match(s.end.time, s.end.price)]
    if matched:
        reason = '同一极值已有完整的一/二类点证据链'
    elif audits:
        reason = '同一极值有背驰审计，但结构/力度条件未全部通过，亦无合格二类点'
    elif endpoints:
        reason = '同一极值已有部分完成结构，但没有合格的一/二类点证据链'
    elif fractals:
        reason = '同一极值只有分型/笔端点证据，尚无完成走势支持的一/二类点'
    else:
        reason = '该周期目标极值尚无稳定端点及合格一/二类点'
    return dict(period=period, last_closed_at=block['end']+width,
                allowed_execution_labels=labels, matches=matched, structural_third_matches=thirds,
                completed_structures_at_target=endpoints, stable_fractals_at_target=fractals,
                strokes_at_target=strokes, divergence_audits_at_target=audits,
                accepted_first_second_total=len(accepted), reason=reason,
                type_layers=[dict(level=l['level'], completed_count=len(l['movements']),
                                  pending=l.get('pending')) for l in tree['levels']])


def audit_snapshot(payload):
    stamp, records = payload
    now = parse_time(stamp)
    minutes = [c for c in HISTORY if c.time + ep.MINUTE <= now]
    frames, blocks = {}, {}
    for period in ep.CHAIN:
        bars, quality = resample(minutes, PERIODS['1m'], PERIODS[period], now)
        analysis = analyze(bars, PERIODS[period])
        assert analysis['gap_count'] == 0
        block = {**analysis['blocks'][-1], 'period': period}
        assert bars[-1].time + period_delta(PERIODS[period]) <= now
        blocks[period] = block
        # Original route is preserved; extra lower periods need their own paths.
        offset = max(0, len(block['strokes'])-40)
        legs = ep.observation_legs(dict(period=period, strokes=block['strokes'][offset:]), minutes, now)
        for leg in legs:
            leg['index'] += offset
            leg['native_structure']['index'] += offset
        reverse = ep.developing_reverse(legs, minutes, now, period)
        frames[period] = dict(bars=bars, legs=legs+([reverse] if reverse else []))
    results = []
    for record in records:
        route = list(record['path'])
        scope = {**route[-1]}
        for key in ('start', 'end', 'focus_time'):
            scope[key] = parse_time(scope[key])
        # If original execution stopped at 15m or 5m, independently examine the
        # remaining chain. No jumping a missing intermediate path.
        for period in ep.CHAIN[len(route):]:
            narrowed = ep.narrow_interval(scope, frames[period]['legs'], record['side'], now)
            if not narrowed:
                break
            route.append(dict(period=period, **narrowed))
            scope = narrowed
        periods = {x['period'] for x in route}
        proofs = {p: frame_proof(blocks[p], p, now, record) for p in ep.CHAIN}
        confirmed = [dict(period=p, **x) for p in ep.CHAIN[1:] if p in periods
                     for x in proofs[p]['matches']]
        main_matches = [x for x in proofs['1h']['matches']
                        if x['signal'].label == record['label']]
        if record['label'].endswith('3'):
            main_matches = [dict(signal=s) for s in proofs['1h']['structural_third_matches']
                            if s.label == record['label']]
        close = next(c for c in reversed(minutes) if c.time + ep.MINUTE == now)
        assert close.close == record['trigger_observed_close'], (record['id'], close.close)
        assert parse_time(record['armed_at']) < now
        assert parse_time(record['evidence_at']) <= parse_time(record['armed_at'])
        original_setup = record['setup']
        end_period = original_setup['period']
        interval = {**record['path'][-1]}
        for key in ('start','end','focus_time'):
            interval[key] = parse_time(interval[key])
        setups = ep.execution_setups(frames[end_period]['bars'], interval, record['side'],
                                     end_period, now, ep.EntryPolicy(profile='nested_1h'))
        setup_reproduced = any(s['id'] == original_setup['id'] for s in setups)
        assert setup_reproduced, record['id']
        # Check the original observed route really shrinks, not merely bucket ends.
        for a, b in zip(record['path'], record['path'][1:]):
            assert b['start'] > a['start'] or b['low'] > a['low'] or b['high'] < a['high']
        results.append(dict(plan_id=record['id'], label=record['label'], side=record['side'],
            as_of=now, target_time=record['interval']['focus_time'], target_price=record['candidate_price'],
            trigger_close=record['trigger_observed_close'], original_execution_period=end_period,
            candidate_details=record['details'], original_route=record['path'], audited_route=route,
            frame_proofs=proofs, main_confirmed=bool(main_matches), main_matches=main_matches,
            execution_confirmed=bool(confirmed), execution_matches=confirmed,
            status='confirmed_under_policy' if confirmed else 'candidate_only',
            checks=dict(closed_data_only=True, original_stable_fractal_reproduced=setup_reproduced,
                        trigger_close_matches_source=True, original_route_actually_shrinks=True),
            confirmation_scope='completed type structures plus cross_kiss_sma(5,10); standard internal-trend branch; not universal Chan proof'))
    outfile = P / 'snapshots' / (now.strftime('%Y%m%dT%H%MZ')+'.json')
    dump(outfile, results)
    return results


def initialize(history):
    global HISTORY
    HISTORY = history


def run():
    began = time.monotonic()
    source = json.loads((SOURCE/'source.json').read_text())
    digest = hashlib.sha256((SOURCE/'history_1m.csv').read_bytes()).hexdigest()
    assert digest == source['history_sha256']
    history = read_csv(SOURCE/'history_1m.csv')
    rows = json.loads((SOURCE/'entries_grouped.json').read_text())
    triggers = json.loads((SOURCE/'triggers.json').read_text())
    batches = defaultdict(list)
    for t in triggers:
        batches[t['triggered_at']].append(t)
    (P/'snapshots').mkdir(exist_ok=True)
    collected = []
    with ProcessPoolExecutor(max_workers=2, initializer=initialize, initargs=(history,)) as pool:
        futures = {pool.submit(audit_snapshot, x): x[0] for x in batches.items()}
        for i, f in enumerate(as_completed(futures), 1):
            result = f.result()
            collected.extend(result)
            print(f'{i}/{len(batches)} {futures[f]} records={len(result)} '
                  f'confirmed={sum(r["execution_confirmed"] for r in result)} '
                  f'elapsed={time.monotonic()-began:.1f}s', flush=True)
    by_id = {r['plan_id']: r for r in collected}
    groups = []
    for number, row in enumerate(rows, 1):
        first = by_id[row['first_plan_id']]
        members = [r for r in collected if r['side'] == first['side']
                   and r['target_time'] == first['target_time'] and r['target_price'] == first['target_price']]
        members.sort(key=lambda r: (r['as_of'], r['plan_id']))
        assert len(members) == row['followup_signal_count']+1
        groups.append(dict(number=number, original=row, first=first, records=members,
                           status=first['status'],
                           first_main_confirmed=first['main_confirmed'],
                           first_execution_confirmed=first['execution_confirmed'],
                           any_recorded_execution_confirmed=any(r['execution_confirmed'] for r in members)))
    counts = dict(groups=len(groups), records=len(collected), snapshots=len(batches),
                  period_snapshots=len(batches)*4,
                  main_confirmed=sum(g['first_main_confirmed'] for g in groups),
                  execution_confirmed=sum(g['first_execution_confirmed'] for g in groups),
                  candidate_only=sum(not g['first_execution_confirmed'] for g in groups),
                  later_recorded_confirmation=sum(g['any_recorded_execution_confirmed']
                                                  and not g['first_execution_confirmed'] for g in groups))
    manifest = dict(counts=counts, history_sha256=digest, history_start=history[0].time,
                    source_as_of=source['as_of'], scope='all original 31 first triggers and their 44 recorded triggers; no later evidence backfill',
                    rule='causal completed-type first/second signals at identical target extreme, with valid nested path',
                    strength_policy='cross_kiss_sma(5,10), standard internal-trend branch only',
                    core_hashes={f: hashlib.sha256((ROOT/f).read_bytes()).hexdigest()
                                 for f in ('chan.py','chan_structure.py','chan_types.py','chan_theory.py',
                                           'chan_dynamics.py','entry_matrix.py','entry_plan.py')},
                    elapsed_seconds=time.monotonic()-began)
    dump(P/'audit_groups.json', groups)
    dump(P/'audit_manifest.json', manifest)
    print('COMPLETE', json.dumps(counts), flush=True)


if __name__ == '__main__':
    run()
