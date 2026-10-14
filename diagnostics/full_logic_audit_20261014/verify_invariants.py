"""Additional deterministic geometry checks and optional frozen-data audit."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import json
import random
from datetime import timedelta
from chan import analyze, read_csv, resample
from chan_structure import build_segments
from test_chan import strokes

result = {'random_seed_count': 200, 'prefix_checks': 0, 'frozen_samples': {}}
for seed in range(200):
    rng = random.Random(seed)
    prices = [100]
    for i in range(32):
        prices.append(prices[-1]+(1 if i%2 == 0 else -1)*rng.randint(1,20))
    seq = strokes(prices)
    full = build_segments(seq)
    mirror = build_segments(strokes([-p for p in prices]))
    signature = lambda r: [(s.first_stroke,s.last_stroke,s.evidence_stroke,s.confirmation) for s in r.segments]
    assert signature(full) == signature(mirror)
    for stop in range(1,len(seq)+1):
        short = build_segments(seq[:stop])
        assert short.segments == [s for s in full.segments if s.evidence_stroke < stop], (seed,stop)
        chain = short.segments + ([short.pending] if short.pending else [])
        for s in chain:
            assert (s.last_stroke-s.first_stroke+1)%2 == 1
            assert s.last_stroke-s.first_stroke+1 >= 3
            part = seq[s.first_stroke:s.last_stroke+1]
            assert s.low == min(x.low for x in part) and s.high == max(x.high for x in part)
        for a,b in zip(chain,chain[1:]):
            assert a.end == b.start and a.last_stroke+1 == b.first_stroke
        assert all(e['turn_stroke'] < stop for e in short.direct_breaks)
        result['prefix_checks'] += 1

folder = ROOT/'reports/BTCUSDT_20260606_20261013_five_periods'
for period,hours in [('8h',8),('4h',4),('2h',2),('1h',1),('15m',.25)]:
    path = folder/f'history_{period}.csv'
    if not path.exists():
        hourly = read_csv(folder/'history_1h.csv')
        raw, _ = resample(hourly,1,hours,hourly[-1].time+timedelta(hours=1))
        source = 'history_1h.csv resampled'
    else:
        raw = read_csv(path)
        source = path.name
    # Keep a bounded, explicitly disclosed sample; do not overwrite reports.
    raw = raw[-1200:]
    report = analyze(raw,hours)
    counts = {'sample_bars':len(raw),'source':source,'start':raw[0].time.isoformat(),
              'end':raw[-1].time.isoformat(),'blocks':len(report['blocks']),
              'completed_strokes':0,'segments':0,'direct_breaks':0,
              'canonical_centers':0,'divergence_audits':0,'confirmed_divergences':0}
    for b in report['blocks']:
        counts['completed_strokes'] += sum(not s.provisional for s in b['strokes'])
        counts['segments'] += len(b['segments'])
        counts['direct_breaks'] += len(b['direct_breaks'])
        counts['canonical_centers'] += sum(len(l['centers']) for l in b['theory']['type_hierarchy']['levels'])
        counts['divergence_audits'] += sum(len(l['audits']) for l in b['theory']['strict_divergence']['levels'])
        counts['confirmed_divergences'] += len(b['theory']['strict_divergence']['confirmed'])
        for s in b['segments']:
            assert s.end.time <= s.evidence_at <= raw[-1].time+timedelta(hours=hours)
    result['frozen_samples'][period] = counts
    print(period, counts, flush=True)
(Path(__file__).parent/'invariants.json').write_text(json.dumps(result,indent=2)+'\n')
print('PASS',result['prefix_checks'],'prefix checks',flush=True)
