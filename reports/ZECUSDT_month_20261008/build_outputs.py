"""整理月度复盘表与独立交互图；不修改策略。"""
import csv
from datetime import datetime, timedelta, timezone
from html import escape
import json
from pathlib import Path

P = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))
LABELS = {'B1':'一买候选','S1':'一卖候选','B2':'二买候选','S2':'二卖候选',
          'B3':'三买','S3':'三卖','TOP_DIV':'顶部指标背离','BOTTOM_DIV':'底部指标背离'}


def bj(t):
    return datetime.fromisoformat(t).astimezone(TZ).strftime('%m-%d %H:%M')


def main():
    summary = json.loads((P/'market_summary.json').read_text())
    all_rows, data = [], {}
    for period in ['4h','2h','1h']:
        report = json.loads((P/f'points_{period}.json').read_text())
        view = json.loads((P/f'views_{period}.json').read_text())
        data[period] = {'report':report,'view':view}
        for r in report['records']:
            s=r['latest_signal']
            weak=s.get('details',{}).get('strength')=='weak_new_extreme'
            all_rows.append(dict(period=period, view=s['view'], label=s['label'],
                endpoint_beijing=bj(s['time']), endpoint_utc=s['time'], price=s['price'],
                first_observed_beijing=bj(r['first_observed_at']),
                first_observed_utc=r['first_observed_at'], first_observed_close=r['first_observed_close'],
                latest_evidence_utc=s['evidence_at'], certainty=s['certainty'],
                present_at_end=r['present_at_end'], retractions=';'.join(map(bj,r['retractions'])),
                excluded_weak_second=weak))
    with (P/'all_points.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(all_rows[0]));w.writeheader();w.writerows(all_rows)
    lines=['# ZECUSDT 永续近30天全部复盘点位','',
           f"区间（北京时间）：{bj(summary['start'])}—{bj(summary['as_of'])}，年份2026。",'',
           '端点是事后图上位置；首次可见是逐主周期收盘回放得到的首次出现时刻。首次可见收盘价仅用于说明确认滞后，不是成交价。', '',
           '二类点是回试几何候选，一类点是MACD辅助候选；笔级三类结构、完成走势三类结构、局部指标背离分别列出。被撤回后重现仍保留撤回记录；当前仍在历史图上不代表现在仍可开仓。未进行低周期组合进场或收益回测。','']
    for period in ['4h','2h','1h']:
        lines += [f'## {period.upper()}','', '| 端点时间 | 价格 | 类别／依据 | 首次可见 | 当时收盘 | 历史状态 |','| --- | ---: | --- | --- | ---: | --- |']
        for r in sorted([x for x in all_rows if x['period']==period],key=lambda x:(x['endpoint_utc'],x['view'],x['label'])):
            basis={'strokes':'笔级','types':'完成走势','local_strength':'局部指标'}[r['view']]
            status='保留于期末结构' if r['present_at_end'] else '已撤回'
            if r['retractions']:status+='；曾撤回 '+r['retractions']
            if r['excluded_weak_second']:status='弱二类，未采用；'+status
            lines.append(f"| {r['endpoint_beijing']} | {r['price']:.2f} | {LABELS[r['label']]}／{basis} | {r['first_observed_beijing']} | {r['first_observed_close']:.2f} | {status} |")
        lines += ['']
    (P/'all_points.md').write_text('\n'.join(lines))
    encoded=json.dumps(data,ensure_ascii=False).replace('</','<\\/')
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ZECUSDT 近30天结构复盘</title><style>
body{font:15px system-ui,sans-serif;background:#0d1421;color:#e1e8f4;margin:0;padding:24px}main{max-width:1450px;margin:auto}h1{font-size:25px}p{line-height:1.7;color:#b4c2d8}button,select{background:#202e43;color:#fff;border:1px solid #58708e;border-radius:6px;padding:9px;margin:4px}svg{width:100%;height:auto;background:#111d30;border-radius:12px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:9px;border-bottom:1px solid #304157;text-align:left}a{color:#8bd6ff}.scroll{overflow:auto}.note{color:#ffd68b}#detail{min-height:60px;white-space:pre-wrap}circle{cursor:pointer}button.active{background:#306493}
</style><main><h1>ZECUSDT 永续 · 近30天结构复盘</h1>
<p id="range"></p><p class="note">端点位置是事后复盘标注，不代表当时已经知道信号。切换“首次可见”可查看主周期收盘回放中的首次出现时间与当时价格。</p>
<div id="periods"></div><select id="basis"><option value="strokes">笔级买卖点</option><option value="types">完成走势买卖点</option><option value="local_strength">局部指标背离</option></select>
<select id="mode"><option value="endpoint">端点位置（事后复盘）</option><option value="observed">首次可见（收盘参考）</option></select>
<label><input id="withdrawn" type="checkbox">包括撤回记录</label>
<p>价格灰线；上涨笔红色、下跌笔绿色；待确认笔保留虚线。“一笔破坏”发起笔宽3.2，颜色与实虚线不变，不代表线段确认结束。空心点为候选，实心点为对应结构口径三类点。</p>
<svg id="chart" viewBox="0 0 1250 550" role="img" aria-label="ZEC价格、笔与买卖点复盘"></svg><p id="detail">点击或悬停点位查看依据。</p>
<div class="scroll"><table><thead><tr><th>端点时间（北京）</th><th>价格</th><th>类别</th><th>首次可见（北京）</th><th>当时收盘</th><th>状态</th></tr></thead><tbody id="rows"></tbody></table></div>
<p><a href="all_points.md">完整点位表</a> · <a href="all_points.csv">下载CSV</a> · <a href="result.md">复盘分析</a> · <a href="source.json">行情来源</a></p></main>
<script>const DATA=__DATA__;let period='4h';const $=id=>document.getElementById(id);const names={B1:'一买候选',S1:'一卖候选',B2:'二买候选',S2:'二卖候选',B3:'三买',S3:'三卖',TOP_DIV:'顶部指标背离',BOTTOM_DIV:'底部指标背离'};
const bj=t=>new Date(t).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});const num=x=>Number(x).toFixed(2);const esc=x=>String(x).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
for(const p of Object.keys(DATA)){const b=document.createElement('button');b.textContent=p.toUpperCase();b.onclick=()=>{period=p;render()};b.dataset.p=p;$('periods').append(b)}
for(const id of ['basis','mode','withdrawn'])$(id).onchange=render;
function render(){document.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.p===period));const d=DATA[period], r=d.report, v=d.view;const basis=$('basis').value, mode=$('mode').value;
$('range').textContent=bj(r.start)+' — '+bj(r.as_of)+' ｜所有价格单位 USDT ｜图中各周期最后一根已收盘时间不同';
const rows=r.records.filter(q=>q.latest_signal.view===basis&&($('withdrawn').checked||q.present_at_end)&&q.latest_signal.details?.strength!=='weak_new_extreme').sort((a,b)=>a.latest_signal.time.localeCompare(b.latest_signal.time));
const bs=v.bars;const t0=+new Date(r.start), t1=+new Date(r.as_of);const low=Math.min(...bs.map(b=>b.low)), high=Math.max(...bs.map(b=>b.high));const pad=(high-low)*.12;const x=t=>70+(+new Date(t)-t0)/(t1-t0)*1150,y=p=>495-(p-low+pad)/(high-low+2*pad)*455;
let s='<defs><clipPath id="clip"><rect x="70" y="35" width="1150" height="460"/></clipPath></defs>';
for(let i=0;i<=5;i++){const p=low+(high-low)*i/5; s+=`<path d="M70 ${y(p)}H1220" stroke="#2b3a50"/><text x="8" y="${y(p)+4}" fill="#acbcd3" font-size="12">${num(p)}</text>`}
for(let i=0;i<=6;i++){const t=new Date(t0+(t1-t0)*i/6);s+=`<text x="${x(t)}" y="530" fill="#acbcd3" text-anchor="middle" font-size="12">${t.toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit'})}</text>`}
s+='<g clip-path="url(#clip)"><polyline fill="none" stroke="#8c9bb2" stroke-width="1" opacity=".65" points="'+bs.map(b=>`${x(b.time)},${y(b.close)}`).join(' ')+'"/>';
for(const n of v.views.strokes.nodes.filter(n=>n.kind==='stroke')){const color=n.end_price>n.start_price?'#ed7985':'#64c9a5';s+=`<path d="M${x(n.start)} ${y(n.start_price)}L${x(n.end)} ${y(n.end_price)}" fill="none" stroke="${color}" stroke-width="${n.direct_break?3.2:1.5}" ${n.state==='pending'?'stroke-dasharray="6 4"':''}><title>${n.direct_break?'一笔破坏发起笔；':''}${n.state}</title></path>`}
rows.forEach((q,i)=>{const a=q.latest_signal, t=mode==='endpoint'?a.time:q.first_observed_at,p=mode==='endpoint'?a.price:q.first_observed_close;const buy=a.label.startsWith('B')&&a.label!=='TOP_DIV';const col=buy?'#70dcb3':'#ff9aa5';const filled=['structural','stroke_structural','policy_confirmed'].includes(a.certainty);const title=names[a.label]+' '+num(a.price)+'；端点 '+bj(a.time)+'；首次可见 '+bj(q.first_observed_at)+'，当时收盘 '+num(q.first_observed_close)+'；'+a.certainty+(q.retractions.length?'；曾撤回':'');s+=`<circle data-i="${i}" cx="${x(t)}" cy="${y(p)}" r="5" fill="${filled?col:'#111d30'}" stroke="${col}" stroke-width="2"><title>${esc(title)}</title></circle><text x="${x(t)+6}" y="${y(p)+(buy?19:-9)}" fill="${col}" font-size="10">${esc(a.label)}</text>`});
s+='</g>';$('chart').innerHTML=s;$('chart').querySelectorAll('circle').forEach(c=>{c.onclick=()=>{$('detail').textContent=c.querySelector('title').textContent}});
$('rows').innerHTML=rows.map(q=>{const a=q.latest_signal;return `<tr><td>${bj(a.time)}</td><td>${num(a.price)}</td><td>${names[a.label]}<br>${esc(a.certainty)}</td><td>${bj(q.first_observed_at)}</td><td>${num(q.first_observed_close)}</td><td>${q.present_at_end?'期末仍在历史结构中':'已撤回'}${q.retractions.length?'；曾撤回':''}</td></tr>`}).join('')}
render();</script></html>'''.replace('__DATA__',encoded)
    (P/'review.html').write_text(html)
    print('wrote',len(all_rows),'rows; HTML',len(html),'characters')


if __name__ == '__main__':
    main()
