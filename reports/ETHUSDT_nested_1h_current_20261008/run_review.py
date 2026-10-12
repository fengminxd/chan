"""本次ETHUSDT永续快照：保存来源，补齐1m，运行1h逐级定位分析。"""
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from chan import Candle, json_default, parse_time, read_csv, validate_candles
from fetch_btc_minutes import validate_page

FOLDER = Path(__file__).resolve().parent
API = "https://fapi.binance.com/fapi/v1/"
UTC = timezone.utc
START = parse_time("2026-06-01T00:00:00Z")


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=json_default, allow_nan=False))


def fetch():
    requests = []
    pages = FOLDER/"raw_pages"
    pages.mkdir(exist_ok=True)

    def get(name, params=None, cache=False):
        url = API+name+("?"+urlencode(params) if params else "")
        path = pages/(hashlib.sha256(url.encode()).hexdigest()[:24]+".json")
        meta_path = path.with_suffix(".meta.json")
        if cache and path.exists() and meta_path.exists():
            raw, meta = path.read_bytes(), json.loads(meta_path.read_text())
            if meta["url"] != url or meta["sha256"] != hashlib.sha256(raw).hexdigest():
                raise ValueError("行情缓存校验失败")
        else:
            with urlopen(url, timeout=25) as response:
                raw = response.read()
                meta = dict(url=url, downloaded_at=datetime.now(UTC), server_date=response.headers.get("Date"),
                            sha256=hashlib.sha256(raw).hexdigest())
            path.write_bytes(raw)
            dump(meta_path, meta)
        requests.append({**meta, "file": str(path.relative_to(FOLDER))})
        return json.loads(raw)

    server = get("time")
    cutoff = min(server["serverTime"], int(datetime.now(UTC).timestamp()*1000))//60000*60000
    own = FOLDER/"history_1m.csv"
    cache = own if own.exists() else ROOT/"reports/ETHUSDT_entries_20261007/history_1m.csv"
    cache_digest = hashlib.sha256(cache.read_bytes()).hexdigest() if cache else None
    bars = [c for c in read_csv(cache) if START <= c.time and int(c.time.timestamp()*1000)+60000 <= cutoff] if cache else []
    cursor = int(bars[-1].time.timestamp()*1000)+60000 if bars else int(START.timestamp()*1000)
    downloaded = 0
    while cursor < cutoff:
        end = min(cutoff, cursor+1000*60000)
        rows = get("klines", dict(symbol="ETHUSDT", interval="1m", startTime=cursor, endTime=end-1, limit=1000), True)
        validate_page(rows, cursor, end)
        bars.extend(Candle(datetime.fromtimestamp(r[0]/1000, UTC), *map(float, r[1:6])) for r in rows)
        downloaded += len(rows)
        cursor = end
        if downloaded % 10000 == 0 or cursor == cutoff:
            print(f"new minutes={downloaded}; through {bars[-1].time}", flush=True)
        time.sleep(.12)
    validate_candles(bars)
    expected = (cutoff-int(START.timestamp()*1000))//60000
    if len(bars) != expected or any((b.time-a.time).total_seconds()!=60 for a,b in zip(bars,bars[1:])):
        raise ValueError("1m行情未完整连续覆盖固定起点至截止时刻")
    with own.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp","open","high","low","close","volume"])
        w.writerows([c.time.isoformat(), c.open,c.high,c.low,c.close,c.volume] for c in bars)
    ticker = get("ticker/24hr", dict(symbol="ETHUSDT"))
    premium = get("premiumIndex", dict(symbol="ETHUSDT"))
    info = get("exchangeInfo")
    contract = next(s for s in info["symbols"] if s["symbol"] == "ETHUSDT")
    if contract["contractType"] != "PERPETUAL":
        raise ValueError("标的不是永续合约")
    dump(FOLDER/"source.json", dict(symbol="ETHUSDT", market="Binance USD-M perpetual",
         as_of=datetime.fromtimestamp(cutoff/1000,UTC), start=START, count=len(bars),
         cache_source=str(cache.relative_to(ROOT)) if cache else None, cache_sha256=cache_digest,
         history_sha256=hashlib.sha256(own.read_bytes()).hexdigest(), ticker=ticker, premium=premium,
         price_filter=next(x for x in contract["filters"] if x["filterType"]=="PRICE_FILTER"), requests=requests,
         validation="all requested minutes closed and contiguous; native OHLCV; no synthetic data"))
    print("FETCH COMPLETE", len(bars), flush=True)


def analyze():
    import entry_plan
    source = json.loads((FOLDER/"source.json").read_text())
    bars = read_csv(FOLDER/"history_1m.csv")
    original = entry_plan.analyze
    def inspect(candles, hours, **kwargs):
        from chan_time import period_name
        name = period_name(hours)
        started = time.monotonic()
        print(f"ANALYZE {name}: {len(candles)} closed bars", flush=True)
        report = original(candles, hours, **kwargs)
        dump(FOLDER/f"analysis_{name}.json", report)
        last = report['blocks'][-1] if report['blocks'] else {}
        print(f"DONE {name}: {time.monotonic()-started:.1f}s; strokes={len(last.get('strokes',[]))}; segments={len(last.get('segments',[]))}", flush=True)
        return report
    entry_plan.analyze = inspect
    report = entry_plan.analyze_entries(bars, parse_time(source["as_of"]), entry_policy=entry_plan.EntryPolicy(profile="nested_1h"))
    report["data_origin"] = dict(symbol="ETHUSDT", source="source.json", minute_csv="history_1m.csv")
    report["code_sha256"] = {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
                           ("entry_plan.py","entry_matrix.py","chan.py","chan_structure.py","chan_theory.py","chan_dynamics.py")}
    entry_plan.write_entry_outputs(report, FOLDER)
    print(json.dumps(report["by_main_period"], ensure_ascii=False, default=json_default),flush=True)


if __name__ == "__main__":
    {"fetch":fetch, "analyze":analyze}[sys.argv[1]]()
