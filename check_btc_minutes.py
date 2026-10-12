"""校验已下载分钟文件及原始分页，并与上次研究的小时数据逐根核对。"""
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from chan import parse_time
from fetch_btc_minutes import MINUTE_MS, validate_page


def main():
    folder = Path("reports/BTCUSDT_perpetual_1m_20260606_20260819")
    prior = Path("reports/BTCUSDT_perpetual_20260606_20260819/context_1h.csv")
    source = json.loads((folder/"source.json").read_text())
    rows = []
    expected = int(parse_time(source["context_start_utc"]).timestamp()*1000)
    for request in source["requests"]:
        raw = (folder/request["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == request["sha256"], "原始分页摘要不符"
        page = json.loads(raw)
        assert page[0][0] == expected, "分页边界存在缺口或重复"
        validate_page(page, expected, page[-1][0]+MINUTE_MS)
        expected = page[-1][0]+MINUTE_MS
        rows.extend(page)
    cutoff = int(parse_time(source["window_end_utc_exclusive"]).timestamp()*1000)
    assert expected == cutoff, "缺少尾部行情"
    actual = []
    for name in ("context_1m.csv", "window_1m.csv"):
        path = folder/name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"][name], "CSV 摘要不符"
        with path.open() as f:
            actual.extend(list(csv.reader(f))[1:])
    assert len(actual) == len(rows) == source["minute_count"], "数量不符"
    for saved, row in zip(actual, rows):
        assert int(parse_time(saved[0]).timestamp()*1000) == row[0], "CSV 时间不符"
        assert saved[1:] == row[1:6], "CSV 数值与原始响应不符"
    with prior.open() as f:
        hourly = list(csv.DictReader(f))
    assert len(rows) == len(hourly)*60, "旧小时区间与新分钟区间不一致"
    mismatches = []
    for i, h in enumerate(hourly):
        page = rows[i*60:(i+1)*60]
        assert int(parse_time(h["timestamp"]).timestamp()*1000) == page[0][0], "小时起点不一致"
        values = {"open": Decimal(page[0][1]), "high": max(Decimal(r[2]) for r in page),
                  "low": min(Decimal(r[3]) for r in page), "close": Decimal(page[-1][4]),
                  "volume": sum(Decimal(r[5]) for r in page)}
        for field, value in values.items():
            if value != Decimal(h[field]):
                mismatches.append({"time": h["timestamp"], "field": field,
                                   "from_minutes": str(value), "previous_hour": h[field]})
    result = {"status": "passed" if not mismatches else "hourly_source_mismatch",
              "minute_count": len(rows), "raw_pages": len(source["requests"]),
              "missing_minutes": 0, "duplicate_minutes": 0, "csv_matches_raw": True,
              "hourly_bars_compared": len(hourly), "comparison_arithmetic": "Decimal exact",
              "hourly_mismatch_count": len(mismatches), "hourly_mismatches": mismatches,
              "old_hourly_sha256": hashlib.sha256(prior.read_bytes()).hexdigest(),
              "csv_sha256": source["sha256"]}
    (folder/"data_validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "hourly_mismatches"}, ensure_ascii=False))
    if mismatches:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
