"""补传数据归属判定（M3 数据可靠性）。

规则：**按绑定窗口判定，不确定写 pending，不猜。**

device_bindings 记录了 (device, greenhouse, point, channel) 的生效区间与绑定版本。
设备可能被搬移，因此"设备当前所在大棚"不等于"数据产生时所在大棚"。

判定结果：
  resolved —— 事件时刻落在唯一一条绑定窗口内
  pending  —— 没有绑定历史 / 落在所有窗口之外 / 同时落在多条同版本窗口内（歧义）
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import DeviceBinding

ATTRIBUTION_RESOLVED = "resolved"
ATTRIBUTION_PENDING = "pending"

REASON_IN_WINDOW = "binding_window"
REASON_NO_HISTORY = "no_binding_history"
REASON_OUTSIDE_WINDOW = "outside_binding_window"
REASON_AMBIGUOUS = "ambiguous_binding_window"


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def resolve_attribution(
    db: AsyncSession,
    *,
    device_id: str,
    at: Optional[datetime] = None,
    channel: Optional[str] = None,
) -> dict:
    """判定某条数据在 `at` 时刻的归属。

    返回：
      {status, reason, greenhouse_id, point_id, binding_id, binding_version}
    status=pending 时 greenhouse_id/point_id 仍会给"尽力而为"的兜底值（可能是 None），
    调用方必须只看 status，不要只看 greenhouse_id。
    """
    at = _as_utc(at) or datetime.now(timezone.utc)

    q = select(DeviceBinding).where(DeviceBinding.device_id == device_id)
    if channel:
        q = q.where(DeviceBinding.channel == channel)
    all_rows = (await db.execute(q)).scalars().all()

    if not all_rows:
        return {"status": ATTRIBUTION_PENDING, "reason": REASON_NO_HISTORY,
                "greenhouse_id": None, "point_id": None, "binding_id": None,
                "binding_version": None}

    covering = []
    for b in all_rows:
        start = _as_utc(b.start_at)
        end = _as_utc(b.end_at)
        if start is not None and at < start:
            continue
        if end is not None and at >= end:
            continue
        covering.append(b)

    if not covering:
        # 落在所有绑定窗口之外（如补传的是装机之前的旧数据）
        latest = max(all_rows, key=lambda b: (_as_utc(b.start_at) or at))
        return {"status": ATTRIBUTION_PENDING, "reason": REASON_OUTSIDE_WINDOW,
                "greenhouse_id": latest.greenhouse_id, "point_id": latest.point_id,
                "binding_id": None, "binding_version": latest.binding_version}

    max_ver = max(int(b.binding_version or 1) for b in covering)
    top = [b for b in covering if int(b.binding_version or 1) == max_ver]
    if len(top) > 1:
        return {"status": ATTRIBUTION_PENDING, "reason": REASON_AMBIGUOUS,
                "greenhouse_id": top[0].greenhouse_id, "point_id": top[0].point_id,
                "binding_id": None, "binding_version": max_ver}

    b = top[0]
    return {"status": ATTRIBUTION_RESOLVED, "reason": REASON_IN_WINDOW,
            "greenhouse_id": b.greenhouse_id, "point_id": b.point_id,
            "binding_id": b.id, "binding_version": int(b.binding_version or 1)}
