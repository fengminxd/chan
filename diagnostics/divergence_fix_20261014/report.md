# 背驰逻辑修复记录

2026-10-14，规则 `chan-rules-v10`。修复 [前次审计](../full_logic_audit_20261014/report.md) 的两类问题，保留工作区原有修订。

## 修改结果

1. `chan_theory.divergence_candidates`：趋势、盘整两种分类均汇总参与判断的中枢 `evidence_at`、`updated_at` 和 `closed_at`。中枢延伸造成分类降级后，证据时间不再停留在降级之前；关闭后回算范围也不会遗漏关闭证据。下游一、二类候选沿用更新后的证据。
2. `chan_dynamics.audit_divergence`：内部中枢须完整落在 c 的时间、价格范围内；核心及 Z 统计须为有限值且在中枢完整价格范围内；内部中枢 ID 必须与 c 声明的成员按序一致且无重复。外部传入 `lineage_valid=True` 不能跳过这些检查。
3. `chan_dynamics.analyze_dynamics`：从分解注册表取中枢时，也检查它是否属于声明拥有它的组成走势。只落在整个 c 的包络范围内、却越出该组成走势的中枢不能通过来源检查。
4. 规则 manifest 升至 v10，写入新校验与证据传播口径，生成不同指纹，避免新旧结果混用。

边界相等仍视为包含。L0 没有内部中枢证据时仍输出待确认；对应夹具清空原本从高层正例遗留的内部中枢 ID。既有上下方向的合法严格背驰正例、自动检测正例和缺证据待确认用例均保持通过。

## 验证

| 验证 | 结果 | 证据 |
| --- | --- | --- |
| 默认全量测试（包含新增 6 项回归） | 267 项通过 | [unittest.log](unittest.log) |
| 前次审计的 4 个失败用例 | 4 项全部转为通过 | [audit_regressions.log](audit_regressions.log) |
| 差异空白检查 | `git diff --check` 通过 | 本地执行 |

永久回归位于 `test_divergence_regressions.py`，覆盖：上下镜像分类降级、中枢关闭证据、一二类候选时间传播、越界时间和价格、非有限值、重复/缺失/乱序/无关 ID、边界相等，以及组成走势归属。

复现命令：

```bash
python3 -m unittest discover -v
python3 -m unittest discover -s diagnostics/full_logic_audit_20261014 -p test_findings.py -v
```

本次修改结构算法的消费与校验，不改变笔、线段划分或 `direct_break` 的 3.2 加粗规则。未重建历史行情 HTML、未发布站点。前次冻结行情未触发严格背驰分支的覆盖限制仍成立；通过合成反例与正例不能替代真实行情端到端正例验证。
