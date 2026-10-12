"""可序列化的研究口径；策略参数不冒充缠论原文定理。"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math


@dataclass(frozen=True)
class AnalysisPolicy:
    min_span: int = 4
    initial_direction: str = "up"
    mode: str = "strict"
    max_levels: int = 5
    zero_axis_ratio: float = 0.2
    strength_fast: int = 5
    strength_slow: int = 10

    def __post_init__(self):
        for name in ("min_span", "max_levels", "strength_fast", "strength_slow"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} 必须为整数")
        if self.min_span < 4 or not 1 <= self.max_levels <= 20:
            raise ValueError("min_span >= 4 且 1 <= max_levels <= 20")
        if self.initial_direction not in ("up", "down") or self.mode not in ("strict", "bi"):
            raise ValueError("initial_direction 或 mode 无效")
        if not 1 <= self.strength_fast < self.strength_slow:
            raise ValueError("力度窗口须满足 1 <= fast < slow")
        if not math.isfinite(self.zero_axis_ratio) or not 0 <= self.zero_axis_ratio <= 1:
            raise ValueError("zero_axis_ratio 必须在 0..1")

    def kwargs(self):
        return asdict(self)

    def manifest(self):
        rules = {
            "version": "chan-rules-v5",
            "parameters": self.kwargs(),
            "inclusion": "directional_high_low_merge; stable_right_neighbor",
            "stroke": "earliest_origin_latest_reachable_max_edges_v1",
            "stroke_evidence": "stable_right_neighbor_witness_first_raw_close",
            "segment": "feature_sequence_no_gap_or_reverse_fractal; reverse_valid_span_required",
            "segment_origin": "earliest_overlapping_first_three; retain_extendable_origin",
            "segment_evidence": "local_pattern_plus_predecessor_publication; max_input_prefix_availability",
            "direct_break": "retain_attacking_stroke_events_across_segment_extension",
            "recursive_center": "same_level_earliest_triple; full_child_range; zd<=zg",
            "completed_type": "opposite_third_point_or_incompatible_center; completed_children_only",
            "completed_type_boundary": "directional_extreme_ranked_legal_prefix; latest_equal_extreme",
            "signal_structure": "completed_type_center_registry; legacy_signals_ineligible",
            "center_ranges": "independent_observation_range; preserve_parent_decomposition_members",
            "third_signal_history": "seed_identity; causal_prefix_publication; retain_closed_center_evidence",
            "third_retry": "new_departure_after_return_to_center; consume_first_success",
            "base_divergence": "missing_L0_internal_centers_is_pending",
            "macd": {"metric": "same_direction_histogram_sum", "parameters": [12, 26, 9],
                     "histogram_multiplier": 2, "interval": "[start,end)",
                     "units": "price_bars", "scope": "auxiliary_only"},
            "strength": {"metric": "cross_kiss_sma", "units": "price_hours",
                         "scope": "complete_aligned_waves_only"},
            "confirmation": "snapshot_evidence_can_be_revised; replay_observation_required",
            "scope": "explicit_engineering_policy_not_unique_canonical_decomposition",
        }
        rules["fingerprint"] = hashlib.sha256(json.dumps(
            rules, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return rules


def load_policy(path=None):
    if path is None:
        return AnalysisPolicy()
    with open(path, encoding="utf-8") as f:
        values = json.load(f)
    if not isinstance(values, dict) or set(values) - set(AnalysisPolicy.__dataclass_fields__):
        raise ValueError("规则 JSON 必须是 AnalysisPolicy 参数对象，不能包含未知参数")
    return AnalysisPolicy(**values)


def candle_digest(candles):
    """包含全部 OHLCV 和 UTC 开盘时间，外部引擎对照必须使用同一输入。"""
    from datetime import timezone
    rows = [[c.time.astimezone(timezone.utc).isoformat(),
             *[float(getattr(c, k)) for k in ("open", "high", "low", "close", "volume")]]
            for c in candles]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
