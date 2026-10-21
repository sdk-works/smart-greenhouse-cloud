"""执行器命令状态机（M4）。

流转：
    pending（待发送）
      → sent（已发送） / cancelled（取消）
    sent（已发送）
      → accepted（已接收 = 设备 ACK） / rejected（设备拒收） / timeout / cancelled
    accepted（已接收）
      → executing（执行中） / failed / timeout / cancelled
    executing（执行中）
      → executed（成功） / failed / timeout
    executed / failed / rejected / timeout / cancelled —— 终态

关键语义：**ACK ≠ 执行结果**。
    accepted 只说明"设备收到了命令"，此时命令**尚未执行成功**；
    只有 executed 才算成功。查询接口必须能区分（is_acked / is_executed）。
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.models.models import CommandStatus

COMMAND_TRANSITIONS = {
    "pending":   {"sent", "cancelled"},
    "sent":      {"accepted", "rejected", "timeout", "cancelled"},
    "accepted":  {"executing", "failed", "timeout", "cancelled"},
    "executing": {"executed", "failed", "timeout"},
    "executed":   set(),
    "failed":     set(),
    "rejected":   set(),
    "timeout":    set(),
    "cancelled":  set(),
}

# 只有 executed 表示"执行成功"
SUCCESS_STATUS = "executed"
# accepted / executing 只是"收到了 / 正在做"，不是结果
ACK_STATUSES = {"accepted", "executing"}


def _v(status) -> str:
    if status is None:
        return ""
    if isinstance(status, CommandStatus):
        return status.value
    return str(status)


def is_acked(status) -> bool:
    return _v(status) in ACK_STATUSES


def is_executed(status) -> bool:
    return _v(status) == SUCCESS_STATUS


def validate_command_transition(current, target) -> None:
    cur, tgt = _v(current), _v(target)
    if cur == tgt:
        return
    if tgt not in COMMAND_TRANSITIONS:
        raise HTTPException(status_code=400, detail=f"未知命令状态：{tgt}")
    if tgt not in COMMAND_TRANSITIONS.get(cur, set()):
        raise HTTPException(status_code=400,
                            detail=f"非法命令状态流转：{cur} → {tgt}")


def try_command_transition(current, target) -> bool:
    """非 HTTP 路径（如 MQTT 回执）用：非法流转返回 False，由调用方记录。"""
    cur, tgt = _v(current), _v(target)
    if cur == tgt:
        return True
    return tgt in COMMAND_TRANSITIONS.get(cur, set())


def _now():
    return datetime.now(timezone.utc)


def apply_side_effects(cmd, target: str) -> None:
    """按目标状态补时间戳。"""
    if target in ("accepted", "executing") and cmd.accepted_at is None:
        cmd.accepted_at = _now()
    if target in ("executed", "failed", "rejected", "timeout", "cancelled"):
        cmd.completed_at = _now()


def command_view(cmd) -> dict:
    st = _v(cmd.status)
    return {
        "id": cmd.id, "command_id": cmd.command_id, "device_id": cmd.device_id,
        "greenhouse_id": cmd.greenhouse_id, "action": cmd.action,
        "params": cmd.params, "status": st,
        "is_acked": is_acked(st),
        "is_executed": is_executed(st),
        "is_finished": st in {"executed", "failed", "rejected", "timeout", "cancelled"},
        "desired_state": cmd.desired_state, "actual_state": cmd.actual_state,
        "issued_at": cmd.issued_at, "expires_at": cmd.expires_at,
        "accepted_at": cmd.accepted_at, "completed_at": cmd.completed_at,
        "error_message": cmd.error_message,
    }
