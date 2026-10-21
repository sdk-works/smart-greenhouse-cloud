"""
workbench.py — 大棚工作台 API（按前端真实契约实现）

前端契约来源（编译产物 source map 中的原始源码）：
  - pages/GreenhouseWorkbench.js  → GET /workbench/greenhouses/{id}
      { profile:{...}, map:{revision,layout,points,defaults,next_name}, latest:{[pointId]:obs} }
  - 同上                          → POST /workbench/greenhouses   → { greenhouse_id }
  - 同上                          → PUT  /workbench/greenhouses/{id}/profile  ({...,revision})
  - pages/PointDetail.js          → GET  /workbench/greenhouses/{id}/points/{pointId}?days=N
      { point:{...}, observations:[{values,quality,captured_at,...}], total, truncated,
        position_history, photos, photo_total, visits }

地图文档（layout/points/defaults/revision）统一由 app.core.map_doc 读写，
存储于 greenhouses.map_data(JSON)。
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.database import get_db
from app.core.map_doc import (
    DEFAULT_DEFAULTS,
    DEFAULT_LAYOUT,
    MAX_POINTS,
    apply_document,
    bump,
    new_point_id,
    normalize_doc,
    to_public,
)
from app.models.models import Device, Greenhouse, InspectionVisit, Telemetry, User

router = APIRouter()

ENV_FIELDS = ["temperature", "humidity", "light_intensity", "co2_level"]
SOIL_FIELDS = [
    "soil_moisture", "soil_temperature", "soil_ec", "ph_value",
    "soil_nitrogen", "soil_phosphorus", "soil_potassium",
]
OBS_LIMIT = 500


# ── Pydantic ──────────────────────────────────────────────────
class WorkbenchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    location: Optional[str] = Field(default=None, max_length=200)
    crop_type: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    structure: Optional[str] = Field(default="solar", max_length=40)
    covering: Optional[str] = Field(default="film", max_length=40)
    actual_area: Optional[float] = Field(default=None, ge=0)
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
    revision: Optional[int] = Field(default=None, ge=0)


# ── 辅助 ──────────────────────────────────────────────────────
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


async def _workbench_payload(db: AsyncSession, gh: Greenhouse) -> Dict[str, Any]:
    doc = normalize_doc(gh.map_data)
    return {
        "profile": _profile(gh, doc),
        "map": to_public(doc),
        "latest": await _latest_by_point(db, gh.id, doc["points"]),
    }


async def _get_or_404(db: AsyncSession, greenhouse_id: str) -> Greenhouse:
    gh = await db.get(Greenhouse, greenhouse_id)
    if not gh:
        raise HTTPException(status_code=404, detail="大棚不存在")
    return gh


def _find_point(doc: Dict[str, Any], point_id: str) -> Optional[Dict[str, Any]]:
    needle = (point_id or "").strip()
    for p in doc["points"]:
        if p.get("id") == needle:
            return p
    for p in doc["points"]:
        if str(p.get("point_name")) == needle:
            return p
    return None


# ── 接口 ──────────────────────────────────────────────────────
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
        map_data=normalize_doc(raw),
    )
    db.add(gh)
    await db.commit()
    await db.refresh(gh)
    # 前端创建后读取 r.data.greenhouse_id 跳转工作台
    return {"greenhouse_id": gh.id}


@router.get("/greenhouses/{greenhouse_id}")
async def get_workbench_greenhouse(
    greenhouse_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_permission(user, "view_data")
    gh = await _get_or_404(db, greenhouse_id)
    return await _workbench_payload(db, gh)


@router.put("/greenhouses/{greenhouse_id}/profile")
async def update_workbench_profile(
    greenhouse_id: str,
    payload: WorkbenchProfileUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_permission(user, "manage_greenhouses")
    gh = await _get_or_404(db, greenhouse_id)
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

    doc = bump(doc)
    gh.map_data = doc
    await db.commit()
    await db.refresh(gh)
    return {"success": True, "revision": doc["revision"]}


@router.get("/greenhouses/{greenhouse_id}/points/{point_id}")
async def get_workbench_point(
    greenhouse_id: str,
    point_id: str,
    days: int = Query(default=7, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_permission(user, "view_data")
    gh = await _get_or_404(db, greenhouse_id)
    doc = normalize_doc(gh.map_data)

    point = _find_point(doc, point_id)
    if point is None:
        raise HTTPException(status_code=404, detail=f"点位不存在：{point_id}")

    binding = point.get("binding") or {}
    since = datetime.now(timezone.utc) - timedelta(days=days)

    conds = [Telemetry.greenhouse_id == gh.id, Telemetry.timestamp >= since]
    nav_code = binding.get("nav_point_code")
    if nav_code:
        conds.append(or_(
            Telemetry.mapping_id == binding.get("mapping_id"),
            Telemetry.greenhouse_point == nav_code,
            Telemetry.greenhouse_point == point.get("point_name"),
        ))
    else:
        conds.append(Telemetry.greenhouse_point == point.get("point_name"))

    total = (await db.execute(
        select(func.count(Telemetry.id)).where(*conds)
    )).scalar() or 0

    rows = (await db.execute(
        select(Telemetry).where(*conds)
        .order_by(Telemetry.timestamp.desc()).limit(OBS_LIMIT)
    )).scalars().all()

    observations = [_observation(t) for t in rows]

    # 位置历史（用于提示"坐标曾调整"）：按出现顺序去重
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
        position_history = [{"x": point.get("x"), "y": point.get("y")}]

    visits: List[Dict[str, Any]] = []
    if nav_code:
        vrows = (await db.execute(
            select(InspectionVisit).where(
                InspectionVisit.greenhouse_id == gh.id,
                InspectionVisit.nav_point_code == nav_code,
            ).order_by(InspectionVisit.arrived_at.desc()).limit(100)
        )).scalars().all()
        visits = [{
            "id": v.id,
            "arrived_at": v.arrived_at,
            "status": v.status,
            "error_code": v.error_code,
        } for v in vrows]

    return {
        "point": point,
        "observations": observations,
        "total": int(total),
        "truncated": total > OBS_LIMIT,
        "position_history": position_history,
        "photos": [],
        "photo_total": 0,
        "visits": visits,
    }
