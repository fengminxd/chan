"""Render this frozen review; do not change shared analysis rules."""
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

P = Path(__file__).resolve().parent
ROOT = P.parents[1]
s = json.loads((P/'source.json').read_text())
notes = {
    '4h': [
        '方向：上涨后的高位震荡，偏多背景尚在；87,249.6 未突破 87,385.1，当前不是已确认的新一轮单边上涨。',
        '近期笔重叠观察区 83,130.1–85,146.4，外围 82,500.1–87,385.1；前一笔中枢 76,151.9–79,555.5。',
        '完成走势口径的历史 L1 中枢 67,300.0–75,998.9；62,228.8→87,385.1 上涨段仍待反向确认，不能把笔区间冒充严格走势中枢。',
        '87,385.1 顶背驰辅助候选的 MACD 面积比 0.713；严格趋势背驰未确认。最新 DIF 314.5、DEA 343.4，柱 -57.9。',
        '程序笔端点会受包含处理影响：87,385.1→83,130.1 的实际原始路径低点为 82,832.0。交易失效点应核对原始价格。'],
    '1h': [
        '主交易级别：向上离开旧笔中枢后的回踩，短线震荡；84,910 历史笔口径三买已出现，当前等待新的15m入场证据。',
        '近期已建立笔中枢 83,450.1–84,638.5；10月6日00:00的84,910首次回踩未入中枢，证据到11:00才可用。',
        '已确认上涨段 74,909.4→87,385.1、下跌段87,385.1→82,500.1；82,500.1→87,249.6上涨段仍待确认。',
        '完成走势 L1 历史中枢 76,853.1–79,384.4；82,500.1为相对它的历史结构三买，证据时间10月3日06:00。',
        '严格B1/B2尚无确认；图中B2/S2几何候选缺少一类点前提，不能作为已确认二类点交易。87,385.1为MACD辅助S1候选。',
        '最新正柱由190.0缩至88.2，反弹减速，不等于新顶背驰。候选下笔之后实际已反弹至86,664.9，再回落；程序尾部滞后，不能当作实时方向。'],
    '15m': [
        '当前：85,072.7反弹至86,664.9后回落，现价约85,544；回落低点未确认，暂不判定新入场点成立。',
        '笔重叠观察区85,044.7–85,390.3。若完整回踩守住85,390.3，再出现底分型、突破反弹高点并回踩守住，可观察笔口径三买。',
        '最近三根负柱-151.1、-175.3、-183.2仍扩大，尚无本轮下跌末端底背驰证据。',
        '跌回85,390.3取消本次15m三买假设；再看85,072.7、85,044.7和84,910。跌破84,910削弱近期1h多头结构；重入84,638.5以下旧中枢，三买延续逻辑失效。',
        '偏空：向下离开85,044.7后首次完整反抽低于此位，并出现顶分型及向下确认，再观察15m三卖。1h三卖须另等跌破83,450.1后反抽不回。']}
(P/'analysis_notes.json').write_text(json.dumps({'asof':s['as_of'],'notes':notes},ensure_ascii=False,indent=2))

# Existing interactive renderer has a dated standalone illustration after this marker.
# Render only its data-driven interactive chart with the matching snapshot notes.
source = (ROOT/'reports/render_btc_snapshot.py').read_text().split('# Standalone overview,')[0]
sys.argv = ['render_btc_snapshot.py','--output-dir',str(P)]
exec(compile(source,str(ROOT/'reports/render_btc_snapshot.py'),'exec'),{'__file__':str(ROOT/'reports/render_btc_snapshot.py'),'__name__':'__main__'})

# Verify overlapping native candles agree, including full raw-path extremes.
series = {}
for tf in ['4h','1h','15m']:
    series[tf] = list(csv.DictReader((P/f'history_{tf}.csv').open()))
checked = 0
for higher, lower, count in [('4h','1h',4),('1h','15m',4)]:
    low = {r['timestamp']:r for r in series[lower]}
    step = timedelta(hours=1 if lower=='1h' else .25)
    for r in series[higher]:
        start = datetime.fromisoformat(r['timestamp'])
        children = [low.get((start+i*step).isoformat()) for i in range(count)]
        if not all(children): continue
        values = [float(children[0]['open']),max(float(x['high']) for x in children),min(float(x['low']) for x in children),float(children[-1]['close'])]
        assert all(abs(float(r[k])-v)<1e-6 for k,v in zip(['open','high','low','close'],values))
        checked += 1
print('Native OHLC aggregation checks:', checked)
print(P/'btc.html')
