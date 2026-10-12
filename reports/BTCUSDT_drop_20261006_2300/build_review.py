"""Render frozen review through the shared renderer and preserve direct_break styles."""
import json,subprocess,sys
from pathlib import Path
P=Path(__file__).resolve().parent
ROOT=P.parents[1]
s=json.loads((P/'source.json').read_text())
notes={
'4h':[
'复盘起点：2026-10-06 23:00 北京时间。结构只使用已收盘K线，各原生周期独立分析。',
'高位震荡背景：近期笔重叠观察区83,130.1–85,146.4，外围82,500.1–87,385.1；这些不是严格完成走势中枢。',
'23:00当时最新已收盘4h柱为16:00–20:00，MACD柱-25.34，DIF338.00低于DEA350.67；反弹仍未消除4h动能走弱。',
'截至本次快照，4h上涨段62,228.8→87,385.1仍待反向结构确认，不能因本次急跌宣布4h线段已结束。'],
'1h':[
'87,249.6→86,976.1→86,664.9形成逐次降低的反弹高点。最后高点当时尚未确认，须结合历史截面阅读。',
'84,910历史笔口径三买相对旧核心83,450.1–84,638.5；其后未突破前高。10月7日09:45–10:00跌破84,910、重回旧核心，延续假设失败。',
'85,072.7–86,034.0是后来确认的新笔重叠核心，回放首次出现于10月7日02:00，不能提前用在10月6日23:00。',
'23:00–24:00：86,563.3→85,675.1，下跌888.2；成交量为此前20小时均量3.08倍，仅为描述性量价辅助。',
'截至快照实际已触及83,500，而程序1h末笔仍停留在85,369候选低点；这是尾部滞后，不能把图上末笔当成即时价格。',
'本轮没有新确认严格S1/S2/S3；旧中枢下沿83,450.1尚未跌破，不得把当前直接叫作该中枢三卖。'],
'15m':[
'86,664.9顶位前已有MACD局部价格创新高、DIF未创新高的预警；程序并未把该点发布为严格顶背驰或S1。',
'23:30–23:45收盘MACD柱转负至-43.05；23:45–24:00跌穿局部低点85,908.8，柱扩大至-118.13。',
'10月7日03:00–03:15触及85,369，回到85,044.7–85,390.3笔重叠区，向上离开后不回枢的三买假设失败。',
'09:45–10:00同时跌破85,072.7、85,044.7、84,910与84,638.5；10:00–10:15最低83,500，距86,664.9回撤3.65%。',
'direct_break发起笔仍是10月2日87,249.6→83,841.9；本轮新下笔83,500越过其终点后，12:30截面才确认此前上涨段结束。',
'图中一笔破坏只加粗发起笔，线宽3.2，颜色和实虚线保持；它不等于当时线段已结束。']}
review=dict(asof=s['as_of'],notes=notes,views={x:f'views_{x}.json' for x in notes},analysis={x:f'analysis_{x}.json' for x in notes},context_starts={x:s['periods'][x]['context_start'] for x in notes})
(P/'analysis_notes.json').write_text(json.dumps(review,ensure_ascii=False,indent=2))
subprocess.run([sys.executable,str(ROOT/'reports/render_multiperiod_snapshot.py'),str(P)],check=True)
