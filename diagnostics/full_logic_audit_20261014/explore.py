"""Deterministic independent probes; no production mutations."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import random
from datetime import timedelta
from dataclasses import asdict
from test_chan import BASE, strokes
from test_theory import movements
from chan_theory import evolving_centers, divergence_candidates, MACDPoint
from chan_structure import build_segments

def main():
    for seed in range(500):
        rng = random.Random(seed)
        prices = [100]
        for i in range(40):
            prices.append(prices[-1] + (1 if i % 2 == 0 else -1)*rng.randint(1, 20))
        seq = movements(prices)
        # Positive and negative histogram pulses both decay over time;
        # intervening exact zero points establish an explicit axis reset.
        points = [MACDPoint(BASE+timedelta(hours=i), 1 if i%4 else 0, 0,
                            (1 if i%2 else -1)/(i+1), True) for i in range(161)]
        seen = {}
        for stop in range(4, len(seq)+1):
            view = evolving_centers(seq[:stop], 1)
            for d in divergence_candidates(seq[:stop], view, points[:4*stop]):
                key = (d['id'], d['kind'])
                if key not in seen and d['kind']=='trend' and d['evidence_at'] < seq[stop-1].evidence_at:
                    print('BACKDATED', seed, prices[:stop+1], stop, d, flush=True)
                    return
                seen.setdefault(key, stop)
    print('No backdated NEW TREND classification in 500 seeds; consolidation downgrades checked separately')

if __name__ == '__main__':
    main()
