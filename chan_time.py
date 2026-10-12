"""周期以整数分钟校验和对齐；保留原 Python 接口的小时单位。"""
from datetime import timedelta
import math


PERIOD_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60,
                  "2h": 120, "4h": 240, "8h": 480, "12h": 720, "1d": 1440}
PERIODS = {name: minutes / 60 if minutes < 60 else minutes // 60
           for name, minutes in PERIOD_MINUTES.items()}


def period_minutes(hours):
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours):
        raise ValueError("周期须为受支持的有限小时数；分钟周期请使用 PERIODS['1m'] 等")
    minutes = round(hours * 60)
    if minutes not in PERIOD_MINUTES.values() or not math.isclose(hours * 60, minutes, rel_tol=0, abs_tol=1e-9):
        raise ValueError("支持周期：" + ", ".join(PERIODS))
    return minutes


def period_delta(hours):
    return timedelta(minutes=period_minutes(hours))


def period_name(hours):
    minutes = period_minutes(hours)
    return next(name for name, value in PERIOD_MINUTES.items() if value == minutes)


def compatible_periods(source_hours):
    source = period_minutes(source_hours)
    return [name for name, minutes in PERIOD_MINUTES.items() if minutes % source == 0]
