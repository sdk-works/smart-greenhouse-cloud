"""
workbench.py — 大棚工作台 API（v2：点位独立于机器人、绑定为投影）

契约（前端 pages/GreenhouseWorkbench.js / PointDetail.js）：
  GET /workbench/greenhouses/{id} -> {profile, map:{revision,layout,defaults,next_name,points}, latest}
  POST /workbench/greenhouses    -> {greenhouse_id}
  PUT  /workbench/greenhouses/{id}/profile
  GET  /workbench/greenhouses/{id}/points/{pointId}?days=N

点位：inspection_points（稳定，独立于机器人）
绑定：robot_point_mappings（point_id ↔ robot_id/nav_point_code，支持一点多机器人）
布局/默认值/修订号：greenhouses.map_data
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.company_scope import (DEFAULT_BASE_ID, can_access_company,
                                   company_filter, is_platform,
                                   resolve_company_id)
from app.core.database import get_db
from app.core.map_doc import DEFAULT_DEFAULTS, DEFAULT_LAYOUT, normalize_doc
from app.models.models import (Device, DeviceStatus, Greenhouse,
                               InspectionPoint, InspectionVisit,
                               RobotPointMapping, Telemetry, User)

router = APIRouter()

ENV_FIELDS = ["temperature", "humidity", "light_intensity", "co2_level"]
SOIL_FIELDS = [
    "soil_moisture", "soil_temperature", "soil_ec", "ph_value",
    "soil_nitrogen", "soil_phosphorus", "soil_potassium",
]
OBS_LIMIT = 500


class WorkbenchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    location: Optional[str] = Field(default=None, max_length=200)
    crop_type: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    structure: Optional[str] = Field(default="solar", max_length=40)
    covering: Optional[str] = Field(default="film", max_length=40)
    actual_area: Optional[float] = Field(default=None, ge=0)
    coord_unit: Optional[str] = Field(default="schematic", max_length=20)
    layout: Optional[Dict[str, Any]] = None
    defaults: Optional[Dict[str, Any]] = None


class WorkbenchProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    location: Optional[str] = Field(default=None, max_length=200)
    crop_type: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    structure: Optional[str] = Field(default=None, max_length=40)
    covering: Optional[str] = Field(default=None, max_length=40)
    actual_area: Optional[float] = Field(default=None, ge=0)
    layout: Optional[Dict[str, Any]] = None  # 局部合并进 doc["layout"]（如长宽/方向/已核实）
    revision: Optional[int] = Field(default=None, ge=0)


def _merge(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(base)
    if isinstance(override, dict):
        for k, v in override.items():
            if v is not None:
                out[k] = v
    return out


def _observation(t: Telemetry) -> Dict[str, Any]:
    values = {k: getattr(t, k, None) for k in ENV_FIELDS + SOIL_FIELDS}
    env_present = any(values[k] is not None for k in ENV_FIELDS)
    soil_valid = any(values[k] is not None for k in SOIL_FIELDS)
    return {
        "id": t.id,
        "captured_at": t.sampled_at or t.timestamp,
        "received_at": t.created_at,
        "point_name": t.greenhouse_point,
        "task_id": t.task_id,
        "values": values,
        "quality": {
            "environment_present": env_present,
            "soil_valid": soil_valid,
            "source": "robot" if (t.mapping_id or t.greenhouse_point) else "fixed",
            "soil_sample_count": t.sample_count,
            "time_quality": t.time_quality,
        },
        "sample_count": t.sample_count,
        "position": {"x": t.position_x, "y": t.position_y},
    }


def _binding_view(m: Optional[RobotPointMapping], point_name: str) -> Optional[Dict[str, Any]]:
    if m is None:
        return None
    return {
        "robot_id": m.robot_id,
        "nav_point_code": m.nav_point_code,
        "mapping_id": m.id,
        "point_name": point_name,
    }


async def _load_points(db: AsyncSession, gh: Greenhouse,
                       doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = (await db.execute(
        select(InspectionPoint).where(
            InspectionPoint.greenhouse_id == gh.id,
            InspectionPoint.retired_at.is_(None),
        ).order_by(InspectionPoint.created_at)
    )).scalars().all()
    maps = (await db.execute(
        select(RobotPointMapping).where(
            RobotPointMapping.greenhouse_id == gh.id,
            RobotPointMapping.effective_to.is_(None),
        )
    )).scalars().all()
    by_point: Dict[str, List[RobotPointMapping]] = {}
    for m in maps:
        by_point.setdefault(m.point_id, []).append(m)

    d = doc["defaults"]
    points: List[Dict[str, Any]] = []
    for p in rows:
        ms = by_point.get(p.id, [])
        primary = ms[0] if ms else None
        points.append({
            "id": p.id,
            "point_name": p.name,
            "zone": p.zone or "",
            "x": p.x,
            "y": p.y,
            "note": "",
            "sample_seconds": primary.sample_seconds if primary else d.get("sample_seconds"),
            "collect_soil": primary.collect_soil if primary else d.get("collect_soil"),
            "collect_environment": primary.collect_environment if primary else d.get("collect_environment"),
            "take_photo": primary.take_photo if primary else d.get("take_photo"),
            "enabled": primary.enabled if primary else d.get("enabled"),
            "binding": _binding_view(primary, p.name),
            "bindings": [_binding_view(m, p.name) for m in ms],
        })
    return points


async def _latest_by_point(db: AsyncSession, gh_id: str,
                           points: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = (await db.execute(
        select(Telemetry).where(Telemetry.greenhouse_id == gh_id)
        .order_by(Telemetry.timestamp.desc()).limit(1000)
    )).scalars().all()
    by_key: Dict[str, Telemetry] = {}
    for t in rows:
        for key in (t.mapping_id, t.greenhouse_point):
            if key and key not in by_key:
                by_key[key] = t
    latest: Dict[str, Any] = {}
    for p in points:
        binding = p.get("binding") or {}
        key = binding.get("mapping_id") or p.get("point_name")
        t = by_key.get(key) if key else None
        if t is not None:
            latest[p["id"]] = _observation(t)
    return latest


def _profile(gh: Greenhouse, doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": gh.name,
        "structure": doc.get("structure") or "solar",
        "covering": doc.get("covering") or "film",
        "location": gh.location,
        "crop_type": gh.crop_type,
        "actual_area": gh.area,
        "description": gh.description,
        "revision": int(doc.get("revision") or 0),
    }


async def _device_summary(db: AsyncSession, gh_id: str) -> Dict[str, int]:
    """每棚设备摘要：总数 + 在线数（2026-10-29：每棚规划至少一套监测设备，0 台要能在前端看见）。"""
    total = (await db.execute(
        select(func.count(Device.id)).where(Device.greenhouse_id == gh_id))).scalar() or 0
    online = (await db.execute(
        select(func.count(Device.id)).where(
            Device.greenhouse_id == gh_id,
            Device.status == DeviceStatus.ONLINE))).scalar() or 0
    return {"total": int(total), "online": int(online)}


async def _payload(db: AsyncSession, gh: Greenhouse) -> Dict[str, Any]:
    doc = normalize_doc(gh.map_data)
    points = await _load_points(db, gh, doc)
    return {
        "devices": await _device_summary(db, gh.id),
        "profile": _profile(gh, doc),
        "map": {
            "revision": int(doc.get("revision") or 0),
            "layout": doc["layout"],
            "defaults": doc["defaults"],
            "next_name": doc["next_name"],
            "points": points,
        },
        "latest": await _latest_by_point(db, gh.id, points),
    }


async def _get_scoped(db: AsyncSession, greenhouse_id: str, user: User) -> Greenhouse:
    gh = await db.get(Greenhouse, greenhouse_id)
    if not gh or not can_access_company(user, gh.company_id):
        raise HTTPException(status_code=404, detail="大棚不存在")
    return gh


@router.post("/greenhouses", status_code=status.HTTP_201_CREATED)
async def create_workbench_greenhouse(
    payload: WorkbenchCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_permission(user, "manage_greenhouses")
    raw = {
        "revision": 1,
        "layout": _merge(DEFAULT_LAYOUT, payload.layout),
        "defaults": _merge(DEFAULT_DEFAULTS, payload.defaults),
        "next_name": "A1",
        "points": [],
        "structure": payload.structure or "solar",
        "covering": payload.covering or "film",
    }
    gh = Greenhouse(
        name=payload.name,
        location=payload.location,
        crop_type=payload.crop_type,
        description=payload.description,
        area=payload.actual_area,
        coord_unit=payload.coord_unit or "schematic",
        company_id=resolve_company_id(user),
        base_id=DEFAULT_BASE_ID,
        map_data=normalize_doc(raw),
    )
    db.add(gh)
    await db.commit()
    await db.refresh(gh)
    return {"greenhouse_id": gh.id}


@router.get("/greenhouses/{greenhouse_id}")
async def get_workbench_greenhouse(
    greenhouse_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_permission(user, "view_data")
    gh = await _get_scoped(db, greenhouse_id, user)
    return await _payload(db, gh)


@router.put("/greenhouses/{greenhouse_id}/profile")
async def update_workbench_profile(
    greenhouse_id: str,
    payload: WorkbenchProfileUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_permission(user, "manage_greenhouses")
    gh = await _get_scoped(db, greenhouse_id, user)
    doc = normalize_doc(gh.map_data)

    if payload.revision is not None and int(payload.revision) != int(doc.get("revision") or 0):
        raise HTTPException(status_code=409, detail="档案已被他人修改，请重新加载后再保存")

    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="请求体为空，未提供任何要更新的字段")

    if "name" in data and data["name"] is not None:
        gh.name = data["name"]
    if "location" in data:
        gh.location = data["location"]
    if "crop_type" in data:
        gh.crop_type = data["crop_type"]
    if "description" in data:
        gh.description = data["description"]
    if "actual_area" in data:
        gh.area = data["actual_area"]
    if data.get("structure"):
        doc["structure"] = data["structure"]
    if data.get("covering"):
        doc["covering"] = data["covering"]
    if isinstance(data.get("layout"), dict):
        # 只合并传入的键（如 length_value/width_value/calibrated），不动其余布局字段
        doc["layout"] = _merge(doc.get("layout") or DEFAULT_LAYOUT, data["layout"])

    doc["revision"] = int(doc.get("revision") or 0) + 1
    gh.map_data = doc
    await db.commit()
    await db.refresh(gh)
    return {"success": True, "revision": doc["revision"]}


@router.get("/greenhouses")
async def list_workbench_greenhouses(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """工作台列表：档案 + 设备摘要 + 点位数（2026-10-29，前端列表卡片用）。"""
    check_permission(user, "view_data")
    # 2026-10-29 反馈：删除后刷新列表不应再出现 -> 列表只返回未停用的大棚
    stmt = select(Greenhouse).where(Greenhouse.is_active == True).order_by(Greenhouse.created_at.desc())  # noqa: E712
    if not is_platform(user):
        stmt = stmt.where(company_filter(user, Greenhouse.company_id))
    ghs = (await db.execute(stmt)).scalars().all()
    out = []
    for gh in ghs:
        item = {
            "id": gh.id, "name": gh.name, "location": gh.location,
            "crop_type": gh.crop_type, "area": gh.area,
            "is_active": gh.is_active,
            "devices": await _device_summary(db, gh.id),
        }
        doc = normalize_doc(gh.map_data)
        item["point_count"] = len(doc.get("points") or [])
        out.append(item)
    return out


@router.delete("/greenhouses/{greenhouse_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workbench_greenhouse(
    greenhouse_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """工作台删除大棚：软删（is_active=False），语义与 /greenhouses 旧接口一致。

    不做物理删除：telemetry / inspection_points / 巡检记录仍挂在棚上，可追溯。
    """
    check_permission(user, "manage_greenhouses")
    gh = await _get_scoped(db, greenhouse_id, user)
    gh.is_active = False
    await db.commit()


@router.get("/greenhouses/{greenhouse_id}/points/{point_id}")
async def get_workbench_point(
    greenhouse_id: str,
    point_id: str,
    days: int = Query(default=7, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_permission(user, "view_data")
    gh = await _get_scoped(db, greenhouse_id, user)

    point = (await db.execute(
        select(InspectionPoint).where(
            InspectionPoint.greenhouse_id == gh.id,
            or_(InspectionPoint.id == point_id, InspectionPoint.name == point_id),
        )
    )).scalars().first()
    if point is None:
        raise HTTPException(status_code=404, detail=f"点位不存在：{point_id}")

    mappings = (await db.execute(
        select(RobotPointMapping).where(
            RobotPointMapping.point_id == point.id,
            RobotPointMapping.effective_to.is_(None),
        )
    )).scalars().all()
    primary = mappings[0] if mappings else None

    since = datetime.now(timezone.utc) - timedelta(days=days)
    conds = [Telemetry.greenhouse_id == gh.id, Telemetry.timestamp >= since]
    if primary is not None:
        conds.append(or_(
            Telemetry.mapping_id == primary.id,
            Telemetry.greenhouse_point == primary.nav_point_code,
            Telemetry.greenhouse_point == point.name,
        ))
    else:
        conds.append(Telemetry.greenhouse_point == point.name)

    total = (await db.execute(select(func.count(Telemetry.id)).where(*conds))).scalar() or 0
    rows = (await db.execute(
        select(Telemetry).where(*conds).order_by(Telemetry.timestamp.desc()).limit(OBS_LIMIT)
    )).scalars().all()
    observations = [_observation(t) for t in rows]

    seen = set()
    position_history: List[Dict[str, Any]] = []
    for t in rows:
        if t.position_x is None and t.position_y is None:
            continue
        key = (t.position_x, t.position_y)
        if key in seen:
            continue
        seen.add(key)
        position_history.append({"x": t.position_x, "y": t.position_y})
    if not position_history:
        position_history = [{"x": point.x, "y": point.y}]

    visits: List[Dict[str, Any]] = []
    if primary is not None:
        vrows = (await db.execute(
            select(InspectionVisit).where(
                InspectionVisit.greenhouse_id == gh.id,
                InspectionVisit.nav_point_code == primary.nav_point_code,
            ).order_by(InspectionVisit.arrived_at.desc()).limit(100)
        )).scalars().all()
        visits = [{"id": v.id, "arrived_at": v.arrived_at,
                   "status": v.status, "error_code": v.error_code} for v in vrows]

    return {
        "point": {
            "id": point.id,
            "point_name": point.name,
            "zone": point.zone or "",
            "x": point.x,
            "y": point.y,
            "note": "",
            "sample_seconds": primary.sample_seconds if primary else None,
            "collect_soil": primary.collect_soil if primary else None,
            "collect_environment": primary.collect_environment if primary else None,
            "take_photo": primary.take_photo if primary else None,
            "enabled": primary.enabled if primary else None,
            "binding": _binding_view(primary, point.name),
            "bindings": [_binding_view(m, point.name) for m in mappings],
        },
        "observations": observations,
        "total": int(total),
        "truncated": total > OBS_LIMIT,
        "position_history": position_history,
        "photos": [],
        "photo_total": 0,
        "visits": visits,
    }
