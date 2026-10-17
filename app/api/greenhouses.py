from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from typing import List
from datetime import datetime, timedelta, timezone

from app.core.database import get_db
from app.models.models import Greenhouse, Device, Telemetry, DeviceStatus
from app.schemas.schemas import GreenhouseCreate, GreenhouseUpdate, GreenhouseResponse

router = APIRouter()


@router.get("/", response_model=List[GreenhouseResponse])
async def list_greenhouses(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Greenhouse).where(Greenhouse.is_active == True).offset(skip).limit(limit)
    )
    return result.scalars().all()


@router.post("/", response_model=GreenhouseResponse, status_code=status.HTTP_201_CREATED)
async def create_greenhouse(payload: GreenhouseCreate, db: AsyncSession = Depends(get_db)):
    gh = Greenhouse(**payload.model_dump())
    db.add(gh)
    await db.commit()
    await db.refresh(gh)
    return gh


@router.get("/{greenhouse_id}", response_model=GreenhouseResponse)
async def get_greenhouse(greenhouse_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
    gh = result.scalar_one_or_none()
    if not gh:
        raise HTTPException(status_code=404, detail="Greenhouse not found")
    return gh


@router.put("/{greenhouse_id}", response_model=GreenhouseResponse)
async def update_greenhouse(greenhouse_id: str, payload: GreenhouseUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
    gh = result.scalar_one_or_none()
    if not gh:
        raise HTTPException(status_code=404, detail="Greenhouse not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(gh, k, v)
    await db.commit()
    await db.refresh(gh)
    return gh


@router.delete("/{greenhouse_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_greenhouse(greenhouse_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
    gh = result.scalar_one_or_none()
    if not gh:
        raise HTTPException(status_code=404, detail="Greenhouse not found")
    gh.is_active = False
    await db.commit()


@router.get("/{greenhouse_id}/stats")
async def get_greenhouse_stats(greenhouse_id: str, db: AsyncSession = Depends(get_db)):
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
