"""结构、力度、交易候选分层；量价仅为独立的描述性辅助。"""
from dataclasses import asdict
from datetime import timedelta
from statistics import mean


def volume_price(candles, hours=1, lookback=20):
    if type(lookback) is not int or lookback < 2:
        raise ValueError("量价 lookback 必须是至少 2 的整数")
    result = {"policy": "relative_volume_absolute_close_change_v1", "lookback": lookback,
              "status": "insufficient_history", "tags": [],
              "scope": "descriptive_auxiliary_not_wyckoff_phase_or_chan_divergence",
              "thresholds": {"high_volume": 1.5, "low_progress": 0.5, "high_progress": 1.5}}
    if len(candles) < lookback + 2:
        return result
    part = candles[-lookback-2:]
    if any(b.time-a.time != timedelta(hours=hours) for a, b in zip(part, part[1:])):
        return {**result, "status": "data_gap"}
    c, prev = part[-1], part[-2]
    volumes = [x.volume for x in part[1:-1]]
    progress = [abs(b.close-a.close) for a, b in zip(part[:-2], part[1:-1])]
    if c.volume <= 0 or any(v <= 0 for v in volumes):
        return {**result, "status": "volume_unavailable"}
    if mean(progress) == 0:
        return {**result, "status": "zero_price_reference"}
    effort, effect = c.volume / mean(volumes), abs(c.close-prev.close) / mean(progress)
    tags = []
    if effort >= 1.5 and effect <= 0.5:
        tags.append("high_effort_low_result")
    if effort >= 1.5 and effect >= 1.5:
        tags.append("high_effort_high_result")
    return {**result, "status": "measured", "tags": tags, "volume_ratio": effort,
            "price_progress_ratio": effect, "direction": "up" if c.close > prev.close else
            "down" if c.close < prev.close else "flat", "bar_time": c.time,
            "available_at": c.time + timedelta(hours=hours)}


def decision_layers(blocks):
    structures, strengths, candidates = [], [], []
    for block in blocks:
        prefix = block["start"].isoformat()
        theory = block.get("theory", {})
        for layer in theory.get("levels", []):
            for d in layer["divergences"]:
                strengths.append({"id": f"{prefix}:{d['id']}", "source": "macd_auxiliary",
                                  "metric": "same_direction_histogram_sum", "units": "price_bars",
                                  "ratio": d["area_ratio"], "evidence_at": d["evidence_at"],
                                  "strict_confirmation": False})
        for layer in theory.get("strict_divergence", {}).get("levels", []):
            for d in layer["audits"]:
                checks = [x for x in d["checks"] if x["rule"] not in
                          ("strength_comparable", "strength_decreases")]
                state = "rejected" if any(x["state"] == "fail" for x in checks) else (
                    "pending" if any(x["state"] == "pending" for x in checks) else "passed")
                structures.append({"id": f"{prefix}:{d['id']}", "state": state,
                                   "checks": checks, "evidence_at": d["evidence_at"]})
                strengths.append({"id": f"{prefix}:{d['id']}", "source": "strict_policy",
                                  "metric": "cross_kiss_sma", "units": "price_hours",
                                  "measurement": d["strength"], "evidence_at": d["evidence_at"]})
        for source, layers in (("signal", theory.get("levels", [])),
                               ("policy_signal", theory.get("strict_divergence", {}).get("levels", []))):
            for layer in layers:
                for signal in layer["signals"]:
                    eligible = source == "policy_signal" and (
                        signal.certainty == "structural" and signal.label in ("B3", "S3")
                        or signal.certainty == "policy_confirmed" and signal.label in ("B1", "S1"))
                    candidates.append({"id": f"{prefix}:{source}:L{signal.level}:{signal.label}:{signal.time.isoformat()}",
                                       "signal": asdict(signal), "eligible_under_default_policy": eligible,
                                       "state": "awaiting_execution_rules" if eligible else "blocked",
                                       "reason": "requires_observation_and_next_open" if eligible else
                                       "auxiliary_or_dependent_signal_not_confirmed"})
    return {"structure_audits": structures, "strength_measurements": strengths,
            "trade_candidates": candidates, "orders": [],
            "scope": "snapshot_candidates; no_order_without_replay_and_execution_policy"}
