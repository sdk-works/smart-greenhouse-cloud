"""点位与机器人映射服务。

- inspection_points：稳定点位（独立于机器人）
- robot_point_mappings：绑定事实来源（一点多机器人）
- robot_point_configs：发布用的单向投影（与 mapping 同 id，保证巡检 mapping_id 稳定）
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (Greenhouse, InspectionPoint, RobotPointConfig,
                               RobotPointMapping)
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


def binding_view(m: Optional[RobotPointMapping], point_name: str) -> Optional[Dict[str, Any]]:
    if m is None:
        return None
    return {
        "robot_id": m.robot_id,
        "nav_point_code": m.nav_point_code,
        "mapping_id": m.id,
        "point_name": point_name,
    }


async def load_points(db: AsyncSession, gh: Greenhouse, doc: Dict[str, Any]) -> List[Dict[str, Any]]:
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
    out: List[Dict[str, Any]] = []
    for p in rows:
        ms = by_point.get(p.id, [])
        primary = ms[0] if ms else None
        out.append({
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
            "binding": binding_view(primary, p.name),
            "bindings": [binding_view(m, p.name) for m in ms],
        })
    return out


async def apply_points(db: AsyncSession, gh: Greenhouse,
                       doc: Dict[str, Any], incoming: List[Dict[str, Any]]) -> None:
    """upsert 提交的点位；未提交的点位置为停用（不物理删除，保留历史）。"""
    existing = (await db.execute(
        select(InspectionPoint).where(InspectionPoint.greenhouse_id == gh.id)
    )).scalars().all()
    by_id = {p.id: p for p in existing}

    kept = set()
    for p in incoming or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or _uuid())
        kept.add(pid)
        row = by_id.get(pid)
        if row is None:
            row = InspectionPoint(id=pid, greenhouse_id=gh.id, code=pid, name="未命名")
            db.add(row)
        row.name = (p.get("point_name") or row.name or "未命名")[:100]
        row.zone = (p.get("zone") or None)
        row.x = p.get("x")
        row.y = p.get("y")
        row.coord_unit = gh.coord_unit or "schematic"
        row.map_edit_revision = int(doc.get("revision") or 0)
        row.retired_at = None

    now = datetime.now(timezone.utc)
    for pid, row in by_id.items():
        if pid not in kept and row.retired_at is None:
            row.retired_at = now
    await db.flush()


async def bind(db: AsyncSession, gh: Greenhouse, point: InspectionPoint,
               robot_id: str, robot_map_version: Optional[str],
               nav_point_code: str, defaults: Dict[str, Any]) -> None:
    """建立/更新 点位 ↔ 机器人导航编号 的映射（同 id 同步到发布投影表）。"""
    code = nav_point_code.strip()

    cfg = (await db.execute(
        select(RobotPointConfig).where(
            RobotPointConfig.robot_id == robot_id,
            RobotPointConfig.nav_point_code == code,
        )
    )).scalars().first()
    if cfg is not None and cfg.point_id and cfg.point_id != point.id:
        raise HTTPException(409, "该导航编号已绑定到其它点位，请先解除原绑定")

    if cfg is None:
        cfg = RobotPointConfig(id=_uuid(), robot_id=robot_id, nav_point_code=code,
                               greenhouse_id=gh.id, point_name=point.name,
                               point_id=point.id)
        db.add(cfg)

    mapping = (await db.execute(
        select(RobotPointMapping).where(
            RobotPointMapping.robot_id == robot_id,
            RobotPointMapping.nav_point_code == code,
            RobotPointMapping.effective_to.is_(None),
        )
    )).scalars().first()
    if mapping is None:
        mapping = RobotPointMapping(id=cfg.id, robot_id=robot_id, nav_point_code=code,
                                    point_id=point.id, greenhouse_id=gh.id,
                                    sample_seconds=defaults.get("sample_seconds", 10))
        db.add(mapping)
    mapping.point_id = point.id
    mapping.greenhouse_id = gh.id
    mapping.robot_map_version = robot_map_version
    mapping.sample_seconds = int(defaults.get("sample_seconds") or 10)
    mapping.collect_soil = bool(defaults.get("collect_soil", True))
    mapping.collect_environment = bool(defaults.get("collect_environment", True))
    mapping.take_photo = bool(defaults.get("take_photo", True))
    mapping.enabled = bool(defaults.get("enabled", True))
    mapping.effective_to = None
    mapping.status = "active"

    cfg.greenhouse_id = gh.id
    cfg.point_id = point.id
    cfg.point_name = point.name
    cfg.robot_map_version = robot_map_version
    cfg.sample_seconds = mapping.sample_seconds
    cfg.collect_soil = mapping.collect_soil
    cfg.collect_environment = mapping.collect_environment
    cfg.take_photo = mapping.take_photo
    cfg.enabled = mapping.enabled
    await db.flush()


async def unbind(db: AsyncSession, gh: Greenhouse, point: InspectionPoint,
                 robot_id: str) -> None:
    now = datetime.now(timezone.utc)
    mappings = (await db.execute(
        select(RobotPointMapping).where(
            RobotPointMapping.point_id == point.id,
            RobotPointMapping.robot_id == robot_id,
            RobotPointMapping.effective_to.is_(None),
        )
    )).scalars().all()
    for m in mappings:
        m.effective_to = now
        m.status = "retired"

    cfgs = (await db.execute(
        select(RobotPointConfig).where(
            RobotPointConfig.robot_id == robot_id,
            or_(RobotPointConfig.point_id == point.id,
                RobotPointConfig.point_name == point.name),
        )
    )).scalars().all()
    for c in cfgs:
        await db.delete(c)
    await db.flush()
