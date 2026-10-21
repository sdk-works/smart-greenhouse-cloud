"""执行器命令（风机/水阀等）流转（M4）。

核心语义：**ACK ≠ 执行结果**
  - POST /actuator-commands/{id}/ack    → sent → accepted（设备已收到，不等于成功）
  - POST /actuator-commands/{id}/result → accepted/executing → executed|failed|timeout

本模块**不主动向设备下发任何控制指令**：`?publish=true` 才会走 MQTT 下发，
默认只做状态流转记录，便于在测试/演练环境验证链路。
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.command_state import (
    COMMAND_TRANSITIONS, apply_side_effects, command_view,
    validate_command_transition,
)
from app.core.company_scope import can_access_company, scoped_greenhouse_ids
from app.core.database import get_db
from app.core.mqtt_policy import assert_can_control
from app.models.models import (
    ActuatorCommand, CommandStatus, Device, Greenhouse, User,
)
from app.mqtt.mqtt_service import mqtt_service

router = APIRouter()

DEFAULT_TTL_SECONDS = 300


class CommandCreateIn(BaseModel):
    device_id:     str
    action:        str = Field(min_length=1, max_length=50)
    params:        dict = Field(default_factory=dict)
    desired_state: dict = Field(default_factory=dict)
    reason:        Optional[str] = Field(default=None, max_length=500)
    ttl_seconds:   int = Field(default=DEFAULT_TTL_SECONDS, ge=10, le=86400)


class CommandResultIn(BaseModel):
    status:        str = Field(min_length=1, max_length=20)
    actual_state:  dict = Field(default_factory=dict)
    error_message: Optional[str] = Field(default=None, max_length=500)


async def _load(db, command_id: str) -> ActuatorCommand:
    cmd = (await db.execute(select(ActuatorCommand).where(
        ActuatorCommand.id == command_id))).scalar_one_or_none()
    if not cmd:
        raise HTTPException(404, "命令不存在")
    return cmd


@router.get("/actuator-commands/state-machine")
async def command_state_machine(_: User = Depends(get_current_user)):
    return {"states": list(COMMAND_TRANSITIONS),
            "transitions": {k: sorted(v) for k, v in COMMAND_TRANSITIONS.items()},
            "success_status": "executed",
            "ack_statuses": sorted({"accepted", "executing"}),
            "note": "accepted 只代表设备已收到（ACK），不代表执行成功"}


@router.get("/actuator-commands")
async def list_commands(device_id: Optional[str] = Query(None),
                        status: Optional[str] = Query(None),
                        limit: int = Query(50, ge=1, le=200),
                        db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    ghq = scoped_greenhouse_ids(user, None)
    q = select(ActuatorCommand)
    if ghq is not None:
        q = q.where(ActuatorCommand.greenhouse_id.in_(ghq))
    if device_id:
        q = q.where(ActuatorCommand.device_id == device_id)
    if status:
        q = q.where(ActuatorCommand.status == status)
    rows = (await db.execute(
        q.order_by(ActuatorCommand.issued_at.desc()).limit(limit))).scalars().all()
    return {"items": [command_view(c) for c in rows], "total": len(rows)}


@router.post("/actuator-commands", status_code=201)
async def create_command(payload: CommandCreateIn,
                         db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    check_permission(user, "manage_robots")
    dev = await db.get(Device, payload.device_id)
    if not dev:
        raise HTTPException(404, "设备不存在")
    gh = await db.get(Greenhouse, dev.greenhouse_id) if dev.greenhouse_id else None
    if not can_access_company(user, gh.company_id if gh else None):
        raise HTTPException(404, "设备不存在")

    import uuid as _uuid
    cmd = ActuatorCommand(
        command_id=str(_uuid.uuid4()),
        device_id=dev.id, greenhouse_id=dev.greenhouse_id,
        action=payload.action, params=payload.params,
        desired_state=payload.desired_state, source="manual",
        reason=payload.reason, status=CommandStatus.PENDING,
        issued_by=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=payload.ttl_seconds),
    )
    db.add(cmd)
    await db.commit()
    await db.refresh(cmd)
    return command_view(cmd)


@router.post("/actuator-commands/{command_id}/send")
async def mark_sent(command_id: str, publish: bool = Query(False),
                    db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """pending → sent。默认**不**真正下发 MQTT（publish=true 才下发）。"""
    check_permission(user, "manage_robots")
    cmd = await _load(db, command_id)
    dev = await db.get(Device, cmd.device_id)
    gh = await db.get(Greenhouse, cmd.greenhouse_id) if cmd.greenhouse_id else None
    if dev is not None:
        # M1 约束沿用：控制下发必须校验设备归属
        assert_can_control(user, gh.company_id if gh else None,
                           dev.device_id if dev else command_id)
    validate_command_transition(cmd.status, "sent")
    if publish:
        ok = await mqtt_service.publish_device_command(
            dev.device_id if dev else "",
            {"command_id": cmd.command_id, "action": cmd.action,
             "params": cmd.params, "desired_state": cmd.desired_state})
        if not ok:
            raise HTTPException(503, "MQTT 未连接，命令未能下发")
    cmd.status = CommandStatus.SENT
    await db.commit()
    await db.refresh(cmd)
    return command_view(cmd)


@router.post("/actuator-commands/{command_id}/ack")
async def ack_command(command_id: str,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """设备回执"已收到"（sent → accepted）。**这只是 ACK，不是执行结果。**"""
    check_permission(user, "manage_robots")
    cmd = await _load(db, command_id)
    validate_command_transition(cmd.status, "accepted")
    cmd.status = CommandStatus.ACCEPTED
    apply_side_effects(cmd, "accepted")
    await db.commit()
    await db.refresh(cmd)
    out = command_view(cmd)
    out["reminder"] = "已收到 ≠ 执行成功，仍需等待执行结果"
    return out


@router.post("/actuator-commands/{command_id}/result")
async def report_result(command_id: str, payload: CommandResultIn,
                        db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """上报真实执行结果（accepted/executing → executed|failed|timeout）。"""
    check_permission(user, "manage_robots")
    cmd = await _load(db, command_id)
    target = payload.status
    validate_command_transition(cmd.status, target)
    try:
        cmd.status = CommandStatus(target)
    except ValueError:
        raise HTTPException(400, f"未知命令状态：{target}")
    if payload.actual_state:
        cmd.actual_state = payload.actual_state
    if payload.error_message:
        cmd.error_message = payload.error_message
    apply_side_effects(cmd, target)
    await db.commit()
    await db.refresh(cmd)
    return command_view(cmd)


@router.get("/actuator-commands/{command_id}")
async def get_command(command_id: str, db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    cmd = await _load(db, command_id)
    gh = await db.get(Greenhouse, cmd.greenhouse_id) if cmd.greenhouse_id else None
    if not can_access_company(user, gh.company_id if gh else None):
        raise HTTPException(404, "命令不存在")
    return command_view(cmd)
