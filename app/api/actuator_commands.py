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
    # 控制契约 v1（与固件对齐）：下发时把代次与序号一起落库，
    # 设备回结果时靠 boot_id 判断这份结果是否属于"这一代"的命令。
    boot_id:       Optional[str] = Field(default=None, max_length=36)
    session_id:    Optional[str] = Field(default=None, max_length=64)
    command_seq:   Optional[int] = Field(default=None, ge=1)


class CommandResultIn(BaseModel):
    status:        str = Field(min_length=1, max_length=20)
    actual_state:  dict = Field(default_factory=dict)
    error_message: Optional[str] = Field(default=None, max_length=500)
    # 设备上报自己所属代次；与命令的 boot_id 不一致说明设备重启过，
    # 这份结果不能再套到这条命令上（迟到结果 / 旧代次结果）。
    boot_id:       Optional[str] = Field(default=None, max_length=36)


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
        boot_id=payload.boot_id, session_id=payload.session_id,
        command_seq=payload.command_seq,
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
        # 重复请求不得产生第二次下发：发布次数是这条命令对外承诺的唯一凭据，
        # 连点/重试/重放都必须落到同一条记录上（设备侧也按 command_id 幂等）。
        if (cmd.publish_count or 0) > 0:
            raise HTTPException(409, "该命令已下发过，不允许重复发布")
        ok = await mqtt_service.publish_device_command(
            dev.device_id if dev else "",
            {"command_id": cmd.command_id, "action": cmd.action,
             "params": cmd.params, "desired_state": cmd.desired_state,
             "boot_id": cmd.boot_id, "session_id": cmd.session_id,
             "command_seq": cmd.command_seq})
        if not ok:
            raise HTTPException(503, "MQTT 未连接，命令未能下发")
        cmd.publish_count = (cmd.publish_count or 0) + 1
        cmd.last_publish_at = datetime.now(timezone.utc)
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

    # 迟到结果：命令已过期，设备这份结果不能再改结论 —— 先落 timeout，
    # 并把迟到的内容留在 error_message 里给人看，不静默丢弃也不改写状态。
    if cmd.expires_at is not None and datetime.now(timezone.utc) > cmd.expires_at:
        if _v_status(cmd.status) not in ("executed", "failed", "rejected",
                                         "timeout", "cancelled"):
            cmd.status = CommandStatus.TIMEOUT
            cmd.error_message = (
                f"结果迟到（命令已于 {cmd.expires_at.isoformat()} 过期）："
                f"status={payload.status}"
            )
            apply_side_effects(cmd, "timeout")
            await db.commit()
            await db.refresh(cmd)
        out = command_view(cmd)
        out["late_result"] = True
        out["publish_count"] = cmd.publish_count or 0
        return out

    # 重启代次变化：结果来自另一代 boot，不能套到这条命令上（否则"新命令被旧结果盖章"）
    if payload.boot_id and cmd.boot_id and payload.boot_id != cmd.boot_id:
        cmd.result_boot_id = payload.boot_id
        cmd.error_message = f"结果代次不匹配：命令 boot={cmd.boot_id}，结果 boot={payload.boot_id}"
        await db.commit()
        await db.refresh(cmd)
        out = command_view(cmd)
        out["generation_mismatch"] = True
        out["publish_count"] = cmd.publish_count or 0
        return out

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


def _v_status(status) -> str:
    return status.value if isinstance(status, CommandStatus) else str(status or "")


@router.post("/actuator-commands/expire-sweep")
async def expire_sweep(limit: int = Query(200, ge=1, le=1000),
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """把已过期的在途命令收敛为 timeout。

    【明确不做自动重发】启动类命令超时后是否重发必须由人决定：
    自动重发会让"电机到底动没动"变成不可判定（设备侧也禁止自动重发启动）。
    """
    check_permission(user, "manage_robots")
    now = datetime.now(timezone.utc)
    rows = (await db.execute(
        select(ActuatorCommand).where(
            ActuatorCommand.expires_at.isnot(None),
            ActuatorCommand.expires_at < now,
            ActuatorCommand.status.in_(
                [CommandStatus.PENDING, CommandStatus.SENT,
                 CommandStatus.ACCEPTED, CommandStatus.EXECUTING]),
        ).limit(limit))).scalars().all()
    changed = []
    for cmd in rows:
        cmd.status = CommandStatus.TIMEOUT
        if not cmd.error_message:
            cmd.error_message = "命令超时，未自动重发"
        apply_side_effects(cmd, "timeout")
        changed.append({"id": cmd.id, "publish_count": cmd.publish_count or 0})
    await db.commit()
    return {"expired": len(changed), "items": changed, "republished": 0}


@router.get("/actuator-commands/{command_id}")
async def get_command(command_id: str, db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    cmd = await _load(db, command_id)
    gh = await db.get(Greenhouse, cmd.greenhouse_id) if cmd.greenhouse_id else None
    if not can_access_company(user, gh.company_id if gh else None):
        raise HTTPException(404, "命令不存在")
    return command_view(cmd)
