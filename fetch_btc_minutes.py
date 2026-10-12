"""下载 Binance BTCUSDT USDT 本位永续成交价分钟线；可校验缓存后续传。"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from chan import Candle, parse_time, validate_candles

ENDPOINT = "https://fapi.binance.com/fapi/v1/klines"
MINUTE_MS = 60000


def validate_page(rows, start, end):
    if not isinstance(rows, list) or len(rows) != (end-start)//MINUTE_MS:
        raise ValueError(f"接口未完整覆盖请求：{start}..{end}")
    for i, r in enumerate(rows):
        if len(r) != 12 or r[0] != start+i*MINUTE_MS or r[6] != r[0]+MINUTE_MS-1:
            raise ValueError("分钟开闭时间、顺序或接口字段异常")
    validate_candles([Candle(datetime.fromtimestamp(r[0]/1000, timezone.utc),
                            *map(float, r[1:6])) for r in rows])


def download(url):
    for attempt in range(5):
        try:
            with urlopen(url, timeout=40) as response:
                return response.read(), {k: v for k, v in response.headers.items()
                                        if k.lower() in ("date", "x-mbx-used-weight-1m", "content-type")}
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            time.sleep(min(60, max(2**attempt, int(exc.headers.get("Retry-After", "1")))))
        except (URLError, TimeoutError):
            if attempt == 4:
                raise
            time.sleep(2**attempt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2026-06-06T00:00:00+08:00")
    parser.add_argument("--end", default="2026-08-19T00:00:00+08:00")
    parser.add_argument("--context-days", type=int, default=120)
    parser.add_argument("--output-dir", type=Path,
                        default=Path("reports/BTCUSDT_perpetual_1m_20260606_20260819"))
    args = parser.parse_args()
    start, end = parse_time(args.start), parse_time(args.end)
    if args.context_days < 1 or end <= start or any(t.second or t.microsecond for t in (start, end)):
        parser.error("需要正整数前置天数、有效区间和整分钟边界")
    context_start = start-timedelta(days=args.context_days)
    folder, rows, requests = args.output_dir, [], []
    pages = folder/"raw_pages"
    pages.mkdir(parents=True, exist_ok=True)
    cursor, cutoff = int(context_start.timestamp()*1000), int(end.timestamp()*1000)
    while cursor < cutoff:
        page_end = min(cutoff, cursor+1000*MINUTE_MS)
        url = ENDPOINT+"?"+urlencode(dict(symbol="BTCUSDT", interval="1m", startTime=cursor,
                                          endTime=page_end-1, limit=1000))
        path = pages/f"{cursor}_{page_end}.json"
        meta_path = path.with_suffix(".meta.json")
        if path.exists() and meta_path.exists():
            payload, meta = path.read_bytes(), json.loads(meta_path.read_text())
            if meta["url"] != url or meta["sha256"] != hashlib.sha256(payload).hexdigest():
                raise ValueError(f"缓存来源或摘要不匹配：{path}")
            page = json.loads(payload)
            validate_page(page, cursor, page_end)
        else:
            payload, headers = download(url)
            page = json.loads(payload)
            validate_page(page, cursor, page_end)
            meta = {"url": url, "downloaded_at": datetime.now(timezone.utc).isoformat(),
                    "headers": headers, "sha256": hashlib.sha256(payload).hexdigest()}
            path.write_bytes(payload)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            time.sleep(.15)
        rows.extend(page)
        requests.append({**meta, "file": str(path.relative_to(folder)), "count": len(page),
                         "first_open_ms": page[0][0], "last_open_ms": page[-1][0]})
        cursor = page_end
        if len(requests) % 10 == 0 or cursor == cutoff:
            print(f"已校验 {len(rows):,} / {int((end-context_start).total_seconds()/60):,} 根 1m", flush=True)
    start_ms = int(start.timestamp()*1000)
    outputs = {"context_1m.csv": [r for r in rows if r[0] < start_ms],
               "window_1m.csv": [r for r in rows if r[0] >= start_ms]}
    for name, selected in outputs.items():
        with (folder/name).open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            writer.writerows([datetime.fromtimestamp(r[0]/1000, timezone.utc).isoformat(), *r[1:6]] for r in selected)
    manifest = {"symbol": "BTCUSDT", "market": "Binance USD-M perpetual", "price_type": "trade_price",
                "interval": "1m", "endpoint": ENDPOINT, "window_start_utc": start.isoformat(),
                "window_end_utc_exclusive": end.isoformat(), "context_start_utc": context_start.isoformat(),
                "context_days": args.context_days, "minute_count": len(rows),
                "counts": {k: len(v) for k, v in outputs.items()}, "requests": requests,
                "sha256": {name: hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in outputs},
                "validation": "every requested minute present exactly once; OHLCV valid; raw response retained",
                "finished_at": datetime.now(timezone.utc).isoformat()}
    (folder/"source.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(folder/"source.json", flush=True)


if __name__ == "__main__":
    main()
