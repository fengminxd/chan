# 可追溯的缠论研究

新增模块借鉴 chan.py 的明确配置、CZSC 的信号/事件/交易分离、chanlun-pro 的回放审阅、威科夫的量价观察，以及 CSCV 的过拟合诊断思想。代码是本项目的独立实现，没有引入这些项目作为依赖，也不把它们的输出当作缠论正确性的裁判。

核心仍只使用 Python 标准库。所有周期先按同一 UTC 时间桶生成，结构级别 L1/L2 与图表周期分开。原有笔、线段、中枢和严格背驰判据没有因为新增交易模块而放松。

新增 [1m 统一递归树](UNIFIED.md)：`research.py --source-period 1m` 默认只运行一条 1m 回放，十个图表周期投影同一棵树；`--context-csv` 接入真实分钟前置行情，`--require-history` 可在历史/完成证据不足时输出诊断并停止研究。投影不作为独立确认信号；该模式的候选 `periods` 和 `confirmation_periods` 只能来自 1m。显式 `--independent-periods` 恢复按图表周期分别分析。

分钟输入时，持仓、训练、测试和隔离的根数都按分钟计，默认持仓 24/48/96 根即 24/48/96 分钟。本文下方五周期候选和小时级 BTC 示例继续用于独立模式。长分钟区间逐前缀重算开销较大，建议先审阅 `unified.py` 的静态结构与历史诊断。

## 规则清单与差异定位

`chan_policy.AnalysisPolicy` 管理 min_span、包含初始方向、递归深度、MACD 回零辅助阈值及均线力度窗口。每份分析增加 `rule_manifest`，包含规则版本、参数、力度单位、适用范围及 SHA-256 指纹。研究报告另外记录实际 Python 源码指纹和输入 OHLCV 指纹，避免仅凭相同参数推断算法版本相同。

```bash
python3 compare_rules.py \
  --csv reports/BTCUSDT_perpetual_20260606_20260819/context_1h.csv \
  --period 1h --anchor-hour 16 --as-of 2026-08-19T00:00:00+08:00 \
  --left-policy research_configs/default_analysis.json \
  --right-policy research_configs/wider_strokes.json
```

输出 `left.json`、`right.json`、`comparison.json`。依次定位分型、笔、线段、中枢、完成走势的首个差异，另外比较结构证据时间。左右快照包含选笔、线段尾部、走势完成诊断。调整 min_span 是敏感性研究，不是推荐把 5 视为更正确的笔标准。

外部引擎使用 `--external external.json` 导入，不执行外部代码。交换协议是 `chan-comparison-v1`：

- `engine`、`engine_version`：真实引擎名称和版本；`rules`：该引擎实际配置和划分口径。
- `input`：必须与 `left.json` 相同，包括聚合后 OHLCV 指纹、根数、周期、UTC 起点、截止时间和首末开盘时间。请先向外部引擎提供**相同的已聚合、已收盘行情**，再独立计算指纹，不能只复制指纹声称相同。
- `structures.fractals`：`time,kind,price`；`kind` 为 top/bottom。
- `structures.strokes/segments`：`start,end,start_price,end_price,confirmed`。
- `structures.centers`：`start,end,level,zd,zg`；这里专指相同定义的递归中枢，不能直接把笔级中枢填成 L1。
- `structures.completed_movements`：`start,end,level,kind,low,high,evidence_at`。
- `confirmation_timing.strokes/segments`：`start,end,evidence_at`；中枢再加 `level`。

时间统一 UTC ISO 8601，价格保持原始精度；不支持或语义不相容的阶段填 `null`，结果为 `not_comparable`，不能填空数组伪装“没有结构”。`[]` 仅表示确实计算后没有对象。比较按开始时间及层级排序，输出最早不同项，不以多数引擎投票判正确。本次提供了交换接口和本地规则对照，**尚未实际运行外部 chan.py/CZSC 引擎进行独立验证**。

## 三层结果

`analyze()` 新增 `decision_layers`：

1. `structure_audits`：严格背驰审计去掉力度条件后，独立返回结构 pass/pending/rejected 及逐项依据。
2. `strength_measurements`：MACD 同方向柱值和（price_bars）与完整 SMA 吻间面积（price_hours）分别列出，保留指标名和来源。不会在两个指标之间隐式切换。
3. `trade_candidates`：结构/策略信号是否满足默认交易资格及阻断理由。静态分析不下订单；必须经过回放观察和交易规则。

默认只允许结构确认 B3/S3、`policy_confirmed` B1/S1。MACD 辅助候选及依赖候选的一二类点不能直接成交。B1/S1 的确认仍只适用于本项目已声明的 SMA 力度政策，不意味着完整原论动力学证明。

规则 v2 的结构确认 B3/S3 只来自完成走势树。旧 `theory.levels` 中的三类点标为 `legacy_structural`，作为对照保留；统一回放只发布完成树注册表的信号与审计。历史 v1 回放不能当成 v2 结果使用，应按新规则指纹重新回放。

## 生命周期与审阅

```bash
python3 replay.py --csv your_1h.csv --period 4h --step 1 \
  --policy research_configs/default_analysis.json --output output/replay_4h.jsonl
python3 replay_viewer.py --replay output/replay_4h.jsonl --output output/replay.html
```

回放对象增加笔和待确认线段。笔、线段及中枢尽量按开始锚点保留身份，端点延长表现为修订；起点或归属改变时表现为撤回/新增。每项事件的 `lifecycle` 包含：

| 字段 | 含义 |
| --- | --- |
| endpoint_time | 当前结构端点，不是成交时刻 |
| structural_evidence_at | 当前划分所引用的证据时间 |
| first_observed_at | 本次回放第一次观察到对象 |
| first_confirmed_observed_at | 本次回放第一次观察到其满足确认条件 |
| current_revision_confirmed_at | 当前版本被观察到确认的时间；撤回或候选为 null |
| revision / status / reappeared | 修订次数、状态、撤回后是否重新出现 |

`confirmed_in_snapshot` 不代表永不重绘；中枢确认仅表示成枢，走势完成仍独立判断。`--step > 1` 是采样回放，交易模拟拒绝使用它。`--min-bars` 跳过早期观察会降低首次观察时间精度；研究入口始终从第一个前缀逐根运行，包括交易起点之前的预热行情。

HTML 为离线审阅页，切换周期和时间滑块后只展示当时可见对象，点击可查看完整依据及本截面撤回记录。`research.py` 自动生成精简的 `signals.html`；完整结构视图可用上面的命令读取完整回放文件生成。

## 量价辅助

`volume_price` 对当前已收盘 K 线与前 20 根对比：成交量/过去平均成交量，以及收盘价绝对变化/过去平均绝对变化。比值阈值为 1.5 和 0.5，明确作为工程参数记录。

- `high_effort_low_result`：量比至少 1.5，推进比不超过 0.5。
- `high_effort_high_result`：两个比值都至少 1.5。
- 未预热、缺量、零价格参考或数据间断返回不可测状态。

这些是受“努力与结果”启发的描述标签，不自动命名吸筹/派发，不证明主力意图，不改变中枢或背驰定义。可以作为可选交易过滤器，并与不加过滤器的策略对照。

## 含成本研究与样本外验证

```bash
python3 research.py \
  --csv reports/BTCUSDT_perpetual_20260606_20260819/context_1h.csv \
  --start 2026-06-06T00:00:00+08:00 --as-of 2026-08-19T00:00:00+08:00 \
  --anchor-hour 16 --periods 1h 4h 8h 12h 1d \
  --candidates research_configs/candidates.json \
  --fee-bps 5 --slippage-bps 2 \
  --train-bars 720 --test-bars 240 --embargo-bars 24 \
  --output-dir output/btc_research
```

`--start` 只限定交易/评估窗口，前置行情仍全部参与逐根分析。`--as-of` 排除未收盘 K 线。五周期逐根回放可能需要数分钟，输出完整结构 JSONL、研究 JSON、Markdown 报告及信号审阅 HTML。已有历史报告不会被覆盖。

交易模型：

- 单品种、线性计价永续、单仓位，默认按交易前权益的 10% 名义金额开仓，不额外放大杠杆。B 买入做多，S 卖出做空。
- 收盘边界观察到信号后，最早在同一边界的下一根源 K 线开盘成交，假设计算/下单零延迟。最后一根收盘才知道的信号没有下一开盘，不能成交。结构端点价格不用作成交价。
- 手续费按成交名义金额双边收取；买入滑点向上、卖出向下；正资金费率多付空收。资金费先向结算前已有仓位收付，再处理同一时点开盘订单。
- 默认反向合格信号反手，来源信号撤回则下一开盘平仓，达到 `hold_bars` 平仓。历史成交不会因后来撤回而从收益中删除。同一信号身份最多触发一次新入场；同向重复信号不加仓，同时多空冲突不入场。
- 可按图表周期、结构层和信号标签过滤；可要求更大周期最新、未撤回且未过期的同向合格信号，或要求指定量价标签。
- 末尾持仓按末根收盘价加交易成本强制平仓，标记 `end_of_sample_mark_close`。这是研究边界处理，不代表该处存在卖点。回撤按源 K 线收盘权益采样，不是盘中最大回撤。
- 缺失源 K 线拒绝交易回测；非正价格拒绝。没有交易所保证金、强平、盘口深度或成交量容量模型，不用于模拟实际杠杆强平。

真实资金费输入：

```csv
timestamp,rate,mark_price
2026-06-06T00:00:00Z,0.0001,65000
2026-06-06T08:00:00Z,-0.00005,64800
```

用 `--funding-csv funding.csv` 接入实际结算时间、费率和当时标记价格。列表的完整性由数据提供方负责，报告记录文件指纹，不宣称已向交易所校验。也可用 `--funding-rate 0.0001 --funding-interval-hours 8` 做常数场景（UTC epoch 起的固定间隔；边界源 K 线开盘价近似标记价，样本终点使用末收盘价）。两者互斥。**两者都不提供时，资金费不计入数值，但 `cost_status=partial_funding_missing`；此收益不是完整净收益。** 默认手续费和滑点也只是研究参数，不是你的账户费率。

示例候选预先定义了基础策略、量价过滤、1h 入场加 4h 确认三个方案。未提供候选文件时，对照三个持仓期限 24/48/96 根。研究程序不会看到样本外收益后自动扩大参数搜索。

滚动验证只用每折训练窗口内的含指定成本收益选择候选，训练交易不足时下一折保持现金。训练/测试之间留 `embargo_bars`，测试区间不重叠，各折清仓重置，交易端不携带窗口之前已经出现的信号重新入场。输出每折训练分数、选择、测试表现及样本外复合收益。它不声称实现全部带标签清洗的交叉验证。

CSCV 将所有已声明候选的同时间逐根收益矩阵分成偶数块，每次一半选参、另一半评估赢家的相对名次；评分为平均逐根收益，样本外相对名次不超过 0.5 的比例记为 PBO。同分采用中位秩，全体同分的分割跳过，尾部不能整分的行明确报告丢弃量。研究入口要求至少两个候选达到最低交易数（默认各 3 笔），否则输出 `insufficient_trading_evidence`、PBO=null，防止现金候选与极少交易产生看似良好的 PBO。达到这一门槛也不代表统计显著。候选不足、数据不足或全零收益不输出虚假的“过拟合通过”。CSCV 是全样本诊断，**不替代按时间向前的样本外测试**，少量交易的估计不稳定。

## 检查

```bash
python3 -m unittest test_chan test_structure test_theory test_dynamics \
  test_cases_replay.ReplayTests test_research test_unified
```

这组检查包含未来后缀不改变过去、确认/修订/撤回、下一开盘成交、费用/资金费手算、多周期确认、样本外选参及 CSCV 排名。按此前要求，不运行原文案例验证器。

参考：[chan.py](https://github.com/Vespa314/chan.py/blob/main/quick_guide.md)、[CZSC](https://github.com/waditu/czsc)、[chanlun-pro](https://github.com/yijixiuxin/chanlun-pro)、[Wyckoff Analytics](https://www.wyckoffanalytics.com/wyckoff-method/)、[The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)。
