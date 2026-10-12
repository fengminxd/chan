三项审计问题已修复，分析规则升级为 `chan-rules-v5`，完成走势策略为 `confirmed_type_repartition_v4`，开单候选策略为 `entry-plan-v5`。修复前的[审计报告](../center_signal_audit_20261011/report.md)和[反例结果](../center_signal_audit_20261011/results.json)保留供对照。

1. **中枢范围与递归成员归属分别保存。**

`canonical_center_view` 从成枢三段重新计算观察范围，每次延伸更新 DD/GG/D/G、成员及时间，关闭时移除离开走势，但保留初始成枢三段。`decomposition_centers` 保留各父走势自身的中枢截面，供内部背驰结构检查；父走势 `child_ids` 不变，不能因为观察中枢延伸而重复归属子走势。

原反例的观察 GG 从 85 正确更新到 89，父走势内部截面的 GG 仍为 85。另一个反例中，不属于初始三段的离开走势被移出观察成员，range_low 从 102 恢复为 106。上述两例均验证了卖点镜像。

2. **三类点按可见前缀保存，重分解不抹去历史事件。**

中枢 ID 改为由成枢子走势确定，例如 `T1:Z:L0:M6`，不再随父走势序号改变。每个完成子走势前缀在采用最终分解后检查三类点，首次发布时冻结信号及已关闭的来源中枢。新分解才发现的中枢/信号，要等待当时已可用的前缀证据，不倒填到较早的几何端点。

退出当前分解的已关闭中枢存于 `historical_centers`，既有三类点仍可引用它。历史中枢不会混入当前中枢的相离/背驰配对，不会作为父走势或上层递归输入。统一图和自动报告同时导出历史来源；统一图以 `historical=true`、无当前父归属标识它，并校验观察成员、范围及证据时间。固定完成输入追加下，原 B3=213 及其镜像 S3 保留原 ID、价格和证据时间。

这里保留的是当时已经发布的结构事件，并不表示价格后来不能失效，也不表示底层选笔修改后永不重绘。底层结构修订仍由逐 K 线回放记录；本次没有把新的当前分解强行改回历史分解。

3. **候选与确认入口共用三类点判据。**

新增共享 `third_point_label`，由延伸中枢、统一中枢和开单候选共同调用。回试入枢或接触边界，表示当前离开尝试失败；中枢继续延伸，之后新的离开允许重新检查紧邻回试。首次成功后消费该中枢，后续回试不得重复发布。原 `0,12,6,10,7,15,9,16,13` 及卖点镜像现在在候选阶段可识别，确认阶段仍须等待完成走势。

验证结果：

| 检查 | 结果 |
| --- | --- |
| 全量 unittest | **234 项通过**，18.904 秒；[日志](unittest.log) |
| 原审计脚本 | **16/16 通过**；[当前结果](../center_signal_audit_20261011/current_results.json) |
| 新增回归 | 观察范围/归属分离、移除离开段、信号和来源保存、迟到证据、重分解发现时间、图与报告引用、候选重试及镜像 |
| 随机前缀 | 12 组固定随机种子、每组 60 条完成走势，对每个可用前缀逐一比较已发布信号的完整对象，覆盖多层递归 |
| BTCUSDT 4h 本地快照 | 8,271 根；结构、范围、信号引用和证据校验通过；保留 1 个历史中枢来源 |
| BTCUSDT 1h 本地快照 | 33,084 根；同上；保留 2 个历史中枢来源 |
| STRKUSDT 1h 本地快照 | 5,639 根；同上；没有退出当前分解的已发布中枢来源 |
| 一笔破坏与显示回归 | 通过；继续加粗攻击笔至 3.2，保留原颜色与实线/虚线 |

本地样本的源路径、SHA256 和统计见 [local_samples.json](local_samples.json)。样本检查复用统一图的节点校验器，不将小时行情冒充 1m 数据，不生成或覆盖历史行情图。报告中的 B1/B2 等计数包含其原有辅助候选性质，不等于全部是严格确认信号。

复核命令：

```bash
python3 -m unittest discover -v
python3 diagnostics/center_signal_audit_20261011/audit.py
python3 diagnostics/center_signal_fix_20261011/verify_local_samples.py
```

主要改动：[chan_theory.py](/root/chan/chan_theory.py)、[chan_types.py](/root/chan/chan_types.py)、[chan_dynamics.py](/root/chan/chan_dynamics.py)、[entry_plan.py](/root/chan/entry_plan.py)、[unified.py](/root/chan/unified.py)、[report_btc_4h_auto.py](/root/chan/report_btc_4h_auto.py)。新增回归见 [test_center_signal_regressions.py](/root/chan/test_center_signal_regressions.py)。规则清单、README、THEORY、UNIFIED、ENTRY 文档已同步。

历史 HTML 和冻结行情未重建，重新生成的输出才使用 v5。此次修复不扩大 SMA 力度策略的理论覆盖，也不把独立二类点几何位置升级为确认信号。
