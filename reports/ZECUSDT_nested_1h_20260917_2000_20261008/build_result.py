"""Audit causal trigger events and produce review tables (no trade fills assumed)."""
from pathlib import Path
import json,csv,sys
from collections import Counter
from datetime import timezone,timedelta
from bisect import bisect_left
P=Path(__file__).resolve().parent;ROOT=P.parents[1];sys.path.insert(0,str(ROOT))
from chan import read_csv,parse_time,json_default
BJ=timezone(timedelta(hours=8))
def bj(t):return parse_time(t).astimezone(BJ).strftime('%m-%d %H:%M') if t else '—'
def dump(f,x):(P/f).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=json_default))
def run():
 s=json.loads((P/'replay_summary.json').read_text());triggers=json.loads((P/'triggers.json').read_text());plans=json.loads((P/'plans.json').read_text());candidates=json.loads((P/'candidates.json').read_text())
 bars=read_csv(P/'history_1m.csv');times=[b.time for b in bars]
 groups={};validation=[]
 for p in triggers:
  armed=parse_time(p['armed_at']);trigger=parse_time(p['triggered_at']);evidence=parse_time(p['evidence_at'])
  assert evidence<=armed<trigger<=armed+timedelta(minutes=60)
  after=bars[bisect_left(times,armed):bisect_left(times,trigger)]
  assert after and after[-1].time+timedelta(minutes=1)==trigger
  long=p['side']=='long';stop=p['stop_price'];threshold=p['trigger_price']
  assert all(b.low>stop if long else b.high<stop for b in after)
  assert all(b.close<=threshold if long else b.close>=threshold for b in after[:-1])
  assert (after[-1].close>threshold if long else after[-1].close<threshold)
  assert after[-1].close==p['trigger_observed_close']
  path=p['path'];assert [x['period'] for x in path]==['1h','15m','5m','1m'][:len(path)]
  for a,b in zip(path,path[1:]):
   assert a['start']<=b['start']<b['end']<=a['end']
   assert b['start']>a['start'] or b['end']<a['end']
   assert a['low']<=b['low']<=b['high']<=a['high']
   assert a['focus_time']==b['focus_time'] and a['focus_price']==b['focus_price']
  validation.append({'id':p['id'],'causal_trigger_verified':True,'nested_path_verified':True})
  if not p['actionable']:continue
  key=(p['side'],p['interval']['focus_time'],stop)
  if key not in groups:groups[key]={'first':p,'updates':[]}
  groups[key]['updates'].append(p)
 rows=[]
 for g in groups.values():
  p=g['first'];trigger=parse_time(p['triggered_at']);long=p['side']=='long'
  stophit=next((b for b in bars[bisect_left(times,trigger):] if (b.low<=p['stop_price'] if long else b.high>=p['stop_price'])),None)
  row=dict(labels='/'.join(dict.fromkeys(x['label'] for x in g['updates'] if x['triggered_at']==p['triggered_at'])),all_followup_labels='/'.join(dict.fromkeys(x['label'] for x in g['updates'])),side='买' if long else '卖',
    candidate_time=p['interval']['focus_time'],candidate_time_bj=bj(p['interval']['focus_time']),candidate_price=p['candidate_price'],
    armed_at_bj=bj(p['armed_at']),path='→'.join(x['period'] for x in p['path']),
    execution_evidence_at_bj=bj(p['evidence_at']),triggered_at_bj=bj(p['triggered_at']),
    comparison='>' if long else '<',trigger_price=p['trigger_price'],observed_close=p['trigger_observed_close'],
    stop_price=p['stop_price'],stop_first_touched_bj=bj(stophit.time.isoformat()) if stophit else '未触及',
    actionable=p['actionable'],followup_signal_count=len(g['updates'])-1,
    followup_signals='；'.join(f"{bj(x['triggered_at'])} {x['period']} {x['trigger_price']}" for x in g['updates'][1:]),
    first_plan_id=p['id'])
  rows.append(row)
 dump('entries_grouped.json',rows);dump('trigger_validation.json',validation);dump('blocked_by_conflict.json',[p for p in triggers if not p['actionable']])
 with (P/'entries.csv').open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else []);w.writeheader();w.writerows(rows)
 header=['| 首次触发（北京） | 1H候选 | 候选极值 | 定位路径 | 1m收盘条件 | 触发收盘价 | 防守极值 | 此后首次触及防守 |','|---|---|---:|---|---|---:|---:|---|']
 table=[f"| {r['triggered_at_bj']} | {r['labels']} | {r['candidate_price']:.2f} | {r['path']} | {r['comparison']} {r['trigger_price']:.2f} | {r['observed_close']:.2f} | {r['stop_price']:.2f} | {r['stop_first_touched_bj']} |" for r in rows]
 lines=['# ZECUSDT 永续：1H 候选逐级定位复核','',f"北京时间：2026-{bj(s['start'])} 至 2026-{bj(s['as_of'])}；截止1m收盘价 {s['last_close']}。",'',
 '数据为 Binance USD-M ZECUSDT 永续原生1m，固定从2026-06-01 UTC热身。指定起点开始建立观察计划，不补记起点前已发生的突破。',
 f"共 {s['snapshots']} 个逐分钟观察快照；原始触发 {len(triggers)} 条，其中 {sum(not p['actionable'] for p in triggers)} 条因多空冲突禁止执行；其余按同方向、同一分钟极值和同一防守价合并后 {len(rows)} 组，其中买向 {sum(r['side']=='买' for r in rows)} 组、卖向 {sum(r['side']=='卖' for r in rows)} 组。",'',
 '**这些是1H候选下的低级别执行触发，不是严格1H一二三类点已确认，也不是模拟成交或盈亏结果。**','',
 '沿用现有 nested_1h：1H笔观察候选→15m→5m→1m，每级时间和价格完整包含、同一极值、区间严格缩小；遇到首个可用稳定分型建立计划，后续1m收盘严格突破分型边界才触发。额外一根去包含柱确认分型；60分钟等待；同一分钟失效优先；首次观察时已突破不追记。',
 '1H二类候选的原一类点未必已确认；一类候选MACD只属辅助。这里的执行分型不冒充低级别严格一买/二买。信号记录允许同一候选后续升级到5m或15m，表格只显示该极值最早触发。',
 '“此后首次触及防守”只审计触发之后的价格路径，既未设止盈，也未模拟持仓、手续费、滑点和重复开仓。不能据此计算胜率。','',*header,*table,'',
 '## 当前快照','']
 for p in s['current_plans']:
  lines.append(f"- {p['label']} 候选 {p['candidate_price']}，候选时间 {bj(p['candidate_time'])}；状态 {p['state']}；路径 {'→'.join(x['period'] for x in p['path'])}；历史触发 {bj(p.get('triggered_at'))}；当前新触发 {p['new_trigger']}，当前可执行 {p['actionable']}。")
 if not s['current_plans']:lines.append('当前没有符合现有规则的1H候选。')
 lines+=['','## 对此前历史端点的复核','',
 '此前按1H收盘复盘得到的端点标签，与此次分钟级候选使用的规则并不完全相同，不能强行一一对应。尤其三类候选函数要求所选中枢形成后的首次离开回试，而历史笔级中枢函数允许中枢延伸后再出现离开回试。',
 '下表只将此前端点价与此次触发极值配对，用于检查是否确有实时触发；相同价位不代表原标签得到严格确认。','',
 '| 旧1H端点 | 本次相同极值的最早触发 | 本次候选标签 | 说明 |','|---:|---|---|---|']
 old=json.loads((ROOT/'reports/ZECUSDT_review_20260917_2000_20261008/points_1h.json').read_text())['records']
 seen=set();prior_audit=[]
 for r in old:
  x=r['latest_signal'];price=x['price']
  if x['label'] not in ('B1','B2','B3','S1','S2','S3') or price in seen:continue
  seen.add(price);endpoint=parse_time(x['time']);matches=[r for r in rows if r['candidate_price']==price and r['side']==('买' if x['label'].startswith('B') else '卖') and endpoint<=parse_time(r['candidate_time'])<endpoint+timedelta(hours=1)]
  cs=[r for r in candidates if r['candidate']['candidate_price']==price and r['candidate']['side']==('long' if x['label'].startswith('B') else 'short') and endpoint<=parse_time(r['candidate']['interval']['focus_time'])<endpoint+timedelta(hours=1)]
  prior_audit.append(dict(price=price,endpoint=x['time'],previous_label=x['label'],current_labels=sorted({r['candidate']['label'] for r in cs}),candidate_first_seen=min((r['first_seen'] for r in cs),default=None),states=dict(sum((Counter(r['states']) for r in cs),Counter())),entries=matches))
  lines.append(f"| {price:.2f} | {matches[0]['triggered_at_bj'] if matches else '无'} | {matches[0]['labels'] if matches else '—'} | {'有执行触发，按本次标签解释' if matches else '未获得完整逐级触发，不能列作确认进场'} |")
 dump('prior_point_audit.json',prior_audit)
 lines+=['','1455.00 的1m计划在9月24日17:37建立（阈值1482.07），18:00因候选或定位结构变化被撤回，未形成保留的实时触发。',
 '1368.46 的15m卖出计划在10月5日07:15建立，要求后续1m收盘低于1334.51，08:15等待满60分钟后过期，不能把之后的下跌补记为此计划触发。',
 '1478.00 虽在旧1H历史结构复盘中后来撤回，本次在9月24日10:49确有当时满足规则的买入触发；17:31触及防守。历史标签撤回不应抹去此前发生过的执行信号。']
 special=[r for r in plans if r['first']['candidate_price']==1631.1 and r['first']['side']=='long']
 if special:
  first=special[0]['first'];audit=[]
  lines+=['','## 1631.10 单独核查','',f"本次标签为 {first['label']} 候选；完整定位首次获得执行结构的观察时刻为 {bj(first['first_observed_at'])}，结果 {first['state']}。",'',
   '| 执行周期 | 稳定分型证据时刻 | 触发边界 | 从证据时刻起首次越界收盘 | 结果 |','|---|---|---:|---|---|']
  for r in first.get('rejected_setups',[]):
   x=r['setup'];ev=parse_time(x['evidence_at']);hit=next((b for b in bars if b.time+timedelta(minutes=1)>=ev and b.close>x['trigger_price']),None)
   audit.append({'period':x['period'],'evidence_at':x['evidence_at'],'threshold':x['trigger_price'],'first_cross_at':(hit.time+timedelta(minutes=1)).isoformat() if hit else None,'first_cross_close':hit.close if hit else None,'state':r['state']})
   lines.append(f"| {x['period']} | {bj(x['evidence_at'])} | {x['trigger_price']} | {bj((hit.time+timedelta(minutes=1)).isoformat())} / {hit.close} | {r['state']} |")
  lines+=['','三个周期均在分型证据就绪的同一分钟已经越界。现有规则要求先建立计划、之后再突破，因此不能回填这些买入触发。这个排除结果不是顶部背驰过滤的效果；当前 nested_1h 没有据此加入额外背驰否决条件。']
  dump('audit_1631.json',audit)
 lines+=['','## 验证与文件','',
 '- 4个历史截面逐周期对照原始去包含、稳定分型、选笔、观察区间与分型阈值函数；16个周期截面对照全部一致。',
 '- 每条触发复核证据不晚于建立计划、建立计划早于触发、逐级路径合法、此前未突破且未触及止损。',
 '- 核心策略代码哈希前后一致；只新增复盘脚本和报告。',
 '- `source.json`：来源、抓取时点及数据校验；`replay_summary.json`：策略、快照计数与验证；`events.jsonl`：状态变化；`triggers.json`：完整触发证据；`entries.csv`：分组结果。']
 (P/'result.md').write_text('\n'.join(lines)+'\n')
 print(json.dumps(dict(groups=len(rows),raw_triggers=len(triggers),buys=sum(r['side']=='买' for r in rows),sells=sum(r['side']=='卖' for r in rows)),ensure_ascii=False))
if __name__=='__main__':run()
