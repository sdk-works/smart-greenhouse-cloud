"""种植批次（M4）：大棚/分区 + 作物 + 起止。"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.company_scope import (
    can_access_company, is_platform, resolve_company_id, scoped_greenhouse_ids,
)
from app.core.database import get_db
from app.models.models import Greenhouse, PlantingBatch, User

router = APIRouter()

VALID_STATUS = ("planned", "active", "harvested", "closed")
STATUS_TRANSITIONS = {
    "planned":   {"active", "closed"},
    "active":    {"harvested", "closed"},
    "harvested": {"closed"},
    "closed":    set(),
}


class PlantingBatchIn(BaseModel):
    code:       str = Field(min_length=1, max_length=60)
    greenhouse_id: str
    zone:       Optional[str] = Field(default=None, max_length=100)
    crop_type:  str = Field(min_length=1, max_length=100)
    variety:    Optional[str] = Field(default=None, max_length=100)
    area:       Optional[float] = Field(default=None, gt=0)
    planted_at: Optional[datetime] = None
    expected_harvest_at: Optional[datetime] = None
    note:       Optional[str] = Field(default=None, max_length=1000)


class PlantingBatchStatusIn(BaseModel):
    status: str = Field(min_length=1, max_length=20)
    note:   Optional[str] = Field(default=None, max_length=1000)


def _out(b: PlantingBatch) -> dict:
    return {"id": b.id, "code": b.code, "greenhouse_id": b.greenhouse_id,
            "zone": b.zone, "crop_type": b.crop_type, "variety": b.variety,
            "area": b.area, "status": b.status,
            "planted_at": b.planted_at,
            "expected_harvest_at": b.expected_harvest_at,
            "harvested_at": b.harvested_at, "note": b.note,
            "company_id": b.company_id, "created_at": b.created_at}


@router.get("/planting-batches")
async def list_batches(greenhouse_id: Optional[str] = Query(None),
                       status: Optional[str] = Query(None),
                       limit: int = Query(50, ge=1, le=200),
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    q = select(PlantingBatch)
    if not is_platform(user):
        q = q.where(PlantingBatch.company_id == resolve_company_id(user))
    if greenhouse_id:
        q = q.where(PlantingBatch.greenhouse_id == greenhouse_id)
    if status:
        q = q.where(PlantingBatch.status == status)
    rows = (await db.execute(
        q.order_by(PlantingBatch.created_at.desc()).limit(limit))).scalars().all()
    return {"items": [_out(b) for b in rows], "total": len(rows)}


@router.post("/planting-batches", status_code=201)
async def create_batch(payload: PlantingBatchIn,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    check_permission(user, "manage_greenhouses")
    gh = await db.get(Greenhouse, payload.greenhouse_id)
    if not gh or not can_access_company(user, gh.company_id):
        raise HTTPException(404, "大棚不存在")
    exists = (await db.execute(select(PlantingBatch).where(
        PlantingBatch.code == payload.code))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "批次编码已存在")
    b = PlantingBatch(
        id=str(uuid.uuid4()),
        code=payload.code, greenhouse_id=gh.id, zone=payload.zone,
        crop_type=payload.crop_type, variety=payload.variety, area=payload.area,
        status="planned", planted_at=payload.planted_at,
        expected_harvest_at=payload.expected_harvest_at, note=payload.note,
        company_id=gh.company_id, created_by=user.id)
    db.add(b)
    await db.commit()
    return _out(b)


@router.get("/planting-batches/{batch_id}")
async def get_batch(batch_id: str, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    b = await db.get(PlantingBatch, batch_id)
    if not b or not can_access_company(user, b.company_id):
        raise HTTPException(404, "批次不存在")
    return _out(b)


@router.post("/planting-batches/{batch_id}/status")
async def move_status(batch_id: str, payload: PlantingBatchStatusIn,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    check_permission(user, "manage_greenhouses")
    b = await db.get(PlantingBatch, batch_id)
    if not b or not can_access_company(user, b.company_id):
        raise HTTPException(404, "批次不存在")
    target = payload.status
    if target not in VALID_STATUS:
        raise HTTPException(400, f"未知批次状态：{target}")
    if target != b.status and target not in STATUS_TRANSITIONS.get(b.status, set()):
        raise HTTPException(400, f"非法批次状态流转：{b.status} → {target}")
    b.status = target
    if target == "harvested":
        b.harvested_at = datetime.now(timezone.utc)
    if payload.note:
        b.note = payload.note
    await db.commit()
    return _out(b)
