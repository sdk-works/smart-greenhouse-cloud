"""
photo_catalog.py — 照片归档目录（M2 机器人闭环）

前端 PointPhotos.js 实际调用：
    GET /photo-catalog?greenhouse_id=&point_id=&page=&page_size=&start=
    GET /photo-catalog/options?greenhouse_id=

响应契约：
    {items:[{id,url,thumbnail_url,captured_at,task_id,point_id,map_version_id,
             greenhouse_id,robot_id,nav_point_code,attribution_status}],
     total, page, page_size}
"""
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.company_scope import can_access_company, scoped_greenhouse_ids
from app.core.database import get_db
from app.core.photo_service import ATTRIBUTION_PENDING, ATTRIBUTION_RESOLVED
from app.models.models import (
    Greenhouse, Image, InspectionPoint, InspectionEvidence, User,
)

router = APIRouter()

DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE = 100


def _item(img: Image) -> dict:
    return {
        "id":                 str(img.id),
        "url":                img.storage_url,
        "thumbnail_url":      img.thumbnail_url,
        "captured_at":        img.captured_at,
        "task_id":            img.task_id,
        "point_id":           img.point_id,
        "map_version_id":     img.map_version_id,
        "greenhouse_id":      img.greenhouse_id,
        "attribution_status": ATTRIBUTION_RESOLVED if img.point_id else ATTRIBUTION_PENDING,
    }


def _parse_start(start: Optional[str]) -> Optional[datetime]:
    if not start:
        return None
    raw = start.strip()
    # 查询串里的 '+' 会被按空格解码，"...T12:00:00 08:00" 还原成 "...T12:00:00+08:00"
    m = re.search(r"\s([+-]?)(\d{2}:?\d{2})$", raw)
    if m:
        raw = raw[:m.start()] + (m.group(1) or "+") + m.group(2)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"start 时间格式非法：{start}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/photo-catalog")
async def list_photo_catalog(
    greenhouse_id: Optional[str] = Query(None),
    point_id:      Optional[str] = Query(None),
    page:          int = Query(1, ge=1),
    page_size:     int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    start:         Optional[str] = Query(None, description="ISO 时间，只看该时刻之后采集的照片"),
    include_unassigned: bool = Query(False, description="是否包含无法解析点位的照片"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    check_permission(user, "view_data")

    # 公司隔离：可见大棚清单
    ghq = scoped_greenhouse_ids(user, None)
    if greenhouse_id:
        gh = await db.get(Greenhouse, greenhouse_id)
        if not gh or not can_access_company(user, gh.company_id if gh else None):
            raise HTTPException(status_code=404, detail="大棚不存在")

    conds = []
    if ghq is not None:
        conds.append(Image.greenhouse_id.in_(ghq))
    if greenhouse_id:
        conds.append(Image.greenhouse_id == greenhouse_id)
    if point_id:
        conds.append(Image.point_id == point_id)
    if not include_unassigned:
        conds.append(Image.point_id.is_not(None))

    start_dt = _parse_start(start)
    if start_dt is not None:
        # captured_at 缺失时退回 created_at，避免老照片凭空消失
        conds.append(or_(Image.captured_at >= start_dt,
                         Image.captured_at.is_(None) & (Image.created_at >= start_dt)))

    base = select(Image).where(*conds) if conds else select(Image)
    total = (await db.execute(
        select(func.count()).select_from(base.subquery()))).scalar() or 0

    rows = (await db.execute(
        base.order_by(Image.captured_at.desc().nullslast(),
                      Image.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return {
        "items":     [_item(r) for r in rows],
        "total":     int(total),
        "page":      page,
        "page_size": page_size,
    }


@router.get("/photo-catalog/options")
async def photo_catalog_options(
    greenhouse_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """筛选下拉选项：可见大棚 + 该大棚下已归档的点位。"""
    check_permission(user, "view_data")

    ghq = scoped_greenhouse_ids(user, None)
    gh_query = select(Greenhouse).where(Greenhouse.is_active == True)
    if ghq is not None:
        gh_query = gh_query.where(Greenhouse.id.in_(ghq))
    houses = (await db.execute(gh_query.order_by(Greenhouse.name))).scalars().all()

    target = greenhouse_id or (houses[0].id if houses else None)
    points = []
    if target:
        gh = await db.get(Greenhouse, target)
        if gh and can_access_company(user, gh.company_id):
            # 只列真正有归档照片的点位，避免前端下拉出现一堆空项
            ids = (await db.execute(
                select(Image.point_id).where(
                    Image.greenhouse_id == target,
                    Image.point_id.is_not(None),
                ).distinct())).scalars().all()
            if ids:
                rows = (await db.execute(
                    select(InspectionPoint).where(InspectionPoint.id.in_(ids)))
                ).scalars().all()
                points = [{"id": p.id, "code": p.code, "name": p.name,
                           "greenhouse_id": p.greenhouse_id} for p in rows]

    return {
        "greenhouses":   [{"id": g.id, "name": g.name} for g in houses],
        "points":        points,
        "greenhouse_id": target,
    }


@router.get("/photo-catalog/tasks/{task_id}/evidence")
async def list_task_evidence(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """某次巡检任务留存的证据（含 point_id 为 NULL 的待确认项）。"""
    check_permission(user, "view_data")

    ghq = scoped_greenhouse_ids(user, None)
    conds = [InspectionEvidence.task_id == task_id]
    if ghq is not None:
        conds.append(InspectionEvidence.greenhouse_id.in_(ghq))
    rows = (await db.execute(
        select(InspectionEvidence).where(*conds)
        .order_by(InspectionEvidence.captured_at.desc()))).scalars().all()
    return {
        "task_id": task_id,
        "items": [{
            "id":                  e.id,
            "task_id":             e.task_id,
            "point_id":            e.point_id,
            "greenhouse_id":       e.greenhouse_id,
            "robot_id":            e.robot_id,
            "nav_point_code":      e.nav_point_code,
            "map_version_id":      e.map_version_id,
            "evidence_type":       e.evidence_type,
            "attribution_status":  e.attribution_status,
            "media":               e.media,
            "captured_at":         e.captured_at,
        } for e in rows],
        "total": len(rows),
    }
