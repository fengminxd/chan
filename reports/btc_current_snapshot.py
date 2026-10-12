"""Freeze Binance public futures candles and audit 4h/1h/15m separately."""
import csv
import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chan import Candle, analyze, validate_candles, read_csv
from report_btc_4h_auto import automatic_views, dump

UTC = timezone.utc
FOLDER = ROOT / 'reports/BTCUSDT_current_20260928'
API = 'https://fapi.binance.com/fapi/v1/'
SYMBOL = 'BTCUSDT'
PERIODS = [('4h', 4, 280), ('1h', 1, 280), ('15m', .25, 90)]

def fetch():
    pages = FOLDER / 'raw_pages'
    pages.mkdir(parents=True, exist_ok=True)
    requests = []
    def get(name, **params):
        url = API + name + ('?' + urlencode(params) if params else '')
        with urlopen(url, timeout=40) as r:
            raw, date = r.read(), r.headers.get('Date')
        filename = hashlib.sha256(url.encode()).hexdigest()[:20] + '.json'
        (pages / filename).write_bytes(raw)
        requests.append(dict(url=url, file='raw_pages/' + filename,
                             sha256=hashlib.sha256(raw).hexdigest(),
                             server_date=date, downloaded_at=datetime.now(UTC)))
        return json.loads(raw)
    server = get('time')
    cutoff = min(server['serverTime'], int(datetime.now(UTC).timestamp()*1000))
    ticker = get('ticker/24hr', symbol=SYMBOL)
    premium = get('premiumIndex', symbol=SYMBOL)
    info = get('exchangeInfo')
    contract = next(s for s in info['symbols'] if s['symbol']==SYMBOL)
    assert contract['contractType'] in ('PERPETUAL','TRADIFI_PERPETUAL') and contract['quoteAsset']=='USDT'
    assert contract['status']=='TRADING' and contract['marginAsset']=='USDT'
    tick = next(f['tickSize'] for f in contract['filters'] if f['filterType']=='PRICE_FILTER')
    def period_fetch(spec):
        period, hours, days = spec
        step = int(hours*3600000)
        end = cutoff // step * step
        # Do not invent pre-listing history or analyze the partial listing bucket.
        first_full = (contract['onboardDate']+step-1)//step*step
        start = max(end-int(days*86400000), first_full)
        rows = []
        for cursor in range(start, end, 1000*step):
            stop = min(end, cursor+1000*step)
            part = get('klines', symbol=SYMBOL, interval=period,
                       startTime=cursor, endTime=stop-1, limit=1000)
            assert isinstance(part, list) and len(part) == (stop-cursor)//step, (period, cursor)
            assert all(len(r)==12 and r[0]==cursor+i*step and r[6]==r[0]+step-1 for i,r in enumerate(part))
            rows.extend(part)
        bars = [Candle(datetime.fromtimestamp(r[0]/1000, UTC), *map(float,r[1:6])) for r in rows]
        validate_candles(bars)
        path = FOLDER/f'history_{period}.csv'
        with path.open('w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['timestamp','open','high','low','close','volume'])
            w.writerows([c.time.isoformat(),c.open,c.high,c.low,c.close,c.volume] for c in bars)
        tail = get('klines', symbol=SYMBOL, interval=period, startTime=end, endTime=cutoff, limit=1)
        dump(FOLDER/f'partial_{period}.json', tail)
        print(period, len(rows), bars[-1].time, flush=True)
        return period, dict(context_start=bars[0].time, closed_end=datetime.fromtimestamp(end/1000,UTC),
                            count=len(rows), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    with ThreadPoolExecutor(max_workers=3) as pool:
        periods = dict(pool.map(period_fetch, PERIODS))
    dump(FOLDER/'source.json', dict(symbol=SYMBOL, market='Binance USD-M perpetual',
        contract_type=contract['contractType'], tick_size=tick,
        underlying_type=contract.get('underlyingType'), underlying_subtype=contract.get('underlyingSubType'),
        onboard_time=datetime.fromtimestamp(contract['onboardDate']/1000,UTC),
        listing_bucket_policy='exclude_partial_listing_bucket',
        price_decimals=len(tick.rstrip('0').split('.')[1]) if '.' in tick else 0,
        as_of=datetime.fromtimestamp(cutoff/1000,UTC), ticker=ticker, premium=premium,
        periods=periods, requests=requests,
        convention='Native candle periods analyzed independently; closed candles only; timestamps are bar opens.'))

def build():
    source = json.loads((FOLDER/'source.json').read_text())
    asof = datetime.fromisoformat(source['as_of'])
    for period, hours, days in PERIODS:
        bars = read_csv(FOLDER/f'history_{period}.csv')
        assert hashlib.sha256((FOLDER/f'history_{period}.csv').read_bytes()).hexdigest()==source['periods'][period]['sha256']
        report = analyze(bars, hours)
        dump(FOLDER/f'analysis_{period}.json',report)
        view = automatic_views(report,bars,asof-timedelta(days=45 if hours>=1 else 10),asof,period_hours=hours)
        view['analysis_period'] = period
        dump(FOLDER/f'views_{period}.json',view)
        b = report['blocks'][-1]
        print(period, 'strokes',len(b['strokes']), 'segments',len(b['segments']), flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=FOLDER)
    parser.add_argument('--fetch', action='store_true')
    parser.add_argument('--symbol', default=SYMBOL)
    args = parser.parse_args()
    SYMBOL = args.symbol.upper()
    if not re.fullmatch(r'[A-Z0-9]{2,20}USDT', SYMBOL): parser.error('Expected a USDT contract symbol')
    FOLDER = args.output_dir.resolve()
    FOLDER.mkdir(parents=True,exist_ok=True)
    if args.fetch: fetch()
    else: build()
