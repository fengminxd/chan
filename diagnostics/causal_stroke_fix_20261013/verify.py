"""Hourly event-prefix audit, with independent raw-prefix checkpoint comparisons."""
import sys,json,hashlib
from pathlib import Path
from datetime import timedelta
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from chan import read_csv,merge_inclusions,find_fractals,parse_time,json_default
from chan_strokes import causal_stroke_runs
SRC=ROOT/'reports/BTCUSDT_20260606_20261013_five_periods/history_1h.csv'
raw=read_csv(SRC);m=merge_inclusions(raw);fs=find_fractals(m[:-1]);hour=timedelta(hours=1)
all_runs=causal_stroke_runs(fs,m,4,hour)
completed=lambda runs:[s for r in runs for s in r['strokes'] if not s.provisional]
full=completed(all_runs);counts=[];previous=[]
start=parse_time('2026-09-07T07:00:00+08:00')
for i in range(256):
    at=start+i*hour
    visible=[f for f in fs if m[f.index+2].start+hour<=at]
    rr=causal_stroke_runs(visible,m,4,hour);pens=completed(rr)
    assert pens[:len(previous)]==previous
    assert full[:len(pens)]==pens
    assert all(s.confirmed_at<=at for s in pens)
    if i%24==0 or i==255:
        local=merge_inclusions([c for c in raw if c.time+hour<=at])
        check=causal_stroke_runs(find_fractals(local[:-1]),local,4,hour)
        assert check==rr,'event prefix differs from independently truncated OHLC'
        print('checkpoint',at,'locked',len(pens),flush=True)
    counts.append(dict(asof=at,completed=len(pens),breaks=len(rr)-1))
    previous=pens
sept=[s for s in full if start<=s.start.time<parse_time('2026-09-11T20:00:00+08:00')]
assert len(sept)==7
augstart=parse_time('2026-08-10T06:00:00+08:00');augend=parse_time('2026-08-14T22:00:00+08:00')
aug=[s for s in full if augstart<=s.start.time<augend]
assert len(aug)==9
assert any(s.start.price==63886 and s.end.price==63574.6 for s in aug)
result=dict(passed=True,source_sha256=hashlib.sha256(SRC.read_bytes()).hexdigest(),
    hourly_cutoffs=counts,raw_prefix_cross_checks=12,completed_strokes_never_retracted=True,
    september_seven_retained=sept,august_nine_unchanged=aug,
    append_only_scope='same data prefix, same parameters; candidate tail may revise; structural breaks explicit')
Path(__file__).with_name('results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=json_default)+'\n')
print('PASS 256 hourly prefixes; 12 raw-prefix cross checks; August and September cases')
