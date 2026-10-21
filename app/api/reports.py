"""报表导出（M4）：**一律限定在操作者公司范围内**。

导出内容：遥测明细。支持 csv / json。
平台运营可用 ?company_id= 指定公司，该次访问会落审计日志（与 M1 一致）。
"""
import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.audit import ensure_company_access
from app.core.company_scope import (
    can_access_company, is_platform, scoped_greenhouse_ids,
)
from app.core.database import get_db
from app.models.models import Greenhouse, Telemetry, User

router = APIRouter()

MAX_ROWS = 10000
EXPORT_COLUMNS = [
    "id", "device_id", "greenhouse_id", "timestamp", "sampled_at",
    "time_quality", "temperature", "humidity", "soil_moisture",
    "soil_temperature", "soil_ec", "ph_value", "light_intensity",
    "co2_level", "attribution_status", "company_id", "created_at",
]


@router.get("/reports/telemetry")
async def export_telemetry(
    greenhouse_id: Optional[str] = Query(None),
    hours: int = Query(24, ge=1, le=24 * 30),
    fmt: str = Query("json", pattern="^(json|csv)$"),
    company_id: Optional[str] = Query(None, description="仅平台运营可用"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await ensure_company_access(db, user, company_id, resource="reports.telemetry",
                                action="export")
    if greenhouse_id:
        gh = await db.get(Greenhouse, greenhouse_id)
        if not gh or not can_access_company(user, gh.company_id):
            raise HTTPException(404, "大棚不存在")
    if company_id and not is_platform(user):
        raise HTTPException(403, "无权导出其它公司数据")

    ghq = scoped_greenhouse_ids(user, company_id)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    conds = [Telemetry.created_at >= since]
    if ghq is not None:
        conds.append(Telemetry.greenhouse_id.in_(ghq))
    if greenhouse_id:
        conds.append(Telemetry.greenhouse_id == greenhouse_id)

    rows = (await db.execute(
        select(Telemetry).where(*conds).order_by(Telemetry.created_at.desc())
        .limit(MAX_ROWS))).scalars().all()
    await db.commit()

    items = [{c: getattr(r, c) for c in EXPORT_COLUMNS} for r in rows]
    meta = {"count": len(items), "truncated": len(items) >= MAX_ROWS,
            "hours": hours, "greenhouse_id": greenhouse_id,
            "scope": "platform_all" if ghq is None else "company"}

    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for it in items:
            writer.writerow({k: ("" if v is None else v) for k, v in it.items()})
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="telemetry-{hours}h.csv"'})

    return {"meta": meta, "items": items}


@router.get("/reports/telemetry/summary")
async def telemetry_summary(
    greenhouse_id: Optional[str] = Query(None),
    hours: int = Query(24, ge=1, le=24 * 30),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """按大棚汇总窗口内的记录数与最新值（同样限定公司范围）。"""
    from sqlalchemy import func
    if greenhouse_id:
        gh = await db.get(Greenhouse, greenhouse_id)
        if not gh or not can_access_company(user, gh.company_id):
            raise HTTPException(404, "大棚不存在")
    ghq = scoped_greenhouse_ids(user, None)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    conds = [Telemetry.created_at >= since]
    if ghq is not None:
        conds.append(Telemetry.greenhouse_id.in_(ghq))
    if greenhouse_id:
        conds.append(Telemetry.greenhouse_id == greenhouse_id)
    rows = (await db.execute(
        select(Telemetry.greenhouse_id, func.count(Telemetry.id),
               func.avg(Telemetry.temperature), func.avg(Telemetry.humidity))
        .where(*conds).group_by(Telemetry.greenhouse_id))).all()
    return {"hours": hours, "items": [
        {"greenhouse_id": r[0], "count": r[1],
         "avg_temperature": None if r[2] is None else round(float(r[2]), 2),
         "avg_humidity": None if r[3] is None else round(float(r[3]), 2)}
        for r in rows]}
