# telemetry.py
from app.api.auth import get_current_user
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List, Optional
from datetime import datetime, timedelta, timezone

from app.core.database import get_db
from app.core.company_scope import company_filter, is_platform
from app.models.models import Greenhouse, User
from app.models.models import Telemetry, Device
from app.schemas.schemas import TelemetryResponse

router = APIRouter()


@router.get("/", response_model=List[TelemetryResponse])
async def list_telemetry(
    device_id: Optional[str] = Query(None),
    greenhouse_id: Optional[str] = Query(None),
    hours: int = Query(default=24, le=168),
    skip: int = 0, limit: int = 500,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    conditions = [Telemetry.created_at >= since]
    if device_id:
        dev = await db.execute(select(Device).where(Device.device_id == device_id))
        device = dev.scalar_one_or_none()
        if device:
            conditions.append(Telemetry.device_id == device.id)
    if greenhouse_id:
        conditions.append(Telemetry.greenhouse_id == greenhouse_id)
    stmt = select(Telemetry).where(and_(*conditions))
    if not is_platform(user):
        stmt = stmt.join(Greenhouse, Telemetry.greenhouse_id == Greenhouse.id).where(
            company_filter(user, Greenhouse.company_id))
    result = await db.execute(
        stmt.order_by(Telemetry.timestamp.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()


@router.get("/latest/{device_id}")
async def get_latest(device_id: str, db: AsyncSession = Depends(get_db)):
    dev = await db.execute(select(Device).where(Device.device_id == device_id))
    device = dev.scalar_one_or_none()
    if not device:
        return {}
    result = await db.execute(
        select(Telemetry).where(Telemetry.device_id == device.id)
        .order_by(Telemetry.timestamp.desc()).limit(1)
    )
    t = result.scalar_one_or_none()
    return TelemetryResponse.model_validate(t) if t else {}
