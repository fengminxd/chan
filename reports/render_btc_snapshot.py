"""Render the frozen three-period BTC review without modifying analysis rules."""
import csv
import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'reports/BTCUSDT_current_20260928'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir',type=Path,default=P)
P=parser.parse_args().output_dir.resolve()
TZ=timezone(timedelta(hours=8))
def dt(t):return datetime.fromisoformat(t)
def stamp(t):return dt(t).timestamp()
def local(t):return dt(t).astimezone(TZ).strftime('%m-%d %H:%M')
source=json.loads((P/'source.json').read_text())
source['ticker_time']=datetime.fromtimestamp(source['ticker']['closeTime']/1000,timezone.utc).isoformat()
notes={
 '4h':[
  '方向：上涨后的回撤，尚无严格完成的反向线段；87,385.1 为阶段高点。',
  '近期笔重叠观察区 76,151.9–79,555.5，外围 74,909.4–82,282.8；严格段构造的历史中枢 67,300.0–75,998.9。两种口径不能互换。',
  '程序在 83,130.1 标注笔口径 B3，但整个回撤曾下探 82,832.0，不能把分型端点当作整段最低价。',
  '87,385.1 的 MACD 面积比约 0.713，只是顶背驰辅助候选。4h 当前柱未收盘。'],
 '1h':[
  '交易方向：短线偏弱，87,385.1 后下跌，再进入 82,832.0–85,224.0 震荡；尚不等于严格确认的下降趋势。',
  '上方笔重叠区 85,080.0–87,247.3；09-24 06:00 的 84,638.5 是该口径历史三卖，证据时间 09-24 23:00，不能追溯成交。',
  '严格段构造的最近 L1 中枢 76,853.1–79,384.4；74,909.4→87,385.1 的上涨段仍待反向特征序列确认。',
  '82,832.0 有局部下跌力度减弱现象，但当前没有已确认的严格 B1/B2。1h 最新 MACD 柱约 -290.6，仍在扩大。'],
 '15m':[
  '近期笔重叠区 83,708.0–84,584.6，价格正向下离开；尚未完成离开后的首次反抽，不能提前认定新三卖。',
  '今日 08:15 的 84,966.8 后跌至 11:30 K线的 83,166.6；该低点尚无已确认底分型。',
  'MACD 负柱由约 -159.2 缩至 -113.8，但仍在零轴下，末段未完成；缩柱不等于底背驰确认。',
  '偏空方案：等待反抽 83,708 附近后失败，并出现顶分型及向下确认。偏多方案：低点稳定、收复 83,708，再回踩不破低点。实际止损以最终确认的反抽高点或回踩低点为准。']}
if (P/'analysis_notes.json').exists():
 review=json.loads((P/'analysis_notes.json').read_text())
 assert review['asof']==source['as_of'], 'Commentary must match the frozen snapshot'
 notes=review['notes']
elif P.name!='BTCUSDT_current_20260928':
 raise ValueError('A new snapshot requires matching analysis_notes.json')
periods={}
for period,hours in [('4h',4),('1h',1),('15m',.25)]:
 v=json.loads((P/f'views_{period}.json').read_text())
 # Replace the display-only 0.25h alias consistently, retaining structural IDs.
 v['analysis_period']=period
 for value in v['views'].values():
  for x in [*value['nodes'],*value['signals'],*value['divergences'],*value['centers'].values()]:
   if 'basis' in x:x['basis']=x['basis'].replace('0.25h','15m')
 tail=json.loads((P/f'partial_{period}.json').read_text())
 if tail:
  r=tail[0]
  v['bars'].append(dict(time=datetime.fromtimestamp(r[0]/1000,timezone.utc).isoformat(),
      **dict(zip(['open','high','low','close','volume'],map(float,r[1:6]))),partial=True))
 v['source']={**source['periods'][period], 'partial_minutes':round((stamp(source['as_of'])-stamp(source['periods'][period]['closed_end']))/60,1)}
 v['analysis_notes']=notes[period]
 periods[period]=v
payload=dict(symbol='BTCUSDT',start=(dt(source['as_of'])-timedelta(days=45)).isoformat(),
 asof=source['as_of'],source=source,price_decimals=1,default_period='1h',default_basis='strokes',
 default_history='snapshot',histories={'snapshot':{'label':'本次冻结行情','periods':periods}},manual_annotations=[])
(P/'chart_data.json').write_text(json.dumps(payload,ensure_ascii=False))
page=(ROOT/'futures_multiperiod_template.html').read_text().replace('SUIUSDT','BTCUSDT').replace('SUI 永续','BTC 永续')
page=page.replace('1h / 4h','4h / 1h / 15m').replace('1h与4h','4h、1h与15m').replace('最近100天','本次快照').replace('>100天<','>全窗口<')
page=re.sub(r'<select id="history">.*?</select>','<select id="history"><option value="snapshot">本次冻结行情</option></select>',page)
page=page.replace('<option value="4h" selected>4h</option>','<option value="4h">4h</option><option value="15m">15m</option>')
for expr in ['ALL.default_period','D.analysis_period']:
 page=page.replace('Number('+expr+'.slice(0,-1))*3600000','({"4h":14400000,"1h":3600000,"15m":900000})['+expr+']')
page=page.replace('默认完成走势中枢采用修正后的完成走势递归；','默认笔中枢是交易观察口径，不等于严格次级走势中枢。完成走势选项采用程序的完成走势递归；')
page=page.replace(' · <a href="analysis_1h_recent.json">近期1h分析</a> · <a href="analysis_4h_recent.json">近期4h分析</a>','')
page=page.replace('全历史1h','1h分析').replace('全历史4h','4h分析')
page=page.replace('PAYLOAD_JSON',json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c'))
(P/'btc.html').write_text(page)
scripts=re.findall(r'<script>(.*?)</script>',page,re.S)
(P/'chart.js').write_text('\n'.join(scripts))
for category in ['signals','divergences','structures']:
 with (P/f'{category}.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['period','basis','kind','start_or_endpoint_BJT','end_BJT','price_or_ZD','ZG','status','evidence_BJT'])
  for period,v in periods.items():
   for basis,x in v['views'].items():
    for n in x['nodes' if category=='structures' else category]:
     w.writerow([period,basis,n.get('label',n.get('kind')),local(n.get('time',n.get('start'))),local(n['end']) if n.get('end') else '',n.get('price',n.get('zd',n.get('end_price'))),n.get('zg',''),n.get('certainty',n.get('state')),local(n['evidence_at']) if n.get('evidence_at') else ''])

# Standalone overview, including the unconfirmed price tail and labeled zones.
svg=['<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="1230" viewBox="0 0 1400 1230" font-family="sans-serif">',
 '<rect width="1400" height="1230" fill="#f5f7fb"/>',
 '<text x="35" y="32" font-size="23" fill="#17283e">BTCUSDT 永续 · 4H / 1H / 15m</text>',
 f'<text x="35" y="58" font-size="14" fill="#586781">快照 {local(source["as_of"])} 北京时间 · 蓝：程序笔　橙：待确认尾部　紫：笔重叠观察区</text>']
zones={'4h':[(76151.9,79555.5)],'1h':[(85080,87247.3)],'15m':[(83708,84584.6)]}
for panel,(period,days,hours) in enumerate([('4h',45,4),('1h',10,1),('15m',4,.25)]):
 top=90+panel*375;bottom=top+285;left=90;right=1210;end=stamp(source['as_of']);start=end-days*86400
 bars=[c for c in periods[period]['bars'] if stamp(c['time'])>=start]
 lo=min(c['low'] for c in bars);hi=max(c['high'] for c in bars);pad=(hi-lo)*.10;lo-=pad;hi+=pad
 x=lambda t:left+(t-start)/(end-start)*(right-left)
 y=lambda p:bottom-(p-lo)/(hi-lo)*(bottom-top)
 svg.append(f'<rect x="20" y="{top-17}" width="1360" height="358" rx="12" fill="white"/>')
 svg.append(f'<text x="35" y="{top+10}" font-size="18" fill="#17283e">{period}</text>')
 svg.append(f'<defs><clipPath id="p{panel}"><rect x="{left}" y="{top}" width="{right-left}" height="{bottom-top}"/></clipPath></defs>')
 for i in range(6):
  price=lo+(hi-lo)*i/5
  svg.append(f'<path d="M{left},{y(price):.1f}H{right}" stroke="#e7edf5"/><text x="82" y="{y(price)+4:.1f}" text-anchor="end" font-size="11" fill="#586781">{price:,.0f}</text>')
 svg.append(f'<g clip-path="url(#p{panel})">')
 for a,b in zones[period]:
  svg.append(f'<rect x="{left}" y="{y(b):.1f}" width="{right-left}" height="{y(a)-y(b):.1f}" fill="#9253b8" opacity=".11"/>')
 width=max(.8,min(6,(right-left)*hours*3600/(end-start)*.65))
 for c in bars:
  xx=x(stamp(c['time'])+hours*1800);color='#269477' if c['close']>=c['open'] else '#ce5068'
  svg.append(f'<path d="M{xx:.1f},{y(c["high"]):.1f}V{y(c["low"]):.1f}" stroke="{color}"/><rect x="{xx-width/2:.1f}" y="{min(y(c["open"]),y(c["close"])):.1f}" width="{width:.1f}" height="{max(1,abs(y(c["open"])-y(c["close"]))):.1f}" fill="{color}"/>')
 for n in periods[period]['views']['strokes']['nodes']:
  if n['kind']!='stroke' or stamp(n['end'])<start:continue
  dash='' if n['state']=='completed' else ' stroke-dasharray="5 4"'
  svg.append(f'<path d="M{x(stamp(n["start"])):.1f},{y(n["start_price"]):.1f}L{x(stamp(n["end"])):.1f},{y(n["end_price"]):.1f}" stroke="#237be5" fill="none" stroke-width="1.5"{dash}/>')
 # Latest high-to-low price leg is explicitly provisional, not a computed stroke.
 high_time={'4h':'2026-09-27T12:00:00+00:00','1h':'2026-09-28T00:00:00+00:00','15m':'2026-09-28T00:15:00+00:00'}[period]
 high_price=85146.4 if period=='4h' else 84966.8
 tail_low=min((c for c in bars if stamp(c['time'])>=stamp(high_time)),key=lambda c:c['low'])
 svg.append(f'<path d="M{x(stamp(high_time)):.1f},{y(high_price):.1f}L{x(stamp(tail_low["time"])):.1f},{y(tail_low["low"]):.1f}" stroke="#d57a15" fill="none" stroke-width="2" stroke-dasharray="6 4"/>')
 svg.append('</g>')
 levels=zones[period][0]+((82832,87385.1) if period in ['4h','1h'] else (tail_low['low'],82832))
 for price in levels:
  if lo<price<hi:
   svg.append(f'<path d="M{left},{y(price):.1f}H{right}" stroke="#8c51bd" stroke-dasharray="3 5" opacity=".65"/><text x="1220" y="{y(price)+4:.1f}" font-size="13" fill="#754096">{price:,.1f}</text>')
 for i in range(6):
  tt=start+(end-start)*i/5;label=datetime.fromtimestamp(tt,TZ).strftime('%m-%d %H:%M')
  svg.append(f'<text x="{x(tt):.1f}" y="{bottom+24}" text-anchor="middle" font-size="12" fill="#586781">{label}</text>')
 svg.append(f'<text x="90" y="{bottom+49}" font-size="12" fill="#586781">{escape(notes[period][0])}</text>')
svg.append('</svg>')
(P/'overview.svg').write_text(''.join(svg))
print(P/'btc.html')
print(P/'overview.svg')
