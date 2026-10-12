# 用户指定的图表显示规则

- 后续生成的所有 HTML 行情图，触发“一笔破坏”的笔必须加粗，颜色不变；保留原实线/虚线状态。
- “一笔破坏”沿用现有线段判定中的 `direct_break`，标注发起破坏的笔，不是把被影响的整条线段加粗，也不代表线段已经确认结束。
- 默认加粗线宽为 3.2。新增或复用其他 HTML、Canvas、内嵌 SVG 生成入口时，同步携带此标记并应用显示规则。
- 自动报告的公共结构导出位于 `report_btc_4h_auto.py` 的 `structure_lines`；交互模板为 `futures_multiperiod_template.html` 和 `btc_4h_auto_template.html`，静态降级图为 `chart_fallback.py`。
