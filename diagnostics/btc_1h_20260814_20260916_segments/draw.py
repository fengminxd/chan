"""Draw the two endpoint choices over the same pens; keep direct_break styling."""
import json
from pathlib import Path
from xml.etree import ElementTree

OUT = Path(__file__).resolve().parent
data = json.loads((OUT/'evidence.json').read_text())
pens = data['strokes'][:28]
points = [pens[0]['start_price']] + [s['end_price'] for s in pens]
parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 950">',
         '<rect width="1280" height="950" fill="#0b1020"/>',
         '<g font-family="WenQuanYi Zen Hei,Microsoft YaHei,sans-serif" fill="#e2e8f0">',
         '<text x="50" y="38" font-size="24">BTCUSDT 1h：同一组 28 笔上的两种分段对照</text>',
         '<text x="50" y="67" font-size="15">2026-08-14 22:00 → 09-16 02:00（北京时间）；横轴按笔端点等距，不是等时间 K 线图。</text>']
def x(i): return 75+i*40
def y(price,top): return top+315-(price-62000)/21000*280
for top,title,ends,color in [(120,'当前 v8：四段；逐个采用最先完成确认的转折',[0,9,14,17,28],'#ffad45'),
                            (535,'用户两段方案：局部候选可取得证据，但跨越了更早已成立的分界',[0,23,28],'#db92ff')]:
    parts.append(f'<text x="50" y="{top}" font-size="19">{title}</text>')
    for price in (64000,68000,72000,76000,80000,82000):
        yy=y(price,top)
        parts.append(f'<path d="M75 {yy} H1205" stroke="#25304a"/><text x="12" y="{yy+5}" font-size="12">{price}</text>')
    for i,s in enumerate(pens):
        width=3.2 if s['direct_break'] else 1.15
        parts.append(f'<path d="M{x(i)} {y(points[i],top)} L{x(i+1)} {y(points[i+1],top)}" stroke="#4c9aff" stroke-width="{width}" fill="none"><title>P{s["index"]}: {s["start"]} {s["start_price"]} → {s["end"]} {s["end_price"]}; direct_break={s["direct_break"]}</title></path>')
    for a,b in zip(ends,ends[1:]):
        parts.append(f'<path d="M{x(a)} {y(points[a],top)} L{x(b)} {y(points[b],top)}" stroke="{color}" stroke-width="2.2" fill="none"/>')
    for i,label in [(0,'08-14 22:00'),(9,'08-25 10:00'),(14,'08-29 00:00'),(17,'08-31 00:00'),(23,'09-04 05:00'),(28,'09-16 02:00')]:
        xx,yy=x(i),y(points[i],top)
        dy=-24 if i in (9,17,23) else 25
        anchor='start' if i==0 else 'end' if i==28 else 'middle'
        parts.append(f'<circle cx="{xx}" cy="{yy}" r="3" fill="{color if i in ends else "#4c9aff"}"/>')
        parts.append(f'<text x="{xx}" y="{yy+dy}" text-anchor="{anchor}" font-size="13">{label}</text>')
        parts.append(f'<text x="{xx}" y="{yy+dy+16}" text-anchor="{anchor}" font-size="13">{points[i]:,.1f}</text>')
    parts.append(f'<text x="75" y="{top+365}" font-size="14">蓝线为当前笔；发起 direct_break 的笔宽 3.2，颜色和实线状态保持。彩色连线表示该面板的分段方案。</text>')
parts.extend(['<text x="50" y="928" font-size="14">两段对照仅用于解释分歧，未写入生产图表；四段的完整确认依赖 09-24 10:00 前的后续行情。</text>','</g></svg>'])
svg='\n'.join(parts)
ElementTree.fromstring(svg)
(OUT/'comparison.svg').write_text(svg)
print('Wrote comparison.svg; actual pens preserve direct_break width, color and solid status.')
