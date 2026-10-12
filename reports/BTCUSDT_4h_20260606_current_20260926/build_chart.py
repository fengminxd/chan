"""只绘制已生成的统一树；不重新计算笔、段或信号。"""
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
def dt(s): return datetime.fromisoformat(s.replace('Z', '+00:00'))
def local(s): return dt(s).astimezone(TZ).strftime('%Y-%m-%d %H:%M') if s else ''

def main():
    r = json.loads((ROOT/'unified/analysis.json').read_text())
    start, end = dt(r['target_start']), dt(r['as_of'])
    nodes = [n for n in r['tree']['nodes'].values() if dt(n['end']) >= start and dt(n['start']) < end and n['kind'] in ('stroke', 'segment', 'center', 'center_seed')]
    signals = sorted([s for s in r['tree']['signals'].values() if start <= dt(s['time']) < end], key=lambda s:s['time'])
    assert all(dt(s['evidence_at']) <= end for s in signals)
    bars = r['views']['4h']['candles'][:]
    last_close = dt(bars[-1]['time']) + timedelta(hours=4)
    with (ROOT/'history_1m.csv').open() as f:
        tail = [c for c in csv.DictReader(f) if last_close <= dt(c['timestamp']) < end]
    if tail:
        assert dt(tail[0]['timestamp']) == last_close
        bars.append(dict(time=last_close.isoformat(),open=float(tail[0]['open']),high=max(float(c['high']) for c in tail),low=min(float(c['low']) for c in tail),close=float(tail[-1]['close']),volume=sum(float(c['volume']) for c in tail),partial=True))
    counts = dict(Counter(n['kind'] for n in nodes))
    summary = dict(as_of=r['as_of'],target_start=r['target_start'],timezone='Asia/Shanghai (UTC+8)',structure_source='1m completed_type_tree; projected on 4h candles',counts=counts,signal_counts=dict(Counter('L'+str(s['level'])+' '+s['label']+' '+s['certainty'] for s in signals)),closed_4h_bars=len(r['views']['4h']['candles']),partial_4h_minutes=len(tail),history=r['history'],latest_signals=signals[-10:])
    (ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    with (ROOT/'signals.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['类别','结构层','端点时间_北京时间','价格_USDT','证据时间_北京时间','确认性质','信号ID','中枢ID'])
        for s in signals:w.writerow([s['label'],s['level'],local(s['time']),s['price'],local(s['evidence_at']),s['certainty'],s['id'],s['center_id']])
    with (ROOT/'structures.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['类型','结构层','状态','开始_北京时间','结束_北京时间','起点价','终点价','ZD','ZG','证据时间_北京时间','ID'])
        for n in nodes:w.writerow([n['kind'],n['level'],n['state'],local(n['start']),local(n['end']),n.get('start_price',''),n.get('end_price',''),n.get('zd',''),n.get('zg',''),local(n.get('evidence_at')),n['id']])
    fields=('id','kind','level','start','end','state','evidence_at','start_price','end_price','zd','zg')
    payload=dict(start=r['target_start'],asof=r['as_of'],bars=bars,nodes=[{k:n[k] for k in fields if k in n} for n in nodes],signals=signals,summary=summary)
    template=(ROOT/'chart_template.html').read_text()
    page=template.replace('PAYLOAD_JSON',json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c'))
    (ROOT/'btc_4h.html').write_text(page)
    names={'B1':'一买','S1':'一卖','B2':'二买','S2':'二卖','B3':'三买','S3':'三卖'}
    lines=['# BTCUSDT 永续合约 4h 结构图','',f"北京时间 {local(r['target_start'])} 至 {local(r['as_of'])}。Binance USD-M 永续，成交价。",'', '[打开交互图](btc_4h.html)：蓝色笔、橙色段、紫色中枢框，绿色买点、红色卖点；未完成笔段为虚线。支持全区间、90/30/7 日视图、滚轮缩放、拖动平移、买卖点点击定位及图像导出。','', '**口径：1m 为唯一递归基底，4h 为展示周期，不把分钟结构称作独立 4h 笔段，也不把 L1/L2 等同于小时周期。**', '', f"完整 4h K 线 {summary['closed_4h_bars']} 根；最新 4h 桶已有 {len(tail)} 分钟，单独标记为未收盘，仅用于行情显示。",'',f"窗口相交结构：{counts}；窗口内信号：{summary['signal_counts']}。",'', '## 最近买卖点', '', '| 类型 | 层级 | 端点时间（北京时间） | 价格 USDT | 证据时间（北京时间） | 性质 |','| --- | --- | --- | ---: | --- | --- |']
    for s in signals[-10:][::-1]:lines.append(f"| {names[s['label']]} | L{s['level']} | {local(s['time'])} | {s['price']:,.1f} | {local(s['evidence_at'])} | {s['certainty']} |")
    lines += ['', '三类点是程序规则下的结构信号；端点时间不是当时已知或可成交时间。本图为截止时刻快照，未执行逐分钟前缀回放。没有满足严格确认条件的一类点时不补造；二类点仍为依赖候选。', '', f"历史状态：`{r['history']['status']}`；原因：{', '.join(r['history']['reasons'])}。不能据此强行确认更高层走势。", '', '[全部买卖点 CSV](signals.csv) · [笔段中枢明细 CSV](structures.csv) · [数据来源与下载摘要](source.json) · [完整统一树](unified/analysis.json)', '', '行情使用 Binance 公开 [USD-M K 线接口](https://fapi.binance.com/fapi/v1/klines)；下载请求、原始响应摘要及沿用历史文件摘要保存在 source.json。']
    (ROOT/'result.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('history','latest_signals')},ensure_ascii=False))
    print('\n'.join(lines[12:26]))

if __name__=='__main__':main()
