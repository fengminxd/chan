# 1m 基底与统一递归中枢

以真实 1m OHLCV 为唯一最低数据尺度，先识别笔和确认线段，再按 `confirmed_type_repartition_v4` 递归已完成走势。1m、5m、15m、30m、1h、2h、4h、8h、12h、1d 是同一棵树的十个观察窗口；切换窗口不会重新划分结构，也不会把 L1、L2 硬编码为 5m、15m。

分析规则 v5 的中枢节点用 `extension_child_ids` 和 `range_scope=observation` 保存完整观察范围，校验其连续性、价格范围和证据时间。父走势归属只由自己的子走势决定。退出当前分解的已关闭来源仍以 `historical=true / owner_movement_id=null` 保留，供既有三类点引用，不进入父走势递归或新的背驰中枢配对。固定完成子走势追加时，已发布 B3/S3 的 ID 和证据时间保持稳定；底层 K 线选笔修订仍可能导致撤回。

## 补齐的三个条件

| 条件 | 实现 | 不满足时 |
| --- | --- | --- |
| 单一基底与父子链 | `tree.nodes` 保存统一 ID、直接子走势、所属父走势、中枢所属走势及证据时间；`views[].projections` 引用相同 ID | 校验拒绝跨缺口、跳级、断链、重复归属或范围不符的结构 |
| 连续前置行情与充分性检查 | 合并主文件和前置文件，检查窗口覆盖、缺根、连续前置数量，并对比完整前置与缩短一半前置后的目标结构 | 返回 `needs_more_evidence` 和具体原因，可补更早真实数据重算 |
| 完成与确认后再升级 | L0 只取确认线段；高层只接收连续、已完成的直接次级走势；核对真实区间重叠和证据先后 | 保留待确认线段、延伸走势和已形成中枢，未完成走势不成为上层输入 |

三条已完成次级走势重叠可以形成中枢，但所属本级走势仍可能延伸。仅有三条子走势而缺进入部分时，记录 `center_seed / formed_parent_uncompleted`，不补造完成父走势。父走势完成沿用反向三类点或不相容中枢的保守分解规则；本次没有放松笔、线段或完成门槛。

这里的“严格”限定于本项目声明的可复现规则，仍不是原论所有特殊笔规则和动力学的完整证明。规则 v2 的背驰、买卖点和图表共享完成走势树的中枢注册表；`tree.audits/signals` 保存统一节点引用及依赖证据，旧三段分解信号只在 `base_analysis` 中供对照。详见 [THEORY.md](THEORY.md)。

## 输入与运行

Python 3.10+，仅标准库。CSV 字段为 `timestamp,open,high,low,close,volume`，时间是 1m 开盘时间，可带 `Z` 或 `+08:00`。输入必须是同交易所、同合约、同价格口径的真实分钟记录；程序不能从现有 1h 行情还原分钟波动，也不能自动核实 CSV 的交易所来源。

```bash
python3 unified.py \
  --csv btc_1m.csv --context-csv btc_prior_1m.csv \
  --start 2026-06-06T00:00:00+08:00 \
  --as-of 2026-08-19T00:00:00+08:00 --anchor-hour 16 \
  --periods 1m 5m 15m 30m 1h 2h 4h 8h 12h 1d \
  --output-dir output/btc_unified
```

`--periods` 不填即输出十个视图。`chan.py --source-period 1m` 自动使用同一入口，其余参数用法相同；该入口也保留原有 `--min-span`、`--max-levels`、力度参数。`unified.py` 可用 `--policy research_configs/default_analysis.json` 传入分析配置。

前置数据可以放在同一主文件内，也可用 `--context-csv` 添加；两者时间重叠且 OHLCV 完全相同会去重，冲突则报错。`--start` 只限定报告观察窗口，之前的行情仍参与结构分析。截止时刻之后和未收盘的 K 线不参与计算；大周期桶缺任何一分钟都会丢弃，不填补。分钟缺口将结构分为独立区段，不能跨段拼接。

`--anchor-hour 16` 表示 UTC 16:00 起算、与 UTC+8 自然日对齐。时间对齐与整倍数合并使用整数分钟；Python 兼容接口仍以小时计，`PERIODS['1m']` 为 `1/60`，原来的 `analyze(candles, 1)` 仍表示 1h。

## 历史检查的含义

- `--min-context-bars 1440` 是默认工程下限，仅要求目标起点之前至少一天连续分钟数据，**不代表一天足够形成高层结构**。
- `--required-level 2` 默认检查目标区间是否至少有一个已完成 L2 走势；只形成 L2 中枢仍返回完成证据不足。该值不得超过 `max_levels`。
- `stability.status` 区分 `stable_on_available_contexts`、`sensitive_to_context`、`no_target_structure`、`not_checked`。对比的是目标区间结构及其直接子走势，不是仅比较中枢数量。
- `history_sufficiency_proven` 始终为 false。`usable_under_checks` 仅表示当前有限检查通过；继续补历史仍可能改变左侧结构。数据根数、稳定性或选取更大周期均不能保证任意高层走势已经完成。
- `--require-history` 在检查不通过时，先写出可审阅报告，再以退出码 2 结束；`research.py` 同选项会在交易回放前停止。

未指定 `--start` 时从首根行情开始显示，因此没有目标之前的前置数据，通常会返回历史证据不足。想检查某一时间段，应显式给出起点，并提供更早数据。

## 输出与审阅

| 文件 / 字段 | 内容 |
| --- | --- |
| `analysis.json / tree` | 每个连续分钟区段的一棵完成走势树、根节点、递归停止和线段等待诊断 |
| `tree.nodes[].parent_id / child_ids` | 唯一所属父走势与全部直接子走势，线段子节点为笔 |
| `tree.audits / signals` | 背驰与买卖点的统一注册表；引用节点、其他审计/信号及证据时间均参与校验 |
| `owner_movement_id / center_membership_ids` | 中枢所属走势、子走势的成枢引用；与父子所有权分开 |
| `state / completion / evidence_at / promotion_ready` | 当前状态、完成原因、证据时间及是否可作为上层输入；可升级不保证实际能组成上层 |
| `views[].projections` | 共享 `node_id`、原始精确端点和所在 K 线索引，切换图表不改变层级 |
| `history` | 连续性、目标覆盖、前置敏感性、各层数量与未达标原因 |
| `base_analysis` | 原有 1m 分析，包括旧分解视角、MACD 辅助和严格政策背驰审计，保留各自语义 |
| `chart.html` | 离线交互图，切换十周期、结构层和显示窗口，点击结构沿父子引用审阅 |
| `report.md` | 各层中枢/完成走势数量、数据来源及历史状态 |

较大周期尚无完整收盘桶时，树仍保留已知的 1m 结构，投影状态为 `outside_closed_view_bars`。图表是截止时刻快照，不把它称为历史时刻就已知道的信号；在线可见性请看逐前缀回放。

## 回放与研究

```bash
python3 replay.py --csv btc_1m.csv --context-csv btc_prior_1m.csv \
  --source-period 1m --as-of 2026-08-19T00:00:00+08:00 \
  --anchor-hour 16 --step 1 --output output/unified/replay.jsonl
python3 replay_viewer.py --replay output/unified/replay.jsonl \
  --output output/unified/replay.html
```

统一回放只有一个 `period=1m` 事件流。中心确认表示已经成枢；走势确认表示已经完成；两者都保留修订与撤回。规则 v2 只发布 `tree.signals/audits` 中的完成走势通路信号和审计，不再混入 `base_theory:` 的旧分解信号。投影不会重复生成信号或充当多周期共振。

`research.py --source-period 1m` 同样默认统一模式，只接受 1m 信号来源的交易候选；原来的“1h 入场 + 4h 确认”候选不能直接用于该模式。`--independent-periods` 可显式恢复各图表周期独立研究。`hold_bars`、训练/测试长度、隔离根数均按**源 K 线**计，1m 输入时默认持仓 24/48/96 根就是 24/48/96 分钟。细节见 [RESEARCH.md](RESEARCH.md)。

当前回放会对每个前缀重算，长分钟历史的逐根研究开销很大，尚未实现增量缓存。长区间先运行静态统一分析；可用 `unified.py --replay-step 60` 额外输出采样审阅，但采样不能用于交易回测，也不保留逐分钟首次观察精度。

## 合成演示与回归

```bash
python3 unified.py --demo --start 2022-01-04T00:00:00Z --output-dir output/unified_demo
python3 -m unittest test_unified test_research test_chan test_structure \
  test_theory test_dynamics test_cases_replay.ReplayTests
```

演示是一周 10,080 根人工生成的 1m 数据，与 BTC 或原文历史案例无关。它展示 L1 完成走势和 L2 中枢，L2 所属走势未完成时继续等待。本次仅运行单元、合成集成和回放回归，不运行原文案例验证器；现有小时级 BTC 历史报告没有被分钟结果替换。
