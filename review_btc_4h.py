"""冻结行情复核：图表周期、结构来源及精确端点分别记录。"""
import csv
import json
from collections import Counter
from dataclasses import asdict
from datetime import timedelta, timezone
from pathlib import Path

from chan import analyze, json_default, parse_time, read_csv, resample
from chan_structure import Movement
from chan_theory import evolving_centers
from chart_buy_points import stroke_buy_annotations

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / 'reports/BTCUSDT_4h_20260606_current_20260926'
OUT = ROOT / 'reports/BTCUSDT_4h_recheck_20260926'
START = parse_time('2026-06-05T16:00:00+00:00')
TZ = timezone(timedelta(hours=8))
# 用户明确给出的参考图一买；用于定位，不伪装为算法背驰确认。
FIRST_REFERENCES = [dict(time=parse_time('2026-06-06T00:00:00+08:00'), price=59080.0,
                        source='/home/ubuntu/btc-4h.png',
                        note='用户指定第一个蓝色1为20260606；匹配现有4h笔端点')]


def local(t):
    return (parse_time(t) if isinstance(t, str) else t).astimezone(TZ).strftime('%Y-%m-%d %H:%M')


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_default))


def nodes_for(report, stroke_zones=False):
    result = []
    for bi, b in enumerate(report['blocks']):
        for kind, items in [('stroke', b['strokes']), ('segment', b['segments'] +
                            ([b['pending_segment']] if b['pending_segment'] else []))]:
            for i, s in enumerate(items):
                done = not s.provisional if kind == 'stroke' else s.confirmed
                result.append(dict(id=f'{bi}:{kind}:{i}', kind=kind, level=-1 if kind == 'stroke' else 0,
                                   start=s.start.time, end=s.end.time, start_price=s.start.price,
                                   end_price=s.end.price, state='completed' if done else 'pending',
                                   evidence_at=s.confirmed_at if kind == 'stroke' else s.evidence_at))
        if stroke_zones:
            children = [Movement(f'P{i}', 0, 'stroke_proxy', s.start, s.end, s.low, s.high,
                                 (), (), s.confirmed_at) for i, s in enumerate(b['strokes']) if not s.provisional]
            centers = evolving_centers(children, 1)['centers']
        else:
            centers = [z for layer in b['theory']['type_hierarchy']['levels'] for z in layer['centers']]
        for z in centers:
            result.append(dict(id=f'{bi}:{z.id}', kind='center', level=z.level, start=z.start, end=z.end,
                               zd=z.zd, zg=z.zg, state=z.status, evidence_at=z.evidence_at,
                               scope='stroke_overlap_observation' if stroke_zones else 'completed_segment_type'))
    return [n for n in result if n['end'] >= START]


def main():
    OUT.mkdir(exist_ok=True)
    metadata = json.loads((SOURCE / 'source.json').read_text())
    asof = parse_time(metadata['as_of'])
    raw = read_csv(SOURCE / 'history_1m.csv')
    reports, bars_by_period = {}, {}
    for hours in (1, 4):
        bars, quality = resample(raw, 1 / 60, hours, asof)
        bars_by_period[hours] = bars
        reports[hours] = analyze(bars, hours)
        save(f'analysis_{hours}h.json', reports[hours])
    closed = [asdict(c) for c in bars_by_period[4] if c.time >= START]
    last_close = bars_by_period[4][-1].time + timedelta(hours=4)
    tail = [c for c in raw if last_close <= c.time < asof]
    bars = closed[:]
    if tail:
        bars.append(dict(time=last_close, open=tail[0].open, high=max(c.high for c in tail),
                         low=min(c.low for c in tail), close=tail[-1].close,
                         volume=sum(c.volume for c in tail), partial=True))
    low = min((c for c in raw if START <= c.time < asof), key=lambda c: c.low)
    anchors = []
    payloads = {}
    buy_annotations, annotation_centers = [], {}
    for bi, block in enumerate(reports[4]['blocks']):
        points, centers = stroke_buy_annotations(block, bi, FIRST_REFERENCES)
        buy_annotations.extend(p for p in points if START <= p['time'] < asof)
        annotation_centers.update(centers)
    for scope, hours, proxy in [('4h', 4, False), ('1h', 1, False), ('4h_strokes', 4, True)]:
        report = reports[hours]
        nodes = nodes_for(report, proxy)
        signals = []
        if not proxy:
            for bi, b in enumerate(report['blocks']):
                for layer in b['theory']['strict_divergence']['levels']:
                    signals.extend(asdict(s) for s in layer['signals'] if s.time >= START)
                for i, p in enumerate(b['theory']['second_retest_positions']):
                    if p['time'] < START:
                        continue
                    s = {**p, 'id': f'{scope}:{bi}:retest:{i}', 'center_id': None}
                    # 原始 K 线极值可能被方向性去包含过滤；单独审计，禁止把
                    # 结构极值冒称原始市场极值。整根端点桶纳入检查，结果保守。
                    part = [c for c in raw if p['origin_time'] <= c.time < p['time'] + timedelta(hours=hours)]
                    buy = p['label'] == 'B2'
                    extreme = min(c.low for c in part) if buy else max(c.high for c in part)
                    holds = extreme >= p['origin_price'] if buy else extreme <= p['origin_price']
                    s['raw_path_holds_origin'] = holds
                    s['raw_path_extreme'] = extreme
                    s['analysis_period'] = f'{hours}h'
                    bucket = [c for c in raw if p['time'] <= c.time < p['time'] + timedelta(hours=hours)]
                    hits = [c.time for c in bucket if (c.low if buy else c.high) == p['price']]
                    s['exact_price_minutes'] = hits
                    anchors.append(s)
                    if holds:
                        signals.append(s)
        if hours == 4:
            signals.extend(buy_annotations)
        signals.sort(key=lambda s: (s['time'], s['label']))
        counts = dict(Counter(n['kind'] for n in nodes))
        summary = dict(counts=counts, closed_4h_bars=len(closed), partial_4h_minutes=len(tail),
                       history=dict(status='frozen_data_recheck', reasons=[]),
                       structure_source=scope, raw_window_low=dict(time=low.time, price=low.low))
        payloads[scope] = dict(start=START, asof=asof, bars=bars, nodes=nodes, signals=signals, summary=summary,
                               annotation_centers=annotation_centers if hours == 4 else {})
    save('buy_annotations.json', dict(signals=buy_annotations, centers=annotation_centers,
                                    first_references=FIRST_REFERENCES,
                                    scope='chart_annotations_only_not_trading_confirmation'))
    save('comparison.json', {k: v['summary'] for k, v in payloads.items()})
    save('retest_positions.json', anchors)
    save('source.json', {**metadata, 'recheck_uses_frozen_data': True})
    with (OUT / 'retest_positions.csv').open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['结构来源', '类型', '端点K线_北京', '价格', '分钟定位_北京', '证据时间_北京', '原始价格守住起点', '性质'])
        for p in anchors:
            w.writerow([p['analysis_period'], p['label'], local(p['time']), p['price'],
                        ';'.join(local(t) for t in p['exact_price_minutes']), local(p['evidence_at']),
                        p['raw_path_holds_origin'], '二类点几何位置，转折/背驰证据待补'])
    with (OUT / 'signals.csv').open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['视图', '类型', '端点K线_北京', '价格', '结构证据时间_北京', '性质', '标注依据', '中枢ID'])
        for scope, payload in payloads.items():
            for s in payload['signals']:
                w.writerow([scope, s['label'], local(s['time']), s['price'], local(s['evidence_at']),
                            s['certainty'], s.get('annotation_basis', scope+'完成走势'), s.get('center_id')])
    template = (SOURCE / 'chart_template.html').read_text()
    replacements = {
        '1m 递归基底 → 4h 观察窗口': '4h 复核 · 结构来源可切换',
        '窗口相交中枢': '中枢 / 笔重叠观察区',
        '窗口内结构买卖点': '结构信号 / 回试位置',
        '全区间分钟结构较密集，可缩放或关闭笔图层。': '蓝色数字为4h笔级买点位置；空心为参考/依赖标注，点击明细查看来源及中枢。',
        '所有笔、段、中枢和信号来自同一棵修复后的完成走势树。4h 只改变展示蜡烛，结构端点保留分钟精度；L1 / L2 不等于 4h 等时间周期。':
            '笔段保持原有划分。默认4h视图增加笔级买点位置：一买引用用户参考图，二买取其后首次已完成反弹与回试，三买按笔重叠中枢的离开与首次回试计算。点击买点可检查对应中枢边界；这些笔级标注不等于严格背驰确认。1h对照采用独立结构。',
        '这是截止时刻的结构快照，没有执行逐分钟前缀回放。确认后的结构仍可随新证据修订；未满足规则的一类点不补造，二类点仍是依赖候选。图中历史买卖点不代表当前同时有效的交易指令。':
            f'原始区间低点：北京时间 {local(low.time)}，{low.low:,.1f}。这是价格定位，尚不能仅凭最低价认定一买。二类点位置独立扫描首次反弹后的已完成回试，并复查原始价格是否破低/破高。证据时刻是快照依赖时间，未做逐根回放。',
        'unified/analysis.json': 'analysis_4h.json',
        'structures.csv': 'comparison.json',
        "const zs=ns.filter": "const ref=D.summary.raw_window_low,rt=Date.parse(ref.time);if(rt>=left&&rt<right){ctx.strokeStyle='#344861';ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(x(rt)-5,y(ref.price));ctx.lineTo(x(rt)+5,y(ref.price));ctx.moveTo(x(rt),y(ref.price)-5);ctx.lineTo(x(rt),y(ref.price)+5);ctx.stroke();ctx.fillStyle='#344861';ctx.fillText('原始低点 '+num(ref.price)+' · 一买性质待核查',Math.max(L+4,Math.min(R-260,x(rt)+8)),y(ref.price)+24)} const zs=ns.filter",
        "const certainty={": "const certainty={second_retest_geometry:'回试位置·待证',",
        "const D=JSON.parse(document.getElementById('data').textContent),$=s=>document.getElementById(s),DAY=86400000,PERIOD=14400000;":
            "const ALL=JSON.parse(document.getElementById('data').textContent),scope=new URLSearchParams(location.search).get('scope')||'4h',D=ALL[scope]||ALL['4h'],$=s=>document.getElementById(s),DAY=86400000,PERIOD=14400000; $('scope').value=scope; $('scope').onchange=()=>{location.search='?scope='+$('scope').value};",
        '<div class="toolbar"><span>显示范围</span>': '<div class="toolbar"><label>结构来源 <select id="scope"><option value="4h">4h 笔段 / 完成走势中枢</option><option value="1h">1h 笔段 / 中枢对照</option><option value="4h_strokes">4h 笔重叠观察区（代理）</option></select></label><span>显示范围</span>',
    }
    for before, after in replacements.items():
        if before not in template:
            raise ValueError(f'模板文本未找到：{before}')
        template = template.replace(before, after)
    template = template.replace("second_retest_geometry:'回试位置·待证',",
                                "reference_annotation:'参考图定位',reference_dependent:'首次回试·依赖参考一买',weak_retest_pending:'弱回试·待证',stroke_structural:'笔中枢结构',second_retest_geometry:'回试位置·待证',")
    template = template.replace("D.nodes.filter(n=>n.kind.startsWith('center')).map(n=>n.level)",
                                "[...D.nodes.filter(n=>n.kind.startsWith('center')),...D.signals].map(n=>n.level)")
    template = template.replace("col=buy?'#008561':'#d23d61'", "col=buy?'#1676d2':'#d23d61'")
    template = template.replace("['structural','policy_confirmed'].includes(s.certainty)",
                                "['structural','policy_confirmed','stroke_structural'].includes(s.certainty)")
    template = template.replace("ctx.fillText('L'+s.level+' '+s.label,cx,cy+(buy?17:-11))",
                                "ctx.fillText(s.annotation_basis?s.label.slice(1)+(s.certainty==='reference_annotation'?'·参考':''):'L'+s.level+' '+s.label,Math.max(L+28,Math.min(R-28,cx)),cy+(buy?17:-11))")
    template = template.replace("const buy=s.label.startsWith('B'),cx=x(s.t),py=y(s.price)",
                                "const buy=s.label.startsWith('B'),cx=Math.max(L+5,Math.min(R-5,x(s.t))),py=y(s.price)")
    template = template.replace("names[s.label]+' '+s.label,'L'+s.level,fmt(s.t)",
                                "names[s.label]+' '+s.label,s.annotation_basis||('L'+s.level),fmt(s.t)")
    template = template.replace("names[s.label]+' '+s.label+' · L'+s.level+' · '",
                                "names[s.label]+' '+s.label+' · '+(s.annotation_basis||('L'+s.level))+' · '")
    template = template.replace("JSON.stringify(s,null,2)",
                                "JSON.stringify({...s,referenced_center:(D.annotation_centers||{})[s.center_id]||null},null,2)")
    template = template.replace('B3 / S3 为结构三类点，端点时刻与证据时刻分开记录。',
                                '参考一买、首次回试二买、笔中枢三买均注明来源；端点时刻与结构证据时刻分开记录。')
    template = template.replace('绿色买点', '蓝色买点')
    template = template.replace('--buy:#008561', '--buy:#1676d2')
    (OUT / 'btc_4h.html').write_text(template.replace('PAYLOAD_JSON', json.dumps(payloads, ensure_ascii=False,
                                  default=json_default, separators=(',', ':')).replace('<', '\\u003c')))
    counts = {k: v['summary']['counts'] for k, v in payloads.items()}
    lines = ['# 4h 中枢与买点标注', '', '[打开修订对照图](btc_4h.html)', '',
             f'使用原图同一批冻结行情，截止北京时间 {local(asof)}；没有追加行情。', '',
             '原图的 115 个中枢全部来自 1m 基底，不能作为 4h 级别中枢数量。修订图默认按 4h K 线自身计算；不同来源必须显式切换。', '',
             '| 来源 | 窗口相交笔 | 窗口相交段（含待确认） | 中枢 / 观察区 |', '| --- | ---: | ---: | ---: |']
    for k, c in counts.items():
        lines.append(f"| {k} | {c.get('stroke', 0)} | {c.get('segment', 0)} | {c.get('center', 0)} |")
    lines += ['', '## 当前图中的买点', '',
              '笔段坐标保持不变；补接此前遗漏的4h笔级标注，不受严格完成走势信号零输出的影响。', '',
              '| 类型 | 端点K线（北京） | 价格 | 来源 |', '| --- | --- | ---: | --- |']
    for s in buy_annotations:
        basis = {'reference_annotation': '用户参考图一买；非算法背驰确认',
                 'reference_dependent': '参考一买后首次已完成反弹/回试',
                 'weak_retest_pending': '首次回试创新低；弱二买待证',
                 'stroke_structural': '4h笔重叠中枢离开后的首次回试'}[s['certainty']]
        lines.append(f"| {s['label']} | {local(s['time'])} | {s['price']:,.1f} | {basis} |")
    lines += ['', '[买点依据与引用中枢](buy_annotations.json) · [当前图表信号CSV](signals.csv)', '',
              '以下为严格完成走势及原始价格路径的另一路审计，不用于隐藏上表的参考图/笔级位置。', '',
              '4h_strokes 是笔重叠观察代理，不能与完成走势中枢混计。4h 严格通路为零，只能说明本实现未给出窗口内递归中枢，不能断言市场没有中枢。1h 对照也不自动等于 4h 级别。', '',
              '## 一买位置核查', '',
              f'区间真实最低价是 **{local(low.time)}，{low.low:,.1f}**（1m K 线定位）。1h 线段保留了此低点；4h 方向性去包含滤掉了这个低价，4h 下行段端点为 6 月 25 日 20:00 的 58,030。不能把这个结构端点当作真实最低价，也不能把最低价本身当作一买证明。', '',
              '当前一买确认通路还要求 c 内部同向中枢及完整均线力度波；最低线段层没有内部证据。这个实现限制会导致零输出，零输出不等于市场不存在一买。本次没有将待证底部改为严格确认一买。', '',
              '## 二买位置核查', '',
              '补上不依赖本级一买输出的首次回试位置扫描。连续已完成的下降—反弹—回试且不破起点极值时记录位置，尚缺转折/低级别背驰证据时保留待证，不放入自动交易候选。', '',
              '| 结构来源 | 类型 | 端点 K 线（北京） | 价格 | 分钟精确定位（北京） | 原始价格路径校验 |', '| --- | --- | --- | ---: | --- | --- |']
    for p in anchors:
        if p['label'] == 'B2':
            lines.append(f"| {p['analysis_period']} | 二买回试位置 | {local(p['time'])} | {p['price']:,.1f} | {' / '.join(local(t) for t in p['exact_price_minutes'])} | {'通过，转折性质待证' if p['raw_path_holds_origin'] else '不通过，不在图中显示'} |")
    lines += ['', '与 7 月 1 日低点相连的 **1h** 首次反弹端点为 7 月 21 日，第一次已完成回试端点为 **7 月 28 日的 62,660.1**。这是该 1h 结构的具体位置，不能跨级别直接标成 4h 二买。4h 通路给出的 8 月 2 日 00:00、62,228.8 回试，起点仍引用 6 月 25 日；其后原始行情已在 7 月 1 日破低，因此这条不破低几何解释被排除。', '',
              '位置时间、快照证据时间和首次实时可知时间分别处理；本次没有运行完整前缀回放，不能将回溯端点当成当时可成交信号。', '',
              '依据：[第 53 课](https://chanlun108.cn/chanzhongshuochan108ke/53.html)说明小转大情况下二类点可以不依赖本级一类点；[第 101 课](https://chanlun108.cn/chanzhongshuochan108ke/101.html)说明首次回试端点及弱二买可能创新低。新增独立扫描仅覆盖不破极值的几何分支，弱二买仍需额外背驰证据。', '',
              '[完整回试审计](retest_positions.json) · [回试审计 CSV](retest_positions.csv) · [4h 分析](analysis_4h.json) · [1h 分析](analysis_1h.json) · [数量对照](comparison.json)']
    (OUT / 'result.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(counts, ensure_ascii=False))
    print('raw low', local(low.time), low.low)
    print('output', OUT)


if __name__ == '__main__':
    main()
