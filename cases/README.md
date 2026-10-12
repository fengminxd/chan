# 案例验证数据

运行 `python3 verify_cases.py`，输出在 `output/cases/`。`core_cases.json` 的每个案例有来源、输入和独立人工预期值。

| 类型 | 当前数量 | 含义 |
| --- | --- | --- |
| `reconstructed_rule` | 11 | 原文规则的归一化重建，价格为合成值；不冒充原图行情 |
| `original_text_points` | 1 | 第 27 课万科季度的 36.7、3.2、15.99、3.12 四个明示价位 |
| `historical_ohlcv_required` | 1 | 第 24 课中国人寿五分钟行情，默认缺原始 CSV 和 C 段终点 |

万科案例可以核对重叠区间 `[3.2,15.99]`、跌幅 33.5/12.87 和后段新低；四个价格不足以计算 MACD，因此只报告 `partial_pass`。没有根据季度价位伪造逐根 OHLC。

第 81 课案例依据文字中的平价/包含关系重建；没有逐坐标录入原图，报告不是“原图三段划分已完整复现”的声明。

## 为中国人寿案例提供数据

可以将数据放在任意目录，通过以下方式指定：

```bash
python3 verify_cases.py --data-dir /path/to/case_data
```

该目录需包含：

- `601628_5m.csv`：与主程序相同的 OHLCV 列，时间明确带 `+08:00` 或 `Z`。需包括 A 段前的 MACD 预热数据，覆盖至 C 段结束。
- `601628_5m.labels.json`：`{"c_end": "人工核对后的 ISO 8601 时间"}`。示意字符串不是有效时间，必须替换成真实标注。

原文给出的 A 起止和 C 起点已经放在清单中，不自行编造 C 的精确结束时刻。验证器得到数据后可核对 A/C 的红柱面积及新高条件，仍只会报告部分验证；完整原图复现还需要交易时段、复权、原文笔/线段标注的对齐。

返回码：规则/预期不符为 1。默认允许待数据案例并在报告中列出；加 `--require-historical` 时，存在原文案例尚未完全验证便返回 2，适合 CI 防止把不完整验证当作全通过。

## 清单结构

```json
{
  "id": "unique_case_name",
  "title": "案例名称",
  "source": "原文页面链接",
  "provenance": "reconstructed_rule",
  "kind": "segments",
  "input": {"prices": [0, 8, 4, 10, 6, 9, 5]},
  "expected": {"end_prices": [10], "confirmation": ["no_gap"]}
}
```

验证器逐项检查 `expected` 中的字段，列表长度必须匹配，浮点数使用小容差比较。支持的 `kind` 为 `inclusion`、`segments`、`evolution`、`hierarchy`、`weak_second`、`price_legs`、`historical_macd`。在 `hierarchy` 案例中，价格序列是人工确认线段的端点，验证范围是递归组合，不是底层划笔。
