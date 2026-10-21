from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import List
from datetime import datetime, timedelta, timezone

from app.core.database import get_db
from app.api.auth import check_permission, get_current_user
from app.models.models import Greenhouse, Device, Telemetry, DeviceStatus
from app.models.models import User
from app.core.company_scope import (DEFAULT_BASE_ID, can_access_company,
                                   company_filter, resolve_company_id)
from app.schemas.schemas import GreenhouseCreate, GreenhouseUpdate, GreenhouseResponse

router = APIRouter()


@router.get("/", response_model=List[GreenhouseResponse])
async def list_greenhouses(skip: int = 0, limit: int = 100,
                           db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)):
    q = select(Greenhouse).where(Greenhouse.is_active == True)
    cond = company_filter(user, Greenhouse.company_id)
    if cond is not None:
        q = q.where(cond)
    result = await db.execute(q.offset(skip).limit(limit))
    return result.scalars().all()


@router.post("/", response_model=GreenhouseResponse, status_code=status.HTTP_201_CREATED)
async def create_greenhouse(payload: GreenhouseCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    check_permission(user, "manage_greenhouses")
    data = payload.model_dump()
    data["company_id"] = resolve_company_id(user)
    data["base_id"] = DEFAULT_BASE_ID
    gh = Greenhouse(**data)
    db.add(gh)
    await db.commit()
    await db.refresh(gh)
    return gh


@router.get("/{greenhouse_id}", response_model=GreenhouseResponse)
async def get_greenhouse(greenhouse_id: str, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    result = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
    gh = result.scalar_one_or_none()
    if not gh or not can_access_company(user, gh.company_id):
        raise HTTPException(status_code=404, detail="Greenhouse not found")
    return gh


@router.put("/{greenhouse_id}", response_model=GreenhouseResponse)
async def update_greenhouse(greenhouse_id: str, payload: GreenhouseUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    check_permission(user, "manage_greenhouses")
    result = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
    gh = result.scalar_one_or_none()
    if not gh or not can_access_company(user, gh.company_id):
        raise HTTPException(status_code=404, detail="Greenhouse not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(gh, k, v)
    await db.commit()
    await db.refresh(gh)
    return gh


@router.delete("/{greenhouse_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_greenhouse(greenhouse_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    check_permission(user, "manage_greenhouses")
    result = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
    gh = result.scalar_one_or_none()
    if not gh or not can_access_company(user, gh.company_id):
        raise HTTPException(status_code=404, detail="Greenhouse not found")
    gh.is_active = False
    await db.commit()


@router.get("/{greenhouse_id}/stats")
async def get_greenhouse_stats(greenhouse_id: str, db: AsyncSession = Depends(get_db),
                               user: User = Depends(get_current_user)):
    gh_chk = await db.get(Greenhouse, greenhouse_id)
    if not gh_chk or not can_access_company(user, gh_chk.company_id):
        raise HTTPException(status_code=404, detail="Greenhouse not found")
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    total = (await db.execute(
        select(func.count(Device.id)).where(Device.greenhouse_id == greenhouse_id)
    )).scalar()
    online = (await db.execute(
        select(func.count(Device.id)).where(
            and_(Device.greenhouse_id == greenhouse_id, Device.status == DeviceStatus.ONLINE)
        )
    )).scalar()
    avgs = (await db.execute(
        select(
            func.avg(Telemetry.temperature),
            func.avg(Telemetry.humidity),
            func.avg(Telemetry.soil_moisture),
            func.avg(Telemetry.co2_level),
        ).where(and_(Telemetry.greenhouse_id == greenhouse_id, Telemetry.created_at >= since))
    )).one()
    return {
        "greenhouse_id": greenhouse_id,
        "total_devices": total,
        "online_devices": online,
        "avg_temperature": round(avgs[0] or 0, 2),
        "avg_humidity": round(avgs[1] or 0, 2),
        "avg_soil_moisture": round(avgs[2] or 0, 2),
        "avg_co2": round(avgs[3] or 0, 2),
    }
