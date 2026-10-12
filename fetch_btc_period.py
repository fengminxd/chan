"""下载本次 BTCUSDT 永续合约研究区间；公开接口，无账户或交易操作。"""
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from chan import Candle, validate_candles


def main():
    utc = timezone.utc
    start = datetime(2026, 6, 5, 16, tzinfo=utc)
    end = datetime(2026, 8, 18, 16, tzinfo=utc)
    context_start = start - timedelta(days=120)
    folder = Path('reports/BTCUSDT_perpetual_20260606_20260819')
    folder.mkdir(parents=True, exist_ok=True)
    endpoint = 'https://fapi.binance.com/fapi/v1/klines'
    cursor, cutoff = int(context_start.timestamp() * 1000), int(end.timestamp() * 1000)
    rows, requests = [], []
    while cursor < cutoff:
        url = endpoint + '?' + urlencode(dict(symbol='BTCUSDT', interval='1h',
                                               startTime=cursor, endTime=cutoff-1, limit=1000))
        with urlopen(url, timeout=30) as response:
            page = json.load(response)
        if not isinstance(page, list) or not page:
            raise ValueError(f'行情未覆盖请求区间：{cursor}; {page}')
        if page[0][0] != cursor or any(r[0] >= cutoff for r in page):
            raise ValueError('接口返回的时间范围不符合请求')
        if any(b[0]-a[0] != 3600000 for a, b in zip(page, page[1:])):
            raise ValueError('接口返回缺根或重复小时线')
        rows.extend(page)
        requests.append({'url': url, 'count': len(page), 'first': page[0][0], 'last': page[-1][0]})
        cursor = page[-1][0] + 3600000
        print(f'Downloaded {len(rows)} hourly candles', flush=True)
    expected = int((end-context_start).total_seconds()/3600)
    if len(rows) != expected or cursor != cutoff:
        raise ValueError('未取得完整区间，停止分析')
    bars = [Candle(datetime.fromtimestamp(r[0]/1000, utc), *map(float, r[1:6])) for r in rows]
    validate_candles(bars)
    for name, selected in [('context_1h.csv', rows), ('window_1h.csv', [r for r in rows if r[0] >= int(start.timestamp()*1000)])]:
        with (folder/name).open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['timestamp','open','high','low','close','volume'])
            for r in selected:
                w.writerow([datetime.fromtimestamp(r[0]/1000, utc).isoformat(), *r[1:6]])
    (folder/'exchange_klines.json').write_text(json.dumps(rows))
    metadata = {'symbol':'BTCUSDT','market':'Binance USD-M perpetual','price_type':'trade_price',
                'window_start_utc':start.isoformat(),'window_end_utc_exclusive':end.isoformat(),
                'context_start_utc':context_start.isoformat(),'context_days':120,
                'downloaded_at':datetime.now(utc).isoformat(),'hourly_count':len(rows),
                'window_hourly_count':1776,'requests':requests,
                'sha256':{name:hashlib.sha256((folder/name).read_bytes()).hexdigest()
                          for name in ['context_1h.csv','window_1h.csv','exchange_klines.json']}}
    (folder/'source.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    print(folder/'source.json')


if __name__ == '__main__':
    main()
