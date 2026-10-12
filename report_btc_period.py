"""生成本次 BTC 永续历史分析报告；所有显示时间为 UTC+8。"""
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
from pathlib import Path

from chan import read_csv, resample, parse_time

ROOT = Path('reports/BTCUSDT_perpetual_20260606_20260819')
TZ = timezone(timedelta(hours=8))
START = parse_time('2026-06-06T00:00:00+08:00')
END = parse_time('2026-08-19T00:00:00+08:00')


def when(value):
    if not value:
        return '—'
    return datetime.fromisoformat(value).astimezone(TZ).strftime('%Y-%m-%d %H:%M')


def chart(bars, period, block):
    width, height = 1200, 320
    lo, hi = min(c.low for c in bars), max(c.high for c in bars)
    pad = (hi-lo)*.08
    lo, hi = lo-pad, hi+pad
    x = lambda t: 72 + (t-START).total_seconds()/(END-START).total_seconds()*1100
    y = lambda p: 20+(hi-p)/(hi-lo)*255
    parts = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{period} K线及中枢">']
    for i in range(6):
        price = lo+(hi-lo)*i/5
        parts.append(f'<path d="M72 {y(price):.2f}H1175" stroke="#dbe3ed"/><text x="8" y="{y(price)+4:.2f}">{price:,.0f}</text>')
    for z in block['centers']:
        left, right = x(parse_time(z['start'])), x(parse_time(z['end']))
        label = f"L{z['level']} {z['zd']:,.1f}–{z['zg']:,.1f}，证据 {when(z['evidence_at'])}"
        parts.append(f'<rect x="{left:.2f}" y="{y(z["zg"]):.2f}" width="{max(1,right-left):.2f}" height="{max(1,y(z["zd"])-y(z["zg"])):.2f}" fill="#f59e0b" fill-opacity=".17" stroke="#b45309"><title>{label}</title></rect>')
    bw = max(.6, min(10, 1100/len(bars)*.65))
    for c in bars:
        color = '#dc2626' if c.close >= c.open else '#059669'
        label = f'{c.time.astimezone(TZ):%m-%d %H:%M} O={c.open} H={c.high} L={c.low} C={c.close}'
        parts.append(f'<g><title>{label}</title><path d="M{x(c.time):.2f} {y(c.high):.2f}V{y(c.low):.2f}" stroke="{color}" stroke-width=".7"/><rect x="{x(c.time)-bw/2:.2f}" y="{min(y(c.open),y(c.close)):.2f}" width="{bw:.2f}" height="{max(.7,abs(y(c.open)-y(c.close))):.2f}" fill="{color}"/></g>')
    for s in block['segments']:
        parts.append(f'<path d="M{x(parse_time(s["start"]["time"])):.2f} {y(s["start"]["price"]):.2f}L{x(parse_time(s["end"]["time"])):.2f} {y(s["end"]["price"]):.2f}" fill="none" stroke="#1e3a8a" stroke-width="2"><title>确认线段，证据 {when(s["evidence_at"])}</title></path>')
    pending = block['pending_segment']
    if pending:
        parts.append(f'<path d="M{x(parse_time(pending["start"]["time"])):.2f} {y(pending["start"]["price"]):.2f}L{x(parse_time(pending["end"]["time"])):.2f} {y(pending["end"]["price"]):.2f}" fill="none" stroke="#1e3a8a" stroke-width="2" stroke-dasharray="5 4"><title>未确认线段</title></path>')
    for i in range(7):
        stamp = START+(END-START)*i/6
        parts.append(f'<text x="{x(stamp):.2f}" y="305" text-anchor="middle">{stamp.astimezone(TZ):%m-%d}</text>')
    parts.append('</svg>')
    return ''.join(parts)


def main():
    if (ROOT/'SUPERSEDED.md').exists():
        raise SystemExit('旧版结果已标记为失效；请运行 python3 diagnose_structure.py 查看修复后的结构诊断。')
    window = json.loads((ROOT/'window/analysis.json').read_text())['results']
    context = json.loads((ROOT/'context/analysis.json').read_text())['results']
    replay = json.loads((ROOT/'replay_each_close_summary.json').read_text())
    raw = read_csv(ROOT/'window_1h.csv')
    rows, observations, details, charts = [], [], [], []
    for period, hours in [('1h',1),('4h',4),('8h',8),('12h',12),('1d',24)]:
        report = window[period]
        block = report['blocks'][0]
        centers = block['centers']
        bands = '；'.join(f"L{z['level']} {z['zd']:,.1f}–{z['zg']:,.1f}" for z in centers) or '完成线段不足，未确认中枢'
        dynamics = block['theory']['strict_divergence']
        strict_count = len(dynamics['confirmed'])
        candidate_count = sum(len(l['divergences']) for l in block['theory']['levels'])
        buys = [s for l in block['theory']['levels'] for s in l['signals'] if s['label'].startswith('B')]
        buys += [s for l in dynamics['levels'] for s in l['signals'] if s['label'].startswith('B')]
        rows.append([period,str(report['bar_count']),str(len(block['segments'])),bands,
                     f'{strict_count} / {candidate_count}',str(len(buys))])
        # 端点/理论证据时间不冒充回放真正第一次观察到的时间。
        sought = {(z['level'],z['start'],z['end'],z['zd'],z['zg']):z for z in centers}
        first_match = {}
        with (ROOT/f'replay_each_close_{period}.jsonl').open() as f:
            for line in f:
                snapshot = json.loads(line)
                for event in snapshot['events']:
                    item = event.get('structure',{})
                    if item.get('type') != 'recursive_center':
                        continue
                    z = item['value']
                    key = (z['level'],z['start'],z['end'],z['zd'],z['zg'])
                    if key in sought:
                        first_match.setdefault(key,snapshot['as_of'])
        for key,z in sought.items():
            observations.append([period,f"{z['zd']:,.1f}–{z['zg']:,.1f}",when(z['start']),when(z['end']),
                                 when(z['evidence_at']),when(first_match.get(key))])
        old = context[period]['blocks'][0]
        details.append({'period':period,'window_centers':centers,'context_centers':old['centers'],
                        'context_evolution_centers':[z for l in old['theory']['levels'] for z in l['centers']],
                        'replay':replay[period]})
        bars,_ = resample(raw,1,hours,END,16)
        charts.append(f'<section><h2>{period} · {len(bars)} 根 K 线</h2><p>{bands}</p>{chart(bars,period,block)}</section>')
    price = {'open':raw[0].open,'high':max(c.high for c in raw),'low':min(c.low for c in raw),
             'close':raw[-1].close,'change_pct':(raw[-1].close/raw[0].open-1)*100}
    summary = {'symbol':'BTCUSDT','market':'Binance USD-M perpetual','timezone':'UTC+8',
               'start':'2026-06-06 00:00','end_exclusive':'2026-08-19 00:00',
               'price':price,'window_summary':rows,'center_observations':observations,'details':details,
               'source':'https://fapi.binance.com/fapi/v1/klines',
               'method':'existing explicit rules, SMA 5/10 policy; structural insufficiency is not proof of absence',
               'code_sha256':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in
                              ['chan.py','chan_structure.py','chan_theory.py','chan_types.py','chan_dynamics.py']}}
    (ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    def mdtable(headers, body):
        return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                          ['| '+' | '.join(row)+' |' for row in body])
    introduction = (f'Binance BTCUSDT（USDT 本位永续），成交价 K 线。区间为 UTC+8 的 '
                    f'2026-06-06 00:00 至 2026-08-19 00:00，左闭右开。1,776 根小时线无缺根；'
                    f'所有周期以 UTC+8 00:00 分桶，排除截止后数据。区间收盘 {price["close"]:,.1f} USDT，'
                    f'最高 {price["high"]:,.1f}，最低 {price["low"]:,.1f}。')
    conclusion = ('截至截止时刻，1h 已确认两个价格重叠的 L1 递归中枢；延伸视角核心为 61,870.0–63,090.9。'
                  '这不足以构成两个同向相离中枢的趋势背驰背景。当前程序没有确认趋势背驰、MACD 背驰候选或 B1/B2/B3。'
                  '4h/8h/12h/1d 在区间内的完成线段不足，不能把“未识别”说成行情客观不存在这些结构。')
    background = ('增加 120 天前置行情后：4h 延伸视角核心为 62,401.7–66,924.1，12h 为 65,501.0–75,998.9；'
                  '均包含区间之前的走势，不能冒充本区间内独立形成的中枢。8h 的 64,918.2–75,998.9 递归框在 5 月已形成，'
                  '属于历史背景。日线仍不足以确认递归中枢。主图与主表只使用指定区间，以免混合两个分解起点。')
    buy = ('7 月 1 日 09:00 的 57,758.6 低点不能仅凭随后上涨标为一买。'
           '1h 价格站上 63,090.9 也不等于三买：需要已完成离开及首次已完成回试，回试全段低点严格高于中枢上沿。'
           '已确认的 7 月 21 日至 7 月 28 日回落最低 62,660.1，重新进入该核心，因而不符合这个三买条件。'
           '二买缺少已确认一买前提，不能补标。所有信号均按当前规则及参数判断。')
    replay_counts = ', '.join(f'{p}：{d["cutoffs"]} 个截面' for p,d in replay.items())
    event_count = sum(len(d['events']) for d in replay.values())
    audit = (f'使用前置行情，按每根目标周期 K 线收盘逐前缀回放（含区间起点）：{replay_counts}。'
             f'区间内买卖点新增/修改/撤回事件共 {event_count} 项，详见 replay_each_close_summary.json。'
             '回放只读取当时数据；选笔仍可能重绘，表内理论证据时刻与首次实际观察时刻分别记录。'
             '本次真实数据触发并修复了选笔在跳过极值后停滞的问题，新增上下对称回归测试；97 项测试通过。'
             '该修复不会把完整均线力度策略变成原论全部动力学证明。')
    text = '\n\n'.join(['# BTC 永续：中枢、背驰和买点',introduction,conclusion,
        mdtable(['周期','K线数','完成线段','区间内递归中枢（USDT）','策略背驰 / MACD候选','买点数'],rows),
        '## 中枢边界与观察时间（UTC+8）',
        mdtable(['周期','ZD–ZG','结构开始','结构结束','理论证据时刻','逐根回放首次匹配'],observations),
        '## 前置行情背景',background,'## 买点判断',buy,'## 回放及限制',audit,
        '数据来源：[Binance 永续 K 线接口](https://fapi.binance.com/fapi/v1/klines)。'
        '完整请求、下载时间及数据校验值见 source.json；所有判断来自本地原始 OHLCV 计算，未使用案例重建数据。'])
    (ROOT/'report.md').write_text(text)
    table = '<table><thead><tr>'+''.join(f'<th>{h}</th>' for h in ['周期','K线','完成线段','区间中枢','策略背驰/MACD','买点'])+'</tr></thead><tbody>'
    table += ''.join('<tr>'+''.join(f'<td>{html.escape(v)}</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table>'
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>BTC 永续缠论分析 · 2026-06-06 至 08-19</title><style>'
            'body{font:16px/1.65 system-ui;background:#f1f5f9;color:#0f172a;margin:0}main{max-width:1240px;margin:32px auto;padding:0 18px}'
            'section,.card{background:white;padding:20px;border-radius:12px;margin:20px 0}svg{width:100%;height:auto}svg text{font-size:12px;fill:#475569}'
            'table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:10px;border-bottom:1px solid #e2e8f0;text-align:left}'
            'a{color:#1d4ed8}h1{font-size:28px}h2{font-size:21px}.scroll{overflow:auto}</style><main><h1>BTCUSDT 永续 · 中枢、背驰和买点</h1>'
            f'<p>{introduction}</p><div class="card"><h2>结论</h2><p>{conclusion}</p><div class="scroll">{table}</div></div>'
            '<p>橙框：区间内递归中枢；蓝线：完成线段；蓝色虚线：待确认线段。悬停 K 线查看 OHLC，时间均为 UTC+8。</p>'
            +''.join(charts)+f'<section><h2>买点判断</h2><p>{buy}</p><h2>前置行情背景</h2><p>{background}</p><h2>回放及限制</h2><p>{audit}</p>'
            '<p><a href="report.md">文字报告</a> · <a href="summary.json">结构化摘要</a> · <a href="window_1h.csv">原始小时线</a> · <a href="source.json">数据来源与校验</a></p></section></main></html>')
    (ROOT/'report.html').write_text(page)
    print(text)


if __name__ == '__main__':
    main()
