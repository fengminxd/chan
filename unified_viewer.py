"""统一树的离线交互视图；切换图表周期只改变蜡烛，不重新划分结构。"""
import json
from pathlib import Path
from chan import json_default


def write_unified_viewer(path, report):
    payload = json.dumps({**{k: report[k] for k in ("tree", "history", "views", "as_of", "source_period")},
                          "data_origin": report.get("data_origin", "caller_supplied")},
                         ensure_ascii=False, default=json_default, allow_nan=False).replace("<", "\\u003c")
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>1m 统一递归中枢</title><style>body{font:15px system-ui;color:#183044;background:#f3f7fa;margin:24px}header,section{background:white;padding:20px;margin:12px 0;border-radius:12px}h1{font-size:24px}select,button{padding:7px;margin:5px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:8px;border-bottom:1px solid #dde6ee;text-align:left;overflow-wrap:anywhere}tr:hover{background:#edf5fc;cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}canvas{width:100%;height:430px}.muted{color:#5a7183}.links button{max-width:100%;font-size:12px;overflow-wrap:anywhere}input{width:50%}</style>
<header><h1>1m 统一递归中枢</h1><p>所有视图引用同一棵 1m 走势树。切换周期不会改变结构级别、节点 ID 或完成状态。</p><p id="summary"></p>
<label>观察周期 <select id="period"></select></label><label>结构层 <select id="level"><option value="all">全部</option></select></label>
<label>显示 <select id="kind"><option value="centers">中枢</option><option value="movement">走势</option><option value="segment">基础线段</option><option value="stroke">笔</option></select></label>
<label>可见 K 线 <select id="width"><option>300</option><option selected>1200</option><option>5000</option></select></label>
<p>窗口结束位置 <input id="end" type="range" min="1" value="1"><span id="range"></span></p></header>
<section><canvas id="chart" aria-label="K 线与统一结构投影"></canvas><p class="muted">粗笔：一笔破坏，不等于线段结束；笔虚线：未完成。图形为截止时刻的结构快照；精确端点仍为 1m 时间。更大周期未收盘的桶不显示，期间已知的 1m 结构仍在树中。</p></section>
<section><h2>当前窗口的结构</h2><p id="counts"></p><table><thead><tr><th>层级</th><th>类型 / 状态</th><th>精确起点</th><th>精确终点</th><th>中枢区间 / 走势范围</th><th>证据时刻</th></tr></thead><tbody id="rows"></tbody></table></section>
<section><h2>结构依据与父子引用</h2><div class="links" id="links"></div><pre id="detail">点击表格中的结构；可沿父子引用逐级检查。</pre></section>
<section><h2>前置历史与完成条件检查</h2><pre id="history"></pre></section>
<script type="application/json" id="data">PAYLOAD</script><script>
const R=JSON.parse(document.getElementById('data').textContent),$=id=>document.getElementById(id),nodes=R.tree.nodes;
for(const p of Object.keys(R.views)){const o=document.createElement('option');o.value=p;o.textContent=p;$('period').append(o)}
for(const l of [...new Set(Object.values(nodes).filter(n=>n.level>=0).map(n=>n.level))].sort((a,b)=>a-b)){const o=document.createElement('option');o.value=l;o.textContent='L'+l;$('level').append(o)}
$('summary').textContent='基底 1m · 截止 '+R.as_of+' · '+Object.keys(nodes).length+' 个对象 · 历史状态 '+R.history.status+' · 来源 '+JSON.stringify(R.data_origin);
$('history').textContent=JSON.stringify(R.history,null,2);
function detail(n){$('detail').textContent=JSON.stringify(n,null,2);$('links').replaceChildren();
for(const [label,ids] of [['所属父走势',n.parent_id?[n.parent_id]:[]],['所属走势',n.owner_movement_id?[n.owner_movement_id]:[]],['直接子走势',n.child_ids],['内部中枢',n.center_ids]])for(const id of ids||[]){const b=document.createElement('button');b.textContent=label+' · '+id;b.onclick=()=>detail(nodes[id]);$('links').append(b)}}
function draw(){const view=R.views[$('period').value],stop=Number($('end').value),bars=view.candles.slice(Math.max(0,stop-Number($('width').value)),stop),canvas=$('chart'),ctx=canvas.getContext('2d');
canvas.width=Math.max(800,canvas.clientWidth)*devicePixelRatio;canvas.height=430*devicePixelRatio;ctx.scale(devicePixelRatio,devicePixelRatio);const W=canvas.width/devicePixelRatio,H=430;ctx.clearRect(0,0,W,H);$('rows').replaceChildren();
if(!bars.length){$('counts').textContent='该观察周期尚无完整已收盘 K 线；请查看树与历史检查。';$('range').textContent='无完整 K 线';return}
const t0=Date.parse(bars[0].time),t1=Date.parse(bars.at(-1).time)+view.period_minutes*60000;
const selected=Object.values(nodes).filter(n=>Date.parse(n.end)>=t0&&Date.parse(n.start)<t1&&($('level').value==='all'||n.level===Number($('level').value))&&($('kind').value==='centers'?['center','center_seed'].includes(n.kind):n.kind===$('kind').value));
let lo=Math.min(...bars.map(c=>c.low)),hi=Math.max(...bars.map(c=>c.high));const pad=(hi-lo)*.08||1;lo-=pad;hi+=pad;
const x=t=>65+(Date.parse(t)-t0)/(t1-t0)*(W-90),y=p=>20+(hi-p)/(hi-lo)*(H-65);ctx.font='11px system-ui';
for(let i=0;i<6;i++){const p=lo+(hi-lo)*i/5;ctx.strokeStyle='#e0e7ef';ctx.beginPath();ctx.moveTo(60,y(p));ctx.lineTo(W-15,y(p));ctx.stroke();ctx.fillStyle='#42596c';ctx.fillText(p.toFixed(2),0,y(p))}
const bw=Math.max(1,Math.min(7,(W-90)/bars.length*.65));for(const c of bars){ctx.strokeStyle=ctx.fillStyle=c.close>=c.open?'#dc4545':'#15936d';const cx=x(c.time);ctx.beginPath();ctx.moveTo(cx,y(c.high));ctx.lineTo(cx,y(c.low));ctx.stroke();ctx.fillRect(cx-bw/2,Math.min(y(c.open),y(c.close)),bw,Math.max(1,Math.abs(y(c.close)-y(c.open))))}
ctx.save();ctx.beginPath();ctx.rect(60,0,W-75,H-35);ctx.clip();const colors=['#d07813','#7653bd','#0f8f9f','#bd4880'];
for(const n of selected){ctx.strokeStyle=colors[Math.max(0,n.level-1)%colors.length];ctx.lineWidth=n.kind==='stroke'&&n.direct_break?3.2:1.8;ctx.setLineDash(['stroke','movement'].includes(n.kind)&&n.state!=='completed'?[5,4]:[]);
if(['center','center_seed'].includes(n.kind)){ctx.globalAlpha=.13;ctx.fillStyle=ctx.strokeStyle;ctx.fillRect(x(n.start),y(n.zg),Math.max(2,x(n.end)-x(n.start)),Math.max(1,y(n.zd)-y(n.zg)));ctx.globalAlpha=1;ctx.strokeRect(x(n.start),y(n.zg),Math.max(2,x(n.end)-x(n.start)),Math.max(1,y(n.zd)-y(n.zg)))}
else{ctx.beginPath();ctx.moveTo(x(n.start),y(n.start_price));ctx.lineTo(x(n.end),y(n.end_price));ctx.stroke()}}
ctx.restore();ctx.fillStyle='#42596c';ctx.fillText(bars[0].time,65,H-10);ctx.fillText(bars.at(-1).time,Math.max(65,W-260),H-10);
$('range').textContent=stop+'/'+view.candles.length;$('counts').textContent=selected.length+' 个匹配对象；表格最多显示最近 500 个。';
for(const n of selected.slice(-500).reverse()){const tr=document.createElement('tr');for(const v of ['L'+n.level,n.kind+' / '+n.state,n.start,n.end,n.zd!==undefined?n.zd+'～'+n.zg:n.low+'～'+n.high,n.evidence_at||'等待证据']){const td=document.createElement('td');td.textContent=v;tr.append(td)}tr.onclick=()=>detail(n);$('rows').append(tr)}}
function changePeriod(){const n=R.views[$('period').value].candles.length;$('end').max=Math.max(1,n);$('end').value=Math.max(1,n);draw()}
$('period').onchange=changePeriod;for(const id of ['kind','level','width'])$(id).onchange=draw;$('end').oninput=draw;window.onresize=draw;changePeriod();
</script></html>'''
    Path(path).write_text(page.replace("PAYLOAD", payload), encoding="utf-8")
