import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.database import get_db
from app.models.models import (Greenhouse, NavPointDiscovery, RobotPointConfig,
                               RobotPointConfigRelease, User)
from app.mqtt.mqtt_service import mqtt_service
from app.api.point_maps import router as map_router, guard_legacy, lock_maps

router = APIRouter()


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


class ResetDiscoveryIn(BaseModel):
    confirmation: str = Field(pattern="^RESET$")


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
    await guard_legacy(db, robot_id, nav_point_code)
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
    await guard_legacy(db, robot_id, nav_point_code)
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


@router.delete("/robots/{robot_id}/discoveries/{nav_point_code}")
async def delete_discovery(robot_id: str, nav_point_code: str,
                           db: AsyncSession = Depends(get_db),
                           _: User = Depends(get_current_user)):
    """Remove one code from the current discovery workspace.

    Published release snapshots and telemetry are intentionally retained.
    """
    await guard_legacy(db, robot_id, nav_point_code)
    await db.execute(delete(RobotPointConfig).where(
        RobotPointConfig.robot_id == robot_id,
        RobotPointConfig.nav_point_code == nav_point_code))
    result = await db.execute(delete(NavPointDiscovery).where(
        NavPointDiscovery.robot_id == robot_id,
        NavPointDiscovery.nav_point_code == nav_point_code))
    await db.commit()
    if not result.rowcount:
        raise HTTPException(404, "当前工作区中不存在该导航点")
    return {"success": True, "nav_point_code": nav_point_code}


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
    await lock_maps(db)
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


@router.post("/robots/{robot_id}/reset-discovery")
async def reset_discovery(robot_id: str, request: ResetDiscoveryIn,
                          db: AsyncSession = Depends(get_db),
                          _: User = Depends(get_current_user)):
    """Start a clean remapping round and publish an empty discovery config.

    Old releases/telemetry remain available for audit.  The active discovery
    and assignment workspace is cleared only after MQTT accepts the new config.
    """
    await guard_legacy(db, robot_id)
    current = (await db.execute(select(func.max(
        RobotPointConfigRelease.config_version)).where(
        RobotPointConfigRelease.robot_id == robot_id))).scalar() or 0
    version = int(current) + 1
    core = {"type": "point_config", "schema_version": "1.0",
            "robot_id": robot_id, "config_version": version,
            "mode": "discovery", "points": [],
            "issued_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    digest = hashlib.sha256(json.dumps(core, ensure_ascii=False,
                            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    payload = {**core, "config_sha256": digest}
    release = RobotPointConfigRelease(
        robot_id=robot_id, config_version=version, mode="discovery",
        config_sha256=digest, payload=payload, sync_status="pending")
    db.add(release)
    await db.commit()
    if not await mqtt_service.publish_config(robot_id, payload):
        release.sync_status = "publish_failed"
        await db.commit()
        raise HTTPException(503, "MQTT配置发布失败，当前点位未清空")
    old_discoveries = (await db.execute(select(func.count()).select_from(
        NavPointDiscovery).where(NavPointDiscovery.robot_id == robot_id))).scalar() or 0
    old_assignments = (await db.execute(select(func.count()).select_from(
        RobotPointConfig).where(RobotPointConfig.robot_id == robot_id))).scalar() or 0
    await db.execute(delete(RobotPointConfig).where(RobotPointConfig.robot_id == robot_id))
    await db.execute(delete(NavPointDiscovery).where(NavPointDiscovery.robot_id == robot_id))
    await db.commit()
    return {"success": True, "config_version": version, "mode": "discovery",
            "point_count": 0, "cleared_discoveries": old_discoveries,
            "cleared_assignments": old_assignments, "config_sha256": digest}


router.include_router(map_router)
