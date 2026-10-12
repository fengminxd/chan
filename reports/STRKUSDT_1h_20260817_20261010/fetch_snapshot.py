"""Freeze verified native STRK perpetual candles with 180 days of context."""
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chan import Candle, read_csv, validate_candles
from report_btc_4h_auto import dump

FOLDER = Path(__file__).resolve().parent
UTC = timezone.utc


def main():
    pages = FOLDER/'raw_pages'
    pages.mkdir(exist_ok=True)
    requests = []

    def get(endpoint, **params):
        url = 'https://fapi.binance.com/fapi/v1/'+endpoint+('?' + urlencode(params) if params else '')
        with urlopen(url, timeout=30) as response:
            raw, server_date = response.read(), response.headers.get('Date')
        filename = hashlib.sha256(url.encode()).hexdigest()[:20]+'.json'
        (pages/filename).write_bytes(raw)
        requests.append(dict(url=url, file='raw_pages/'+filename, server_date=server_date,
                             downloaded_at=datetime.now(UTC), sha256=hashlib.sha256(raw).hexdigest()))
        return json.loads(raw)

    server = get('time')
    cutoff = min(server['serverTime'], int(datetime.now(UTC).timestamp()*1000))//60000*60000
    ticker = get('ticker/24hr', symbol='STRKUSDT')
    contract = next(s for s in get('exchangeInfo')['symbols'] if s['symbol']=='STRKUSDT')
    assert contract['contractType']=='PERPETUAL' and contract['quoteAsset']=='USDT'
    periods = {}
    cache_sources = []

    def klines(period, start, end, step):
        rows = get('klines', symbol='STRKUSDT', interval=period, startTime=start, endTime=end-1, limit=1000)
        assert isinstance(rows,list) and len(rows)==(end-start)//step
        assert all(len(r)==12 and r[0]==start+i*step and r[6]==r[0]+step-1 for i,r in enumerate(rows))
        bars = [Candle(datetime.fromtimestamp(r[0]/1000,UTC),*map(float,r[1:6])) for r in rows]
        validate_candles(bars)
        return bars

    for h in (1,):
        period, step = f'{h}h', h*3600000
        bars = []
        cursor = int(datetime.fromisoformat('2026-02-18T00:00:00+08:00').timestamp()*1000)
        end = cutoff//step*step
        while cursor < end:
            stop = min(end,cursor+1000*step)
            bars.extend(klines(period,cursor,stop,step))
            cursor = stop
        validate_candles(bars)
        assert all(int((b.time-a.time).total_seconds()*1000)==step for a,b in zip(bars,bars[1:]))
        out = FOLDER/f'history_{period}.csv'
        with out.open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['timestamp','open','high','low','close','volume'])
            w.writerows([b.time.isoformat(),b.open,b.high,b.low,b.close,b.volume] for b in bars)
        periods[period]=dict(context_start=bars[0].time,closed_end=datetime.fromtimestamp(end/1000,UTC),
                             closed_bars=len(bars),sha256=hashlib.sha256(out.read_bytes()).hexdigest())
        print(period,len(bars),flush=True)
    minute_start = cutoff//14400000*14400000
    minutes = klines('1m',minute_start,cutoff,60000) if cutoff>minute_start else []
    partials = {}
    for h in (1,):
        begin=cutoff//(h*3600000)*(h*3600000)
        tail=[c for c in minutes if c.time.timestamp()*1000>=begin]
        partials[f'{h}h']=None if not tail else dict(time=datetime.fromtimestamp(begin/1000,UTC),
            open=tail[0].open,high=max(c.high for c in tail),low=min(c.low for c in tail),
            close=tail[-1].close,volume=sum(c.volume for c in tail),partial=True,available_minutes=len(tail))
    dump(FOLDER/'partials.json',partials)
    dump(FOLDER/'source.json',dict(symbol='STRKUSDT',market='Binance USD-M perpetual',price_type='trade_price',
        as_of=datetime.fromtimestamp(cutoff/1000,UTC),ticker=ticker,
        ticker_time=datetime.fromtimestamp(ticker['closeTime']/1000,UTC),price_decimals=5,
        tick_size=next(f['tickSize'] for f in contract['filters'] if f['filterType']=='PRICE_FILTER'),
        contract_type=contract['contractType'],periods=periods,requests=requests,cache_sources=cache_sources,
        display_start='2026-08-17T00:00:00+08:00',context_policy='180 day prehistory',
        validation='Native closed bars continuous and OHLC validated; partial bars from closed minutes only'))


if __name__=='__main__':
    main()
