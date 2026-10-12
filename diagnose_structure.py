"""在已下载 BTC 原始行情上逐层消融，保存可复查的结构阻塞诊断。

不下载数据、不调宽 min_span、不运行历史案例验证器。
"""
from contextlib import ExitStack
from datetime import timezone, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

import chan
import chan_structure
import chan_types

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'diagnostics/structure_blocking'
SOURCE = ROOT / 'reports/BTCUSDT_perpetual_20260606_20260819/context_1h.csv'
LEGACY_PERIODS = ('1h', '4h', '8h', '12h', '1d')  # 固定历史消融口径，不能用小时源反推分钟线。


def old_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def counts(report):
    block = report['blocks'][0]
    theory = block['theory']
    return {'bars': report['bar_count'], 'fractals': len(block['fractals']),
            'strokes': len(block['strokes']), 'segments': len(block['segments']),
            'first_stroke_start': block['strokes'][0].start.time if block['strokes'] else None,
            'last_segment_end': block['segments'][-1].end.time if block['segments'] else None,
            'completed_types': {f"L{l['level']}": len(l['movements']) for l in theory['type_hierarchy']['levels']},
            'next_level_center_seeds': theory['type_hierarchy']['next_level_center_seeds'],
            'audits': sum(len(l['audits']) for l in theory['strict_divergence']['levels']),
            'confirmed': len(theory['strict_divergence']['confirmed']),
            'stroke_diagnostics': block.get('stroke_diagnostics'),
            'segment_diagnostics': block['segment_diagnostics'],
            'type_diagnostics': [l.get('diagnostics') for l in theory['type_hierarchy']['levels']],
            'divergence_diagnostics': [{'level': l['level'], 'status': l['evaluation_status'],
                                        'waiting_reason': l['waiting_reason'], 'audits': l['audits']}
                                       for l in theory['strict_divergence']['levels']]}


def main():
    old_chan = old_module('before_chan', OUT/'chan.py')
    old_structure = old_module('before_structure', OUT/'chan_structure.py')
    old_types = old_module('before_types', OUT/'chan_types.py')
    stages = [
        ('before', old_chan.build_strokes, old_structure.build_segments, old_types._boundary),
        ('pen_fix', chan.build_strokes, old_structure.build_segments, old_types._boundary),
        ('segment_fix', chan.build_strokes, chan_structure.build_segments, old_types._boundary),
        ('all_fixes', chan.build_strokes, chan_structure.build_segments, chan_types._boundary),
    ]
    raw = chan.read_csv(SOURCE)
    cutoff = chan.parse_time('2026-08-19T00:00:00+08:00')
    summary = {'input_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
               'as_of': cutoff, 'anchor_hour_utc': 16, 'min_span': 4,
               'stages': {}, 'code_sha256': {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
                    for p in ['chan.py', 'chan_structure.py', 'chan_types.py', 'chan_dynamics.py']}}
    fixed_series, fixed_results = {}, {}
    for name, pens, segments, boundary in stages:
        stage = {}
        # 旧版函数没有新增诊断参数/字段；兼容包装只补元数据，不改旧逻辑。
        def compatible_pens(fs, bars, min_span=4, diagnostics=None):
            if pens is old_chan.build_strokes:
                if diagnostics is not None:
                    diagnostics['policy'] = 'before_cascading_rollback'
                return pens(fs, bars, min_span)
            return pens(fs, bars, min_span, diagnostics)

        def compatible_segments(strokes):
            result = segments(strokes)
            if not hasattr(result, 'tail_diagnostics'):
                result.tail_diagnostics = {'status': 'not_instrumented_in_old_version'}
            return result

        with ExitStack() as stack:
            stack.enter_context(patch.object(chan, 'build_strokes', compatible_pens))
            stack.enter_context(patch.object(chan, 'build_segments', compatible_segments))
            stack.enter_context(patch.object(chan_types, '_boundary', boundary))
            for period in LEGACY_PERIODS:
                hours = chan.PERIODS[period]
                bars, quality = chan.resample(raw, 1, hours, cutoff, 16)
                report = chan.analyze(bars, hours)
                report['data_quality'] = quality
                stage[period] = counts(report)
                if name == 'all_fixes':
                    fixed_results[period] = report
                    fixed_series[period] = bars
        summary['stages'][name] = stage
        print(name, [(p, d['strokes'], d['segments'], d['completed_types'], d['audits'])
                     for p, d in stage.items()], flush=True)
    (OUT/'comparison.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2,
                                                 default=chan.json_default))
    (OUT/'analysis_after.json').write_text(json.dumps({'schema_version':4,'results':fixed_results},
                                         ensure_ascii=False, indent=2, default=chan.json_default))
    chan.write_chart(OUT/'chart_after.html', fixed_results, fixed_series)
    lines = ['# BTC 结构阻塞诊断', '',
             '输入为同一份 Binance BTCUSDT 永续小时线，包含 120 天前置行情；'
             '截止 UTC+8 2026-08-19 00:00。各阶段保持数据、min_span=4、周期锚点和力度参数不变。', '',
             '| 周期 | 旧笔数→修复后 | 旧线段→修复后 | 完成 L1 走势 | 背驰审计 |',
             '| --- | --- | --- | --- | --- |']
    for p in LEGACY_PERIODS:
        a, b = summary['stages']['before'][p], summary['stages']['all_fixes'][p]
        lines.append(f"| {p} | {a['strokes']}→{b['strokes']} | {a['segments']}→{b['segments']} | "
                     f"{a['completed_types']['L1']}→{b['completed_types']['L1']} | {a['audits']}→{b['audits']} |")
    lines += ['', '## 根因与修复', '',
        '1. **选笔提前撤回并级联删除历史。** 旧版遇到间距不足的反向分型就撤笔，'
        '随后因跨区间极值校验失败成对删除。1h 最早笔由 2 月被推到 6 月。'
        '改为保留满足相同距离/方向/极值约束的端点备选图：最早可成笔起点、最新可达终点、'
        '同一起终点最多合法笔。没有连接的新根不替换旧历史，不可达尾部另报。'
        '这是公开的确定性选笔政策，不声称所有新笔规则都已实现。', '',
        '2. **特征序列包含方向与直接破坏分支。** 待确认分型的右侧包含提前按反向合并，'
        '可能抹去中间元素顶底。修正为按旧段方向初始化；首笔直接破坏时，'
        '必须等待后续笔越过首笔终点，不能让包含合并产生的表面分型抢先确认。', '',
        '3. **线段起点被额外要求永久是全段极值。** 第 71 课允许一笔破坏后原方向继续，'
        '旧约束会让后续合法转折全部无效。保留终点方向极值、奇数笔和前三笔重叠条件，'
        '单独保存全段 range_low/high，不丢失越过起点的价格。', '',
        '4. **盘整结束被额外要求创新高/低。** 反向三类点出现后，较低顶/较高底不能通过旧的'
        '全区间极值检查。移除该额外限制，仍须已有完整中枢、合法方向端点及后续完成证据。', '',
        '## 尚待确认与政策限制', '',
        '这些修复不以增加信号数量为目标。4h/8h/12h/1d 的尾段仍需各自的特征序列证据；'
        '未完成走势不会强行提升。具体候选转折、等待第一分型/反向分型、缺少完成子走势等原因'
        '见 comparison.json 中的 segment_diagnostics/type_diagnostics。', '',
        '完成走势分解政策保留一个进入段，因而上层需要四个完成子走势才能进入该分解。'
        '三个已完成子走势本身可能有重叠中枢；现在另行输出 next_level_center_seeds，'
        '明确标为中枢已形成、父走势未完成。这与保留进入段的完整走势分解不是同一个判断。'
        '本次没有把这个明示的政策门槛偷偷放宽成三段就确认完整走势。', '',
        '背驰审计记录非零不等于背驰成立：必须查看每项 pass/fail/pending。'
        '原报告的零信号结论受到结构错误影响，不应继续作为市场判断。'
        '本诊断为截止时刻重算；原有逐根回放属于旧版本，不能用于验证本次结果。', '',
        '## 文件', '',
        '- comparison.json：逐层消融、尾部阻塞原因、背驰审计及输入/代码校验值。',
        '- analysis_after.json / chart_after.html：修复后的完整五周期分析及图表。',
        '- 本目录 chan.py / chan_structure.py / chan_types.py / chan_theory.py：修复前代码快照。', '',
        '参考：[第 62 课](https://chanlun108.cn/chanzhongshuochan108ke/62.html)、'
        '[第 67 课](https://chanlun108.cn/chanzhongshuochan108ke/67.html)、'
        '[第 71 课](https://chanlun108.cn/chanzhongshuochan108ke/71.html)。']
    (OUT/'report.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    main()
