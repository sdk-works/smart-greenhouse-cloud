"""设备绑定管理（M3）：通道互斥 + 绑定版本在代码层强制。"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.binding_service import create_binding, end_binding, next_binding_version
from app.core.company_scope import can_access_company
from app.core.database import get_db
from app.models.models import Device, DeviceBinding, Greenhouse, User

router = APIRouter()


class BindingIn(BaseModel):
    greenhouse_id:   str
    point_id:        Optional[str] = None
    channel:         str = Field(default="default", max_length=50)
    binding_version: Optional[int] = Field(default=None, ge=1)
    start_at:        Optional[datetime] = None
    end_at:          Optional[datetime] = None


def _out(b: DeviceBinding) -> dict:
    return {"id": b.id, "device_id": b.device_id, "greenhouse_id": b.greenhouse_id,
            "point_id": b.point_id, "channel": b.channel,
            "binding_version": b.binding_version,
            "start_at": b.start_at, "end_at": b.end_at,
            "active": b.end_at is None}


async def _load_device(db, device_id: str) -> Device:
    dev = (await db.execute(select(Device).where(Device.device_id == device_id))
           ).scalar_one_or_none() or await db.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "设备不存在")
    return dev


@router.get("/devices/{device_id}/bindings")
async def list_bindings(device_id: str, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    dev = await _load_device(db, device_id)
    rows = (await db.execute(select(DeviceBinding).where(
        DeviceBinding.device_id == dev.id).order_by(
        DeviceBinding.binding_version.desc()))).scalars().all()
    return {"device_id": dev.device_id,
            "next_binding_version": await next_binding_version(db, dev.id),
            "items": [_out(b) for b in rows], "total": len(rows)}


@router.post("/devices/{device_id}/bindings", status_code=201)
async def add_binding(device_id: str, payload: BindingIn,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    check_permission(user, "manage_robots")
    dev = await _load_device(db, device_id)
    gh = await db.get(Greenhouse, payload.greenhouse_id)
    if not gh or not can_access_company(user, gh.company_id):
        raise HTTPException(404, "大棚不存在")
    b = await create_binding(
        db, device_id=dev.id, greenhouse_id=payload.greenhouse_id,
        point_id=payload.point_id, channel=payload.channel,
        binding_version=payload.binding_version,
        start_at=payload.start_at, end_at=payload.end_at)
    await db.commit()
    return _out(b)


@router.post("/devices/bindings/{binding_id}/end")
async def close_binding(binding_id: str,
                        at: Optional[datetime] = Query(None),
                        db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """结束绑定：写 end_at，保留历史（补传归属判定依赖这些历史）。"""
    check_permission(user, "manage_robots")
    b = await end_binding(db, binding_id, at)
    if b is None:
        raise HTTPException(404, "绑定不存在")
    await db.commit()
    return _out(b)
