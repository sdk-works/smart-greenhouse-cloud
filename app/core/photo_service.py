"""照片点位归属解析（M2 机器人闭环）。

原则：**能确定才写，解析不出留 NULL，不猜。**

优先级：
  1) robot_point_mappings：(robot_id, nav_point_code) → point_id
  2) robot_point_mappings：(greenhouse_id, nav_point_code) → point_id（机器人未知时退化）
  3) inspection_points：greenhouse_id + name 精确匹配上报的 greenhouse_point
  解析不出 → 返回 None（调用方要保持 point_id=NULL、attribution_status='pending'）
"""
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Greenhouse, InspectionEvidence, InspectionPoint, MapVersion, RobotPointMapping,
)

ATTRIBUTION_RESOLVED = "resolved"
ATTRIBUTION_PENDING = "pending"


async def resolve_point(
    db: AsyncSession,
    *,
    robot_id: Optional[str] = None,
    nav_point_code: Optional[str] = None,
    greenhouse_id: Optional[str] = None,
    point_name: Optional[str] = None,
):
    """按优先级解析巡检点位；无法确定返回 None。"""
    code = (nav_point_code or "").strip()

    if code:
        q = select(RobotPointMapping).where(
            RobotPointMapping.nav_point_code == code,
            RobotPointMapping.status == "active",
            RobotPointMapping.enabled == True,
        )
        if robot_id:
            rows = (await db.execute(q.where(
                RobotPointMapping.robot_id == robot_id))).scalars().all()
        else:
            rows = []
        if not rows and greenhouse_id:
            rows = (await db.execute(q.where(
                RobotPointMapping.greenhouse_id == greenhouse_id))).scalars().all()
        for m in rows:
            pt = await db.get(InspectionPoint, m.point_id)
            if pt is not None:
                return pt

    name = (point_name or "").strip()
    if name and greenhouse_id:
        pt = (await db.execute(select(InspectionPoint).where(
            InspectionPoint.greenhouse_id == greenhouse_id,
            InspectionPoint.name == name,
        ))).scalars().first()
        if pt is not None:
            return pt
    return None


async def latest_map_version(db: AsyncSession, greenhouse_id: Optional[str]) -> Optional[MapVersion]:
    if not greenhouse_id:
        return None
    return (await db.execute(
        select(MapVersion).where(MapVersion.greenhouse_id == greenhouse_id)
        .order_by(MapVersion.version.desc()).limit(1))).scalar_one_or_none()


async def greenhouse_company(db: AsyncSession, greenhouse_id: Optional[str]):
    if not greenhouse_id:
        return None
    gh = await db.get(Greenhouse, greenhouse_id)
    return gh.company_id if gh is not None else None


async def record_evidence(
    db: AsyncSession,
    *,
    image_id: Optional[str] = None,
    image_url: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    task_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    point_id: Optional[str] = None,
    greenhouse_id: Optional[str] = None,
    robot_id: Optional[str] = None,
    nav_point_code: Optional[str] = None,
    map_version_id: Optional[str] = None,
    evidence_type: str = "photo",
    captured_at=None,
    file_size: Optional[int] = None,
) -> InspectionEvidence:
    ev = InspectionEvidence(
        task_id=task_id,
        batch_id=batch_id,
        point_id=point_id,
        greenhouse_id=greenhouse_id,
        robot_id=robot_id,
        nav_point_code=nav_point_code,
        map_version_id=map_version_id,
        evidence_type=evidence_type,
        attribution_status=ATTRIBUTION_RESOLVED if point_id else ATTRIBUTION_PENDING,
        media={
            "image_id": image_id,
            "url": image_url,
            "thumbnail_url": thumbnail_url,
            "file_size": file_size,
        },
        captured_at=captured_at or datetime.now(timezone.utc),
        company_id=await greenhouse_company(db, greenhouse_id),
    )
    db.add(ev)
    await db.flush()
    return ev


def snapshot_digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode()
    ).hexdigest()
