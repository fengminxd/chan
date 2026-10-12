"""Binance USDT perpetual: fixed window, selectable 1h offline report."""
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

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from chan import Candle, analyze, json_default, parse_time, read_csv, validate_candles
from report_btc_4h_auto import automatic_views, dump, local

ROOT = Path(__file__).resolve().parents[2]
API = 'https://fapi.binance.com/fapi/v1/'
SYMBOL = 'STRKUSDT'
UTC = timezone.utc



def render(folder, payload):
    symbol=payload['symbol']
    if not re.fullmatch(r'[A-Z0-9]{2,20}USDT',symbol):
        raise ValueError('Invalid USDT symbol')
    asset=symbol[:-4]
    filename=asset.lower()+'.html'
    template=(folder/'template.html').read_text().replace('SUIUSDT',symbol).replace('SUI 永续',asset+' 永续')
    encoded=json.dumps(payload,ensure_ascii=False,default=json_default,separators=(',',':')).replace('<','\\u003c')
    (folder/filename).write_text(template.replace('PAYLOAD_JSON',encoded))
    lines=[f'# {symbol} 永续 · 2026-08-17起', '', f'[打开可切换1h图表]({filename})', '',
           f'结构截至北京时间 {local(payload["asof"])}，窗口开始 {local(payload["start"])}。', '',
           '默认展示近期结构：指定窗口＋180天前置历史。完成走势与笔观察口径可切换。', '',
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
            f'复现：`python3 reports/STRKUSDT_1h_20260817_20261010/build_report.py`；更新：`python3 reports/STRKUSDT_1h_20260817_20261010/fetch_snapshot.py`。未收盘桶仅供显示，结构输入仅含已收盘K线。']
    (folder/'result.md').write_text('\n'.join(lines)+'\n')


def build(folder):
    source=json.loads((folder/'source.json').read_text())
    asof=parse_time(source['as_of']);start=parse_time('2026-08-17T00:00:00+08:00')
    partials=json.loads((folder/'partials.json').read_text())
    payload=dict(symbol=source['symbol'],start=start,asof=asof,source=source,price_decimals=source['price_decimals'],
                 default_period='1h',default_basis='types',default_history='recent',
                 histories={'recent':{'label':'近期结构（180天前置）','periods':{}}},manual_annotations=[])
    summaries={}
    for history,hours in [(history,hours) for history in ('recent',) for hours in (1,)]:
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
        notes.append('局部计算：展示2026-08-17起，另加180天前置历史；完成走势与笔观察口径分别标注。'
                     if history=='recent' else '长历史计算：使用交易所2023年起全部已收盘K线。')
        if latest:
            relation='上方' if last_price>latest.zg else '下方' if last_price<latest.zd else '内部'
            notes.append(f'最近L1完成走势中枢为 {latest.zd:.{source["price_decimals"]}f}–{latest.zg:.{source["price_decimals"]}f}，报价位于中枢{relation}；图表周期与L1/L2结构层级分别计算。')
        else:
            notes.append('当前完成走势口径尚未识别L1中枢；这不等于行情客观没有其他合理划分。')
        if pending:
            side='上涨' if pending.end.price>pending.start.price else '下跌'
            notes.append(f'最后候选线段：{local(pending.start.time)} → {local(pending.end.time)}，{side}段待确认；其终点不是已确认买卖点。')
        notes.append('下表为指定窗口内的历史结构点与候选点；端点时间和证据时间分列，不代表当前同时可交易。')
        if history=='full' and not view['views']['types']['counts'].get('segment',0):
            tail=block['segment_diagnostics']['tail']
            notes.append(f'长历史{period}在展示窗口内没有线段输出，尾部有{tail["unassigned_completed_strokes"]}条已完成笔等待确认。可切换近期结构查看局部重建，不能将其冒充长历史已确认结果。')
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


if __name__=='__main__':
    build(Path(__file__).resolve().parent)
