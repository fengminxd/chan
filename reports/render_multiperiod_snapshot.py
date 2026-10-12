"""Render a frozen futures snapshot with explicitly reviewed analysis inputs."""
import argparse
import csv
import json
import re
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('folder',type=Path)
p=parser.parse_args().folder.resolve()
s=json.loads((p/'source.json').read_text())
review=json.loads((p/'analysis_notes.json').read_text())
assert review['asof']==s['as_of']
symbol=s['symbol'];asset=symbol[:-4]
assert re.fullmatch(r'[A-Z0-9]+USDT',symbol)
s['ticker_time']=datetime.fromtimestamp(s['ticker']['closeTime']/1000,timezone.utc).isoformat()
periods={}
for period in ['4h','1h','15m']:
 v=json.loads((p/review['views'][period]).read_text());v['analysis_period']=period
 for value in v['views'].values():
  for n in [*value['nodes'],*value['signals'],*value['divergences'],*value['centers'].values()]:
   if 'basis' in n:n['basis']=n['basis'].replace('0.25h','15m')
 rows=json.loads((p/f'partial_{period}.json').read_text())
 if rows:
  r=rows[0];v['bars'].append(dict(time=datetime.fromtimestamp(r[0]/1000,timezone.utc).isoformat(),
      **dict(zip(['open','high','low','close','volume'],map(float,r[1:6]))),partial=True))
 v['source']={**s['periods'][period], 'context_start':review['context_starts'][period],
   'partial_minutes':round((datetime.fromisoformat(s['as_of'])-datetime.fromisoformat(s['periods'][period]['closed_end'])).total_seconds()/60,1)}
 v['analysis_notes']=review['notes'][period];periods[period]=v
payload=dict(symbol=symbol,start=(datetime.fromisoformat(s['as_of'])-timedelta(days=45)).isoformat(),
 asof=s['as_of'],source=s,price_decimals=s['price_decimals'],default_period='1h',default_basis='strokes',
 default_history='snapshot',histories={'snapshot':{'label':'本次冻结行情','periods':periods}})
(p/'chart_data.json').write_text(json.dumps(payload,ensure_ascii=False))
page=(ROOT/'futures_multiperiod_template.html').read_text().replace('SUIUSDT',symbol).replace('SUI 永续',asset+' 永续')
page=page.replace('1h / 4h','4h / 1h / 15m').replace('1h与4h','4h、1h与15m').replace('最近100天','本次快照').replace('>100天<','>全窗口<')
page=re.sub(r'<select id="history">.*?</select>','<select id="history"><option value="snapshot">本次冻结行情</option></select>',page)
page=page.replace('<option value="4h" selected>4h</option>','<option value="4h">4h</option><option value="15m">15m</option>')
for expr in ['ALL.default_period','D.analysis_period']:
 page=page.replace('Number('+expr+'.slice(0,-1))*3600000','({"4h":14400000,"1h":3600000,"15m":900000})['+expr+']')
page=page.replace('默认完成走势中枢采用修正后的完成走势递归；','默认笔中枢是笔重叠观察口径，不直接等同于完成次级走势中枢；')
page=page.replace(' · <a href="analysis_1h_recent.json">近期1h分析</a> · <a href="analysis_4h_recent.json">近期4h分析</a>','')
page=page.replace('href="analysis_1h.json">全历史1h','href="'+review['analysis']['1h']+'">本次1h分析').replace('全历史4h','本次4h分析')
page=page.replace('PAYLOAD_JSON',json.dumps(payload,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c'))
(p/(asset.lower()+'.html')).write_text(page)
(p/'chart.js').write_text('\n'.join(re.findall(r'<script>(.*?)</script>',page,re.S)))
for category in ['signals','divergences','structures']:
 with (p/f'{category}.csv').open('w',newline='') as f:
  w=csv.writer(f);w.writerow(['period','basis','kind','time_UTC','price_or_ZD','ZG','status','evidence_UTC'])
  for period,v in periods.items():
   for basis,x in v['views'].items():
    for n in x['nodes' if category=='structures' else category]:
     w.writerow([period,basis,n.get('label',n.get('kind')),n.get('time',n.get('start')),n.get('price',n.get('zd',n.get('end_price'))),n.get('zg',''),n.get('certainty',n.get('state')),n.get('evidence_at','')])
print(p/(asset.lower()+'.html'))
