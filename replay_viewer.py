"""把回放 JSONL 转成离线可交互审阅页；滑块只展示当时已观察的对象。"""
import argparse
import json
from pathlib import Path

from chan import json_default


def write_viewer(path, frames):
    rows = sorted(frames, key=lambda x: (str(x["as_of"]), x["period"]))
    payload = json.dumps(rows, ensure_ascii=False, default=json_default, allow_nan=False).replace("<", "\\u003c")
    template = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>结构回放审阅</title>
<style>body{font:15px system-ui;margin:24px;color:#183044;background:#f5f8fb}header,section{background:white;padding:20px;border-radius:10px;margin:12px 0}h1{font-size:24px}input{width:60%}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;border-bottom:1px solid #dde5ed;padding:9px;word-break:break-word}tr:hover{background:#eff6ff;cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere}button,select{padding:7px;margin:5px}.note{color:#53697a}</style>
<header><h1>结构回放审阅</h1><p class="note">端点时间、结构证据时间和首次观察时间分别显示。确认只针对当时快照；后续修订和撤回仍会保留。</p>
<label>周期 <select id="period"></select></label><button id="prev">上一步</button><button id="next">下一步</button>
<input id="slider" type="range" min="0" value="0" aria-label="回放时间截面"><p id="time"></p></header>
<section><h2>当前可见对象</h2><p id="count"></p><table><thead><tr><th>类型</th><th>状态</th><th>端点时间</th><th>结构证据</th><th>首次观察</th><th>首次观察到确认</th><th>修订</th></tr></thead><tbody id="objects"></tbody></table></section>
<section><h2>本截面变更</h2><div id="changes"></div></section><section><h2>对象详情</h2><pre id="detail">点击对象查看完整依据</pre></section>
<script id="data" type="application/json">PAYLOAD</script><script>
const rows=JSON.parse(document.getElementById('data').textContent), byId=x=>document.getElementById(x);
const select=byId('period'),slider=byId('slider');let chosen=[];
for(const p of [...new Set(rows.map(x=>x.period))]){const o=document.createElement('option');o.value=p;o.textContent=p;select.append(o)}
function show(){const index=Number(slider.value),current=new Map();
for(let i=0;i<=index&&i<chosen.length;i++)for(const e of chosen[i].events){if(e.action==='retracted')current.delete(e.id);else current.set(e.id,e)}
const row=chosen[index];byId('time').textContent=row?'观察截止 '+row.as_of+'（UTC 偏移见时间字段）':'暂无回放';
byId('objects').replaceChildren();byId('changes').replaceChildren();byId('count').textContent='当前 '+current.size+' 个对象，最多展示最近 1000 个；点击查看来源。';
for(const e of [...current.values()].slice(-1000).reverse()){const s=e.structure,v=s.value,l=e.lifecycle||{},tr=document.createElement('tr');
const endpoint=l.endpoint_time||(v.end&&v.end.time)||v.end||v.time||'';
for(const x of [s.type,l.status||v.certainty||v.status||'',endpoint,l.structural_evidence_at||v.evidence_at||v.confirmed_at||'',e.first_observed_at,l.first_confirmed_observed_at||'',l.revision||'']){const td=document.createElement('td');td.textContent=String(x);tr.append(td)}
tr.onclick=()=>byId('detail').textContent=JSON.stringify(e,null,2);byId('objects').append(tr)}
for(const e of row?row.events:[]){const line=document.createElement('p');line.textContent=e.action+' · '+e.id;line.onclick=()=>byId('detail').textContent=JSON.stringify(e,null,2);byId('changes').append(line)}
}
function reset(){chosen=rows.filter(x=>x.period===select.value);slider.max=Math.max(0,chosen.length-1);slider.value=0;show()}
select.onchange=reset;slider.oninput=show;byId('prev').onclick=()=>{slider.value=Math.max(0,Number(slider.value)-1);show()};byId('next').onclick=()=>{slider.value=Math.min(Number(slider.max),Number(slider.value)+1);show()};reset();
</script></html>'''
    Path(path).write_text(template.replace("PAYLOAD", payload), encoding="utf-8")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--replay", nargs="+", required=True, type=Path)
    p.add_argument("--output", type=Path, default=Path("output/replay.html"))
    args = p.parse_args()
    try:
        rows = []
        for path in args.replay:
            with path.open(encoding="utf-8") as f:
                rows.extend(json.loads(line) for line in f if line.strip())
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_viewer(args.output, rows)
    except (ValueError, OSError, KeyError) as exc:
        p.error(str(exc))
    print(args.output)


if __name__ == "__main__":
    main()
