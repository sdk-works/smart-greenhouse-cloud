"""
workbench.py — 大棚工作台 API

前端 /greenhouses 页面请求 GET /api/v1/workbench/greenhouses/{id}，
此前后端无此模块（404）。本模块补齐工作台所需的最小接口集。

设计约束（与既有代码保持一致，避免引入新的 schema 变更）：
  - 大棚布局/点位坐标继续存放在 greenhouses.map_data(JSON) 中，
    与 app/api/waypoints.py 的 [{id, x, y, label, description, actions}] 形状兼容。
  - 点位 ↔ 机器人导航点的绑定复用 robot_point_configs 表
    （RobotPointConfig.point_name 存大棚点位 code），与 point_configs.py 的
    assign/unassign 保持单一数据源。
  - 鉴权复用 app.api.auth.get_current_user + check_permission。
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.database import get_db
from app.models.models import (
    Device,
    Greenhouse,
    RobotPointConfig,
    Telemetry,
    User,
)

router = APIRouter()

MAX_POINTS_HISTORY = 20


# ── Pydantic ──────────────────────────────────────────────────
class WorkbenchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    location: Optional[str] = Field(default=None, max_length=200)
    area: Optional[float] = Field(default=None, ge=0)
    crop_type: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    map_data: Optional[Dict[str, Any]] = None


class WorkbenchProfileUpdate(BaseModel):
    """仅更新大棚档案字段；点位布局走 /point-config/maps/{gh}。"""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    location: Optional[str] = Field(default=None, max_length=200)
    area: Optional[float] = Field(default=None, ge=0)
    crop_type: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    is_active: Optional[bool] = None


# ── 内部辅助 ──────────────────────────────────────────────────
def _map_data(gh: Greenhouse) -> Dict[str, Any]:
    md = gh.map_data
    return dict(md) if isinstance(md, dict) else {}


def _waypoints(gh: Greenhouse) -> List[Dict[str, Any]]:
    wps = _map_data(gh).get("waypoints", [])
    return [w for w in wps if isinstance(w, dict)] if isinstance(wps, list) else []


def _point_code(wp: Dict[str, Any]) -> str:
    """点位 code：优先显式 code，其次 label，最后回落到数字 id。"""
    for key in ("code", "label"):
        val = wp.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return str(wp.get("id"))


async def _binding_index(db: AsyncSession, greenhouse_id: str) -> Dict[str, Dict[str, Any]]:
    """大棚点位 code -> 机器人绑定（以 point_name 为 key）。"""
    rows = (await db.execute(
        select(RobotPointConfig).where(
            RobotPointConfig.greenhouse_id == greenhouse_id)
    )).scalars().all()
    index: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        index[r.point_name] = {
            "robot_id": r.robot_id,
            "nav_point_code": r.nav_point_code,
            "config_id": r.id,
            "point_name": r.point_name,
            "sample_seconds": r.sample_seconds,
            "enabled": r.enabled,
        }
    return index


def _point_view(wp: Dict[str, Any], binding: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "id": wp.get("id"),
        "code": _point_code(wp),
        "name": wp.get("label") or _point_code(wp),
        "x": wp.get("x"),
        "y": wp.get("y"),
        "label": wp.get("label"),
        "description": wp.get("description"),
        "actions": wp.get("actions", []),
        "binding": binding,
    }


async def _get_greenhouse_or_404(db: AsyncSession, greenhouse_id: str) -> Greenhouse:
    gh = await db.get(Greenhouse, greenhouse_id)
    if not gh:
        raise HTTPException(status_code=404, detail="大棚不存在")
    return gh


async def _device_view(db: AsyncSession, greenhouse_id: str) -> List[Dict[str, Any]]:
    rows = (await db.execute(
        select(Device).where(Device.greenhouse_id == greenhouse_id)
        .order_by(Device.device_id)
    )).scalars().all()
    return [{
        "id": d.id,
        "device_id": d.device_id,
        "name": d.name,
        "device_type": d.device_type.value if d.device_type else None,
        "status": d.status.value if d.status else None,
        "last_seen": d.last_seen,
    } for d in rows]


async def _summary(db: AsyncSession, greenhouse_id: str) -> Dict[str, Any]:
    total = (await db.execute(
        select(func.count(Device.id)).where(Device.greenhouse_id == greenhouse_id)
    )).scalar() or 0
    latest = (await db.execute(
        select(Telemetry).where(Telemetry.greenhouse_id == greenhouse_id)
        .order_by(Telemetry.timestamp.desc()).limit(1)
    )).scalar_one_or_none()
    summary: Dict[str, Any] = {
        "device_count": int(total),
        "latest_telemetry_at": latest.timestamp if latest else None,
        "latest_telemetry": None,
    }
    if latest:
        summary["latest_telemetry"] = {
            "id": latest.id,
            "device_id": latest.device_id,
            "timestamp": latest.timestamp,
            "sampled_at": latest.sampled_at,
            "time_quality": latest.time_quality,
            "temperature": latest.temperature,
            "humidity": latest.humidity,
            "soil_moisture": latest.soil_moisture,
            "co2_level": latest.co2_level,
            "chassis_point": latest.chassis_point,
            "greenhouse_point": latest.greenhouse_point,
        }
    return summary


async def _workbench_payload(db: AsyncSession, gh: Greenhouse) -> Dict[str, Any]:
    bindings = await _binding_index(db, gh.id)
    points = [_point_view(w, bindings.get(_point_code(w))) for w in _waypoints(gh)]
    return {
        "id": gh.id,
        "name": gh.name,
        "location": gh.location,
        "area": gh.area,
        "crop_type": gh.crop_type,
        "description": gh.description,
        "is_active": gh.is_active,
        "created_at": gh.created_at,
        "updated_at": gh.updated_at,
        "map_data": _map_data(gh),
        "points": points,
        "devices": await _device_view(db, gh.id),
        "summary": await _summary(db, gh.id),
    }


# ── 接口 ──────────────────────────────────────────────────────
@router.post("/greenhouses", status_code=status.HTTP_201_CREATED)
async def create_workbench_greenhouse(
    payload: WorkbenchCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_permission(user, "manage_greenhouses")
    gh = Greenhouse(**payload.model_dump())
    db.add(gh)
    await db.commit()
    await db.refresh(gh)
    return await _workbench_payload(db, gh)


@router.get("/greenhouses/{greenhouse_id}")
async def get_workbench_greenhouse(
    greenhouse_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_permission(user, "view_data")
    gh = await _get_greenhouse_or_404(db, greenhouse_id)
    return await _workbench_payload(db, gh)


@router.put("/greenhouses/{greenhouse_id}/profile")
async def update_workbench_profile(
    greenhouse_id: str,
    payload: WorkbenchProfileUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_permission(user, "manage_greenhouses")
    gh = await _get_greenhouse_or_404(db, greenhouse_id)
    if not payload.model_dump(exclude_unset=True):
        raise HTTPException(status_code=400, detail="请求体为空，未提供任何要更新的字段")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(gh, key, value)
    await db.commit()
    await db.refresh(gh)
    return await _workbench_payload(db, gh)


@router.get("/greenhouses/{greenhouse_id}/points/{point_id}")
async def get_workbench_point(
    greenhouse_id: str,
    point_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """点位详情：优先按 code/label 匹配，其次按数字 id 匹配。"""
    check_permission(user, "view_data")
    gh = await _get_greenhouse_or_404(db, greenhouse_id)

    waypoints = _waypoints(gh)
    needle = point_id.strip()
    target = None
    for wp in waypoints:
        if _point_code(wp) == needle:
            target = wp
            break
    if target is None:
        for wp in waypoints:
            if str(wp.get("id")) == needle:
                target = wp
                break
    if target is None:
        raise HTTPException(status_code=404, detail=f"点位不存在：{point_id}")

    code = _point_code(target)
    binding = (await _binding_index(db, gh.id)).get(code)

    history = (await db.execute(
        select(Telemetry).where(
            Telemetry.greenhouse_id == gh.id,
            Telemetry.greenhouse_point == code,
        ).order_by(Telemetry.timestamp.desc()).limit(MAX_POINTS_HISTORY)
    )).scalars().all()

    return {
        **_point_view(target, binding),
        "greenhouse_id": gh.id,
        "greenhouse_name": gh.name,
        "history": [{
            "id": t.id,
            "timestamp": t.timestamp,
            "sampled_at": t.sampled_at,
            "time_quality": t.time_quality,
            "msg_id": t.msg_id,
            "temperature": t.temperature,
            "humidity": t.humidity,
            "soil_moisture": t.soil_moisture,
            "co2_level": t.co2_level,
            "chassis_point": t.chassis_point,
            "greenhouse_point": t.greenhouse_point,
        } for t in history],
    }
