"""Self-contained SVG preview for Canvas reports, including script-disabled viewers."""
from datetime import datetime, timedelta, timezone
from html import escape
import json


def _time(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() * 1000


def static_chart(payload):
    """Render the same full-window candles and structural annotations without JS."""
    start, end = _time(payload['start']), _time(payload['asof'])
    symbol = escape(payload.get('symbol', 'BTCUSDT'))
    period = escape(payload.get('analysis_period', '4h'))
    period_ms = payload.get('period_ms', 14400000)
    decimals = payload.get('price_decimals', 1)
    bars = payload['bars']
    lo, hi = min(c['low'] for c in bars), max(c['high'] for c in bars)
    pad = (hi - lo) * .12 or 1
    lo, hi = lo - pad, hi + pad
    width, height, left, right, top, bottom = 1600, 690, 90, 1580, 36, 636
    x = lambda t: left + (t - start) / (end - start) * (right - left)
    y = lambda p: top + (hi - p) / (hi - lo) * (bottom - top)
    def date(t):
        return datetime.fromtimestamp(t / 1000, timezone(timedelta(hours=8))).strftime('%Y-%m-%d')
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'role="img" aria-label="{symbol} {period} K线与结构标注静态图" style="display:block;width:100%;height:auto" '
           'font-family="system-ui, sans-serif" font-size="12">',
           '<rect width="1600" height="690" fill="white"/>',
           '<defs><clipPath id="staticPlot"><rect x="90" y="36" width="1490" height="600"/></clipPath></defs>']
    for i in range(7):
        p = lo + (hi - lo) * i / 6
        out.append(f'<path d="M{left},{y(p):.2f} H{right}" stroke="#e8edf3"/>')
        out.append(f'<text x="80" y="{y(p)+4:.2f}" text-anchor="end" fill="#607086">{p:,.{decimals}f}</text>')
    for i in range(6):
        t = start + (end - start) * i / 5
        anchor = 'start' if i == 0 else 'end' if i == 5 else 'middle'
        out.append(f'<text x="{x(t):.2f}" y="669" text-anchor="{anchor}" fill="#607086">{date(t)}</text>')
    out.append(f'<text x="90" y="20" fill="#52647e">{symbol} · {period} · 蓝：笔　橙：段　紫：中枢</text><g clip-path="url(#staticPlot)">')
    for n in payload['nodes']:
        if n['kind'].startswith('center'):
            out.append(f'<rect data-kind="center" x="{x(_time(n["start"])):.2f}" y="{y(n["zg"]):.2f}" '
                       f'width="{max(1,x(_time(n["end"]))-x(_time(n["start"]))):.2f}" '
                       f'height="{max(1,y(n["zd"])-y(n["zg"])):.2f}" fill="#8c51bd" fill-opacity=".1" stroke="#8c51bd"/>')
    bw = max(.8, min(10, (right-left)*period_ms/(end-start)*.6))
    for c in bars:
        t = _time(c['time'])
        cx = x(t + min(period_ms, end-t)/2)
        color = '#3b9e82' if c['close'] >= c['open'] else '#c3677b'
        out.append(f'<g data-kind="candle" fill="{color}" stroke="{color}" stroke-width=".8">'
                   f'<path d="M{cx:.2f},{y(c["high"]):.2f} V{y(c["low"]):.2f}"/>'
                   f'<rect x="{cx-bw/2:.2f}" y="{min(y(c["open"]),y(c["close"])):.2f}" '
                   f'width="{bw:.2f}" height="{max(1,abs(y(c["open"])-y(c["close"]))):.2f}"/></g>')
    for kind, color, line_width in [('stroke', '#237be5', 1.05), ('segment', '#ec870b', 2.6)]:
        for n in payload['nodes']:
            if n['kind'] != kind:
                continue
            dash = '' if n['state'] == 'completed' else ' stroke-dasharray="6 4"'
            width = 3.2 if kind == 'stroke' and n.get('direct_break') else line_width
            out.append(f'<path data-kind="{kind}" d="M{x(_time(n["start"])):.2f},{y(n["start_price"]):.2f} '
                       f'L{x(_time(n["end"])):.2f},{y(n["end_price"]):.2f}" fill="none" stroke="{color}" '
                       f'stroke-width="{width}"{dash}/>')
    ref = payload['summary'].get('raw_window_low')
    if ref:
        cx, cy = x(_time(ref['time'])), y(ref['price'])
        out.append(f'<path data-kind="reference" d="M{cx-5:.2f},{cy:.2f} h10 M{cx:.2f},{cy-5:.2f} v10" stroke="#344861"/>')
        out.append(f'<text x="{max(left+4,min(right-290,cx+8)):.2f}" y="{cy+24:.2f}" fill="#344861">原始低点 {ref["price"]:,.{decimals}f} · 一买性质待核查</text>')
    placed = {}
    for i, s in enumerate(payload['signals']):
        buy = s['label'].startswith('B')
        cx, py = x(_time(s['time'])), y(s['price'])
        key = (round(cx / 12), buy)
        offset = 12 + placed.get(key, 0) * 16
        placed[key] = placed.get(key, 0) + 1
        cy, color = py + (offset if buy else -offset), '#1676d2' if buy else '#d23d61'
        fill = color if s['certainty'] in ('structural', 'policy_confirmed') else 'white'
        delta = -5 if buy else 5
        out.append(f'<g data-kind="signal" stroke="{color}" fill="{fill}"><path d="M{cx:.2f},{py:.2f} V{cy:.2f}"/>'
                   f'<path d="M{cx:.2f},{cy+delta:.2f} L{cx-4:.2f},{cy-delta:.2f} L{cx+4:.2f},{cy-delta:.2f} Z"/></g>')
        if len(payload['signals']) <= 35:
            label = s['label'] + ('?' if s['certainty']=='pending_stroke_candidate' else '')
            out.append(f'<text x="{cx:.2f}" y="{cy+(17 if buy else -11):.2f}" text-anchor="middle" fill="{color}">L{s["level"]} {escape(label)}</text>')
    return ''.join(out) + '</g></svg>'


def add_chart_fallback(page, payload):
    """Progressively enable Canvas; keep a complete visible chart on script failure."""
    # Normalize datetimes without importing the analysis stack.
    payload = json.loads(json.dumps(payload, default=lambda v: v.isoformat()))
    svg = static_chart(payload)
    page = page.replace('<canvas id="chart"', '<p id="renderStatus" class="muted">静态图已载入；交互功能启动后可缩放、切换结构。</p>'
                        f'<div id="chartFallback">{svg}</div><canvas hidden id="chart"', 1)
    page = page.replace('</style>', '[hidden]{display:none!important}</style>', 1)
    for key, value in [('strokeCount', payload['summary']['counts'].get('stroke', 0)),
                       ('segmentCount', payload['summary']['counts'].get('segment', 0)),
                       ('centerCount', sum(payload['summary']['counts'].get(k, 0) for k in ('center', 'center_seed'))),
                       ('signalCount', len(payload['signals']))]:
        page = page.replace(f'id="{key}"></strong>', f'id="{key}">{value}</strong>')
    # Scope report variables so host previews can evaluate more than one report.
    start, finish = page.rfind('<script>') + len('<script>'), page.rfind('</script>')
    code = page[start:finish]
    code = code.replace("function draw(){", "function draw(){canvas.hidden=false;", 1)
    code = code.replace('window.chartReady=true', "window.chartReady=true;document.getElementById('chartFallback').hidden=true;document.getElementById('renderStatus').textContent='交互图已就绪';")
    prefix = """(function(){
function showStaticChart(){document.getElementById('chart').hidden=true;document.getElementById('chartFallback').hidden=false;document.getElementById('renderStatus').textContent='交互图未能启动，已显示完整静态图；可重新打开此文件重试。';}
window.addEventListener('error',showStaticChart);
try {
"""
    return page[:start] + prefix + code + "\n} catch(error) {showStaticChart();console.error(error);}\n})();\n" + page[finish:]
