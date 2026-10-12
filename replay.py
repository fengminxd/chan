"""逐前缀回放，记录结构首次观察、修订和撤回，避免用事后端点冒充信号时间。"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Iterator

from chan import PERIODS, analyze, json_default, parse_time, read_csv, resample, validate_candles
from chan_policy import AnalysisPolicy, load_policy


def snapshot_objects(report: dict) -> dict:
    objects = {}
    for block in report["blocks"]:
        prefix = block["start"].isoformat()
        for s in block["strokes"]:
            key = f"{prefix}:stroke:{s.start.time.isoformat()}"
            objects[key] = {"type": "stroke", "value": asdict(s)}
        pending = block.get("pending_segment")
        for s in block.get("segments", []) + ([pending] if pending else []):
            key = f"{prefix}:segment:{s.start.time.isoformat()}"
            objects[key] = {"type": "segment", "value": asdict(s)}
        for z in block["centers"]:
            key = f"{prefix}:center:L{getattr(z, 'level', 1)}:{z.start.isoformat()}"
            objects[key] = {"type": "recursive_center", "value": asdict(z)}
        for layer in block.get("theory", {}).get("levels", []):
            for z in layer["centers"]:
                key = f"{prefix}:evolving:L{z.level}:{z.start.isoformat()}"
                objects[key] = {"type": "evolving_center", "value": asdict(z)}
            for s in layer["signals"]:
                key = f"{prefix}:signal:L{s.level}:{s.label}:{s.time.isoformat()}"
                objects[key] = {"type": "legacy_signal", "value": asdict(s)}
        for layer in block.get("theory", {}).get("type_hierarchy", {}).get("levels", []):
            for m in layer["movements"] + ([layer["active_movement"]] if layer["active_movement"] else []):
                key = f"{prefix}:type:L{m.level}:{m.start.time.isoformat()}"
                objects[key] = {"type": "movement_type", "value": asdict(m)}
        for layer in block.get("theory", {}).get("strict_divergence", {}).get("levels", []):
            for d in layer["audits"]:
                key = f"{prefix}:divergence:L{d['level']}:{d['start'].isoformat()}:{d['end'].isoformat()}"
                objects[key] = {"type": "divergence_audit", "value": d}
            for s in layer["signals"]:
                key = f"{prefix}:policy_signal:L{s.level}:{s.label}:{s.time.isoformat()}"
                objects[key] = {"type": "policy_signal", "value": asdict(s)}
    return objects


def object_timing(item):
    value = item["value"]
    endpoint = value.get("end", value.get("time"))
    if isinstance(endpoint, dict):
        endpoint = endpoint["time"]
    evidence = value.get("evidence_at", value.get("confirmed_at"))
    kind = item["type"]
    if kind == "stroke":
        confirmed = not value["provisional"] and evidence is not None
    elif kind == "segment":
        confirmed = value["confirmed"]
    elif kind == "movement_type":
        confirmed = value["status"] == "completed"
    elif kind == "divergence_audit":
        confirmed = value["state"] == "confirmed_under_policy"
    elif kind in ("signal", "policy_signal", "legacy_signal"):
        confirmed = kind != "legacy_signal" and value["certainty"] in ("structural", "policy_confirmed")
    else:
        confirmed = evidence is not None
    return endpoint, evidence, confirmed


class LifecycleTracker:
    """同一开始锚点的修订共享身份；确认仅针对当时快照，不承诺不可撤回。"""
    def __init__(self):
        self.previous = {}
        self.history = {}

    def update(self, current, observed_at):
        events = []
        for key, item in current.items():
            if key in self.previous and item == self.previous[key]:
                continue
            endpoint, evidence, confirmed = object_timing(item)
            if evidence is not None and evidence > observed_at:
                raise ValueError(f"未来证据不能进入回放: {key}")
            old = self.history.get(key)
            first_confirmed = old["first_confirmed_observed_at"] if old else None
            life = {"first_observed_at": old["first_observed_at"] if old else observed_at,
                    "revision": old["revision"] + 1 if old else 1,
                    "first_confirmed_observed_at": first_confirmed or (observed_at if confirmed else None),
                    "current_revision_confirmed_at": observed_at if confirmed else None,
                    "endpoint_time": endpoint, "structural_evidence_at": evidence,
                    "status": "confirmed_in_snapshot" if confirmed else "candidate",
                    "reappeared": old is not None and key not in self.previous}
            self.history[key] = life
            events.append({"action": "updated" if key in self.previous else "added", "id": key,
                           "first_observed_at": life["first_observed_at"], "observed_at": observed_at,
                           "lifecycle": dict(life), "structure": item})
        for key in sorted(self.previous.keys() - current.keys()):
            life = {**self.history[key], "status": "retracted", "retracted_at": observed_at,
                    "current_revision_confirmed_at": None}
            self.history[key] = life
            events.append({"action": "retracted", "id": key, "first_observed_at": life["first_observed_at"],
                           "observed_at": observed_at, "lifecycle": dict(life),
                           "previous_structure": self.previous[key]})
        self.previous = current
        return events


def replay(candles, source_hours: int = 1, target_hours: int = 1, step: int = 24,
           min_bars: int = 1, as_of: datetime | None = None, anchor_hour: int = 0,
           max_levels: int = 5, strength_fast: int = 5, strength_slow: int = 10,
           policy: AnalysisPolicy | None = None) -> Iterator[dict]:
    validate_candles(candles)
    if step < 1 or min_bars < 1:
        raise ValueError("step 和 min_bars 必须为正整数")
    cutoff = as_of or datetime.now(timezone.utc)
    # 使用与主程序相同的参数校验，包括无法整倍数合并的周期。
    resample([], source_hours, target_hours, cutoff, anchor_hour)
    closed = [c for c in candles if c.time + timedelta(hours=source_hours) <= cutoff]
    stops = list(range(min_bars, len(closed) + 1, step))
    if len(closed) >= min_bars and (not stops or stops[-1] != len(closed)):
        stops.append(len(closed))
    policy = policy or AnalysisPolicy(max_levels=max_levels, strength_fast=strength_fast,
                                      strength_slow=strength_slow)
    tracker = LifecycleTracker()
    last_bars, report = None, None
    for stop in stops:
        current_time = closed[stop - 1].time + timedelta(hours=source_hours)
        bars, quality = resample(closed[:stop], source_hours, target_hours, current_time, anchor_hour)
        if bars != last_bars:
            report = analyze(bars, target_hours, **policy.kwargs())
            last_bars = bars
        current = snapshot_objects(report)
        events = tracker.update(current, current_time)
        yield {"as_of": current_time, "source_bar_count": stop, "target_bar_count": len(bars),
               "sampling_step_source_bars": step, "events": events,
               "object_count": len(current), "period": report["period"],
               "rule_fingerprint": report["rule_manifest"]["fingerprint"],
               "volume_price": report["volume_price"], "data_quality": quality}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--context-csv", help="统一 1m 模式的前置行情")
    parser.add_argument("--independent-periods", action="store_true", help="显式逐周期重算；1m 输入默认统一树")
    parser.add_argument("--source-period", choices=PERIODS, default="1h")
    parser.add_argument("--period", choices=PERIODS, help="独立模式的目标周期，默认 1h；统一模式只有 1m 事件来源")
    parser.add_argument("--step", type=int, default=24, help="每多少根源 K 线采样一次；1 为逐根，耗时更长")
    parser.add_argument("--min-bars", type=int, default=1)
    parser.add_argument("--anchor-hour", type=int, default=0)
    parser.add_argument("--max-levels", type=int, default=5)
    parser.add_argument("--strength-fast", type=int, default=5)
    parser.add_argument("--strength-slow", type=int, default=10)
    parser.add_argument("--as-of")
    parser.add_argument("--policy", type=Path, help="AnalysisPolicy JSON；提供时覆盖回放的分析参数")
    parser.add_argument("--output", type=Path, default=Path("output/replay.jsonl"))
    args = parser.parse_args()
    try:
        data = read_csv(args.csv)
        if args.source_period == "1m" and not args.independent_periods:
            from unified import replay_unified
            if args.min_bars != 1:
                raise ValueError("统一 1m 回放从首根运行，不能跳过前置观察；请保留 --min-bars 1")
            if args.period not in (None, "1m"):
                raise ValueError("统一回放只有 1m 事件来源；独立目标周期请显式加 --independent-periods")
            policy = load_policy(args.policy) if args.policy else AnalysisPolicy(
                max_levels=args.max_levels, strength_fast=args.strength_fast, strength_slow=args.strength_slow)
            rows = replay_unified(data, parse_time(args.as_of) if args.as_of else datetime.now(timezone.utc),
                                  read_csv(args.context_csv) if args.context_csv else [], policy, args.step, args.anchor_hour)
        else:
            if args.context_csv:
                raise ValueError("--context-csv 需要统一 1m 回放")
            rows = replay(data, PERIODS[args.source_period], PERIODS[args.period or '1h'], args.step, args.min_bars,
                          parse_time(args.as_of) if args.as_of else None, args.anchor_hour, args.max_levels,
                          args.strength_fast, args.strength_slow, load_policy(args.policy) if args.policy else None)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with args.output.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=json_default, allow_nan=False) + "\n")
                count += 1
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"回放 {count} 个截面：{args.output}（首次观察时间受 step 采样间隔影响）")


if __name__ == "__main__":
    main()
