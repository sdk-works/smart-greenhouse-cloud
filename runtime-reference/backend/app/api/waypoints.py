"""
waypoints.py — 点位管理 API (V8新增)
支持对大棚内巡检点位的完整 CRUD 操作
点位数据存储在 greenhouses.map_data.waypoints 中
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db
from app.models.models import Greenhouse

router = APIRouter()


class WaypointIn(BaseModel):
    x: float
    y: float
    label: Optional[str] = None
    description: Optional[str] = None
    actions: List[str] = []  # capture_image / measure_env / pest_detect


class WaypointOut(BaseModel):
    id: int
    x: float
    y: float
    label: Optional[str] = None
    description: Optional[str] = None
    actions: List[str] = []


def _get_waypoints(gh: Greenhouse) -> list:
    return (gh.map_data or {}).get("waypoints", [])


def _save_waypoints(gh: Greenhouse, waypoints: list):
    md = dict(gh.map_data or {})
    md["waypoints"] = waypoints
    gh.map_data = md


@router.get("/{greenhouse_id}/waypoints", response_model=List[WaypointOut])
async def list_waypoints(greenhouse_id: str, db: AsyncSession = Depends(get_db)):
    """获取大棚所有点位"""
    async with db.begin():
        # 👇 强制刷新，不读缓存！
        await db.expire_all()
        r = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
        gh = r.scalar_one_or_none()
    if not gh:
        raise HTTPException(status_code=404, detail="大棚不存在")
    return _get_waypoints(gh)

@router.post("/{greenhouse_id}/waypoints", response_model=WaypointOut, status_code=201)
async def create_waypoint(
    greenhouse_id: str,
    payload: WaypointIn,
    db: AsyncSession = Depends(get_db),
):
    """新增点位（自动分配 id）"""
    async with db.begin():
        # 👇 加 FOR UPDATE 锁，强制等前一个请求完成
        r = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id).with_for_update())
        gh = r.scalar_one_or_none()
        if not gh:
            raise HTTPException(status_code=404, detail="大棚不存在")

        wps = _get_waypoints(gh)
        new_id = max((w.get("id", 0) for w in wps), default=0) + 1
        wp = {"id": new_id, **payload.model_dump()}
        wps.append(wp)
        _save_waypoints(gh, wps)
        await db.commit()

    return wp

@router.put("/{greenhouse_id}/waypoints/{wp_id}", response_model=WaypointOut)
async def update_waypoint(
    greenhouse_id: str,
    wp_id: int,
    payload: WaypointIn,
    db: AsyncSession = Depends(get_db),
):
    """修改指定点位"""
    async with db.begin():
        r = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
        gh = r.scalar_one_or_none()
        if not gh:
            raise HTTPException(status_code=404, detail="大棚不存在")

        wps = _get_waypoints(gh)
        idx = next((i for i, w in enumerate(wps) if w.get("id") == wp_id), None)
        if idx is None:
            raise HTTPException(status_code=404, detail="点位不存在")

        wps[idx] = {"id": wp_id, **payload.model_dump()}
        _save_waypoints(gh, wps)
        await db.commit()
    return wps[idx]


@router.delete("/{greenhouse_id}/waypoints/{wp_id}", status_code=204)
async def delete_waypoint(
    greenhouse_id: str,
    wp_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除指定点位"""
    async with db.begin():
        r = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
        gh = r.scalar_one_or_none()
        if not gh:
            raise HTTPException(status_code=404, detail="大棚不存在")

        wps = [w for w in _get_waypoints(gh) if w.get("id") != wp_id]
        _save_waypoints(gh, wps)
        await db.commit()
