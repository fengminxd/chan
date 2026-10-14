"""Offline TradingView BTC perpetual chart; verified native sources, independent structures."""
import argparse
import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from chan import Candle, analyze, json_default, parse_time, read_csv, resample, validate_candles
from chan_time import period_name
from report_btc_4h_auto import automatic_views, dump, local

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
START = parse_time('2026-06-06T00:00:00+08:00')
HOURS = (8, 4, 2, 1, 0.25)
CACHE = ROOT/'reports/BTCUSDT_20260626_20261010_4h_2h_1h'
VENDOR = ROOT/'reports/ZECUSDT_nested_1h_fixed_20261008/vendor/lightweight-charts-5.0.4.js'


def fetch(folder):
    pages = folder/'raw_pages'
    pages.mkdir(parents=True, exist_ok=True)
    requests = []

    def get(name, **params):
        url = 'https://fapi.binance.com/fapi/v1/'+name+('?' + urlencode(params) if params else '')
        with urlopen(url, timeout=30) as response:
            raw, date = response.read(), response.headers.get('Date')
        filename = hashlib.sha256(url.encode()).hexdigest()[:24]+'.json'
        (pages/filename).write_bytes(raw)
        requests.append(dict(url=url, file='raw_pages/'+filename, server_date=date,
                             downloaded_at=datetime.now(UTC), sha256=hashlib.sha256(raw).hexdigest()))
        return json.loads(raw)

    server = get('time')
    cutoff = min(server['serverTime'], int(datetime.now(UTC).timestamp()*1000))//60000*60000
    asof = datetime.fromtimestamp(cutoff/1000, UTC)
    prior = json.loads((CACHE/'source.json').read_text())
    path = CACHE/'history_1h.csv'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == prior['periods']['1h']['sha256']
    bars = read_csv(path)

    def candles(period, begin, end, step):
        result = []
        while begin < end:
            stop = min(end, begin+1000*step)
            rows = get('klines', symbol='BTCUSDT', interval=period, startTime=begin, endTime=stop-1, limit=1000)
            assert isinstance(rows, list) and len(rows) == (stop-begin)//step
            assert all(len(r)==12 and r[0]==begin+i*step and r[6]==r[0]+step-1 for i,r in enumerate(rows))
            result.extend(Candle(datetime.fromtimestamp(r[0]/1000, UTC), *map(float,r[1:6])) for r in rows)
            begin = stop
        if result:validate_candles(result)
        return result

    end_hour = cutoff//3600000*3600000
    bars.extend(candles('1h', int(bars[-1].time.timestamp()*1000)+3600000, end_hour, 3600000))
    validate_candles(bars)
    assert all(b.time-a.time==timedelta(hours=1) for a,b in zip(bars,bars[1:]))
    with (folder/'history_1h.csv').open('w',newline='') as f:
        w=csv.writer(f); w.writerow(['timestamp','open','high','low','close','volume'])
        w.writerows([c.time.isoformat(),c.open,c.high,c.low,c.close,c.volume] for c in bars)
    quarter_start = START-timedelta(days=120)
    quarters = candles('15m', int(quarter_start.timestamp()*1000), cutoff//900000*900000, 900000)
    with (folder/'history_15m.csv').open('w',newline='') as f:
        w=csv.writer(f); w.writerow(['timestamp','open','high','low','close','volume'])
        w.writerows([c.time.isoformat(),c.open,c.high,c.low,c.close,c.volume] for c in quarters)
    aggregated, _ = resample(quarters,0.25,1,asof)
    hourly = {c.time:c for c in bars}
    for c in aggregated:
        assert all(abs(getattr(c,k)-getattr(hourly[c.time],k))<1e-7 for k in ('open','high','low','close'))
    minute_begin = cutoff//28800000*28800000
    minutes = candles('1m', minute_begin, cutoff, 60000) if cutoff>minute_begin else []
    partials = {}
    for h in HOURS:
        width = int(h*3600000)
        begin = cutoff//width*width
        tail = [c for c in minutes if int(c.time.timestamp()*1000)>=begin]
        partials[period_name(h)] = None if not tail else dict(time=datetime.fromtimestamp(begin/1000,UTC),
            open=tail[0].open, high=max(c.high for c in tail), low=min(c.low for c in tail),
            close=tail[-1].close, volume=sum(c.volume for c in tail), partial=True, available_minutes=len(tail))
    # Independently compare the most recent complete aggregated buckets to native exchange candles.
    checks = {}
    for h in HOURS:
        width = int(h*3600000)
        end = cutoff//width*width
        native = candles(period_name(h),end-6*width,end,width)
        grouped, _ = resample(quarters if h<1 else bars,0.25 if h<1 else 1,h,asof)
        for a,b in zip(grouped[-6:],native):
            assert a.time==b.time and all(abs(getattr(a,k)-getattr(b,k))<1e-7 for k in ('open','high','low','close'))
        checks[period_name(h)] = dict(native_buckets_checked=6,ohlc_match=True)
    ticker = get('ticker/24hr',symbol='BTCUSDT')
    contract = next(x for x in get('exchangeInfo')['symbols'] if x['symbol']=='BTCUSDT')
    assert contract['contractType']=='PERPETUAL' and contract['quoteAsset']=='USDT'
    dump(folder/'partials.json',partials)
    dump(folder/'source.json',dict(symbol='BTCUSDT',market='Binance USD-M perpetual',as_of=asof,
        start=START,context_start=bars[0].time,ticker=ticker,requests=requests,native_checks=checks,
        cache_source=str(path.relative_to(ROOT)),cache_sha256=digest,
        history_sha256=hashlib.sha256((folder/'history_1h.csv').read_bytes()).hexdigest(),
        history_15m_sha256=hashlib.sha256((folder/'history_15m.csv').read_bytes()).hexdigest(),
        context_start_15m=quarter_start,quarter_hour_cross_checks=len(aggregated),
        validation='Continuous native 1h and 15m sources; every overlapping complete hour checked against 15m OHLC; 8h/4h/2h resampled on UTC grid; partials from closed minutes only'))
    print('Fetched',len(bars),'hourly bars; cutoff',asof,flush=True)


def build(folder):
    source=json.loads((folder/'source.json').read_text())
    asof=parse_time(source['as_of'])
    assert hashlib.sha256((folder/'history_1h.csv').read_bytes()).hexdigest()==source['history_sha256']
    raw=read_csv(folder/'history_1h.csv')
    assert hashlib.sha256((folder/'history_15m.csv').read_bytes()).hexdigest()==source['history_15m_sha256']
    quarters=read_csv(folder/'history_15m.csv')
    partials=json.loads((folder/'partials.json').read_text())
    data=dict(symbol='BTCUSDT',start=int(START.timestamp()),asof=int(asof.timestamp()),
              source=source,frames={},default_period='8h')
    summary={}
    signal_rows=[]
    revision_log={}
    for h in HOURS:
        period=period_name(h);bars,quality=resample(quarters if h<1 else raw,0.25 if h<1 else 1,h,asof)
        assert bars[0].time<START and bars[-1].time+timedelta(hours=h)<=asof
        report=analyze(bars,h)
        dump(folder/f'analysis_{period}.json',report)
        revision_log[period]=[e for block in report['blocks']
            for e in block['stroke_diagnostics'].get('revisions',[]) if START <= e['observed_at'] <= asof]
        partial=partials[period]
        if partial:partial={**partial,'time':parse_time(partial['time'])}
        view=automatic_views(report,bars,START,asof,partial,period_hours=h)
        frame=dict(width=int(h*3600),bars=[],views=view['views'],context_start=int(bars[0].time.timestamp()),
                   continuity_breaks=[{**b,**{k:int(b[k].timestamp()) for k in ('start','end','observed_at')}}
                                      for b in view.get('continuity_breaks',[])],
                   stroke_revision_count=len(revision_log[period]),
                   chart_signal_policy=view['chart_signal_policy'])
        for bar in view['bars']:
            frame['bars'].append({**bar,'time':int(bar['time'].timestamp())})
        for basis,v in frame['views'].items():
            for n in v['nodes']:
                if isinstance(n['start'],datetime):n['start']=int(n['start'].timestamp())
                if isinstance(n['end'],datetime):n['end']=int(n['end'].timestamp())
            for s in v['signals']:
                assert s['evidence_at']<=asof
                signal_rows.append([period,basis,s['label'],local(s['time']),s['price'],local(s['evidence_at']),s['certainty'],s['id']])
                s['time']=int(s['time'].timestamp())
                s['evidence_at']=int(s['evidence_at'].timestamp())
        data['frames'][period]=frame
        nodes=frame['views']['strokes']['nodes']
        summary[period]=dict(bars=len(frame['bars']),closed_bars=sum(not b.get('partial') for b in frame['bars']),
            strokes=sum(n['kind']=='stroke' for n in nodes),segments=sum(n['kind']=='segment' for n in nodes),
            direct_breaks=sum(bool(n.get('direct_break')) for n in nodes),
            continuity_breaks=len(frame['continuity_breaks']),
            centers={k:sum(n['kind']=='center' for n in v['nodes']) for k,v in frame['views'].items()},
            signals={k:v['signal_counts'] for k,v in frame['views'].items()},quality=quality)
        print(period,json.dumps(summary[period],ensure_ascii=False),flush=True)
    # automatic_views shares structure dictionaries between its two views; times were converted only once.
    dump(folder/'chart_data.json',data)
    dump(folder/'stroke_revisions.json',dict(
        rule_manifest=report['rule_manifest'], periods=revision_log,
        note='按观察时刻保存历史笔修订；当前完成状态不等于以后不可重划。时间为UTC。'))
    with (folder/'signals.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f);w.writerow(['周期','口径','类型','端点_北京','价格','证据_北京','性质','ID']);w.writerows(signal_rows)
    bundle=VENDOR.read_text()
    encoded=json.dumps(data,ensure_ascii=False,default=json_default,separators=(',',':')).replace('<','\\u003c')
    template=(ROOT/'btc_multiperiod_tradingview_template.html').read_text()
    (folder/'btc_tradingview.html').write_text(template.replace('LIBRARY_JS',bundle).replace('PAYLOAD_JSON',encoded))
    dump(folder/'summary.json',summary)
    dump(folder/'code_manifest.json',dict(rule_manifest=report['rule_manifest'],
        library='TradingView Lightweight Charts 5.0.4',library_sha256=hashlib.sha256(bundle.encode()).hexdigest(),
        sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
        ('report_btc_tradingview.py','btc_multiperiod_tradingview_template.html','report_btc_4h_auto.py','entry_plan.py','chan.py','chan_strokes.py','chan_policy.py','chan_structure.py','chan_types.py','chan_dynamics.py')}))
    lines=['# BTCUSDT 永续 · 8h / 4h / 2h / 1h / 15m','', '[打开TradingView交互图](btc_tradingview.html)','',
        f'展示：北京时间 {local(START)} 至 {local(asof)}。小时周期保留2023年起历史，15m保留展示起点前120天历史，各周期独立划笔、划段。','',
        '蓝线为笔、橙线为段。发起一笔破坏的笔宽3.2，保留原颜色及虚实状态；虚线表示未确认结构。',
        '默认笔观察口径，可切换完成走势口径。B1/B2/B3分别为一二三买，S1/S2/S3为一二三卖；问号与空心标记为辅助或几何候选。没有符合条件的类别不补画。',
        '稳定端点的未确认末笔也显示符合规则的二／三类候选，标为“末笔二／三类候选”，不改变笔段确认状态、不赋予执行许可。',
        '端点时间与证据时间分开；这些是截止时刻结构快照，不是逐根回放的实时成交记录。末根未收盘K线仅显示，不参与结构确认。','',
        '规则v9：第一反向笔进入旧特征元素区间即记录破坏；后续严格越过首笔终点才确认，先越过首笔起点则假设失效。有缺口情形等待反向特征分型。沿用连续严格选笔，保留至少5根去包含K线与全跨度极值条件，连续行情输出连续笔链。受影响历史笔可以重划，撤回及替代记录按实际可知时刻保存；完成状态仅针对当前划分，不承诺永不修订。只在真实行情缺口处分块，MACD按完整连续行情计算。','',
        '| 周期 | 完整K线 | 笔 | 段（含待确认） | 破坏笔 |','|---|---:|---:|---:|---:|']
    for p,s in summary.items():lines.append(f'| {p} | {s["closed_bars"]} | {s["strokes"]} | {s["segments"]} | {s["direct_breaks"]} |')
    lines+=['','## 截止时刻结构观察','','| 周期 | 最近末笔 | 末笔状态 | 待确认线段端点 |','|---|---|---|---|']
    for h in HOURS:
        period=period_name(h)
        a=json.loads((folder/f'analysis_{period}.json').read_text());b=a['blocks'][-1]
        stroke=b['strokes'][-1] if b['strokes'] else None;pending=b['pending_segment']
        if stroke is None:
            lines.append(f'| {period} | 等待新笔 | 未成笔 | 无 |')
            continue
        lines.append(f'| {period} | {stroke["start"]["price"]:,.1f} → {stroke["end"]["price"]:,.1f} | '+
            ('待确认' if stroke['provisional'] else '已确认')+' | '+
            (f'{pending["start"]["price"]:,.1f} → {pending["end"]["price"]:,.1f}' if pending else '无')+' |')
    lines+=['','各周期确认进度不同，低周期反弹不等于高周期下跌已结束。待确认段仅使用已完成笔作为输入，可能落后于最新价格。']
    missing=[label for label in ('B1','B2','B3','S1','S2','S3') if not any(
        label in v['signal_counts'] for f in data['frames'].values() for v in f['views'].values())]
    if missing:lines+=['','本次所有周期与口径均未检出：'+', '.join(missing)+'；不为凑齐类别补画。']
    lines+=['','[数据来源](source.json) · [信号CSV](signals.csv) · [图表数据](chart_data.json) · [历史笔修订](stroke_revisions.json)',
        '',f'复现：`python3 report_btc_tradingview.py --output-dir {folder.relative_to(ROOT)}`；加 `--refresh` 更新行情。']
    (folder/'result.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'reports/BTCUSDT_20260606_current_tradingview')
    parser.add_argument('--refresh',action='store_true')
    parser.add_argument('--fetch-only',action='store_true')
    args=parser.parse_args();folder=args.output_dir.resolve();folder.mkdir(parents=True,exist_ok=True)
    if args.refresh:fetch(folder)
    if not args.fetch_only:build(folder)
