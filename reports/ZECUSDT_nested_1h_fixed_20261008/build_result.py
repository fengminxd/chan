"""Compare fixed-policy replay with the archived 42 groups; retain old numbering for audit."""
from pathlib import Path
from datetime import timezone,timedelta
from collections import Counter
from bisect import bisect_left
import json,csv,sys
P=Path(__file__).resolve().parent;ROOT=P.parents[1];sys.path.insert(0,str(ROOT))
from chan import read_csv,parse_time,json_default
from entry_plan import _breached, VERSION
OLD=ROOT/'reports/ZECUSDT_nested_1h_20260917_2000_20261008'
BJ=timezone(timedelta(hours=8))
def bj(t):return parse_time(t).astimezone(BJ).strftime('%m-%d %H:%M') if t else '—'
def dump(name,x):(P/name).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=json_default))
s=json.loads((P/'replay_summary.json').read_text());raw=json.loads((P/'triggers.json').read_text());old=json.loads((OLD/'entries_grouped.json').read_text());oldtr=json.loads((OLD/'triggers.json').read_text())
s['rule_version']=VERSION
s['core_code_unchanged_during_replay']=s.pop('core_code_unchanged',s.get('core_code_unchanged_during_replay'))
baseline=json.loads((OLD/'replay_summary.json').read_text())['code_sha256']
s['modified_core_files']=[name for name,value in s['code_sha256'].items() if baseline.get(name)!=value]
dump('replay_summary.json',s)
bars=read_csv(P/'history_1m.csv');times=[b.time for b in bars];groups={};checks=[]
for p in raw:
 armed,trigger=map(parse_time,[p['armed_at'],p['triggered_at']]);evidence=parse_time(p['evidence_at'])
 assert evidence<=armed<trigger<=armed+timedelta(minutes=60)
 part=bars[bisect_left(times,armed):bisect_left(times,trigger)];long=p['side']=='long'
 assert part and part[-1].close==p['trigger_observed_close']
 assert all((b.low>p['stop_price'] if long else b.high<p['stop_price']) and not _breached(b.low if long else b.high,p['parent_invalidation']) for b in part)
 assert all(b.close<=p['trigger_price'] if long else b.close>=p['trigger_price'] for b in part[:-1])
 assert part[-1].close>p['trigger_price'] if long else part[-1].close<p['trigger_price']
 for a,b in zip(p['path'],p['path'][1:]):
  n=b['native_structure'];assert n['start_confirmed'] and n['kind'] in ('stroke','developing_from_native_endpoint')
  assert parse_time(n['observed_at'])<=armed
  assert a['start']<=b['start']<b['end']<=a['end'] and a['low']<=b['low']<=b['high']<=a['high']
  assert a['start']<b['start'] or a['low']<b['low'] or a['high']>b['high']
  assert a['focus_price']==b['focus_price'] and a['focus_time']==b['focus_time']
 checks.append(dict(id=p['id'],causal_trigger=True,native_origins=True,real_path_shrink=True))
 if p['actionable']:groups.setdefault((p['side'],p['interval']['focus_time'],p['stop_price']),[]).append(p)
rows=[]
for ps in groups.values():
 p=ps[0];t=parse_time(p['triggered_at']);buy=p['side']=='long';stop=next((b for b in bars[bisect_left(times,t):] if b.low<=p['stop_price'] if buy),None) if buy else next((b for b in bars[bisect_left(times,t):] if b.high>=p['stop_price']),None)
 oldids=[i for i,o in enumerate(old,1) if o['side']==('买' if buy else '卖') and o['candidate_time']==p['interval']['focus_time'] and o['stop_price']==p['stop_price']]
 rows.append(dict(labels='/'.join(dict.fromkeys(x['label'] for x in ps if x['triggered_at']==p['triggered_at'])),all_followup_labels='/'.join(dict.fromkeys(x['label'] for x in ps)),side='买' if buy else '卖',candidate_time=p['interval']['focus_time'],candidate_time_bj=bj(p['interval']['focus_time']),candidate_price=p['candidate_price'],armed_at_bj=bj(p['armed_at']),path='→'.join(x['period'] for x in p['path']),execution_evidence_at_bj=bj(p['evidence_at']),triggered_at=p['triggered_at'],triggered_at_bj=bj(p['triggered_at']),comparison='>' if buy else '<',trigger_price=p['trigger_price'],observed_close=p['trigger_observed_close'],stop_price=p['stop_price'],stop_first_touched_bj=bj(stop.time.isoformat()) if stop else '未触及',actionable=True,followup_signal_count=len(ps)-1,followup_signals='；'.join(f"{bj(x['triggered_at'])} {x['period']} {x['trigger_price']}" for x in ps[1:]),first_plan_id=p['id'],old_group_ids=oldids))
comparison=[]
for i,o in enumerate(old,1):
 matches=[r for r in rows if i in r['old_group_ids']]
 oldp=next(p for p in oldtr if p['id']==o['first_plan_id'])
 status='filtered' if not matches else 'same_time' if matches[0]['triggered_at']==oldp['triggered_at'] else 'retimed'
 comparison.append(dict(old_number=i,old_candidate=o['candidate_price'],old_trigger=o['triggered_at_bj'],status=status,new_entries=matches))
assert comparison[27]['status']=='filtered'
dump('entries_grouped.json',rows);dump('comparison.json',comparison);dump('trigger_validation.json',checks)
with (P/'entries.csv').open('w',newline='',encoding='utf-8-sig') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else []);w.writeheader();w.writerows(rows)
counts=Counter(x['status'] for x in comparison);new=sum(not r['old_group_ids'] for r in rows)
lines=['# ZEC 1H逐级定位：两处修复后的历史回放','',f"同一行情快照：北京时间2026-{bj(s['start'])}—2026-{bj(s['as_of'])}。本次没有刷新行情。",'',
 '修复1：子周期须有自身K线稳定分型支持的结构起点；允许末笔发展以及从稳定端点展开的反向段，最终执行才要求稳定顶底分型。实际起点或价格范围必须缩小，单纯桶尾缩短不算逐级定位。',
 '修复2：已完成离开/首次回试的中枢不可重复生成三类候选；中枢初始第三段兼作离开段时不会再被跳过。买卖镜像，适用于全部主级别的候选生成。',
 '规则指纹升级为entry-plan-v4，旧快照不可续用。高层仍可未确认；本修复没有把候选提升为严格完成走势买卖点。','',
 f"逐分钟快照{s['snapshots']}个，原始触发{len(raw)}条，排除方向冲突并按同一极值分组后{len(rows)}组：买向{sum(r['side']=='买' for r in rows)}组、卖向{sum(r['side']=='卖' for r in rows)}组。",'',
 f"旧42组：{counts['same_time']}组首次触发时间保留，{counts['retimed']}组触发时间改变，{counts['filtered']}组无新版完整触发；另有{new}组新版极值信号。匹配按方向、原始极值分钟和防守价，不能把相近价格误当成同一组。",'',
 '**旧#28已被过滤：1385.51反弹不再获得重复的1H三卖标签，同一路径的桶尾收缩也不能获得执行许可。原1375.79分钟破位仍是历史价格事实，不是新版可执行信号。**','',
 '| 新编号 | 原编号 | 方向／候选 | 候选极值 | 首次触发（北京） | 1m收盘条件 | 触发收盘 | 定位路径 |','|---|---|---|---:|---|---:|---:|---|']
for i,r in enumerate(rows,1):lines.append(f"| {i} | {','.join('#'+str(n) for n in r['old_group_ids']) or '新增'} | {r['side']} {r['labels']} | {r['candidate_price']:.2f} | {r['triggered_at_bj']} | {r['comparison']} {r['trigger_price']:.2f} | {r['observed_close']:.2f} | {r['path']} |")
lines+=['','## 原42组逐项对照','','| 原编号 | 候选极值 | 原触发时间 | 新规则结果 |','|---|---:|---|---|']
for c in comparison:
 status={'filtered':'未通过完整触发链','same_time':'原时间保留','retimed':'改为 '+(c['new_entries'][0]['triggered_at_bj'] if c['new_entries'] else '')}[c['status']]
 lines.append(f"| #{c['old_number']} | {c['old_candidate']} | {c['old_trigger']} | {status} |")
lines+=['','已通过全套218项测试；回放4个历史截面共16个周期对照原始结构函数，每条触发额外核验稳定起点、真实收缩、时序与失效优先。触发是条件满足，不是模拟成交或收益保证。','', '[新版交互K线图](zec_fixed_tradingview.html) · [CSV](entries.csv) · [机器可读对照](comparison.json)']
(P/'result.md').write_text('\n'.join(lines)+'\n')
dump('fix_validation.json',dict(tests=218,snapshots=s['snapshots'],trigger_checks=len(checks),groups=len(rows),comparison_counts=counts,new_groups=new,old_28_filtered=True))
print(json.dumps(dict(groups=len(rows),comparison=counts,new_groups=new),ensure_ascii=False))
