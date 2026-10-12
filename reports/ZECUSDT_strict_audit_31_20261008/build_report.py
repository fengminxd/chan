"""Publish causal audit labels without changing the original strategy or prices."""
from pathlib import Path
from collections import Counter
import csv
import io
import json
import hashlib

P = Path(__file__).resolve().parent
SOURCE = P.parent / 'ZECUSDT_nested_1h_fixed_20261008'


def run():
    groups = json.loads((P/'audit_groups.json').read_text())
    manifest = json.loads((P/'audit_manifest.json').read_text())
    counts = manifest['counts']
    rows = []
    for g in groups:
        e, first = g['original'], g['first']
        confirmed = first['execution_confirmed']
        lower = '；'.join(f"{x['period']} {x['signal']['label']} L{x['signal']['level']}"
                         for x in first['execution_matches']) if confirmed else '未确认'
        row = dict(编号=g['number'], 旧编号='/'.join(map(str,e['old_group_ids'])),
                   方向=e['side'], 主级别候选=e['labels'],
                   候选时间=e['candidate_time_bj'], 候选极值=e['candidate_price'],
                   原触发时间=e['triggered_at_bj'], 原触发收盘=e['observed_close'],
                   主级别严格确认='已确认' if first['main_confirmed'] else '未确认',
                   低级别严格确认=lower, 原分型执行周期=first['original_execution_period'],
                   原分型突破='已触发', 路径=e['path'],
                   审核分类='执行结构确认（指定政策）' if confirmed else '候选／仅分型突破',
                   后续已记录触发确认='有' if g['any_recorded_execution_confirmed'] else '无')
        for period in ('15m','5m','1m'):
            row[period+'证据'] = first['frame_proofs'][period]['reason']
        rows.append(row)
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    csv_text = stream.getvalue()
    (P/'audit_31.csv').write_text('\ufeff'+csv_text)
    md = [
        '# ZEC 永续31组：候选与严格确认结构复核', '',
        f"原31组的首次触发：低级别严格一/二类点确认 **{counts['execution_confirmed']}组**，"
        f"未通过确认、保留候选 **{counts['candidate_only']}组**；同价同位的主级别标签确认 **{counts['main_confirmed']}组**。",
        f"已覆盖全部{counts['records']}条原始触发记录、{counts['snapshots']}个历史截止时刻、"
        f"{counts['period_snapshots']}份1h/15m/5m/1m结构快照。"
        f"首次未确认但在后续已记录触发时确认的组数：{counts['later_recorded_confirmation']}。", '',
        '**“未确认”指当时证据不足，不等于不存在其他缠论解释，也不按事后盈亏判定。候选为历史状态，不表示当前仍有效。**', '',
        '## 审核口径', '',
        '- 固定原始分钟数据和历史起点；每次只输入原触发时刻已经收盘的K线。后来的端点修订或确认不得回填。',
        '- 主级别仍允许为候选；其自身确认状态另列，不以“1h未确认”直接否决低级别执行。',
        '- 一类点要求已完成走势来源、结构审计和力度审计全部通过，使用现有`confirmed_execution_signals`证据链。',
        '- 二类点必须引用本级已确认一类点，并有首尾连续、已完成的反弹/回试结构；几何不破前低/前高不计确认。',
        '- 对1h逐级路线检查15m、5m、1m的一买/二买或一卖/二卖；原来停在15m或5m的，也继续检查剩余层级，但缺中间包含路径不能跳级。',
        '- 信号方向、端点价格、端点所在周期柱必须对应本组同一原始分钟极值，且证据时间不晚于该次触发；不借用附近无关信号。',
        '- 三类结构另存证据，但不擅自替代本次要求的低级别一/二类执行点；主级别三类标签则核对完成走势树的三类点。',
        '- 力度采用项目指定`cross_kiss_sma(5,10)`及标准内部趋势分支。递归层级L与15m/5m等时间周期分别记录；这不是对所有缠论动力学定义的普遍证明。',
        '- 全部原稳定分型和触发收盘价重新核对；“分型已确认”“突破已触发”“买卖点结构已确认”分列。', '',
        '## 31组逐项结果（北京时间，2026年）', '',
        '|编号|方向／1h候选|候选极值|原触发时间|原触发收盘|原分型周期|1h严格确认|低级别严格确认|',
        '|---|---|---:|---|---:|---|---|---|'
    ]
    for r in rows:
        md.append(f"|#{r['编号']}|{r['方向']} {r['主级别候选']}|{r['候选极值']:.2f}|"
                  f"{r['原触发时间']}|{r['原触发收盘']:.2f}|{r['原分型执行周期']}|"
                  f"{r['主级别严格确认']}|{r['低级别严格确认']}|")
    md += ['', '## 每组缺失证据', '']
    for g, r in zip(groups, rows):
        e, first = g['original'], g['first']
        main_reasons = '；'.join(dict.fromkeys(
            x['label']+'：'+x['candidate_details']['missing'] for x in g['records']
            if x['as_of'] == first['as_of']))
        md += [f"### #{g['number']} {e['side']} {e['observed_close']:.2f}", '',
               f"主级别：{e['labels']}候选；{main_reasons}。",
               f"原分型路径：{e['path']}；审核可达路径："+
               '→'.join(x['period'] for x in first['audited_route'])+'。', '']
        for period in ('15m','5m','1m'):
            proof = first['frame_proofs'][period]
            failed = sorted({c['rule'] for a in proof['divergence_audits_at_target']
                             for c in a['checks'] if c['state'] != 'pass'})
            extra = ('；缺失/不通过项：'+', '.join(failed)) if failed else ''
            thirds = proof['structural_third_matches']
            extra += ('；另有三类结构：'+','.join(s['label'] for s in thirds)) if thirds else ''
            md.append(f"- {period}：{proof['reason']}{extra}。")
        snapshot = first['as_of'].replace('-','').replace(':','')[:13]+'Z.json'
        md += ['', f"已复核本组{len(g['records'])}条原始触发记录。"
               f"[首次触发证据](snapshots/{snapshot})。", '']
    md += ['## 阅读图表', '',
           '买向保持蓝色、卖向保持橙色。空心箭头及“候”表示候选／仅分型突破；实心箭头及“确”表示低级别严格执行结构通过。'
           '顶部统计、表格和详情分别显示主候选、严格确认状态及原突破事实。确认仅指原触发时刻可见状态，极值时间不是确认时间。', '',
           '触发收盘价不冒充买卖点端点或成交价。1h笔仍是全区间截止快照；粗笔3.2仍只标发起direct_break的笔，保留颜色和实虚线。', '',
           '[打开TradingView图](zec_strict_audit_tradingview.html) · [31组CSV](audit_31.csv) · '
           '[全部审核证据](audit_groups.json) · [数据与规则指纹](audit_manifest.json)', '',
           '本次新增审核和图表标注，未修改核心策略或改写原始31组触发记录。']
    (P/'result.md').write_text('\n'.join(md)+'\n')
    data = json.loads((SOURCE/'chart_data.json').read_text())
    data['csv'] = csv_text
    data['audit_counts'] = counts
    for e, g in zip(data['entries'], groups):
        assert e['number'] == g['number'] and e['observed_close'] == g['original']['observed_close']
        first = g['first']
        e['audit'] = dict(status=g['status'], confirmed=first['execution_confirmed'],
                          main_confirmed=first['main_confirmed'],
                          text='执行结构确认（指定政策）' if first['execution_confirmed'] else '候选／仅分型突破',
                          execution_period=first['original_execution_period'],
                          confirmations=[dict(period=x['period'], signal=x['signal']) for x in first['execution_matches']],
                          reasons={p:first['frame_proofs'][p]['reason'] for p in ('15m','5m','1m')},
                          recorded_triggers=len(g['records']),
                          later_confirmed=g['any_recorded_execution_confirmed'],
                          details_file='snapshots/'+first['as_of'].replace('-','').replace(':','')[:13]+'Z.json')
    payload = json.dumps(data, ensure_ascii=False, separators=(',',':')).replace('</','<\\/')
    bundle = (SOURCE/'vendor/lightweight-charts-5.0.4.js').read_text()
    html = (P/'tradingview_template.html').read_text()
    for key, value in dict(LIBRARY_JS=bundle, PAYLOAD_JSON=payload, TOTAL_GROUPS=str(len(groups)),
                           BUY_GROUPS=str(sum(e['side']=='买' for e in data['entries'])),
                           SELL_GROUPS=str(sum(e['side']=='卖' for e in data['entries'])),
                           CONFIRMED_GROUPS=str(counts['execution_confirmed']),
                           CANDIDATE_GROUPS=str(counts['candidate_only'])).items():
        html = html.replace(key,value)
    (P/'zec_strict_audit_tradingview.html').write_text(html)
    (P/'chart_data.json').write_text(json.dumps(data, ensure_ascii=False))
    chart_manifest = dict(groups=len(groups), counts=counts, direct_break_width=3.2,
                           source_strokes_unchanged=data['strokes']==json.loads((SOURCE/'chart_data.json').read_text())['strokes'],
                           library='TradingView Lightweight Charts 5.0.4', offline_single_file=True,
                           html_sha256=hashlib.sha256(html.encode()).hexdigest())
    (P/'chart_manifest.json').write_text(json.dumps(chart_manifest,ensure_ascii=False,indent=2))
    print(json.dumps(chart_manifest,ensure_ascii=False))


if __name__ == '__main__':
    run()
