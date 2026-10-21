"""异常事件（事件单）状态机（M3）。

流转：
    pending_confirm（待确认）
      → confirmed（已确认） / closed（直接关闭：误报）
    confirmed
      → handling（处理中） / closed
    handling
      → pending_review（待复核） / closed
    pending_review
      → closed / handling（复核不通过，退回处理中）
    closed —— 终态

与"告警停止"刻意分开：
    环境恢复 → alerts.cleared_at 置位（告警不再响），
    但事件单状态不变，仍需人工走到 closed。
"""
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

INCIDENT_TRANSITIONS = {
    "pending_confirm": {"confirmed", "closed"},
    "confirmed":       {"handling", "closed"},
    "handling":        {"pending_review", "closed", "confirmed"},
    "pending_review":  {"closed", "handling"},
    "closed":          set(),
}
ALL_INCIDENT_STATES = tuple(INCIDENT_TRANSITIONS)


def _s(v) -> str:
    return str(v)


def allowed_incident_next(status) -> set:
    return set(INCIDENT_TRANSITIONS.get(_s(status), set()))


def validate_incident_transition(current, target) -> None:
    cur, tgt = _s(current), _s(target)
    if cur == tgt:
        return
    if tgt not in ALL_INCIDENT_STATES:
        raise HTTPException(status_code=400, detail=f"未知事件状态：{tgt}")
    if tgt not in INCIDENT_TRANSITIONS.get(cur, set()):
        raise HTTPException(status_code=400,
                            detail=f"非法事件状态流转：{cur} → {tgt}")


async def move_incident(db, incident, target: str, *, actor_id=None, note=None,
                        auto_closed_by_recovery: bool = False) -> None:
    """按状态机流转并留痕。"""
    from app.models.models import IncidentEvent

    validate_incident_transition(incident.status, target)
    old = incident.status
    if old == target:
        return
    incident.status = target
    if target == "closed":
        incident.note = (
            f"{incident.note or ''}\n[自动关闭] 环境恢复且告警已停止"
            if auto_closed_by_recovery else incident.note)
    db.add(IncidentEvent(incident_id=incident.id, from_status=old, to_status=target,
                         actor_id=actor_id, note=note))
    await db.flush()


def make_code(prefix: str = "INC") -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
