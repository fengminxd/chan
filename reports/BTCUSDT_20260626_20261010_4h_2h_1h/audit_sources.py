"""Recheck rare native-period differences in cached prehistory against the API."""
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

F=Path(__file__).resolve().parent
sys.path.insert(0,str(F.parents[1]))
from chan import Candle,read_csv
from report_btc_4h_auto import dump

data={h:read_csv(F/f'history_{h}h.csv') for h in (1,2,4)}
source=json.loads((F/'source.json').read_text())
groups={}
bad=[]
for h in (2,4):
    g=defaultdict(list)
    for c in data[1]:g[int(c.time.timestamp())//(h*3600)].append(c)
    for c in data[h]:
        b=g[int(c.time.timestamp())//(h*3600)]
        expected=[b[0].open,max(x.high for x in b),min(x.low for x in b),b[-1].close]
        native=[c.open,c.high,c.low,c.close]
        if any(abs(a-z)>.10001 for a,z in zip(expected,native)):
            bad.append(dict(period=f'{h}h',time=c.time,aggregated=expected,native=native))
audit=dict(original_differences=bad,changes=[],requests=[])
starts=sorted(set(int(c['time'].timestamp())//14400*14400 for c in bad))
for start in starts:
    for h in (1,2,4):
        params=dict(symbol='BTCUSDT',interval=f'{h}h',startTime=start*1000,endTime=(start+14400)*1000-1,limit=1000)
        url='https://fapi.binance.com/fapi/v1/klines?'+urlencode(params)
        with urlopen(url,timeout=30) as r:raw=r.read();server_date=r.headers.get('Date')
        name='recheck_'+hashlib.sha256(url.encode()).hexdigest()[:20]+'.json'
        (F/'raw_pages'/name).write_bytes(raw)
        meta=dict(url=url,file='raw_pages/'+name,sha256=hashlib.sha256(raw).hexdigest(),server_date=server_date,downloaded_at=datetime.now(timezone.utc))
        audit['requests'].append(meta)
        rows=json.loads(raw);assert len(rows)==4//h
        mapping={c.time:i for i,c in enumerate(data[h])}
        for row in rows:
            c=Candle(datetime.fromtimestamp(row[0]/1000,timezone.utc),*map(float,row[1:6]));idx=mapping[c.time];old=data[h][idx]
            if old!=c:
                audit['changes'].append(dict(period=f'{h}h',time=c.time,before=old,after=c))
                data[h][idx]=c
for h,bars in data.items():
    path=F/f'history_{h}h.csv'
    with path.open('w',newline='') as f:
        w=csv.writer(f);w.writerow(['timestamp','open','high','low','close','volume'])
        w.writerows([c.time.isoformat(),c.open,c.high,c.low,c.close,c.volume] for c in bars)
    source['periods'][f'{h}h']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
source['requests'].extend(audit['requests'])
source['cache_recheck']='source_audit.json'
dump(F/'source_audit.json',audit)
dump(F/'source.json',source)
print('differences',len(bad),'rechecked buckets',len(starts),'changed candles',len(audit['changes']))
