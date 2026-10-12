"""Binance USDT perpetual: rolling 100-day, selectable 1h/4h offline report."""
import argparse
import csv
import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from chan import Candle, analyze, json_default, parse_time, read_csv, validate_candles
from report_btc_4h_auto import automatic_views, dump, local

ROOT = Path(__file__).resolve().parent
API = 'https://fapi.binance.com/fapi/v1/'
SYMBOL = 'SUIUSDT'
UTC = timezone.utc


def fetch(folder, symbol=SYMBOL):
    pages = folder/'raw_pages'
    pages.mkdir(parents=True, exist_ok=True)
    requests = []

    def get(name, params=None):
        url = API+name+('?' + urlencode(params) if params else '')
        with urlopen(url, timeout=30) as response:
            raw, server_date = response.read(), response.headers.get('Date')
        filename = hashlib.sha256(url.encode()).hexdigest()[:20]+'.json'
        (pages/filename).write_bytes(raw)
        requests.append(dict(url=url, file='raw_pages/'+filename,
                             sha256=hashlib.sha256(raw).hexdigest(), server_date=server_date,
                             downloaded_at=datetime.now(UTC)))
        return json.loads(raw)

    server = get('time')
    cutoff = min(server['serverTime'], int(datetime.now(UTC).timestamp()*1000))//60000*60000
    ticker = get('ticker/24hr', dict(symbol=symbol))
    info = get('exchangeInfo')
    contract = next(s for s in info['symbols'] if s['symbol'] == symbol)
    if contract['contractType'] != 'PERPETUAL' or contract['quoteAsset'] != 'USDT':
        raise ValueError('Unexpected contract type')
    tick = next(f['tickSize'] for f in contract['filters'] if f['filterType']=='PRICE_FILTER')
    decimals = len(tick.rstrip('0').split('.')[1]) if '.' in tick else 0
    periods = {}

    def klines(interval, begin, end, step):
        rows = get('klines', dict(symbol=symbol, interval=interval, startTime=begin,
                                 endTime=end-1, limit=1000))
        if not isinstance(rows, list) or len(rows) != (end-begin)//step:
            raise ValueError(f'Incomplete {interval} candles for {begin}–{end}')
        if any(len(r)!=12 or r[0]!=begin+i*step or r[6]!=r[0]+step-1 for i,r in enumerate(rows)):
            raise ValueError(f'Invalid {interval} timestamps')
        validate_candles([Candle(datetime.fromtimestamp(r[0]/1000,UTC),*map(float,r[1:6])) for r in rows])
        return rows

    for hours in (1,4):
        period, step = f'{hours}h', hours*3600000
        first = get('klines',dict(symbol=symbol,interval=period,startTime=0,limit=1))
        begin, end = first[0][0], cutoff//step*step
        if begin % step:
            raise ValueError('Listing candle does not align to period grid')
        cursor, rows = begin, []
        while cursor < end:
            stop = min(end,cursor+1000*step)
            rows.extend(klines(period,cursor,stop,step))
            cursor = stop
        with (folder/f'history_{period}.csv').open('w',newline='') as f:
            writer=csv.writer(f)
            writer.writerow(['timestamp','open','high','low','close','volume'])
            writer.writerows([datetime.fromtimestamp(r[0]/1000,UTC).isoformat(),*r[1:6]] for r in rows)
        periods[period] = dict(context_start=datetime.fromtimestamp(begin/1000,UTC),
                               closed_bars=len(rows),closed_end=datetime.fromtimestamp(end/1000,UTC),
                               sha256=hashlib.sha256((folder/f'history_{period}.csv').read_bytes()).hexdigest())
        print(f'{period}: {len(rows)} closed bars from listing',flush=True)
    # A shared minute tail covers both unclosed buckets, solely for display.
    start_minutes = cutoff//14400000*14400000
    minutes = klines('1m',start_minutes,cutoff,60000) if cutoff>start_minutes else []
    partials = {}
    for hours in (1,4):
        begin = cutoff//(hours*3600000)*(hours*3600000)
        tail = [r for r in minutes if r[0]>=begin]
        partials[f'{hours}h'] = None if not tail else dict(
            time=datetime.fromtimestamp(begin/1000,UTC), open=float(tail[0][1]),
            high=max(float(r[2]) for r in tail), low=min(float(r[3]) for r in tail),
            close=float(tail[-1][4]), volume=sum(float(r[5]) for r in tail),
            partial=True,available_minutes=len(tail))
    dump(folder/'partials.json',partials)
    dump(folder/'source.json',dict(symbol=symbol,market='Binance USD-M perpetual',
         price_type='trade_price',as_of=datetime.fromtimestamp(cutoff/1000,UTC),
         ticker=ticker,ticker_time=datetime.fromtimestamp(ticker['closeTime']/1000,UTC),
         price_decimals=decimals,tick_size=tick,contract_type=contract['contractType'],
         contract_status=contract['status'],periods=periods,requests=requests,
         window_days=100,context_policy='all_available_native_candles_from_listing',
         validation='closed bars continuous; unclosed buckets from closed minutes for display only'))


def render(folder, payload):
    symbol=payload['symbol']
    if not re.fullmatch(r'[A-Z0-9]{2,20}USDT',symbol):
        raise ValueError('Invalid USDT symbol')
    asset=symbol[:-4]
    filename=asset.lower()+'.html'
    template=(ROOT/'futures_multiperiod_template.html').read_text().replace('SUIUSDT',symbol).replace('SUI 永续',asset+' 永续')
    encoded=json.dumps(payload,ensure_ascii=False,default=json_default,separators=(',',':')).replace('<','\\u003c')
    (folder/filename).write_text(template.replace('PAYLOAD_JSON',encoded))
    lines=[f'# {symbol} 永续 · 最近100天', '', f'[打开可切换1h/4h图表]({filename})', '',
           f'结构截至北京时间 {local(payload["asof"])}，窗口开始 {local(payload["start"])}。', '',
           '默认展示近期结构：100天窗口＋180天前置历史。全历史对照保留上市以来数据，历史范围与中枢口径均可切换。', '',
           '蓝色数字为买点、红色数字为卖点；空心/问号是候选，虚线为未完成结构。端点时间和证据时间分开列示。', '',
           '| 历史范围 | 周期 | 口径 | 笔 | 段 | 中枢 | 信号/候选 |', '|---|---|---|---:|---:|---:|---:|']
    for history,h in payload['histories'].items():
        for period,pd in h['periods'].items():
            for basis,v in pd['views'].items():
                c=v['counts'];lines.append(f'| {h["label"]} | {period} | {basis} | {c.get("stroke",0)} | {c.get("segment",0)} | {c.get("center",0)} | {len(v["signals"])} |')
    for history,h in payload['histories'].items():
        for period,pd in h['periods'].items():
            lines+=['',f'## {h["label"]} · {period}','',*['- '+note for note in pd['analysis_notes']]]
    lines+=['','[行情来源](source.json) · [信号CSV](signals.csv) · [结构CSV](structures.csv) · [图表数据](chart_data.json)', '',
            f'复现：`python3 report_sui.py --symbol {symbol}`；更新：`python3 report_sui.py --symbol {symbol} --refresh`。未收盘桶仅供显示，结构输入仅含已收盘K线。']
    (folder/'result.md').write_text('\n'.join(lines)+'\n')


def build(folder):
    source=json.loads((folder/'source.json').read_text())
    asof=parse_time(source['as_of']);start=asof-timedelta(days=100)
    partials=json.loads((folder/'partials.json').read_text())
    payload=dict(symbol=source['symbol'],start=start,asof=asof,source=source,price_decimals=source['price_decimals'],
                 default_period='4h',default_basis='types',default_history='recent',
                 histories={'recent':{'label':'近期结构（180天前置）','periods':{}},
                            'full':{'label':'上市以来（全历史）','periods':{}}},manual_annotations=[])
    summaries={}
    for history,hours in [(history,hours) for history in ('full','recent') for hours in (1,4)]:
        period=f'{hours}h';bars=read_csv(folder/f'history_{period}.csv')
        if hashlib.sha256((folder/f'history_{period}.csv').read_bytes()).hexdigest()!=source['periods'][period]['sha256']:
            raise ValueError('Frozen candle hash mismatch')
        if bars[0].time>start or bars[-1].time+timedelta(hours=hours)>asof:
            raise ValueError('Invalid history coverage')
        if any(b.time-a.time!=timedelta(hours=hours) for a,b in zip(bars,bars[1:])):
            raise ValueError('Candle gap')
        if history=='recent':
            cutoff=start-timedelta(days=180)
            cutoff=datetime.fromtimestamp(int(cutoff.timestamp())//(hours*3600)*(hours*3600),UTC)
            bars=[c for c in bars if c.time>=cutoff]
        report=analyze(bars,hours)
        dump(folder/f'analysis_{period}{"_recent" if history=="recent" else ""}.json',report)
        partial=partials[period]
        if partial:
            partial={**partial,'time':parse_time(partial['time'])}
        view=automatic_views(report,bars,start,asof,partial,period_hours=hours)
        view['source']={**source['periods'][period], 'context_start':bars[0].time,
                        'history_scope':history,'partial_minutes':partial['available_minutes'] if partial else 0}
        block=report['blocks'][-1]
        layers=block['theory']['type_hierarchy']['levels']
        latest=next((z for z in reversed(layers[0]['centers'])),None)
        pending=block['pending_segment']
        last_price=float(source['ticker']['lastPrice'])
        notes=[]
        notes.append('局部计算：展示最近100天，另加180天前置历史；与全历史结果分别计算，不能混用结构和信号。'
                     if history=='recent' else '全历史计算：使用交易所上市以来全部已收盘K线。')
        if latest:
            relation='上方' if last_price>latest.zg else '下方' if last_price<latest.zd else '内部'
            notes.append(f'最近L1完成走势中枢为 {latest.zd:.{source["price_decimals"]}f}–{latest.zg:.{source["price_decimals"]}f}，报价位于中枢{relation}；图表周期与L1/L2结构层级分别计算。')
        else:
            notes.append('当前完成走势口径尚未识别L1中枢；这不等于行情客观没有其他合理划分。')
        if pending:
            side='上涨' if pending.end.price>pending.start.price else '下跌'
            notes.append(f'最后候选线段：{local(pending.start.time)} → {local(pending.end.time)}，{side}段待确认；其终点不是已确认买卖点。')
        notes.append('下表为100天窗口内的历史结构点与候选点；端点时间和证据时间分列，不代表当前同时可交易。')
        if history=='full' and not view['views']['types']['counts'].get('segment',0):
            tail=block['segment_diagnostics']['tail']
            notes.append(f'全历史{period}在展示窗口内没有线段输出，尾部有{tail["unassigned_completed_strokes"]}条已完成笔等待确认。可切换近期结构查看局部重建，不能将其冒充全历史已确认结果。')
        view['analysis_notes']=notes
        view['latest_completed_center']=asdict(latest) if latest else None
        view['pending_segment']=asdict(pending) if pending else None
        payload['histories'][history]['periods'][period]=view
        summaries.setdefault(history,{})[period]=dict(last_closed_bar=asdict(bars[-1]),pending_segment=asdict(pending) if pending else None,
            latest_center=asdict(latest) if latest else None,
            completed_types=[dict(level=l['level'],**l['diagnostics']) for l in layers],
            strict_confirmed_count=len(block['theory']['strict_divergence']['confirmed']),
            views={k:dict(counts=v['counts'],signals=v['signal_counts'],divergences=len(v['divergences'])) for k,v in view['views'].items()})
        for v in view['views'].values():
            for s in v['signals']+v['divergences']:
                if s['evidence_at']>asof:raise ValueError('Future evidence in exported signal')
                if s.get('center_id') and s['center_id'] not in v['centers']:raise ValueError('Missing referenced center')
        print(history,period,json.dumps(summaries[history][period]['views'],ensure_ascii=False),flush=True)
    notes_path=folder/'analysis_notes.json'
    if notes_path.exists():
        review=json.loads(notes_path.read_text())
        # Written commentary belongs to one frozen snapshot, never silently reuse it after refresh.
        if review.get('asof')==source['as_of']:
            for history,periods in review['histories'].items():
                for period,notes in periods.items():
                    payload['histories'][history]['periods'][period]['analysis_notes']=notes
    dump(folder/'chart_data.json',payload)
    dump(folder/'summary.json',dict(start=start,asof=asof,source=source,histories=summaries))
    render(folder,payload)
    for category in ('signals','divergences','structures'):
        with (folder/f'{category}.csv').open('w',newline='') as f:
            writer=csv.writer(f)
            writer.writerow(['历史范围','周期','口径','类别','结构层','开始/端点_北京','结束_北京','价格/ZD','ZG','证据_北京','性质','ID'])
            for history,period,view in [(h,p,v) for h,x in payload['histories'].items() for p,v in x['periods'].items()]:
                for basis,v in view['views'].items():
                    for item in v['nodes' if category=='structures' else category]:
                        writer.writerow([history,period,basis,item.get('label',item.get('kind')),item['level'],
                            local(item.get('time',item.get('start'))),local(item['end']) if item.get('end') else '',
                            item.get('price',item.get('zd',item.get('end_price'))),item.get('zg',''),
                            local(item['evidence_at']) if item.get('evidence_at') else '',
                            item.get('certainty',item.get('state')),item['id']])
    dump(folder/'code_manifest.json',dict(rule_manifest=report['rule_manifest'],sha256={
        name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
        ['report_sui.py','report_btc_4h_auto.py','futures_multiperiod_template.html','chan.py',
         'chan_structure.py','chan_types.py','chan_theory.py','chan_dynamics.py','chan_policy.py']}))
    return payload


def main(default_symbol=SYMBOL):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbol',default=default_symbol)
    p.add_argument('--output-dir',type=Path)
    p.add_argument('--refresh',action='store_true')
    p.add_argument('--fetch-only',action='store_true')
    args=p.parse_args();args.symbol=args.symbol.upper()
    if not re.fullmatch(r'[A-Z0-9]{2,20}USDT',args.symbol):p.error('Expected an uppercase USDT contract symbol')
    args.output_dir=args.output_dir or ROOT/'reports'/f'{args.symbol}_100d_current'
    args.output_dir.mkdir(parents=True,exist_ok=True)
    if args.refresh or not (args.output_dir/'source.json').exists():fetch(args.output_dir,args.symbol)
    if json.loads((args.output_dir/'source.json').read_text())['symbol']!=args.symbol:
        raise ValueError('Frozen data symbol does not match requested contract')
    if not args.fetch_only:build(args.output_dir)


if __name__=='__main__':main()
