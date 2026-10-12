"""Read-only strategy audit for entry group 28, using the original fixed source snapshot."""
from pathlib import Path
import sys,json
from datetime import timedelta
P=Path(__file__).resolve().parent;sys.path.insert(0,str(P.parents[1]))
from chan import read_csv,resample,parse_time,analyze,json_default
from chan_time import PERIODS
from chan_structure import Movement
from chan_theory import evolving_centers
from entry_plan import observation_legs,developing_reverse
rows=json.loads((P/'entries_grouped.json').read_text());row=rows[27]
triggers=[x for x in json.loads((P/'triggers.json').read_text()) if x['candidate_price']==1385.51 and x['triggered_at']=='2026-09-29T03:32:00+00:00']
now=parse_time(triggers[0]['triggered_at']);all_minutes=read_csv(P/'history_1m.csv');minutes=[c for c in all_minutes if c.time+timedelta(minutes=1)<=now]
bars,_=resample(minutes,PERIODS['1m'],PERIODS['1h'],now);report=analyze(bars,PERIODS['1h']);block={**report['blocks'][-1],'period':'1h'}
strokes=block['strokes'];legs=observation_legs(block,minutes,now);reverse=developing_reverse(legs,minutes,now,'1h')
children=[Movement(f'P{i}',0,'stroke_proxy',s.start,s.end,s.low,s.high,(),(),s.confirmed_at) for i,s in enumerate(strokes) if not s.provisional]
view=evolving_centers(children,1);center=view['centers'][-1];previous=[s for s in view['signals'] if s.center_id==center.id]
assert center.status=='left_down' and center.return_index==170
assert previous[-1].price==1599.24 and previous[-1].evidence_at<=now
assert reverse['price']==1385.51 and reverse['live_reverse'] and reverse['evidence_at'] is None
path=triggers[0]['path'];assert len({(x['start'],x['low'],x['high']) for x in path})==1
chart=json.loads((P/'chart_data.json').read_text());final=[x for x in chart['strokes'] if x['start']<=now.timestamp()<=x['end']]
stop=next(c for c in all_minutes if c.time>=now and c.high>=1385.51)
future=[c for c in all_minutes if parse_time('2026-09-29T03:32:00Z')<=c.time<parse_time('2026-09-29T06:00:00Z')]
low=min(future,key=lambda x:x.low)
audit=dict(group=28,entry=row,triggers=triggers,as_of=now,main_strokes_at_trigger=strokes[-5:],
 last_observation_legs=legs[-3:],developing_reverse=reverse,referenced_center=center,
 existing_third_point=previous[-1],final_chart_containing_strokes=final,
 first_stop_touch_minute=stop,subsequent_local_low=low,
 findings=['Actual minute close trigger is causal and correctly plotted.',
 'The 1h reverse candidate is a live minute-price rebound, not a confirmed 1h upward stroke or completed submovement.',
 'All four path levels share one start and one complete price range; only their bucket end times shrink. This does not establish four distinct completed structural levels.',
 'S3 reuses an already left_down center whose first return signal was 1599.24; it is not another first return to that same center.',
 'S2 relies on unconfirmed first-point origin and a live reverse; strict 1h S2 is unproven.',
 'The final chart 1h downstroke continued from 1599.24 to 1357.01, so the minute trigger lies near its bottom. This retrospective endpoint was not known at trigger time.'],
 disposition='Keep the historical minute trigger record; reject its duplicate 1h S3 attribution and do not treat it as a confirmed 1h S2/S3 entry. No strategy code changed.')
(P/'audit_28.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2,default=json_default))
(P/'audit_28.md').write_text('''# #28 卖出 1375.79 复核

复核使用原固定行情快照，主结构按北京时间2026-09-29 11:32可用数据重算；不使用后来的低点证明当时可以买卖。

**结论：分钟级破位触发确实发生，图上的价格位置正确；1H三卖归属有重复引用旧中枢的问题，1H二卖也没有严格结构确认。不能将#28作为已确认的1H二卖/三卖。**

## 触发事实（北京时间）

- 10:51：下跌发展中的临时低点1366.43。
- 11:21：局部反弹至1385.51，反弹幅度19.08。
- 11:30：1m稳定顶分型证据就绪。
- 11:31：建立计划，要求后续1m收盘低于1376.43。
- 11:32：收盘1375.79，触发；初始防守1385.51。
- 12:17所在分钟：价格触及防守。该结果不用于倒推触发本身无效。

## 为何落在笔底附近

图上的1H笔是截止快照的结构：1599.24→1357.01。#28标的是这段下跌内部一次短反弹后的1m触发收盘价；它既不是1H笔顶，也不是反弹顶1385.51。

主候选由`entry_plan.py::developing_reverse`从分钟低点直接展开反向路径产生，当时没有1H向上笔的确认。15m、5m、1m定位路径都从10:51开始，价格范围同为1366.43～1385.51，只是结束时间依次为11:31、11:30、11:25、11:22。现有“严格缩小”检查允许这种仅由周期桶结束时间造成的缩小，没有据此证明逐级独立结构已经完成。

## 三卖标签的具体错误

所引用中枢L1:E26为1631.10～1669.44。在触发时刻，这个中枢已经是`left_down`，已有首次回试三卖1599.24，完成证据不晚于当时（本次快照记为9月29日09:00）。

`period_candidates`仍选择最后一个中枢，未排除已离开且已有首次回试记录的中枢；同时过滤掉`index <= first_index + 2`的离开笔，把本来的P169离开、P170回试跳过，转而使用P171下跌和新生成的P172分钟反弹。因此1385.51被重复贴上同一中枢的三卖候选标签。它不能再称为该中枢的首次回试三卖。

二卖标签仅来自“反弹不破1599.24”的几何关系；原一类卖点未严格确认，反弹也只是发展中的分钟路径，不能由此证明1H二卖成立。

## 结论适用范围

保留#28作为旧规则确实产生过的分钟级执行信号记录；撤销其重复的1H三卖解释，将1H二卖归属列为待证。42组是原规则的历史触发记录，不能将其数量解读为42组均通过严格1H结构审核。本次仅复核#28，没有重算或删改其余41组。

核心策略、原始触发记录和图表没有在本次复核中修改。结构和时间证据见`audit_28.json`。
''')
print('AUDITED',center.id,center.status,'prior S3',previous[-1].price,'stop minute',stop.time,'future low',low.time,low.low)
