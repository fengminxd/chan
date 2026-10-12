# 未确认末笔二／三类候选显示修复

问题：`automatic_views` 只把非 provisional 的笔交给历史信号生成，因此末笔已经具有稳定顶／底分型时，其二类、三类候选仍被一起隐藏。

修复：在公共图表导出中单独追加稳定端点末笔的候选。复用 `entry_plan.period_candidates` 的二类／三类判据；不把末笔传入完成走势或线段计算。新状态为 `pending_stroke_candidate`，空心问号显示，并明确 `actionable=false`、`higher_point_confirmed=false`。

输入只使用已收盘K线，等待端点分型稳定及前置依赖可用；取完整原始价格范围，严格排除触及中枢边界、已消费中枢和被后续新极值突破的旧端点。候选证据时间与实际观察时间分别保存，不声称回放时已执行。

## BTC 原快照验证

使用原来截至北京时间2026-10-13 01:43的同一份行情，没有刷新数据。

- 1h：北京时间2026-10-12 14:00的84,340.2新增S2?和S3?，候选证据时间为18:00。
- S3来源笔观察中枢为85,072.7–86,034.0，反抽高点严格低于下沿。
- 2h新增S2?、S3?；4h新增S2?；8h没有新增符合条件的末笔候选。
- 前后对比确认：所有既有信号、完成走势口径、笔段坐标与状态、中枢、破坏笔标记均保持一致。

## 验证

50项报告与逐级定位相关单元测试通过，另4项静态候选和破坏笔显示测试通过；三个HTML模板的JavaScript语法检查通过。

真实数据复核：`python3 diagnostics/pending_stroke_candidates_20261012/verify_snapshot.py`。

浏览器检查：`node verify_btc_tradingview.mjs`，覆盖四周期×两种口径、56组筛选、候选空心属性、目标日期S2/S3、点击定位、破坏笔原色/虚实及3.2线宽、手机显示。

[原数据前后校验](snapshot_validation.json) · [浏览器结果](../../reports/BTCUSDT_20260606_current_tradingview/browser_validation.json) · [修复后的图表](../../reports/BTCUSDT_20260606_current_tradingview/btc_tradingview.html)
