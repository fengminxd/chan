"""Audit both directions without treating independent lower-period setups as 1h entry permission."""
import json
import sys
from pathlib import Path
from datetime import timedelta

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chan import read_csv, parse_time, resample, json_default
from chan_time import PERIODS
from entry_plan import EntryPolicy, execution_setups, setup_status

FOLDER = Path(__file__).resolve().parent
source = json.loads((FOLDER / 'source.json').read_text())
as_of = parse_time(source['as_of'])
minutes = read_csv(FOLDER / 'history_1m.csv')
local = json.loads((FOLDER / 'local_candidates.json').read_text())
assert set(local['periods']) == {'1h', '15m', '5m', '1m'}
audit = {}
for period in ('15m', '5m', '1m'):
    bars, _ = resample(minutes, PERIODS['1m'], PERIODS[period], as_of)
    records = []
    for candidate in local['periods'][period]['candidates']:
        scope = dict(candidate['interval'])
        for key in ('start', 'end', 'focus_time'):
            scope[key] = parse_time(scope[key])
        setups = execution_setups(bars, scope, candidate['side'], period, as_of,
                                  EntryPolicy(profile='nested_1h'))
        checked = []
        for setup in setups:
            hits = [c for c in minutes if c.time + timedelta(minutes=1) >= setup['evidence_at']
                    and (c.close > setup['trigger_price'] if candidate['side'] == 'long'
                         else c.close < setup['trigger_price'])]
            checked.append(dict(setup=setup, local_status=setup_status(setup, candidate, minutes),
                                first_local_cross=(dict(close_at=hits[0].time + timedelta(minutes=1),
                                                        close=hits[0].close) if hits else None)))
        records.append(dict(candidate=candidate, setups=checked))
    audit[period] = records
    print(period, json.dumps(records, ensure_ascii=False, default=json_default), flush=True)

result = dict(as_of=as_of, periods=audit,
              scope='独立低周期候选与局部分型条件；不构成1h完整定位许可；历史越线不回填成交')
(FOLDER / 'local_execution_audit.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2, default=json_default))
