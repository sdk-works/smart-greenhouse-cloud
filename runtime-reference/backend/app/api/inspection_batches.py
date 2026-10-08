"""Automatic, navigation-agnostic inspection batch APIs."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import InspectionBatch, InspectionVisit

router = APIRouter()
IDLE_TIMEOUT_MINUTES = 15


class RouteLabelIn(BaseModel):
    label: Optional[str] = Field(None, max_length=200)


async def expire_stale_batches(db: AsyncSession, robot_id: Optional[str] = None):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=IDLE_TIMEOUT_MINUTES)
    query = select(InspectionBatch).where(
        InspectionBatch.status == "running", InspectionBatch.last_event_at < cutoff)
    if robot_id:
        query = query.where(InspectionBatch.robot_id == robot_id)
    rows = (await db.execute(query)).scalars().all()
    for row in rows:
        row.status = "partial"
        row.ended_at = row.last_event_at
        row.close_reason = "arrival_event_timeout"
    if rows:
        await db.commit()


def _batch_dict(row, counts=None):
    result = {
        "id": row.id, "robot_id": row.robot_id, "status": row.status,
        "source": row.source,
        "navigation_route_label": row.navigation_route_label,
        "route_identification": row.route_identification,
        "config_version": row.config_version,
        "started_at": row.started_at, "last_event_at": row.last_event_at,
        "ended_at": row.ended_at, "close_reason": row.close_reason,
        "summary": row.summary or {},
        "idle_timeout_minutes": IDLE_TIMEOUT_MINUTES,
    }
    if counts:
        result.update(counts)
    return result


@router.get("")
async def list_batches(robot_id: Optional[str] = None, limit: int = Query(50, ge=1, le=200),
                       db: AsyncSession = Depends(get_db)):
    await expire_stale_batches(db, robot_id)
    q = select(InspectionBatch)
    if robot_id:
        q = q.where(InspectionBatch.robot_id == robot_id)
    q = q.order_by(InspectionBatch.started_at.desc()).limit(limit)
    rows = (await db.execute(q)).scalars().all()
    output = []
    for row in rows:
        stats = (await db.execute(select(
            func.count(InspectionVisit.id),
            func.count(InspectionVisit.id).filter(InspectionVisit.status == "sampled"),
            func.count(func.distinct(InspectionVisit.nav_point_code)),
        ).where(InspectionVisit.batch_id == row.id))).one()
        output.append(_batch_dict(row, {
            "visit_count": stats[0], "sampled_count": stats[1],
            "distinct_point_count": stats[2],
        }))
    return output


@router.get("/{batch_id}")
async def get_batch(batch_id: str, db: AsyncSession = Depends(get_db)):
    await expire_stale_batches(db)
    row = await db.get(InspectionBatch, batch_id)
    if not row:
        raise HTTPException(404, "巡检批次不存在")
    visits = (await db.execute(select(InspectionVisit).where(
        InspectionVisit.batch_id == batch_id).order_by(InspectionVisit.arrived_at)))
    visits = visits.scalars().all()
    data = _batch_dict(row)
    data["visits"] = [{
        "id": v.id, "event_id": v.event_id, "task_id": v.task_id,
        "nav_point_code": v.nav_point_code, "greenhouse_id": v.greenhouse_id,
        "point_name": v.point_name, "mapping_id": v.mapping_id,
        "config_version": v.config_version, "status": v.status,
        "arrived_at": v.arrived_at, "sampled_at": v.sampled_at,
        "telemetry_id": v.telemetry_id, "error_code": v.error_code,
    } for v in visits]
    return data


@router.put("/{batch_id}/route-label")
async def set_route_label(batch_id: str, body: RouteLabelIn,
                          db: AsyncSession = Depends(get_db)):
    row = await db.get(InspectionBatch, batch_id)
    if not row:
        raise HTTPException(404, "巡检批次不存在")
    label = body.label.strip() if body.label else None
    row.navigation_route_label = label
    row.route_identification = "operator_confirmed" if label else "unknown"
    await db.commit(); await db.refresh(row)
    return _batch_dict(row)
