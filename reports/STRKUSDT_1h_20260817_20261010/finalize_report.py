"""Enrich the frozen STRK report with point-time audits and practical qualifications."""
from pathlib import Path
import sys,json,csv,hashlib
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from report_btc_4h_auto import local,dump
from chart_fallback import add_chart_fallback
P=Path(__file__).resolve().parent;ROOT=P.parents[1]
p=json.loads((P/'chart_data.json').read_text());d=p['histories']['recent']['periods']['1h'];src=p['source']
audit=json.loads((P/'point_time_audit.json').read_text());assert audit['asof']==p['asof'];byid={x['id']:x for x in audit['results']}
labels={'B1':'一买','B2':'二买','B3':'三买','S1':'一卖','S2':'二卖','S3':'三卖'}
certainty={'structural':'完成走势结构成立','stroke_structural':'笔观察结构成立','macd_candidate':'MACD辅助候选','dependent_candidate':'依赖一类候选','second_retest_geometry':'首次回试几何候选','policy_dependent':'依赖策略','policy_confirmed':'力度政策确认'}
for basis,v in d['views'].items():
 for cat in ('signals','divergences'):
  for s in v[cat]:
   s['time_audit']=byid[s['id']]
   s['execution_confirmed']=False
   s['execution_note']='未完成15m→5m→1m的实时逐级定位及后续突破验证；此处不是成交记录或当前开仓指令。'
   if cat=='signals':
    s['practical_status']=certainty[s['certainty']]
    if s.get('details',{}).get('strength')=='weak_new_extreme':
     s['practical_status']='创新高弱二卖候选；当前实盘政策不接收'
    elif s['label'].endswith('2'):
     s['practical_status']+='；未确认一类点/次级完成'
    if s['label'].startswith('S'):
     later=[b for b in d['bars'] if b['time']>=s['evidence_at'] and b['high']>s['price']]
    else:
     later=[b for b in d['bars'] if b['time']>=s['evidence_at'] and b['low']<s['price']]
    s['post_evidence_extreme_breach']=dict(time=later[0]['time'],scope='证据时刻后首次1h柱越过端点极值；非逐分钟止损模拟') if later else None
    if later:s['practical_status']+='；其后端点极值已被越过'
notes=[
 '指定区间为北京时间2026-08-17 00:00至2026-10-10 23:56，另用180天前置历史稳定结构。仅已收盘1h柱参与划笔、划段。',
 '区间开盘0.02341，报价0.09302（约+297.35%）；区间低点0.02217、高点0.09420。24小时涨幅23.928%。报价时间23:56:27；结构与末桶行情冻结到23:56，不混作同一时刻。',
 '完成走势中枢从0.02594–0.03079上移至0.03846–0.04354；最新笔观察中枢0.05061–0.05224。价格位于二者上方，当前结构偏多。',
 '窗口内62笔（含1条候选末笔），8条确认段及1条待确认段，6条direct_break破坏笔。当前待确认上段起于9月28日17:00的0.03846。算法段端点暂落在10月10日09:00的0.07767；末笔候选到16:00的0.06896。其后新高0.09420尚未形成稳定端点，不补画为确认笔段。',
 '完成走势口径仅有1个三买结构：9月24日10:00，0.03796，证据到9月27日10:00才齐备。未出现严格确认一买/一卖，二类点均待证；笔观察三类点须另核低周期执行。',
 '笔级背驰位置共2处：8月29日00:00、0.02421为底盘背候选（MACD面积比22.26%）；10月8日22:00、0.06245为顶背驰候选（57.49%）。后者已经被价格继续创新高越过，不能据此认定当前见顶。',
 '最新笔观察三买：10月9日05:00、0.05455，证据到10月10日14:00才齐备；10月10日09:00、0.07767的弱二卖候选创新高，当前实盘政策不接收。',
 '50条信号/背驰记录全部通过各自证据时刻的截断复算（37个截面）。这不等于逐根首次发现回放，也不代表低周期执行已确认。默认笔观察口径便于看候选；切换完成走势可看更严格结构。'
]
d['analysis_notes']=notes;p['default_basis']='strokes'
p['execution_scope']='1h结构与证据时刻复核；不包含低周期区间套与订单执行'
dump(P/'chart_data.json',p)
t=(P/'template.html').read_text().replace('SUIUSDT','STRKUSDT').replace('SUI 永续','STRK 永续')
t=t.replace('笔中枢（对照）','笔观察（实盘候选）')
t=t.replace("certainty[s.certainty]||s.certainty]);", "s.practical_status||certainty[s.certainty]||s.certainty]);")
t=t.replace('当前为结构快照，未执行全历史逐根回放。','已对50条信号/背驰记录逐证据时刻截断复算；未做全历史逐根首次出现回放，也未验证15m→5m→1m执行。')
t=t.replace('只有满足当前程序条件才输出，不加入人工指定点位。','一类点需严格背驰证据；二类点不能仅凭不破前低/前高确认，创新极值的弱二类点当前实盘政策不接收。笔三类仅表示笔观察几何成立；实盘还需次级结构、稳定分型及后续1m收盘突破。')
t=t.replace('后段 / 前段力度','MACD后段 / 前段面积')
t=t.replace('结构证据时间 · 北京','截断复核通过的证据时间 · 北京')
t=t.replace('<a href="result.md">报告</a>','<a href="result.md">完整分析</a> · <a href="point_time_audit.json">时序复核</a>')
# Preserve the shared Canvas colors, widths and dash states, including direct_break=3.2.
page=t.replace('PAYLOAD_JSON',json.dumps(p,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c'))
# Default static view uses the strict completed-type view, which is less crowded.
v=d['views']['types']
fallback=dict(start=p['start'],asof=p['asof'],bars=d['bars'],nodes=v['nodes'],signals=v['signals'],
 symbol=p['symbol'],analysis_period='1h',period_ms=3600000,price_decimals=5,summary={'counts':v['counts']})
page=add_chart_fallback(page,fallback)
(P/'strk.html').write_text(page)
for cat in ('signals','divergences','structures'):
 with (P/f'{cat}.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['口径','类别','开始/端点_北京','结束_北京','价格','证据_北京','性质','时序复核','一笔破坏','比较前段开始','比较前段结束','后段前段面积比','详情'])
  for basis,v in d['views'].items():
   for s in v['nodes' if cat=='structures' else cat]:
    w.writerow([basis,s.get('label',s.get('kind')),local(s.get('time',s.get('start'))),local(s['end']) if s.get('end') else '',s.get('price',s.get('end_price',s.get('zd'))),local(s['evidence_at']) if s.get('evidence_at') else '',s.get('practical_status',s.get('certainty',s.get('state'))),s.get('time_audit',{}).get('present_at_claimed_evidence',''),s.get('direct_break',False),local(s['a_start']) if s.get('a_start') else '',local(s['a_end']) if s.get('a_end') else '',s.get('area_ratio',''),json.dumps(s,ensure_ascii=False)])
lines=['# STRKUSDT 永续 · 1h行情分析','', '[打开交互图](strk.html) · [买卖点明细](signals.csv) · [背驰明细](divergences.csv) · [行情来源](source.json)','',
 '时间均为北京时间，价格单位USDT。数据为币安USDT本位永续成交K线。', '', *[n+'\n' for n in notes[:7]],
 '## 行情结构','',
 '8月以0.02217附近的低位震荡为起点，8月22日触及0.03079后回落至8月29日0.02421；9月10日到0.03424后回撤至9月17日0.02594。此后上冲9月21日0.05095，回落形成抬高的中枢。9月28日0.03846后的上涨段仍在发展，10月10日最新小时出现0.09420的新高。确认结构落后于行情极值属于规则本身的等待，不把最后一个高点直接命名为一卖。','',
 '## 一、二、三类买卖点','',
 '| 类别 | 完成走势口径 | 笔观察口径与实盘含义 |','|---|---|---|',
 '| 一买 B1 | 未确认 | 无满足当前扫描条件的B1；不能把8月低点或盘整背驰直接叫一买 |',
 '| 一卖 S1 | 未确认 | 10月8日22:00、0.06245，仅MACD顶背驰候选，后续创新高 |',
 '| 二买 B2 | 4个几何候选，均未严格确认 | 19个候选；较近10月9日05:00、0.05455，不破0.04785仅证明回试几何关系 |',
 '| 二卖 S2 | 1个几何候选，未严格确认 | 11个候选；10月10日09:00、0.07767为创新高弱二卖，当前实盘政策不接收 |',
 '| 三买 B3 | 9月24日10:00、0.03796；9月27日10:00证据齐备 | 7个笔观察三买，与完成走势三买分开计数 |',
 '| 三卖 S3 | 本窗口无 | 4个笔观察三卖；不等同于完成走势三卖或当前做空许可 |','',
 '三类点的回试完整范围必须严格位于中枢之外，碰边即回中枢；不能跳过失败回试去选后面的点。以下均为历史结构位置，实盘可知时间见证据列，不能按端点价回填成交。','',
 '| 口径 | 点位 | 端点时间 | 价格 | 证据时间 | 对应中枢ZD–ZG |','|---|---|---|---:|---|---|']
for basis,v in d['views'].items():
 for s in v['signals']:
  if s['label'].endswith('3'):
   q=s['details'];lines.append(f"| {'笔观察' if basis=='strokes' else '完成走势'} | {labels[s['label']]} | {local(s['time'])} | {s['price']:.5f} | {local(s['evidence_at'])} | {q['zd']:.5f}–{q['zg']:.5f} |")
lines+=['','完成走势的二类候选：','', '| 点位 | 端点时间 | 价格 | 证据时间 |','|---|---|---:|---|']
for s in d['views']['types']['signals']:
 if s['label'].endswith('2'):lines.append(f"| {labels[s['label']]}待证 | {local(s['time'])} | {s['price']:.5f} | {local(s['evidence_at'])} |")
lines+=['','## 背驰位置与比较依据','',
 '本窗口严格结构与SMA力度政策确认的背驰为0。下列是笔级MACD(12,26,9)同向柱面积辅助候选；程序同时要求价格创新极值及中间MACD满足回零阈值。面积比不是胜率，也不单独证明趋势结束。','',
 '| 性质 | 端点时间/价格 | 前段比较区间 | 后段比较区间 | 后/前面积 | 证据时间 |','|---|---|---|---|---:|---|']
for s in d['views']['strokes']['divergences']:
 name=('底' if s['side']=='down' else '顶')+('盘背' if s['kind']=='consolidation' else '趋势背驰')+'候选'
 lines.append(f"| {name} | {local(s['time'])} / {s['price']:.5f} | {local(s['a_start'])} → {local(s['a_end'])} | {local(s['start'])} → {local(s['end'])} | {s['area_ratio']:.2%} | {local(s['evidence_at'])} |")
lines+=['',
 '8月29日00:00的0.02421是单中枢的底盘背候选，不能升级为趋势一买。10月8日22:00的0.06245相对前一上涨比较段创新高而面积缩小，构成笔级顶背驰候选；随后0.07767、0.09420继续越过该高点，故这只是历史警示，不是当前反转确认。最新快速上涨还缺稳定完成端点，不用未完成面积推断新的背驰。','',
 '## 当前实盘观察','',
 '价格保持在抬高的中枢上方，结构判断偏多，但没有在本次报告中验证可立即执行的新开仓信号。最近0.05455笔三买是历史回试点，不能在0.09302把它重新当成新三买。', '',
 '- 上方0.09420是当前未收盘小时内的高点，仅为观察极值，尚非确认压力或一卖。',
 '- 近端观察0.07767（已被越过的高点）、0.06896（未完成末笔候选低点）；这两个价格都不是直接下单信号。',
 '- 下方0.05455是最近笔三买回试低点；0.05061–0.05224是其笔中枢；更大结构中枢为0.03846–0.04354。跌回这些位置需要按相应层级重新判断，不能混用为同一止损。',
 '- 做多需等待新的有效回试与低周期确认；做空需新的顶背驰/反向结构或向下离开后的首次不过枢回抽。当前尚未提供这些执行证据。',
 '- 按项目1h实盘政策，还要逐级检查1h→15m→5m→1m区间包含、稳定分型及后续1m收盘突破；多头止损参考分型低点、空头对称。本报告未下载整个区间的低周期数据，因此未生成执行触发价、精确止损或历史成交。','',
 '## 图例、校验与复现','',
 '蓝色为笔、橙色为段；未确认结构保留虚线。发起direct_break的6条笔加粗为3.2，颜色不变、虚实线不变。蓝色数字买点、红色数字卖点；空心与问号为候选。实心三类点只代表相应口径的结构成立，不代表低周期执行确认。', '',
 notes[-1], '',
 '共1319根展示区间完整1h柱；前置历史4320根，共5639根完整柱，连续性、OHLC及原始响应哈希已校验。当前23:00小时仅拼入56根已收盘分钟供展示。全区间分析只使用已收盘1h柱。','',
 '复现：先运行同目录build_report.py，再运行audit_points.py、finalize_report.py。数据刷新使用fetch_snapshot.py；本次解读为固定快照，刷新后需重写行情说明，不能沿用旧结论。', '',
 '[完整原始分析](analysis_1h_recent.json) · [图表数据](chart_data.json) · [证据时刻复核](point_time_audit.json) · [结构明细](structures.csv)']
(P/'result.md').write_text('\n'.join(lines)+'\n')
dump(P/'final_manifest.json',dict(asof=p['asof'],rule_manifest=d['rule_manifest'],direct_break_count=sum(n.get('direct_break',False) for n in d['views']['types']['nodes']),point_time_audit=audit['counts'],sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['chan.py','chan_structure.py','chan_theory.py','chan_types.py','chan_dynamics.py','report_btc_4h_auto.py','chart_fallback.py','futures_multiperiod_template.html']}))
print('Wrote strk.html, result.md, CSVs, manifests')
