"""数据新鲜度（M3 数据可靠性）。

新鲜度必须在**查询时按时间计算**，不能入库时写死一个状态再用好几天。

时间基准优先级：
  1. sampled_at（设备采样时间）—— 仅当 time_quality=1（时间可信）时使用
  2. created_at（入库时间）—— 时间不可信时退化，可信度会一并标注

状态：fresh（≤60s）/ lagging（≤5min）/ stale（≤15min）/ expired（>15min）/ unknown
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

FRESH_SECONDS = 60
LAGGING_SECONDS = 5 * 60
STALE_SECONDS = 15 * 60


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def pick_basis(sampled_at, created_at, time_quality) -> Tuple[Optional[datetime], str]:
    """返回 (基准时间, 基准来源)。"""
    s = _as_utc(sampled_at)
    if s is not None and int(time_quality or 0) == 1:
        return s, "sampled_at"
    c = _as_utc(created_at)
    if c is not None:
        return c, "created_at"
    return None, "none"


def compute_freshness(
    *,
    sampled_at=None,
    created_at=None,
    time_quality=1,
    now: Optional[datetime] = None,
) -> dict:
    now = _as_utc(now) or datetime.now(timezone.utc)
    basis_at, basis = pick_basis(sampled_at, created_at, time_quality)
    if basis_at is None:
        return {"state": "unknown", "age_seconds": None, "basis": "none",
                "time_quality": int(time_quality or 0), "as_of": now.isoformat()}

    age = (now - basis_at).total_seconds()
    if age < 0:
        # 设备时钟超前（未校时）：时间不可信，但记录仍然新鲜
        state = "future"
    elif age <= FRESH_SECONDS:
        state = "fresh"
    elif age <= LAGGING_SECONDS:
        state = "lagging"
    elif age <= STALE_SECONDS:
        state = "stale"
    else:
        state = "expired"
    return {"state": state, "age_seconds": round(age, 1), "basis": basis,
            "time_quality": int(time_quality or 0), "as_of": now.isoformat()}


def is_fresh_enough(fresh: dict, max_age_seconds: int = STALE_SECONDS) -> bool:
    age = fresh.get("age_seconds")
    if age is None:
        return False
    return 0 <= age <= max_age_seconds
