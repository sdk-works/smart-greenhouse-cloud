import hashlib
import json
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.database import get_db
from app.models.models import (Device, Greenhouse, InspectionVisit, NavPointDiscovery,
                               RobotPointConfig, RobotPointConfigRelease, Telemetry, User)
from app.mqtt.mqtt_service import mqtt_service

router = APIRouter()

MAX_MAP_WAYPOINTS = 500


class AssignmentIn(BaseModel):
    greenhouse_id: str
    point_name: str = Field(min_length=1, max_length=100)
    sample_seconds: int = Field(default=10, ge=1, le=120)
    collect_soil: bool = True
    collect_environment: bool = True
    take_photo: bool = True
    enabled: bool = True
    note: Optional[str] = Field(default=None, max_length=500)


class PublishIn(BaseModel):
    mode: str = Field(pattern="^(discovery|operational)$")


class MapWaypointIn(BaseModel):
    """大棚地图点位。坐标/布局继续存在 greenhouses.map_data(JSON)，不加列。"""
    id: Optional[int] = None
    code: Optional[str] = Field(default=None, max_length=64)
    label: Optional[str] = Field(default=None, max_length=100)
    x: Optional[float] = None
    y: Optional[float] = None
    description: Optional[str] = None
    actions: List[str] = Field(default_factory=list)


class MapLayoutIn(BaseModel):
    width: Optional[float] = Field(default=None, gt=0)
    height: Optional[float] = Field(default=None, gt=0)
    waypoints: List[MapWaypointIn] = Field(default_factory=list)


class BindingIn(BaseModel):
    robot_id: str = Field(min_length=1, max_length=100)
    nav_point_code: str = Field(min_length=1, max_length=64)
    point_name: Optional[str] = Field(default=None, max_length=100)
    enabled: bool = True


def _map_data(gh: Greenhouse) -> dict:
    md = gh.map_data
    return dict(md) if isinstance(md, dict) else {}


def _config_dict(row: RobotPointConfig) -> dict:
    return {"config_id": row.id, "robot_id": row.robot_id,
            "nav_point_code": row.nav_point_code, "greenhouse_id": row.greenhouse_id,
            "point_name": row.point_name, "sample_seconds": row.sample_seconds,
            "collect_soil": row.collect_soil, "collect_environment": row.collect_environment,
            "take_photo": row.take_photo, "enabled": row.enabled, "note": row.note}


def _discovery_dict(row):
    return {"id": row.id, "robot_id": row.robot_id,
            "nav_point_code": row.nav_point_code, "status": row.status,
            "first_seen_at": row.first_seen_at, "last_seen_at": row.last_seen_at,
            "seen_count": row.seen_count}


@router.get("/robots/{robot_id}/discoveries")
async def discoveries(robot_id: str, db: AsyncSession = Depends(get_db),
                      _: User = Depends(get_current_user)):
    rows = (await db.execute(select(NavPointDiscovery).where(
        NavPointDiscovery.robot_id == robot_id).order_by(
        NavPointDiscovery.last_seen_at.desc()))).scalars().all()
    configs = (await db.execute(select(RobotPointConfig).where(
        RobotPointConfig.robot_id == robot_id))).scalars().all()
    by_code = {x.nav_point_code: x for x in configs}
    result = []
    for row in rows:
        item = _discovery_dict(row)
        cfg = by_code.get(row.nav_point_code)
        item["assignment"] = None if not cfg else {
            "greenhouse_id": cfg.greenhouse_id, "point_name": cfg.point_name,
            "sample_seconds": cfg.sample_seconds,
            "collect_soil": cfg.collect_soil,
            "collect_environment": cfg.collect_environment,
            "take_photo": cfg.take_photo, "enabled": cfg.enabled, "note": cfg.note}
        result.append(item)
    return result


@router.put("/robots/{robot_id}/points/{nav_point_code}")
async def assign(robot_id: str, nav_point_code: str, payload: AssignmentIn,
                 db: AsyncSession = Depends(get_db),
                 _: User = Depends(get_current_user)):
    if not (await db.get(Greenhouse, payload.greenhouse_id)):
        raise HTTPException(404, "大棚不存在")
    row = (await db.execute(select(RobotPointConfig).where(
        RobotPointConfig.robot_id == robot_id,
        RobotPointConfig.nav_point_code == nav_point_code))).scalar_one_or_none()
    if not row:
        row = RobotPointConfig(robot_id=robot_id, nav_point_code=nav_point_code)
        db.add(row)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    disc = (await db.execute(select(NavPointDiscovery).where(
        NavPointDiscovery.robot_id == robot_id,
        NavPointDiscovery.nav_point_code == nav_point_code))).scalar_one_or_none()
    if disc:
        disc.status = "assigned"
    await db.commit()
    return {"success": True, "nav_point_code": nav_point_code}


@router.delete("/robots/{robot_id}/points/{nav_point_code}")
async def unassign(robot_id: str, nav_point_code: str,
                   db: AsyncSession = Depends(get_db),
                   _: User = Depends(get_current_user)):
    row = (await db.execute(select(RobotPointConfig).where(
        RobotPointConfig.robot_id == robot_id,
        RobotPointConfig.nav_point_code == nav_point_code))).scalar_one_or_none()
    if row:
        await db.delete(row)
    disc = (await db.execute(select(NavPointDiscovery).where(
        NavPointDiscovery.robot_id == robot_id,
        NavPointDiscovery.nav_point_code == nav_point_code))).scalar_one_or_none()
    if disc:
        disc.status = "unassigned"
    await db.commit()
    return {"success": True}


@router.get("/robots/{robot_id}/status")
async def status(robot_id: str, db: AsyncSession = Depends(get_db),
                 _: User = Depends(get_current_user)):
    latest = (await db.execute(select(RobotPointConfigRelease).where(
        RobotPointConfigRelease.robot_id == robot_id).order_by(
        RobotPointConfigRelease.config_version.desc()).limit(1))).scalar_one_or_none()
    return {"robot_id": robot_id, "config_version": latest.config_version if latest else 0,
            "mode": latest.mode if latest else "discovery",
            "sync_status": latest.sync_status if latest else "not_published",
            "published_at": latest.published_at if latest else None,
            "applied_at": latest.applied_at if latest else None,
            "sync_detail": latest.sync_detail if latest else None}


@router.post("/robots/{robot_id}/publish")
async def publish(robot_id: str, request: PublishIn,
                  db: AsyncSession = Depends(get_db),
                  _: User = Depends(get_current_user)):
    rows = (await db.execute(select(RobotPointConfig).where(
        RobotPointConfig.robot_id == robot_id).order_by(
        RobotPointConfig.nav_point_code))).scalars().all()
    if request.mode == "operational" and not rows:
        raise HTTPException(400, "正式模式至少需要配置一个采集点")
    current = (await db.execute(select(func.max(
        RobotPointConfigRelease.config_version)).where(
        RobotPointConfigRelease.robot_id == robot_id))).scalar() or 0
    version = int(current) + 1
    points = [{"nav_point_code": r.nav_point_code,
               "greenhouse_id": r.greenhouse_id, "point_name": r.point_name,
               "mapping_id": r.id, "sample_seconds": r.sample_seconds,
               "collect_soil": r.collect_soil,
               "collect_environment": r.collect_environment,
               "take_photo": r.take_photo, "enabled": r.enabled,
               "note": r.note} for r in rows]
    core = {"type": "point_config", "schema_version": "1.0",
            "robot_id": robot_id, "config_version": version,
            "mode": request.mode, "points": points,
            "issued_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    digest = hashlib.sha256(json.dumps(core, ensure_ascii=False,
                            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    payload = {**core, "config_sha256": digest}
    release = RobotPointConfigRelease(robot_id=robot_id, config_version=version,
        mode=request.mode, config_sha256=digest, payload=payload, sync_status="pending")
    db.add(release)
    await db.commit()
    if not await mqtt_service.publish_config(robot_id, payload):
        release.sync_status = "publish_failed"
        await db.commit()
        raise HTTPException(503, "MQTT配置发布失败")
    return {"success": True, "config_version": version,
            "mode": request.mode, "point_count": len(points),
            "config_sha256": digest}


# ── 大棚地图 / 点位绑定 / 地图监控 ───────────────────────────
@router.get("/maps/{greenhouse_id}")
async def get_map(greenhouse_id: str, db: AsyncSession = Depends(get_db),
                  _: User = Depends(get_current_user)):
    check_permission(_, "view_point_mapping")
    gh = await db.get(Greenhouse, greenhouse_id)
    if not gh:
        raise HTTPException(404, "大棚不存在")
    md = _map_data(gh)
    configs = (await db.execute(select(RobotPointConfig).where(
        RobotPointConfig.greenhouse_id == gh.id).order_by(
        RobotPointConfig.nav_point_code))).scalars().all()
    return {"greenhouse_id": gh.id, "greenhouse_name": gh.name,
            "width": md.get("width"), "height": md.get("height"),
            "waypoints": md.get("waypoints", []), "map_data": md,
            "bindings": [_config_dict(c) for c in configs],
            "updated_at": gh.updated_at}


@router.put("/maps/{greenhouse_id}")
async def put_map(greenhouse_id: str, payload: MapLayoutIn,
                  db: AsyncSession = Depends(get_db),
                  _: User = Depends(get_current_user)):
    check_permission(_, "edit_point_mapping")
    gh = await db.get(Greenhouse, greenhouse_id)
    if not gh:
        raise HTTPException(404, "大棚不存在")
    if len(payload.waypoints) > MAX_MAP_WAYPOINTS:
        raise HTTPException(400, f"点位数量超过上限 {MAX_MAP_WAYPOINTS}")

    md = _map_data(gh)
    existing = md.get("waypoints") or []
    next_id = max((w.get("id", 0) for w in existing if isinstance(w, dict)), default=0)

    waypoints = []
    for item in payload.waypoints:
        wp = item.model_dump(exclude_none=True)
        if not wp.get("id"):
            next_id += 1
            wp["id"] = next_id
        waypoints.append(wp)

    md["waypoints"] = waypoints
    if payload.width is not None:
        md["width"] = payload.width
    if payload.height is not None:
        md["height"] = payload.height
    gh.map_data = md
    await db.commit()
    await db.refresh(gh)
    return {"success": True, "greenhouse_id": gh.id,
            "width": md.get("width"), "height": md.get("height"),
            "waypoints": waypoints, "point_count": len(waypoints)}


@router.put("/maps/{greenhouse_id}/points/{point_code}/binding")
async def bind_point(greenhouse_id: str, point_code: str, payload: BindingIn,
                     db: AsyncSession = Depends(get_db),
                     _: User = Depends(get_current_user)):
    """把大棚点位 point_code 绑定到机器人的导航点 nav_point_code。"""
    check_permission(_, "edit_point_mapping")
    gh = await db.get(Greenhouse, greenhouse_id)
    if not gh:
        raise HTTPException(404, "大棚不存在")
    code = point_code.strip()
    if not code:
        raise HTTPException(400, "点位 code 不能为空")

    row = (await db.execute(select(RobotPointConfig).where(
        RobotPointConfig.robot_id == payload.robot_id,
        RobotPointConfig.nav_point_code == payload.nav_point_code))).scalar_one_or_none()
    if row and row.greenhouse_id != gh.id:
        raise HTTPException(409, "该导航点已绑定到其它大棚的点位，请先解除原绑定")
    if not row:
        row = RobotPointConfig(robot_id=payload.robot_id,
                               nav_point_code=payload.nav_point_code)
        db.add(row)
    row.greenhouse_id = gh.id
    row.point_name = payload.point_name or code
    row.enabled = payload.enabled

    disc = (await db.execute(select(NavPointDiscovery).where(
        NavPointDiscovery.robot_id == payload.robot_id,
        NavPointDiscovery.nav_point_code == payload.nav_point_code))).scalar_one_or_none()
    if disc:
        disc.status = "assigned"
    await db.commit()
    await db.refresh(row)
    return {"success": True, **_config_dict(row)}


@router.get("/map-monitor/{robot_id}")
async def map_monitor(robot_id: str, limit: int = Query(default=20, ge=1, le=100),
                      db: AsyncSession = Depends(get_db),
                      _: User = Depends(get_current_user)):
    """地图监控：机器人配置状态 + 绑定关系 + 最新位置 + 近期到点记录（只读）。"""
    check_permission(_, "view_point_mapping")
    latest_release = (await db.execute(select(RobotPointConfigRelease).where(
        RobotPointConfigRelease.robot_id == robot_id).order_by(
        RobotPointConfigRelease.config_version.desc()).limit(1))).scalar_one_or_none()

    configs = (await db.execute(select(RobotPointConfig).where(
        RobotPointConfig.robot_id == robot_id).order_by(
        RobotPointConfig.nav_point_code))).scalars().all()

    gh_names = {}
    for c in configs:
        if c.greenhouse_id not in gh_names:
            g = await db.get(Greenhouse, c.greenhouse_id)
            gh_names[c.greenhouse_id] = g.name if g else None
    bindings = [dict(_config_dict(c), greenhouse_name=gh_names.get(c.greenhouse_id))
                for c in configs]

    device = (await db.execute(select(Device).where(
        Device.device_id == robot_id))).scalar_one_or_none()
    latest_position = None
    if device:
        t = (await db.execute(select(Telemetry).where(
            Telemetry.device_id == device.id).order_by(
            Telemetry.timestamp.desc()).limit(1))).scalar_one_or_none()
        if t:
            latest_position = {"position_x": t.position_x, "position_y": t.position_y,
                               "at": t.timestamp, "sampled_at": t.sampled_at,
                               "time_quality": t.time_quality,
                               "chassis_point": t.chassis_point,
                               "greenhouse_point": t.greenhouse_point,
                               "greenhouse_id": t.greenhouse_id}

    visits = (await db.execute(select(InspectionVisit).where(
        InspectionVisit.robot_id == robot_id).order_by(
        InspectionVisit.arrived_at.desc()).limit(limit))).scalars().all()

    return {"robot_id": robot_id,
            "device_registered": device is not None,
            "config": {"config_version": latest_release.config_version if latest_release else 0,
                       "mode": latest_release.mode if latest_release else "discovery",
                       "sync_status": latest_release.sync_status if latest_release else "not_published",
                       "published_at": latest_release.published_at if latest_release else None,
                       "applied_at": latest_release.applied_at if latest_release else None},
            "bindings": bindings,
            "latest_position": latest_position,
            "recent_visits": [{"id": v.id, "batch_id": v.batch_id,
                               "nav_point_code": v.nav_point_code,
                               "greenhouse_id": v.greenhouse_id,
                               "point_name": v.point_name, "status": v.status,
                               "arrived_at": v.arrived_at, "sampled_at": v.sampled_at,
                               "telemetry_id": v.telemetry_id} for v in visits]}


@router.post("/robots/{robot_id}/reset-discovery")
async def reset_discovery(robot_id: str,
                          purge_configs: bool = Query(default=False),
                          db: AsyncSession = Depends(get_db),
                          _: User = Depends(get_current_user)):
    """清空该机器人的点位发现记录，重新进入发现阶段。"""
    check_permission(_, "manage_robots")
    deleted_disc = (await db.execute(
        delete(NavPointDiscovery).where(
            NavPointDiscovery.robot_id == robot_id))).rowcount or 0
    deleted_cfg = 0
    if purge_configs:
        deleted_cfg = (await db.execute(
            delete(RobotPointConfig).where(
                RobotPointConfig.robot_id == robot_id))).rowcount or 0
    await db.commit()
    return {"success": True, "robot_id": robot_id,
            "deleted_discoveries": int(deleted_disc),
            "deleted_configs": int(deleted_cfg),
            "purge_configs": purge_configs}
