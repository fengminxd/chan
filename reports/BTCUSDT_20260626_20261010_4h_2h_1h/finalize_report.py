"""Add reviewable explanations and publish the frozen three-period report."""
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from chan import parse_time
from report_btc_4h_auto import local, dump

F=Path(__file__).resolve().parent
NAMES=dict(B1='一买',B2='二买',B3='三买',S1='一卖',S2='二卖',S3='三卖')
STATUS={'macd_candidate':'MACD辅助候选','dependent_candidate':'依赖一类候选，未确认',
        'second_retest_geometry':'首次回试几何，待证','structural':'完成走势结构点',
        'stroke_structural':'笔中枢结构点','policy_confirmed':'指定策略确认','policy_dependent':'依赖策略信号'}
BASIS={'types':'完成走势中枢','strokes':'笔中枢对照'}


def signal_reason(s):
    d=s.get('details',{})
    if s['label'].endswith('3'):
        if s['label']=='B3':
            return f"向上离开中枢后首次回试低点 {d['return_low']:,.1f} 高于原中枢上沿 {d['zg']:,.1f}。"
        return f"向下离开中枢后首次反抽高点 {d['return_high']:,.1f} 低于原中枢下沿 {d['zd']:,.1f}。"
    if s['certainty']=='second_retest_geometry':
        return f"首次回试未越过原点 {d['origin_price']:,.1f}（{local(d['origin_time'])}）；仍缺原趋势终结或次级背驰证据。"
    if s['certainty']=='dependent_candidate':
        return f"跟随一类候选后的首次回试；一类参考价 {d['first_price']:,.1f}，前提仍是候选，二类也未确认。"
    return '来自趋势背驰的MACD辅助比较；不以柱面积缩小单独证明严格一类点。'


def div_reason(d):
    side='顶' if d['side']=='up' else '底'
    kind='趋势背驰' if d['kind']=='trend' else '盘整背驰'
    return (f"{side}{kind}辅助候选。A段 {local(d['a_start'])} → {local(d['a_end'])}，"
            f"C段 {local(d['start'])} → {local(d['end'])}；同向MACD柱面积 "
            f"{d['a_area']:.2f} → {d['c_area']:.2f}，C/A={d['area_ratio']:.2%}，"
            f"缩小 {1-d['area_ratio']:.2%}，零轴回归条件通过。"
            + ('程序识别同向相离中枢，允许列为趋势背驰候选。' if d['kind']=='trend' else '未满足同向相离双中枢的趋势条件，不能标为一卖/一买。')
            + '当前没有指定力度策略的严格背驰确认。')


def main():
    d=json.loads((F/'chart_data.json').read_text())
    d['source']=json.loads((F/'source.json').read_text())
    d['default_history']='full'
    d['histories']['full']['label']='2023年起（主分析）'
    d['histories']['recent']['label']='180天前置（敏感性对照）'
    general='主分析使用2023年起连续历史，显示窗口为2026-06-26起；截短历史仅作起点敏感性对照。'
    extra={
      '4h':'区间内27笔（含末笔）、2条确认段和1条候选段。8月2日起的上行候选段尚未确认结束；9月22日87,385.1的顶背驰辅助候选位于“笔中枢对照”，不是完成走势口径的一卖。',
      '2h':'区间内53笔（含末笔）、5条确认段和1条候选段。末候选段仍停留在8月28日至9月16日的结构，后续笔尚未完成线段确认；不能将这条旧候选段直接解读成当前持续下跌趋势。',
      '1h':'区间内99笔（含末笔）、14条确认段和1条候选段。9月22日87,385.1为一卖辅助候选，10月2日87,249.6为其二卖候选，证据至10月10日03:00才齐备；并非10月2日即能成交。'
    }
    for hist,h in d['histories'].items():
        for per,pd in h['periods'].items():
            if hist=='full':
                pd['analysis_notes']=[n for n in pd['analysis_notes'] if n not in (general,extra[per])]
                pd['analysis_notes'].insert(0,general)
                pd['analysis_notes'].insert(1,extra[per])
            if hist=='recent' and per=='2h':
                pd['analysis_notes']=[n for n in pd['analysis_notes'] if not n.startswith('起点敏感性异常：')]
                pd['analysis_notes'].insert(0,'起点敏感性异常：截短历史的初始笔未能延续到展示区间，本口径不用于2h行情结论；请切换2023年起主分析。空输出不等于行情没有笔段。')
            for basis,v in pd['views'].items():
                for s in v['signals']:
                    s['explanation']=signal_reason(s)
                    after=[b for b in pd['bars'] if not b.get('partial') and parse_time(b['time'])>=parse_time(s['evidence_at'])]
                    hits=[b for b in after if (b['low']<s['price'] if s['label'].startswith('B') else b['high']>s['price'])]
                    s['subsequent_endpoint_breach']=hits[0]['time'] if hits else None
                    s['followup_note']=('证据后价格曾越过该买点低价/卖点高价；历史标记保留，不是当前新触发。' if hits else '截至快照，证据后完整K线未越过该端点价格；这本身不构成当前交易触发。')
                for x in v['divergences']:
                    x['explanation']=div_reason(x)
    dump(F/'chart_data.json',d)
    for category in ('signals','structures'):
        with (F/f'{category}.csv').open('w',newline='') as f:
            w=csv.writer(f)
            if category=='signals':
                w.writerow(['历史','周期','口径','类别','层级','端点_北京','价格','证据_北京','性质','依据','证据后首次越过端点价_北京','ID'])
            else:
                w.writerow(['历史','周期','口径','类别','层级','开始_北京','结束_北京','起价/ZD','终价/ZG','状态','direct_break','破坏原因','ID'])
            for hist,h in d['histories'].items():
                for per,pd in h['periods'].items():
                    for basis,v in pd['views'].items():
                        for x in v['signals' if category=='signals' else 'nodes']:
                            if category=='signals':
                                w.writerow([hist,per,basis,x['label'],x['level'],local(x['time']),x['price'],local(x['evidence_at']),STATUS[x['certainty']],x['explanation'],local(x['subsequent_endpoint_breach']) if x['subsequent_endpoint_breach'] else '',x['id']])
                            else:
                                w.writerow([hist,per,basis,x['kind'],x['level'],local(x['start']),local(x['end']),x.get('start_price',x.get('zd')),x.get('end_price',x.get('zg')),x['state'],bool(x.get('direct_break')),x.get('direct_break_reason',''),x['id']])
    template=(F/'template.html').read_text().replace('SUIUSDT','BTCUSDT').replace('SUI 永续','BTC 永续')
    template=template.replace('近期结构（180天前置）','180天前置（敏感性对照）').replace('2023年起（长历史对照）','2023年起（主分析）')
    template=template.replace("$('detail').textContent=JSON.stringify", "$('detail').textContent=(item.explanation?item.explanation+'\\n\\n':'')+JSON.stringify")
    template=template.replace('<p class="muted">默认完成走势中枢', '<p class="muted">主图默认2023年起连续历史。默认完成走势中枢')
    template=template.replace('</main><script id="data"', '<noscript><p>浏览器未启用脚本，请查看静态图：<a href="4h_types_full.png">4h</a> · <a href="2h_types_full.png">2h</a> · <a href="1h_types_full.png">1h</a>，或打开 result.md 报告。</p></noscript></main><script id="data"')
    for per in ('4h','2h','1h'):
        pd=dict(d,default_period=per)
        encoded=json.dumps(pd,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
        (F/f'btc_{per}.html').write_text(template.replace('PAYLOAD_JSON',encoded))
        if per=='4h':(F/'btc.html').write_text(template.replace('PAYLOAD_JSON',encoded))

    p=d['histories']['full']['periods']
    bars=p['1h']['bars'];closed=[b for b in bars if not b.get('partial')]
    low=min(closed,key=lambda b:b['low']);high=max(closed,key=lambda b:b['high'])
    price=float(d['source']['ticker']['lastPrice']);begin=bars[0]['open']
    lines=['# BTCUSDT 永续：2026-06-26 至当前快照','',
      f"统计截止 **{local(d['asof'])}（北京时间）**。币安 USDT 本位永续成交价 **{price:,.1f} USDT**；报价时刻 {local(d['source']['ticker_time'])}。",'',
      '[总交互图](btc.html) · [4h图](btc_4h.html) · [2h图](btc_2h.html) · [1h图](btc_1h.html)','',
      f"窗口首根开盘 {begin:,.1f}，现价相对窗口起点 {price/begin-1:+.2%}；区间低点 **{low['low']:,.1f}（{local(low['time'])}）**，高点 **{high['high']:,.1f}（{local(high['time'])}）**。",'',
      '行情经历6月底至7月初探底、7月至8月中旬震荡、8月中旬上台阶、9月中旬回调后冲高，以及9月下旬至10月的高位回落。10月2日87,249.6未超过9月22日87,385.1，随后回落至10月9日80,344.8，目前反弹尚未越过83,499.9。结合当前笔段，应按回落后的反弹观察，不能仅凭价格上涨认定新的一买。','',
      '当前关键位置：80,344.8为最近低点；83,499.9为反弹高点；86,664.9、87,249.6及87,385.1为上方历史高点。82,500.1虽是历史1h三买端点，但其价格已经被后续行情跌破，不能继续当作完整有效的新买点。','',
      '蓝笔、橙段；虚线表示未完成。发起 direct_break 的笔保持蓝色与原实虚线，仅线宽加粗到3.2，不把整条线段加粗，也不代表段结束。蓝色1/2/3为买点，红色1/2/3为卖点，空心及问号表示候选/待证，紫色菱形表示背驰位置。','',
      '三个K线周期分别计算，不能把4h/2h/1h直接等同于缠论递归级别。完成走势中枢为主口径，笔中枢是另一种较细的观察口径。两者分别列出，不能拼接成同一条买卖序列。','',
      '**以下均为历史结构快照。端点时间是K线开盘时间，证据时间才是本次算法确认所需数据的截止；未做全区间逐根回放，不表示端点处已能实盘成交。没有输出的类别明确列为“无”，不补造点位。**','',
      '| 周期 | 笔（含末笔） | 确认段 / 候选段 | 一笔破坏笔 | 完成走势口径信号/候选 | 笔口径信号/候选 |','|---|---:|---:|---:|---:|---:|']
    for per,pd in p.items():
        v=pd['views']['types'];nodes=v['nodes'];segments=[n for n in nodes if n['kind']=='segment']
        lines.append(f"| {per} | {v['counts']['stroke']} | {sum(n['state']=='completed' for n in segments)} / {sum(n['state']=='pending' for n in segments)} | {sum(bool(n.get('direct_break')) for n in nodes)} | {len(v['signals'])} | {len(pd['views']['strokes']['signals'])} |")
    for per,pd in p.items():
        lines+=['',f'## {per}：结构与一二三买卖点','',extra[per],'',f'[交互图](btc_{per}.html) · [完成走势全区间图]({per}_types_full.png) · [笔中枢全区间图]({per}_strokes_full.png) · [最近7天]({per}_recent7d.png)','']
        pending=pd['pending_segment']
        if pending:
            lines.append(f"候选线段：{local(pending['start']['time'])} / {pending['start']['price']:,.1f} → {local(pending['end']['time'])} / {pending['end']['price']:,.1f}。尚未确认结束。")
        for basis in ('types','strokes'):
            v=pd['views'][basis]
            lines+=['',f'### {BASIS[basis]}','', '| 类别 | 数量 | 最近一个端点（北京） | 价格 | 性质 | 证据时间（北京） |','|---|---:|---|---:|---|---|']
            for label,name in NAMES.items():
                ss=[s for s in v['signals'] if s['label']==label]
                if ss:
                    s=ss[-1];lines.append(f"| {name} {label} | {len(ss)} | {local(s['time'])} | {s['price']:,.1f} | {STATUS[s['certainty']]} | {local(s['evidence_at'])} |")
                else:lines.append(f'| {name} {label} | 0 | 无 | — | 本规则未输出 | — |')
            lines+=['','全部端点与依据：','', '| 类别 | 端点（北京） | 价格 | 证据时间（北京） | 性质与依据 | 后续是否越过该端点价 |','|---|---|---:|---|---|---|']
            for s in v['signals']:
                breach=local(s['subsequent_endpoint_breach']) if s['subsequent_endpoint_breach'] else '完整K线未见'
                lines.append(f"| {NAMES[s['label']]} | {local(s['time'])} | {s['price']:,.1f} | {local(s['evidence_at'])} | {STATUS[s['certainty']]}；{s['explanation']} | {breach} |")
            if not v['signals']:lines.append('| 无 | — | — | — | — | — |')
        lines+=['',f'### {per} 背驰位置与比较依据','']
        for basis in ('types','strokes'):
            divs=pd['views'][basis]['divergences']
            if not divs:lines.append(f'- {BASIS[basis]}：区间内没有满足本程序条件的背驰输出；不将普通回调或面积缩小强行标为背驰。')
            for x in divs:
                lines.append(f"- **{BASIS[basis]}，{local(x['time'])}，{x['price']:,.1f}**：{x['explanation']} 证据时间：{local(x['evidence_at'])}。")
        lines+=['','一笔破坏清单（列出发起破坏的笔）：','', '| 起点（北京） | 起价 | 终点（北京） | 终价 | 笔状态 |','|---|---:|---|---:|---|']
        for n in pd['views']['types']['nodes']:
            if n.get('direct_break'):lines.append(f"| {local(n['start'])} | {n['start_price']:,.1f} | {local(n['end'])} | {n['end_price']:,.1f} | {n['state']} |")
    lines+=['','## 数据范围与复现','',
        '数据复核补充：展示区间内2h、4h原生OHLC与1h聚合一致。2023–2024年前置数据有5个高周期桶存在跨周期差异，重新查询交易所原接口后数值未变；保留各周期原生数据独立计算，详见 source_audit.json。','',
        '主分析沿用2023-01-01北京时间起的连续原生4h、2h、1h K线。另算180天前置历史供敏感性检查：4h和1h在窗口内的笔段及信号数量一致；2h截短历史仅生成早期初始候选笔，未能延续至展示窗口，因此不能用该空输出下行情结论。主图默认长历史，未移动起点寻找想要的信号。','',
        '未收盘桶仅用已结束分钟聚合展示，不输入笔段和买卖点计算。数据来源为币安公开期货接口，原始响应、时间戳及SHA256保存在source.json；旧数据来自已校验的本地原始历史快照。','',
        '[信号CSV](signals.csv) · [背驰CSV](divergences.csv) · [结构CSV](structures.csv) · [完整图表数据](chart_data.json) · [行情来源](source.json) · [浏览器验证](browser_validation.json)','',
        '复现当前快照：在本目录运行 `python3 build_report.py` 后运行 `python3 finalize_report.py`；重新下载需单独运行 `python3 fetch_snapshot.py`。本报告的日期、快照和人工文字分析针对本次冻结数据，刷新后须重新复核。']
    (F/'result.md').write_text('\n'.join(lines)+'\n')
    with (F/'divergences.csv').open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['历史','周期','口径','类别','端点_北京','价格','A开始_北京','A结束_北京','C开始_北京','C结束_北京','A柱面积','C柱面积','C/A','证据_北京','严格确认','说明'])
        for hist,h in d['histories'].items():
            for per,pd in h['periods'].items():
                for basis,v in pd['views'].items():
                    for x in v['divergences']:w.writerow([hist,per,basis,x['kind'],local(x['time']),x['price'],local(x['a_start']),local(x['a_end']),local(x['start']),local(x['end']),x['a_area'],x['c_area'],x['area_ratio'],local(x['evidence_at']),x['strict_divergence_confirmed'],x['explanation']])
    manifest=json.loads((F/'code_manifest.json').read_text())
    manifest['report_files_sha256']={name:hashlib.sha256((F/name).read_bytes()).hexdigest() for name in ['build_report.py','fetch_snapshot.py','finalize_report.py','audit_sources.py','verify_chart.cjs','template.html']}
    dump(F/'code_manifest.json',manifest)
    print(json.dumps(dict(asof=d['asof'],quote=price,window_low=low['low'],window_high=high['high'],default_history=d['default_history']),ensure_ascii=False))


if __name__=='__main__':main()
