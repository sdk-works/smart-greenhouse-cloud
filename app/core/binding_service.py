"""设备绑定（第三批）：通道互斥 + 绑定版本在代码层强制。

device_bindings 的 schema 早已支持 channel / binding_version / 起止时间，
但之前没有任何代码层校验，可能产生"同一通道同时绑两个大棚"或"绑定版本回退"。

规则：
  1. **通道互斥**：同一 (device_id, channel) 的生效区间不得重叠。
  2. **绑定版本单调递增**：新绑定的 binding_version 必须 > 该设备历史最大版本；
     版本回退 / 与当前版本相同 → 拒绝（409）。
  3. 结束绑定 → 写 end_at（不物理删除，保留历史，供补传归属判定）。
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import DeviceBinding, Greenhouse, InspectionPoint


def _as_utc(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    """两个区间是否有交集（开区间 [start, end)）。"""
    a_end = a_end or datetime.max.replace(tzinfo=timezone.utc)
    b_end = b_end or datetime.max.replace(tzinfo=timezone.utc)
    return a_start < b_end and b_start < a_end


async def next_binding_version(db: AsyncSession, device_id: str) -> int:
    cur = (await db.execute(select(func.max(DeviceBinding.binding_version)).where(
        DeviceBinding.device_id == device_id))).scalar() or 0
    return int(cur) + 1


async def create_binding(
    db: AsyncSession,
    *,
    device_id: str,
    greenhouse_id: str,
    point_id: Optional[str] = None,
    channel: str = "default",
    binding_version: Optional[int] = None,
    start_at: Optional[datetime] = None,
    end_at: Optional[datetime] = None,
    raise_on_conflict: bool = True,
) -> DeviceBinding:
    """登记一条绑定。违反通道互斥或版本回退时抛 409。"""
    if not (await db.get(Greenhouse, greenhouse_id)):
        raise HTTPException(404, "大棚不存在")
    if point_id and not (await db.get(InspectionPoint, point_id)):
        raise HTTPException(404, "点位不存在")

    start = _as_utc(start_at) or datetime.now(timezone.utc)
    end = _as_utc(end_at)
    if end is not None and end <= start:
        raise HTTPException(400, "绑定结束时间必须晚于开始时间")

    want_version = binding_version if binding_version is not None \
        else await next_binding_version(db, device_id)

    # 规则 2：绑定版本必须严格递增
    history = (await db.execute(select(DeviceBinding).where(
        DeviceBinding.device_id == device_id))).scalars().all()
    if history:
        max_ver = max(int(b.binding_version or 0) for b in history)
        if int(want_version) <= max_ver:
            if raise_on_conflict:
                raise HTTPException(
                    409, f"绑定版本必须大于该设备的最大版本 {max_ver}（收到 {want_version}）")
            return None

    # 规则 1：通道互斥 —— 同通道的生效区间不得重叠。
    # 设备搬迁必须先结束旧绑定（保留历史）再登记新绑定，不做任何自动改写。
    for b in history:
        if (b.channel or "default") != (channel or "default"):
            continue
        if _overlaps(start, end, _as_utc(b.start_at), _as_utc(b.end_at)):
            if raise_on_conflict:
                raise HTTPException(
                    409,
                    f"通道 '{channel}' 在 {start.isoformat()} 起的时间段内已有绑定"
                    f"（绑定 {b.id}，版本 {b.binding_version}）；请先结束旧绑定")
            return None

    binding = DeviceBinding(
        id=str(uuid.uuid4()),
        device_id=device_id, greenhouse_id=greenhouse_id, point_id=point_id,
        channel=channel or "default",
        binding_version=int(want_version),
        start_at=start, end_at=end,
    )
    db.add(binding)
    await db.flush()
    return binding


async def end_binding(db: AsyncSession, binding_id: str,
                      at: Optional[datetime] = None) -> Optional[DeviceBinding]:
    b = await db.get(DeviceBinding, binding_id)
    if b is None:
        return None
    b.end_at = _as_utc(at) or datetime.now(timezone.utc)
    await db.flush()
    return b
