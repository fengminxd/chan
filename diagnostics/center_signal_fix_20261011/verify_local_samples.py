"""离线检查现有行情的结构、信号引用及证据；不下载或覆盖行情报告。"""
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chan import analyze, read_csv
from report_btc_4h_auto import automatic_views
from unified import build_graph


def main():
    samples = [
        ('BTCUSDT', 4, 'reports/BTCUSDT_20260626_20261010_4h_2h_1h/history_4h.csv'),
        ('BTCUSDT', 1, 'reports/BTCUSDT_20260626_20261010_4h_2h_1h/history_1h.csv'),
        ('STRKUSDT', 1, 'reports/STRKUSDT_1h_20260817_20261010/history_1h.csv'),
    ]
    results = []
    for symbol, hours, name in samples:
        path = ROOT / name
        raw = read_csv(path)
        started = time.monotonic()
        report = analyze(raw, hours)
        asof = raw[-1].time + timedelta(hours=hours)
        payload = automatic_views(report, raw, raw[0].time, asof, period_hours=hours)
        for view in payload['views'].values():
            for signal in view['signals']:
                assert signal['evidence_at'] <= asof
                if signal.get('center_id'):
                    assert signal['center_id'] in view['centers']
            assert all(not n.get('direct_break') for n in view['nodes'] if n['kind'] != 'stroke')
        # 仅复用节点引用/范围校验；不将这些1h/4h输入冒充1m行情，也不导出此图。
        build_graph(report, asof)
        historical = sum(len(layer.get('historical_centers', []))
                         for block in report['blocks']
                         for layer in block['theory']['type_hierarchy']['levels'])
        item = dict(symbol=symbol, hours=hours, bars=len(raw), source=name,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    historical_centers=historical, signals=payload['views']['types']['signal_counts'],
                    seconds=round(time.monotonic()-started, 3), graph_reference_validation=True)
        results.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    (Path(__file__).parent / 'local_samples.json').write_text(
        json.dumps(results, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
