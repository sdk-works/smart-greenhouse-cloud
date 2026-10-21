"""异常事件与告警（M3）。

刻意把两件事分开：
  * 告警（alerts）：环境指标越界 → 环境恢复后**告警停止**（cleared_at 置位）
  * 事件单（incidents）：一次异常的处置过程 → 必须人工走到 closed

即"环境恢复"不会自动关闭事件单。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.company_scope import can_access_company, is_platform, resolve_company_id
from app.core.database import get_db
from app.core.incident_state import (
    ALL_INCIDENT_STATES, INCIDENT_TRANSITIONS, make_code, move_incident,
)
from app.models.models import Alert, Greenhouse, Incident, IncidentEvent, User

router = APIRouter()


class IncidentCreateIn(BaseModel):
    title:      str = Field(min_length=1, max_length=200)
    category:   str = Field(default="environment", max_length=40)
    severity:   str = Field(default="warning", max_length=20)
    greenhouse_id: Optional[str] = None
    device_id:  Optional[str] = None
    source:     str = Field(default="manual", max_length=30)
    payload:    dict = Field(default_factory=dict)
    note:       Optional[str] = Field(default=None, max_length=1000)


class IncidentMoveIn(BaseModel):
    status: str = Field(min_length=1, max_length=20)
    note:   Optional[str] = Field(default=None, max_length=1000)


class AlertCreateIn(BaseModel):
    rule_code:  str = Field(min_length=1, max_length=60)
    incident_id: Optional[str] = None
    metric:     Optional[str] = Field(default=None, max_length=50)
    value:      Optional[float] = None
    threshold:  Optional[float] = None
    greenhouse_id: Optional[str] = None
    device_id:  Optional[str] = None


def _incident_out(row: Incident, events=None) -> dict:
    return {
        "id": row.id, "code": row.code, "title": row.title,
        "category": row.category, "severity": row.severity, "status": row.status,
        "source": row.source, "greenhouse_id": row.greenhouse_id,
        "device_id": row.device_id, "company_id": row.company_id,
        "detected_at": row.detected_at, "note": row.note,
        "created_at": row.created_at, "events": events or [],
    }


@router.get("/incidents/state-machine")
async def incident_state_machine(_: User = Depends(get_current_user)):
    return {"states": list(ALL_INCIDENT_STATES),
            "transitions": {k: sorted(v) for k, v in INCIDENT_TRANSITIONS.items()}}


@router.get("/incidents")
async def list_incidents(
    status: Optional[str] = Query(None),
    greenhouse_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = select(Incident)
    if not is_platform(user):
        q = q.where(Incident.company_id == resolve_company_id(user))
    if status:
        q = q.where(Incident.status == status)
    if greenhouse_id:
        q = q.where(Incident.greenhouse_id == greenhouse_id)
    rows = (await db.execute(
        q.order_by(Incident.detected_at.desc()).limit(limit))).scalars().all()
    return {"items": [_incident_out(r) for r in rows], "total": len(rows)}


@router.post("/incidents", status_code=201)
async def create_incident(payload: IncidentCreateIn,
                          db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_user)):
    check_permission(user, "manage_robots")
    company_id = None
    if payload.greenhouse_id:
        gh = await db.get(Greenhouse, payload.greenhouse_id)
        if not gh or not can_access_company(user, gh.company_id):
            raise HTTPException(404, "大棚不存在")
        company_id = gh.company_id
    if payload.severity not in ("info", "warning", "critical"):
        raise HTTPException(400, f"未知严重级别：{payload.severity}")
    inc = Incident(
        code=make_code(), title=payload.title, category=payload.category,
        severity=payload.severity, status="pending_confirm", source=payload.source,
        greenhouse_id=payload.greenhouse_id, device_id=payload.device_id,
        company_id=company_id, payload=payload.payload, note=payload.note,
        created_by=user.id,
    )
    db.add(inc)
    await db.flush()
    db.add(IncidentEvent(incident_id=inc.id, from_status=None,
                         to_status="pending_confirm", actor_id=user.id,
                         note="事件创建"))
    await db.commit()
    await db.refresh(inc)
    return _incident_out(inc)


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str, db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    inc = await db.get(Incident, incident_id)
    if not inc or not can_access_company(user, inc.company_id):
        raise HTTPException(404, "事件不存在")
    events = (await db.execute(select(IncidentEvent).where(
        IncidentEvent.incident_id == inc.id).order_by(IncidentEvent.created_at))
    ).scalars().all()
    alerts = (await db.execute(select(Alert).where(
        Alert.incident_id == inc.id).order_by(Alert.raised_at.desc()))
    ).scalars().all()
    out = _incident_out(inc)
    out["events"] = [{"id": e.id, "from_status": e.from_status, "to_status": e.to_status,
                      "actor_id": e.actor_id, "note": e.note,
                      "created_at": e.created_at} for e in events]
    out["alerts"] = [{"id": a.id, "rule_code": a.rule_code, "metric": a.metric,
                      "value": a.value, "threshold": a.threshold,
                      "raised_at": a.raised_at, "cleared_at": a.cleared_at,
                      "clear_reason": a.clear_reason,
                      "active": a.cleared_at is None} for a in alerts]
    out["open_alert_count"] = sum(1 for a in alerts if a.cleared_at is None)
    return out


@router.post("/incidents/{incident_id}/status")
async def move_incident_status(incident_id: str, payload: IncidentMoveIn,
                               db: AsyncSession = Depends(get_db),
                               user: User = Depends(get_current_user)):
    """按状态机流转；非法流转 400。"""
    check_permission(user, "manage_robots")
    inc = await db.get(Incident, incident_id)
    if not inc or not can_access_company(user, inc.company_id):
        raise HTTPException(404, "事件不存在")
    await move_incident(db, inc, payload.status, actor_id=user.id, note=payload.note)
    await db.commit()
    await db.refresh(inc)
    return {"success": True, "id": inc.id, "status": inc.status}


@router.post("/alerts", status_code=201)
async def raise_alert(payload: AlertCreateIn, db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    check_permission(user, "manage_robots")
    company_id = None
    if payload.greenhouse_id:
        gh = await db.get(Greenhouse, payload.greenhouse_id)
        if not gh or not can_access_company(user, gh.company_id):
            raise HTTPException(404, "大棚不存在")
        company_id = gh.company_id
    if payload.incident_id and not (await db.get(Incident, payload.incident_id)):
        raise HTTPException(404, "关联事件不存在")
    alert = Alert(rule_code=payload.rule_code, incident_id=payload.incident_id,
                  metric=payload.metric, value=payload.value,
                  threshold=payload.threshold, greenhouse_id=payload.greenhouse_id,
                  device_id=payload.device_id, company_id=company_id)
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return {"id": alert.id, "rule_code": alert.rule_code, "raised_at": alert.raised_at,
            "incident_id": alert.incident_id, "active": True}


@router.post("/alerts/{alert_id}/clear")
async def clear_alert(alert_id: str, reason: str = Query("environment_recovered"),
                      auto_close_incident: bool = Query(False),
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """环境恢复 → 告警停止。**默认不关闭事件单**（两者刻意分开）。"""
    check_permission(user, "manage_robots")
    alert = await db.get(Alert, alert_id)
    if not alert or not can_access_company(user, alert.company_id):
        raise HTTPException(404, "告警不存在")
    if alert.cleared_at is None:
        from datetime import datetime, timezone
        alert.cleared_at = datetime.now(timezone.utc)
        alert.clear_reason = reason
    incident_closed = False
    if auto_close_incident and alert.incident_id:
        inc = await db.get(Incident, alert.incident_id)
        if inc is not None and inc.status != "closed":
            await move_incident(db, inc, "closed", actor_id=user.id,
                                note=f"告警清除并显式要求关闭事件单（{reason}）")
            incident_closed = True
    await db.commit()
    return {"success": True, "alert_id": alert.id, "cleared": alert.cleared_at is not None,
            "incident_closed": incident_closed,
            "note": "默认情况下告警停止不会关闭事件单"}


@router.get("/alerts")
async def list_alerts(active_only: bool = Query(True),
                      greenhouse_id: Optional[str] = Query(None),
                      limit: int = Query(50, ge=1, le=200),
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    q = select(Alert)
    if not is_platform(user):
        q = q.where(Alert.company_id == resolve_company_id(user))
    if active_only:
        q = q.where(Alert.cleared_at.is_(None))
    if greenhouse_id:
        q = q.where(Alert.greenhouse_id == greenhouse_id)
    rows = (await db.execute(
        q.order_by(Alert.raised_at.desc()).limit(limit))).scalars().all()
    return {"items": [{"id": a.id, "rule_code": a.rule_code, "metric": a.metric,
                       "value": a.value, "threshold": a.threshold,
                       "greenhouse_id": a.greenhouse_id,
                       "incident_id": a.incident_id,
                       "raised_at": a.raised_at, "cleared_at": a.cleared_at,
                       "clear_reason": a.clear_reason,
                       "active": a.cleared_at is None} for a in rows],
            "total": len(rows)}
