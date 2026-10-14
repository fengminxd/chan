"""Independent geometry, source-indicator and raw-prefix publication checks."""
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from chan import read_csv, parse_time, resample, merge_inclusions, find_fractals, json_default
from chan_strokes import connected_stroke_runs
from chan_theory import macd

OUT = Path(__file__).resolve().parent
REPORT = ROOT/'reports/BTCUSDT_20260606_20261013_connected_v7'


def check_chain(items):
    for a,b in zip(items,items[1:]):
        assert (a['end'],a['end_price']) == (b['start'],b['start_price'])
        assert (a['end_price']>a['start_price']) != (b['end_price']>b['start_price'])
    pending = [i for i,n in enumerate(items) if n['state']=='pending']
    assert not pending or pending == [len(items)-1]


def main():
    source = json.loads((REPORT/'source.json').read_text())
    asof = parse_time(source['as_of'])
    raw = read_csv(REPORT/'history_1h.csv')
    quarters = read_csv(REPORT/'history_15m.csv')
    html = (REPORT/'btc_tradingview.html').read_text()
    data = json.loads(re.search(r'<script id="data" type="application/json">(.*?)</script>',html,re.S).group(1))
    assert data == json.loads((REPORT/'chart_data.json').read_text())
    assert 'v6因果选笔' not in html and '灰竖虚线' not in html
    manifest = json.loads((REPORT/'code_manifest.json').read_text())
    assert manifest['rule_manifest']['version']=='chan-rules-v7'
    for name,digest in manifest['sha256'].items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    for name,key in [('history_1h.csv','history_sha256'),('history_15m.csv','history_15m_sha256')]:
        assert hashlib.sha256((REPORT/name).read_bytes()).hexdigest()==source[key]
    result = dict(passed=True,code_hashes_verified=True,source_hashes_verified=True,
                  embedded_equals_chart_data=True,periods={},raw_prefixes=[])
    for period,hours in [('8h',8),('4h',4),('2h',2),('1h',1),('15m',.25)]:
        bars,_ = resample(quarters if hours<1 else raw,.25 if hours<1 else 1,hours,asof)
        report = json.loads((REPORT/f'analysis_{period}.json').read_text())
        assert report['gap_count']==report['structure_break_count']==0
        assert len(report['blocks'])==1
        block = report['blocks'][0]
        assert block['start']==bars[0].time.isoformat()
        merged = merge_inclusions(bars)
        for i,s in enumerate(block['strokes']):
            a,b=s['start'],s['end']
            assert b['index']-a['index']>=4
            span=merged[a['index']:b['index']+1]
            assert min(a['price'],b['price'])==min(c.low for c in span)
            assert max(a['price'],b['price'])==max(c.high for c in span)
            assert a['kind']!=b['kind']
            if i:assert block['strokes'][i-1]['end']==a
            assert s['provisional']==(i==len(block['strokes'])-1)
            if not s['provisional']:assert parse_time(s['confirmed_at'])<=asof
        # Independently use the complete source run, not the structure's slice.
        expected=macd(bars)
        actual=block['theory']['macd']['points']
        assert len(actual)==len(expected)==len(bars)
        for p,q in zip(actual,expected):
            assert parse_time(p['time'])==q.time
            assert all(p[key]==getattr(q,key) for key in ['dif','dea','histogram','warmed_up'])
        frame=data['frames'][period]
        assert not frame['continuity_breaks']
        for basis in ['strokes','types']:
            for kind in ['stroke','segment']:
                check_chain([n for n in frame['views'][basis]['nodes'] if n['kind']==kind])
        pens=[n for n in frame['views']['strokes']['nodes'] if n['kind']=='stroke']
        segments=[n for n in frame['views']['strokes']['nodes'] if n['kind']=='segment']
        result['periods'][period]=dict(strokes=len(pens),stroke_discontinuities=0,
            segment_discontinuities=0,segment_states=dict(Counter(n['state'] for n in segments)),
            macd_matches_complete_source=True,historical_revisions=frame['stroke_revision_count'])
        print(period,result['periods'][period],flush=True)
    merged=merge_inclusions(raw)
    fractals=find_fractals(merged[:-1])
    full=connected_stroke_runs(fractals,merged,4,timedelta(hours=1))[0]
    events=full['stroke_diagnostics']['revisions']
    for hour in [0,24,48,72,96,109,112,120,144,192,216,255]:
        at=parse_time('2026-09-07T07:00:00+08:00')+timedelta(hours=hour)
        local=merge_inclusions([c for c in raw if c.time+timedelta(hours=1)<=at])
        actual=connected_stroke_runs(find_fractals(local[:-1]),local,4,timedelta(hours=1))[0]
        visible=[f for f in fractals if merged[f.index+2].start+timedelta(hours=1)<=at]
        event_prefix=connected_stroke_runs(visible,merged,4,timedelta(hours=1))[0]
        assert actual==event_prefix
        assert actual['stroke_diagnostics']['revisions']==[e for e in events if e['observed_at']<=at]
        strokes=actual['strokes']
        assert all(a.end==b.start for a,b in zip(strokes,strokes[1:]))
        assert all(s.provisional or s.confirmed_at<=at for s in strokes)
        result['raw_prefixes'].append(dict(asof=at,strokes=len(strokes),connected=True,
            revisions=actual['stroke_diagnostics']['revision_count']))
        print('raw prefix',at,'PASS',flush=True)
    start=parse_time('2026-09-07T07:00:00+08:00')
    target=next(s for s in full['strokes'] if s.start.time==start)
    assert target.end.time==parse_time('2026-09-16T02:00:00+08:00')
    result['september_strict_long_stroke']=target
    result['september_revision_events']=[e for e in events
        if parse_time('2026-09-11T20:00:00+08:00')<e['observed_at']<=parse_time('2026-09-17T00:00:00+08:00')]
    (OUT/'validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=json_default)+'\n')
    print('PASS: five periods, continuous pens/segments, full-source MACD, 12 raw prefixes',flush=True)


if __name__=='__main__':
    main()
