"""数据新鲜度查询（M3）。

新鲜度**在查询时按时间计算**，不是入库时写死的状态。
时间基准：sampled_at（time_quality=1 时）→ 否则 created_at。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.company_scope import scoped_greenhouse_ids
from app.core.database import get_db
from app.core.freshness import compute_freshness, is_fresh_enough
from app.models.models import Device, Greenhouse, Telemetry, User

router = APIRouter()


@router.get("/freshness/devices")
async def device_freshness(
    greenhouse_id: Optional[str] = Query(None),
    max_age_seconds: int = Query(900, ge=1, le=86400),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """各设备最新一条数据的新鲜度（查询时计算）。"""
    ghq = scoped_greenhouse_ids(user, None)
    q = select(Device)
    if ghq is not None:
        q = q.where(Device.greenhouse_id.in_(ghq))
    if greenhouse_id:
        q = q.where(Device.greenhouse_id == greenhouse_id)
    devices = (await db.execute(q.order_by(Device.device_id))).scalars().all()

    items = []
    for d in devices:
        row = (await db.execute(
            select(Telemetry).where(Telemetry.device_id == d.id)
            .order_by(Telemetry.created_at.desc()).limit(1))).scalar_one_or_none()
        if row is None:
            items.append({"device_id": d.device_id, "name": d.name,
                          "greenhouse_id": d.greenhouse_id,
                          "freshness": compute_freshness(sampled_at=None,
                                                         created_at=None),
                          "has_data": False, "within_max_age": False})
            continue
        fresh = compute_freshness(sampled_at=row.sampled_at, created_at=row.created_at,
                                  time_quality=row.time_quality)
        items.append({
            "device_id": d.device_id, "name": d.name,
            "greenhouse_id": d.greenhouse_id, "has_data": True,
            "sampled_at": row.sampled_at, "created_at": row.created_at,
            "time_quality": row.time_quality,
            "freshness": fresh,
            "within_max_age": is_fresh_enough(fresh, max_age_seconds),
        })
    total = len(items)
    ok = sum(1 for i in items if i["within_max_age"])
    return {"items": items, "total": total, "within_max_age": ok,
            "max_age_seconds": max_age_seconds}


@router.get("/freshness/greenhouses/{greenhouse_id}")
async def greenhouse_freshness(greenhouse_id: str,
                               db: AsyncSession = Depends(get_db),
                               user: User = Depends(get_current_user)):
    """某大棚最新一条遥测的新鲜度。"""
    from app.core.company_scope import can_access_company
    gh = await db.get(Greenhouse, greenhouse_id)
    if not gh or not can_access_company(user, gh.company_id):
        raise HTTPException(404, "大棚不存在")
    row = (await db.execute(
        select(Telemetry).where(Telemetry.greenhouse_id == greenhouse_id)
        .order_by(Telemetry.created_at.desc()).limit(1))).scalar_one_or_none()
    if row is None:
        return {"greenhouse_id": greenhouse_id, "has_data": False,
                "freshness": compute_freshness()}
    return {"greenhouse_id": greenhouse_id, "has_data": True,
            "sampled_at": row.sampled_at, "created_at": row.created_at,
            "time_quality": row.time_quality,
            "freshness": compute_freshness(sampled_at=row.sampled_at,
                                           created_at=row.created_at,
                                           time_quality=row.time_quality)}
