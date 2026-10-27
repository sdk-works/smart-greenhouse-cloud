"""执行器命令（卷帘机控制闭环，控制契约 v1 rev.2）。

核心语义（契约 §1 / §6.3 / §11.2）：
  - 先落库、后发布；发布成功（sent）≠ 设备接受（accepted）≠ 执行成功（executed）；
  - boot_id / session_id / ref_uptime_ms / command_seq 由**服务端**从该设备最近
    一次可信 device_state 取得，客户端自报一律忽略（契约 §5 / §8.5）；
  - 启动类命令（move_up/move_down）只在设备状态新鲜时创建与发布，
    状态过期返回 409 device_offline，不入离线队列；
  - stop/get_state 是安全/只读动作：状态过期仍可尝试（信封携带最后已知代次，
    由设备侧 boot/session/seq 校验兜底）；
  - 发布后长时间无回执 → recover 置 unknown（结果未知），绝不自动重发启动命令。
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.command_state import (
    COMMAND_TRANSITIONS, apply_side_effects, command_view,
    validate_command_transition,
)
from app.core.company_scope import can_access_company, scoped_greenhouse_ids
from app.core.curtain_control import (
    ACTIONS_V1, MAX_CMD_STALE_MS, ONLINE_WINDOW_SECONDS, SAFE_ACTIONS,
    START_ACTIONS,     allocate_command_seq, build_command_envelope,
    load_profile, require_fresh_state_for_start,
    require_trusted_state, state_age_seconds,
)
from app.core.database import get_db
from app.core.mqtt_policy import assert_can_control
from app.models.models import (
    ActuatorCommand, CommandStatus, Device, Greenhouse, User,
)
from app.mqtt.mqtt_service import mqtt_service

router = APIRouter()

# recover 阈值：发布后超过该窗口仍无设备回执 → 结果未知（草案值）
RECOVER_UNKNOWN_AFTER_SECONDS = 120


class CommandCreateIn(BaseModel):
    device_id:  str
    action:     str = Field(min_length=1, max_length=20)
    channel_id: str = Field(default="curtain_1", min_length=1, max_length=16)
    ttl_ms:     int = Field(default=15000, ge=1000, le=60000)  # 契约 §5.1 草案范围
    reason:     Optional[str] = Field(default=None, max_length=500)
    # 兼容字段：老客户端可能继续传 boot_id/session_id/command_seq，
    # 一律**忽略**——代次与序号只信设备状态（契约禁止客户端自报）。


class CommandResultIn(BaseModel):
    status:        str = Field(min_length=1, max_length=20)
    actual_state:  dict = Field(default_factory=dict)
    error_message: Optional[str] = Field(default=None, max_length=500)
    boot_id:       Optional[str] = Field(default=None, max_length=36)


async def _load(db, command_id: str) -> ActuatorCommand:
    cmd = (await db.execute(select(ActuatorCommand).where(
        ActuatorCommand.id == command_id))).scalar_one_or_none()
    if not cmd:
        raise HTTPException(404, "命令不存在")
    return cmd


async def _owned_device(db, user: User, device_uuid: Optional[str]) -> Device:
    """归属校验：设备存在且属于用户公司（不可见 = 404，不泄露存在性）。"""
    dev = await db.get(Device, device_uuid) if device_uuid else None
    if not dev:
        raise HTTPException(404, "设备不存在")
    gh = await db.get(Greenhouse, dev.greenhouse_id) if dev.greenhouse_id else None
    if not can_access_company(user, gh.company_id if gh else None):
        raise HTTPException(404, "设备不存在")
    return dev


def _v_status(status) -> str:
    return status.value if isinstance(status, CommandStatus) else str(status or "")


@router.get("/actuator-commands/state-machine")
async def command_state_machine(_: User = Depends(get_current_user)):
    return {"states": list(COMMAND_TRANSITIONS),
            "transitions": {k: sorted(v) for k, v in COMMAND_TRANSITIONS.items()},
            "success_status": "executed",
            "ack_statuses": sorted({"accepted", "executing"}),
            "unknown_statuses": ["unknown"],
            "note": "accepted 只代表设备已接收（ACK），不代表执行成功；"
                    "unknown 代表发布结果不可判定（PUBLISH_UNKNOWN）"}


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
    # ① 独立控制权限（契约 §11.3）：不再是 manage_robots；VIEWER/OPERATOR 之外，
    #    越权者在任何入库/发布之前就被拒绝。
    check_permission(user, "control_device")
    # ② 归属校验：设备存在 + 公司一致；不可见即 404，不产生任何命令行。
    dev = await _owned_device(db, user, payload.device_id)
    if payload.action not in ACTIONS_V1:
        raise HTTPException(400, f"ACTION_UNKNOWN: v1 仅支持 {ACTIONS_V1}")

    # ③ 可信设备状态：代次字段只能来自这里（契约 §5 / §8.5）。
    profile = await load_profile(db, dev.id)
    require_trusted_state(profile)
    if payload.action in START_ACTIONS:
        require_fresh_state_for_start(profile)
    else:
        age = state_age_seconds(profile)
        if age is not None and age > ONLINE_WINDOW_SECONDS:
            pass  # stop/get_state：状态过期仍允许尝试，设备侧校验兜底

    # ④ 分配序号并落库（pending；不发布——发布是显式的 send 步骤）。
    command_seq = await allocate_command_seq(db, dev.id, profile)
    now = datetime.now(timezone.utc)
    cmd = ActuatorCommand(
        command_id=str(uuid.uuid4()),
        device_id=dev.id, greenhouse_id=dev.greenhouse_id,
        channel_id=payload.channel_id, action=payload.action,
        source="manual", reason=payload.reason,
        status=CommandStatus.PENDING,
        boot_id=str(profile.boot_id), session_id=profile.session_id,
        command_seq=command_seq, ttl_ms=payload.ttl_ms,
        ref_uptime_ms=profile.uptime_ms,
        issued_by=user.id,
        expires_at=now + timedelta(milliseconds=payload.ttl_ms),
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
    check_permission(user, "control_device")
    cmd = await _load(db, command_id)
    dev = await _owned_device(db, user, cmd.device_id)
    # M1 约束沿用：控制下发必须校验设备归属（平台运营放行，调用方落审计）
    gh = await db.get(Greenhouse, cmd.greenhouse_id) if cmd.greenhouse_id else None
    assert_can_control(user, gh.company_id if gh else None, dev.device_id)

    if publish:
        # 重复请求不得产生第二次下发（设备按 command_id 幂等，云端发布次数可核）
        if (cmd.publish_count or 0) > 0:
            raise HTTPException(409, "该命令已下发过，不允许重复发布")
        if _v_status(cmd.status) != "pending":
            raise HTTPException(409, f"命令已离开待发送状态（{_v_status(cmd.status)}），"
                                     f"不得再次发布")

        # 发布前重核设备状态（create 与 send 之间设备可能重启/换会话/状态过期）
        profile = await load_profile(db, dev.id)
        require_trusted_state(profile)
        if str(profile.boot_id) != str(cmd.boot_id):
            raise HTTPException(409, "BOOT_MISMATCH: 设备已重启，命令依据的代次失效，"
                                     "请重新 get_state 后新建命令")
        if profile.session_id != cmd.session_id:
            raise HTTPException(409, "SESSION_MISMATCH: 设备会话已轮换，"
                                     "请重新 get_state 后新建命令")
        if cmd.action in START_ACTIONS:
            age = state_age_seconds(profile)
            if age is not None and age * 1000 > MAX_CMD_STALE_MS:
                raise HTTPException(409, "COMMAND_STALE: 命令依据的设备状态过旧"
                                         f"（{age:.1f}s > {MAX_CMD_STALE_MS / 1000:.0f}s），"
                                         "设备将拒绝（ref_uptime_ms 超限）")

        # 契约信封：全部代次字段来自刚核过的设备状态；ref_uptime 取最新 uptime
        envelope = build_command_envelope(
            command_id=cmd.command_id, device_hw_id=dev.device_id,
            channel_id=cmd.channel_id or "curtain_1", action=cmd.action,
            session_id=profile.session_id, boot_id=profile.boot_id,
            ttl_ms=cmd.ttl_ms or 15000, command_seq=cmd.command_seq,
            ref_uptime_ms=profile.uptime_ms)
        ok = await mqtt_service.publish_device_command(dev.device_id, envelope)
        if not ok:
            raise HTTPException(503, "MQTT 未连接，命令未能下发")
        cmd.publish_count = (cmd.publish_count or 0) + 1
        cmd.last_publish_at = datetime.now(timezone.utc)
        cmd.ref_uptime_ms = profile.uptime_ms

    # 条件更新（契约 §11.2）：只有仍处于 pending 才置 sent，结果先到不回退
    await db.commit()
    await db.execute(
        update(ActuatorCommand)
        .where(ActuatorCommand.id == cmd.id,
               ActuatorCommand.status == CommandStatus.PENDING)
        .values(status=CommandStatus.SENT))
    await db.commit()
    await db.refresh(cmd)
    return command_view(cmd)


@router.post("/actuator-commands/{command_id}/ack")
async def ack_command(command_id: str,
                      db: AsyncSession = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """设备回执"已接收"（sent/unknown → accepted）。**这只是 ACK，不是执行结果。**"""
    check_permission(user, "control_device")
    cmd = await _load(db, command_id)
    validate_command_transition(cmd.status, "accepted")
    cmd.status = CommandStatus.ACCEPTED
    apply_side_effects(cmd, "accepted")
    await db.commit()
    await db.refresh(cmd)
    out = command_view(cmd)
    out["reminder"] = "已接收 ≠ 执行成功，仍需等待执行结果"
    return out


@router.post("/actuator-commands/{command_id}/result")
async def report_result(command_id: str, payload: CommandResultIn,
                        db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """演练用结果上报（正式链路走 MQTT /up 的 command_result，带归属校验）。"""
    check_permission(user, "control_device")
    cmd = await _load(db, command_id)
    target = payload.status

    # 迟到结果：命令已过期，设备这份结果不能再改结论——先落 timeout，
    # 迟到内容留在 error_message 里，不静默丢弃也不改写状态。
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

    # 代次校验：结果来自另一代 boot，不能套到这条命令上
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


@router.post("/actuator-commands/recover")
async def recover_unknown(limit: int = Query(200, ge=1, le=1000),
                          db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_user)):
    """崩溃恢复（契约 §11.2）：已发布但超过恢复窗口仍无设备回执的命令
    置 unknown（PUBLISH_UNKNOWN），等待结果对账或超时收敛。
    **不重发任何命令**；需要停止时由人工下发新的 stop（新 command_id）。
    """
    check_permission(user, "control_device")
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=RECOVER_UNKNOWN_AFTER_SECONDS)
    res = await db.execute(
        update(ActuatorCommand)
        .where(ActuatorCommand.status == CommandStatus.SENT,
               ActuatorCommand.publish_count > 0,
               ActuatorCommand.accepted_at.is_(None),
               ActuatorCommand.last_publish_at.isnot(None),
               ActuatorCommand.last_publish_at < cutoff)
        .values(status=CommandStatus.UNKNOWN,
                error_message="PUBLISH_UNKNOWN: 已发布但无设备回执，结果未知；"
                              "等待 device_state 对账或超时，不自动重发"))
    await db.commit()
    return {"recovered": res.rowcount or 0,
            "window_seconds": RECOVER_UNKNOWN_AFTER_SECONDS, "republished": 0}


@router.post("/actuator-commands/expire-sweep")
async def expire_sweep(limit: int = Query(200, ge=1, le=1000),
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """把已过期的在途命令（含 unknown）收敛为 timeout。

    【明确不做自动重发】启动类命令超时后是否重发必须由人决定：
    自动重发会让"电机到底动没动"变成不可判定（契约 §11.2）。
    """
    check_permission(user, "control_device")
    now = datetime.now(timezone.utc)
    rows = (await db.execute(
        select(ActuatorCommand).where(
            ActuatorCommand.expires_at.isnot(None),
            ActuatorCommand.expires_at < now,
            ActuatorCommand.status.in_(
                [CommandStatus.PENDING, CommandStatus.SENT,
                 CommandStatus.ACCEPTED, CommandStatus.EXECUTING,
                 CommandStatus.UNKNOWN]),
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
