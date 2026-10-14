# 缠论线段与递归走势中枢（Python）

规则 v9 修复第一反向笔破坏：上涨段比较旧特征元素高点，下跌段比较低点，触及即记录破坏；后续须严格越过首笔终点才确认，先越过首笔起点则假设失效。首笔已破坏时，不再因后续包含而错误转入双序列等待，也不能绕过严格突破条件提前确认。验证见[修复记录](diagnostics/direct_break_fix_20261013/report.md)。

v8 的部分缺口处理及测试预期因笔破坏阈值错误而不成立，现由 v9 更正；保留[原记录](diagnostics/feature_sequence_fix_20261013/report.md)与[后续审查](diagnostics/direct_break_audit_20261013/report.md)供追溯。

规则 v7 默认使用连续严格选笔：同一段连续行情只生成一条首尾相接的笔链。保留中心间距至少4及全跨度极值条件；允许受影响的历史笔重划，并按实际可知时间保存撤回和替代记录，不再承诺已完成笔永不修订。只在真实数据缺口处分块，MACD保持完整连续行情上下文。v6断链方案已撤销，详见 [v6审查](diagnostics/v6_continuity_review_20261013/report.md) 与 [v7修复验证](diagnostics/connected_stroke_fix_20261013/report.md)。

新增：[通用多周期开单计划](ENTRY.md)。`chan.py --entry-plan --source-period 1m` 默认同时分析**4h、2h、1h**三个主级别：4h→1h完成→15m执行点，2h→30m完成→5m或15m执行点，以及原1h→15m→5m→1m逐级分型定位。卖点镜像，各主级别独立列出候选、触发价、止损、失效边界与状态；`--previous-entry-plan`延续观察。默认`--entry-profile combined`，也可单独选择`matrix`或`nested_1h`。此入口不绑定品种、不下载行情、不发送订单。

新增：[可追溯研究工作流](RESEARCH.md)提供规则指纹与差异定位、确认/修订/撤回回放、结构/力度/交易分层、量价辅助、手续费/滑点/资金费模拟、滚动样本外验证和 CSCV 过拟合诊断。入口为 `research.py`，仍仅依赖 Python 标准库。

读取 OHLCV CSV，支持 **1m、5m、15m、30m、1h、2h、4h、8h、12h、1d**。新增 [1m 统一递归树](UNIFIED.md)：以 1m 为唯一基底，十个图表周期引用同一棵完成走势树，包含前置历史检查、父子引用和完成证据校验。`chan.py --source-period 1m` 默认进入统一模式；小时输入继续兼容原有独立周期分析。

输出分型、笔、特征序列线段、递归中枢、中枢演化、走势分类、三类点证据、完成走势递归、趋势背驰结构审计、均线力度比较、MACD 辅助与区间套。Python 3.10+，无需第三方依赖。

HTML 图表默认显示约定：触发“一笔破坏”的笔加粗，保留原颜色与实线/虚线状态。自动报告使用 `direct_break` 标记，破坏笔线宽为 3.2；加粗不表示线段已确认结束。后续生成或新增的 HTML 图表也应遵循此约定。

自动报告的笔观察口径同时显示稳定端点末笔的二／三类候选：复用逐级定位入口的候选判据，以 `pending_stroke_candidate`、空心问号展示。未确认末笔不会因此进入完成走势或线段计算；候选证据时间等待端点分型稳定和依赖输入可用，不回填端点时间，也不代表已触发交易。尚无稳定分型、端点已被新极值突破、回试触及中枢边界或中枢已被成功三类点消费时，不强行补画。

规则 v5 修复中枢范围与三类点：观察范围随延伸和关闭重算，父走势自身的中枢截面另存 `decomposition_centers`；中枢 ID 由成枢子走势确定，已发布三类点在固定完成输入追加下保留原身份与证据，退出当前分解的来源存入 `historical_centers`，不混入新的趋势比较。回枢失败后允许重新离开，候选和确认入口共用判据；成功三类点消费旧中枢后禁止复用。详见 [修复记录](diagnostics/center_signal_fix_20261011/report.md)。已有 HTML 是旧快照，重新生成后才采用新规则。

规则 v4 修复笔/段审计中的漏段、证据时间及破坏笔漏标问题：前三笔重叠但第三笔尚非方向极值时保留首段起点；线段发布须同时满足局部证据、前段确认和全部输入笔的可用时间；笔确认使用稳定右邻证明块的首根原始 K 线收盘时间。`direct_breaks` 保留原段延续前的破坏笔事件，并统一提供给自动报告、通用 SVG 和统一树图表。旧报告保持原快照，需重建后才采用新规则指纹。修复验证见 [记录](diagnostics/pen_segment_fix_20261011/report.md)。

默认 `--mode strict`：使用特征序列的两种线段确认方式，同时输出原有**同级别最早三段分解**和新增的**已完成走势类型递归**。旧三笔重叠近似保留为 `--mode bi`，不会混入默认结果。

“严格”指下述明确的线段判据及递归约束，**不是全部缠论都已严格实现的声明**。同级别递归与中枢演化分别输出，不能混用其边界。背驰现在分别输出 MACD 辅助候选，以及通过结构审计和 `cross_kiss_sma` 力度比较的策略确认；策略确认不等于原论全部动力学已被证明。K 线到笔仍采用原有五根去包含 K 线选笔规则，特殊新笔、跳空成笔规则未加入。完整覆盖表、公式及尚未实现的部分见 [THEORY.md](THEORY.md)。

## 运行

```bash
# 统一树演示：十个周期共享 1m 结构；合成数据
python3 unified.py --demo --start 2022-01-04T00:00:00Z --output-dir output/unified_demo

# 真实 1m 数据 + 同品种前置行情
python3 chan.py --csv your_1m.csv --source-period 1m --context-csv prior_1m.csv \
  --start 2026-06-06T00:00:00+08:00 --as-of 2026-08-19T00:00:00+08:00 \
  --anchor-hour 16 --output-dir output/unified

# 旧独立模式演示：小时及以上可合成周期；合成数据，不是原文案例验证
python3 chan.py --demo

# 默认严格线段 + 递归中枢，输入 1h，自动合并 4h / 8h
python3 chan.py --csv your_1h.csv --max-levels 5

# 只看新增的两个周期
python3 chan.py --csv your_1h.csv --periods 12h 1d

# 直接输入日线
python3 chan.py --csv your_daily.csv --source-period 1d

# 单独分析现成的 4h K 线
python3 chan.py --csv your_4h.csv --source-period 4h --periods 4h

# BTCUSDT 永续4h自动图：2025-10-06至当前，包含买卖点及背驰位置
python3 report_btc_4h_auto.py --refresh
# 使用已冻结行情重建；无人工参考点，输出到 reports/BTCUSDT_4h_20251006_current
python3 report_btc_4h_auto.py

# SUIUSDT 永续最近100天：1h/4h可切换，近期结构与全历史独立对照
python3 report_sui.py --refresh
# 离线重建 reports/SUIUSDT_100d_current/sui.html
python3 report_sui.py

# NEARUSDT 永续：同样的100天图表和1h/4h切换
python3 report_near.py --refresh

# 历史截止时刻：排除当时未收盘的 K 线
python3 chan.py --csv your_1h.csv --as-of 2024-03-01T00:00:00Z --output-dir history

# 保留旧版近似，供对照
python3 chan.py --csv your_1h.csv --mode bi --output-dir output-bi

# 本次新增功能的单元回归，不调用案例验证器
python3 -m unittest test_chan test_structure test_theory test_dynamics test_cases_replay.ReplayTests test_research test_unified

# 原文规则/原文价位案例验证，输出每项的预期、实际和来源
python3 verify_cases.py

# 要求原文历史案例全部验证时，缺数据也返回非零状态
python3 verify_cases.py --require-historical

# 按截面回放，记录首次观察、修改、撤回；step=1 为逐根源 K 线
python3 replay.py --csv your_1h.csv --period 1d --step 24
```

`--max-levels` 为 1..20，默认 5；数据不足时自动停止向上递归。结构级别 L1、L2 不对应图表周期。1m 输入默认共用统一树；其他输入或显式 `--independent-periods` 时各周期独立构造层级。默认只选择能从源周期整倍数合并的周期，例如 8h 输入默认输出 8h、1d，12h 输入默认输出 12h、1d；显式要求用 8h 合成 12h 会报错。

`chan.py` 输出目录默认 `output/`，同名文件会更新。统一模式的 `analysis.json` 使用 `chan-unified-v1`，含 `tree/history/views/base_analysis`，同时输出可切换十周期的 `chart.html` 和 `report.md`，详见 [UNIFIED.md](UNIFIED.md)。以下为原独立模式的文件格式：

- `analysis.json`：schema v4，包含参数、数据质量、结构证据、各层子走势引用及 `theory` 分析。
- `chart.html`：蓝线为笔，黑色粗线为线段，虚线为未确认结构；橙/紫/青色框为 L1/L2/L3。鼠标悬停查看信息，长图横向滚动。
- `demo_1h.csv`：仅小时示例模式生成。17425 根合成 K 线，不是真实行情。
- `cases/case_report.json`、`cases/case_report.html`：单独运行案例验证器生成；重建测试与真实历史验证分开统计。
- `replay.jsonl`：单独运行回放器生成；每行对应一个当时可见的截面。

## CSV 与交易时段

```csv
timestamp,open,high,low,close,volume
2024-01-01T00:00:00Z,100,103,99,102,1000
2024-01-01T01:00:00Z,102,104,101,103,1200
2024-01-01T02:00:00Z,103,105,102,104,1100
```

`timestamp` 是**开盘时间**，ISO 8601 格式，支持 `Z` / `+08:00`，无时区按 UTC 解释。交易所整数时间戳需先转换。`volume` 可省略；一个文件只放一个品种、一个源周期，时间必须递增且无重复，OHLCV 必须有效。

默认适用 **24 小时连续市场**。4h 桶从 UTC 00:00、04:00… 开始，8h 从 00:00、08:00、16:00 开始，12h 从 00:00、12:00 开始，1d 为 UTC 00:00 起的 24 小时；`--anchor-hour` 可以调整 UTC 起点。例如北京时间自然日可用 `--anchor-hour 16`。源 K 线也必须对齐其开盘网格，日线是固定 24 小时而非交易所交易日历，不能从大周期反推小周期。

仅完整且已收盘的时间桶参与计算。缺根不填充，时间间断会分成独立 `blocks`，笔、线段及递归均不跨区段。`dropped_incomplete_buckets` 统计非空但不完整的桶，完全空的桶不计入其中；`gap_count` 是输出 K 线的时间间断数。

**有午休、夜盘或周末休市的市场，需要先适配交易日历及连续性判断。** 当前实现会把休市当作间断，不能直接用来分析跨交易时段的连续结构。

## 笔与特征序列线段

K 线包含处理、三根严格顶底分型、中心间距至少4及每笔全跨度极值约束保持不变。默认 `chan_strokes.connected_stroke_runs` 按稳定分型的可知顺序选择最早根节点可达的最新端点，输出一条连续笔链。新路径替换原先已完成的后缀时，记录修订时刻、撤回笔和替代笔；保留不变前缀的确认时间，替代笔不倒填确认时间。完成仅指当前划分，历史重划可影响段、中枢与信号。`--stroke-policy causal` 兼容映射到 connected，已移除v6强制分块；snapshot保留旧几何快照对照。`--initial-direction up/down` 决定样本开头包含方向；`--min-span` 可增大间距。末根去包含K线不用于确认分型，末笔候选不参与划段。

划段位于 `chan_structure.py`：

1. 向上段取向下笔为特征元素，向下段取向上笔。包含处理只在同一假设序列内顺序进行，并保留原始笔索引。
2. 每条确认线段至少三笔，前三笔价格区间有交集，笔数为奇数；终点为方向极值，全段价格范围另存，起点不强制为全段另一侧极值。
3. 针对每个候选转折，分别标准化转折前和转折后的序列，**不跨假设转折边界合并包含元素**。
4. 第一反向笔先与旧特征元素近端边界比较：上涨段比较高点，下跌段比较低点，含触及。已破坏时等待反向至少三笔跨度及后续严格越过首笔终点；仅触及时等待，先越过首笔起点则假设失效。包含后的表面分型不能替代该等待条件。
5. 有缺口：第一特征序列分型只是候选，还要从转折点出发的反向序列形成相反分型。第二个分型不再要求无缺口，也不要求价格填补第一个缺口。
6. 等待期间如果价格越过候选转折极值，候选失效。平高/平低先按包含处理，不直接当作严格分型。
7. 多个假设并存时，选择最先得到完整证据的转折。最后没有确认的段放在 `pending_segment`，不进入递归。开头不足以组成段的笔作为 `skipped_prefix_strokes` 报告。

线段终点须为方向极值；第 71 课的一笔破坏后原方向延续，可能越过旧起点，因此另用 `range_low/high` 保存全段波动，不把起点永久作为全段另一侧极值。首笔直接破坏时，后续第三笔未越过首笔终点之前，不能用包含合并形成的表面分型提前确认。

确认段输出 `confirmation=no_gap` 或 `gap_reverse_fractal`、`evidence_stroke`、`feature_indices`，第二类还包括 `reverse_feature_indices`，可以沿笔索引复核。

规则 v2 还要求转折后的已完成笔已经构成合法反向跨度（至少三奇数笔、前三笔重叠、终点为方向极值）。特征序列去包含形成的表面分型不能代替这一条件；否则首个反向笔的孤立极值可能把后续划段长期锁住。未满足时保留原段延续假设，不跳过中间笔。

## 递归规则：走势类型是上层的输入

本节说明保留作对照的旧 `hierarchy`，采用同级别分解策略 `same_level_earliest_triple`，不会直接把三个中枢矩形当作上一级的三段。**统一模式的 `tree/views` 使用下一节的已完成走势递归，不使用这些三段分解单位升级。**

```text
已确认线段（L0 基础构件）
  └─ 连续三段重叠 → L1 中枢 → 包含该中枢的 L1 盘整走势
       └─ 连续三个 L1 走势重叠 → L2 中枢 → L2 盘整走势
            └─ 连续三个 L2 走势重叠 → L3 中枢 → …
```

每一层按时间向前寻找最早的连续三子走势交集：

```python
ZD = max(child.low for child in triple)
ZG = min(child.high for child in triple)
formed = ZD <= ZG
```

这里 `child.low/high` 是**该下层走势经过的完整价格范围**，包含其内部波动，不是下层中枢的 ZD/ZG，也不是仅取走势首尾价格。严格模式把单点交集保留为零宽度中枢，图上显示为水平细带；旧 `bi` 模式保持原有正宽度约定。

找到三段重叠即形成一个本层中枢和一个本层盘整走势。按同级别分解，六段可以成为两个盘整，九段可成为三个盘整；下一层仍必须检查这三个走势的真实价格交集，**不会仅按数量强行升级**。如果最初几段没有交集，先保留为连接部分，找到第一个中枢后按 `a+B` 归入该盘整；中枢仍只引用实际形成交集的三段。末尾不足或无交集的部分列在 `unassigned_child_ids`，不补成上层走势。

每个子走势只能归属该层的一个父走势。父子级别严格相差 1，子走势必须首尾连续，禁止跳过中间走势后拼接，也禁止跨缺失数据区段。L1、L2… 是这种分解下的结构级别。`relations` 记录同层相邻中枢波动范围的相离/重叠关系，仅为几何关系，不把它直接冒充高层中枢或已完成趋势。

此视角下中枢 `status=formed`，走势 `kind=consolidation`。它们表示在指定同级别分解中的已形成单位，**不表示市场随后停止盘整**。非同级别视角的“固定中枢延伸直到第三类买卖点”和最长趋势合并是另一种分解，不能与本策略的输出混为一谈。

## 完成走势与背驰审计（新增）

默认分析五个周期时均运行新增模块，无需另开开关：

```bash
python3 chan.py --csv your_1h.csv --periods 1h 4h 8h 12h 1d --strength-fast 5 --strength-slow 10
```

`theory.type_hierarchy` 是 `chan_types.py` 输出的完成走势树：

- 单中枢保留为延伸中的盘整；多个同向相离中枢保留为延续中的趋势。三段成枢不再自动表示该走势完成。
- 采用保守的形态完成政策：反向三类点，或新中枢与原方向不相容（包括波动区间扩展）。在事件允许范围内按方向极值优先、同价取最后一次的顺序检查分界；首选极值不合法时继续检查其他位置，直到找到合法的完成前缀。所有候选失败才继续等待，子走势不重复、不遗漏。
- 规则 v3 修复了只检查单个极值导致的完成走势递归停滞。完成事件、已完成子走势输入及证据时间要求保持不变；历史结果需按新的规则指纹重新生成。
- `movements` 只包含已完成类型，`active_movement` 保存尾部，`events` 记录完成原因与重新分解前后的子走势 ID。上一级只接收 `movements`，每条子走势连续且仅归属一个完成父走势。
- 这是一种明确、保守的结合律分解政策，不声称是唯一分解或在转折当时立即确认；新旧递归视角的 ID 不能互换。

`theory.strict_divergence` 是 `chan_dynamics.py` 输出的审计：

规则 v2 的背驰与可用买卖点统一引用 `type_hierarchy.levels[].centers/structural_view`。旧 `theory.levels` 的三类点标为 `legacy_structural`，仅供对照，不进入可用交易候选。完成走势通路的 B3/S3 与 B1/S1 一并从 `strict_divergence.levels[].signals` 发布；B2/S2 仍为 `policy_dependent`，不会自动升级为确认信号。

L0 缺少内部中枢时，内部中枢数量、包含关系和标准趋势三个检查均为 `pending`。没有其他反例时保留待证状态，不把缺少内部证据当成背驰已经被证伪。

- 检查 `a+A+b+B+c` 顺序、A/B 同层同向相离、c 直接次级别、b 不高于 c、新高/新低、完成状态、组成来源、c 内部至少两个中枢、包含 B 的三类点，以及 c 内不得出现更高层中枢。
- 自动确认采用 c 内部中枢同向相离的标准分支。c 是离开、回试、再离开的同级走势结合区间，保存全部组成 ID；不是一条离开线就满足内部结构条件。
- 力度比较对象是 **b 与 c**。`cross_kiss_sma` 使用短/长 SMA 的完整相交波面积，以价格×小时为单位，在线性零点处切分并作梯形积分。默认 5/10 是工程参数；只覆盖相交或相切的吻，不猜测飞吻、唇吻。
- 同向波按峰值时刻归属价格走势；左界不得早于走势起点，闭合不得晚于该走势的结构证据时刻。全部归属波必须完整、无重复。无完整对应、预热不足或波未闭合均不能确认；不以未完面积推算最终面积。
- `audits[].checks` 逐条返回 `pass/fail/pending`。最终状态为 `confirmed_under_policy`、`rejected` 或 `pending`。`confirmed`、一二类点与确认区间套只使用通过全部条件的结果；MACD 辅助仍单独保留。

**力度策略的限制**：第 15 课提供了均线面积定义，第 72 课将均线系统与后来理论的动力学区分开。本实现把这一定义写成可复现算法，并不把它改名为完整动力学证明。非标准 c 内部形态、无低层内部中枢的 L0 基底和无法对应完整均线波的结构，不会强行生成确认信号。依据及参数约定见 [THEORY.md](THEORY.md)。

## JSON 字段与证据时间

`results[period].blocks[i]` 中：

| 字段 | 内容 |
| --- | --- |
| `stroke_diagnostics` | 选笔政策、保留起点/末端、无法从原起点衔接的尾部分型数量 |
| `strokes` | 笔及末笔候选，包含结构证据时刻 `confirmed_at` |
| `segments` | 已确认线段及其确认方式、笔索引、特征元素来源 |
| `direct_breaks` | 当前笔序列中的破坏事件，含发起笔 `turn_stroke` 和原段起点；原段延续后仍保留，不代表段结束 |
| `pending_segment` | 尚待确认的末段，或 null |
| `segment_diagnostics` | 被跳过的开头笔数、未归属笔起点、可用笔数及尾部每个转折的等待/失效原因 |
| `hierarchy.base_movements` | L0 已确认线段的统一走势表示 |
| `hierarchy.levels` | 每层的 `centers`、`movements`、`relations` 和未归属尾部 |
| `centers` | 各层中枢的展平列表，便于绘图 |
| `theory.levels` | 每层中枢延伸/离开/扩展、DD/GG/D/G、走势分类、买卖点与背驰辅助 |
| `theory.type_hierarchy` | 完成走势、延伸尾部、内部中枢、完成与重新分解事件 |
| `theory.strict_divergence` | 逐项结构审计、完整均线波、策略确认信号与确认区间套 |
| `theory.macd` | 参数、预热规则、回零阈值及 DIF/DEA/柱值 |
| `theory.nested_intervals` | 相邻结构层、同方向、时间包含的区间套候选关系 |

原有 `hierarchy` 中枢的 `child_ids` 恰好引用三条直接次级别走势；`movement_id` 指向包含它的本层走势。走势的 `child_ids` 覆盖全部组成部分，`center_ids` 指向其内部中枢。ID 在各连续数据区段内有效，跨区段引用时需带上周期和 block 索引。

`range_low/high` 表示形成该中枢的三子走势的整体波动范围，**没有沿用旧代理模式的 DD/GG 命名**。`latest_center_by_level` 提供最新连续区段各层最新中枢及收盘价相对位置；顶层 `last_close_position` 对应 L1。

**端点时间与结构证据时间不同**：`formed_at` 是第三子走势的终点，`evidence_at` 是其组成结构证据时刻的最大值。CSV 分析中，笔使用下一笔端点分型的稳定右邻作为证据；线段再等待特征序列条件。直接传入人工已完成笔时，可提供 `Stroke.confirmed_at`；未提供时按调用者声明其端点时已可用处理。

线段的 `local_evidence_stroke` 保存局部分型完成位置，`evidence_stroke` 则同时等待前段发布；`evidence_at` 取到该发布位置为止全部输入笔可用时间的最大值，且不早于笔自身终点。这样独立 API 输入的确认时间即使未按笔索引排序，也不会提前发布依赖尚不可用的线段。首段前缀只在前三笔无重叠时跳过，不因前三笔末端未达到方向极值而丢弃可延伸起点。

2026-10-12 转折审计修复直接破坏确认分支的诊断状态同步：`confirmed_direct_break` 不再同时报告 `reverse_span_ready=false`。分段边界及确认条件不变。穷举、随机序列和冻结 BTC 四周期证据复核见 [段转折审计](diagnostics/segment_turn_audit_20261012/report.md)。

在一条**固定、已完成的笔序列**上，确认线段只使用证据位置之前的数据，测试检查了前缀一致性。但现有 K 线选笔可在后续数据下撤回或修订，因此整个 K 线分析仍是快照，不能承诺全链条永不重绘。`evidence_at` 不是实际运行系统第一次观察到该对象的审计记录。`replay.py` 逐前缀运行并输出 `added/updated/retracted`、`first_observed_at`、`observed_at`；`--step` 大于 1 时首次观察仅精确到采样截面。不能把全量事后划分的端点当作历史信号。

## 案例验证

`cases/core_cases.json` 保存独立的输入、人工预期及原文链接；验证器逐项比较，不把没有数据的案例算作通过。现有 13 项：11 项原文规则的归一化重建，1 项第 27 课万科四个原文价位的局部核对，1 项第 24 课中国人寿五分钟历史案例待数据验证。案例详情见 [cases/README.md](cases/README.md)。

`python3 verify_cases.py` 在规则不符或输入错误时退出码为 1；`--require-historical` 在仍有部分验证或待数据的原文案例时退出码为 2。常规重建测试全部通过，不意味着原文历史案例已经全部复现。

## Python 接口

```python
from datetime import datetime, timezone
from chan import read_csv, resample, analyze

raw = read_csv("your_1h.csv")
bars, _ = resample(raw, 1, 4, datetime.now(timezone.utc))
report = analyze(bars, hours=4, mode="strict", max_levels=5)
for block in report["blocks"]:
    for layer in block["hierarchy"]["levels"]:
        for z in layer["centers"]:
            print(z.level, z.zd, z.zg, z.child_ids, z.evidence_at)
```

也可使用 `build_segments(strokes)` 和 `build_hierarchy(result.segments)` 独立核对。直接调用 `analyze` 时调用方负责传入已收盘 K 线。结果包含 dataclass/datetime；JSON 序列化可使用 `chan.json_default`。

## 测试与理论参考

测试覆盖：无缺口与有缺口的上下对称案例、跨转折包含、第三笔包含等待、第二分型仍有缺口、缺口未回补、候选被新高/新低否定、平高平低、末笔排除、固定完成笔的前缀一致性，以及 L1/L2/L3 的价格区间、谱系、尾部和确认时间传递。另有三个周期从数据处理到图表的集成验证。

原文转载参考（代码为本项目独立实现）：

- [第 67 课：线段的划分标准](https://chanlun108.cn/chanzhongshuochan108ke/67.html)
- [第 71 课：线段划分标准的再分辨](https://chanlun108.cn/chanzhongshuochan108ke/71.html)
- [第 81 课：图例及包含关系更正](https://chanlun108.cn/chanzhongshuochan108ke/81.html)
- [第 83 课：线段与最小中枢](https://chanlun108.cn/chanzhongshuochan108ke/83.html)
- [第 35 课：中枢与走势类型递归](https://chanlun108.cn/chanzhongshuochan108ke/35.html)
- [第 38 课：走势类型连接的同级别分解](https://chanlun108.cn/chanzhongshuochan108ke/38.html)

最早三段、连接部分归属、样本开头、平价和证据字段的处理，是为了让实现可复现而明确的规则约定；结果应与这些约定一起使用。


## 真实 BTC 行情的结构阻塞修复

[诊断报告](diagnostics/structure_blocking/report.md)记录选笔提前回退、特征序列包含/直接破坏分支、线段起点极值和盘整完成约束的问题。`python3 diagnose_structure.py` 使用本地同一份 BTC 行情及修复前代码快照做逐层对照，不下载行情，也不调松 `min_span`。

`type_hierarchy.next_level_center_seeds` 区分“三个完成子走势已经形成上级中枢”与“上级走势已经完成”。严格背驰层的 `evaluation_status=not_evaluated` 表示尚无可审计结构，不能解读成背驰条件已经逐项被否定。
