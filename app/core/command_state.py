"""执行器命令状态机（M4 → 控制契约 v1 rev.2）。

流转（契约 §11.2：发布成功 ≠ 设备接受；ACK ≠ 执行结果）：
    pending（待发送）
      → sent（已发送） / cancelled（取消）
    sent（已发送）
      → accepted（设备已接收） / rejected（设备拒收） / unknown（结果未知） / timeout / cancelled
    unknown（结果未知：发布后无任何设备回执，PUBLISH_UNKNOWN 恢复）
      → accepted / executing / executed / failed / rejected / timeout（任何回执或对账都可推进，不回退）
    accepted（已接收 = 设备 ACK）
      → executing（执行中） / failed / timeout / cancelled
    executing（执行中）
      → executed（成功） / failed / timeout
    executed / failed / rejected / timeout / cancelled —— 终态

关键语义（契约 §1.2 / §6.3）：
  - published（sent）只代表 broker 接受，不代表设备接受；
  - accepted 只说明"设备收到了命令"，尚未执行成功；
  - 只有 executed 才算成功；
  - unknown 表示"发布结果不可判定"（发布后崩溃/长时间无回执），
    不是失败也不是成功，等待结果对账或超时收敛，绝不自动重发启动命令。
"""
from datetime import datetime, timezone

from fastapi import HTTPException

from app.models.models import CommandStatus

COMMAND_TRANSITIONS = {
    "pending":   {"sent", "cancelled"},
    "sent":      {"accepted", "rejected", "unknown", "timeout", "cancelled"},
    "unknown":   {"accepted", "executing", "executed", "failed", "rejected", "timeout"},
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
# 结果未知：发布后无可判定回执（PUBLISH_UNKNOWN 恢复所得）
UNKNOWN_STATUSES = {"unknown"}


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


def is_unknown(status) -> bool:
    return _v(status) in UNKNOWN_STATUSES


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
    """按目标状态补时间戳。unknown 是非终态，不写 completed_at。"""
    if target in ("accepted", "executing") and cmd.accepted_at is None:
        cmd.accepted_at = _now()
    if target in ("executed", "failed", "rejected", "timeout", "cancelled"):
        cmd.completed_at = _now()


def command_view(cmd) -> dict:
    st = _v(cmd.status)
    return {
        "id": cmd.id, "command_id": cmd.command_id, "device_id": cmd.device_id,
        "greenhouse_id": cmd.greenhouse_id, "action": cmd.action,
        "channel_id": getattr(cmd, "channel_id", None),
        "params": cmd.params, "status": st,
        "is_acked": is_acked(st),
        "is_executed": is_executed(st),
        "is_unknown": is_unknown(st),
        "is_finished": st in {"executed", "failed", "rejected", "timeout", "cancelled"},
        "desired_state": cmd.desired_state, "actual_state": cmd.actual_state,
        "issued_at": cmd.issued_at, "expires_at": cmd.expires_at,
        "accepted_at": cmd.accepted_at, "completed_at": cmd.completed_at,
        "error_message": cmd.error_message,
        # 控制闭环可核字段：下发过几次、属于哪一代、设备回的是哪一代、
        # 命令信封依据的设备状态（契约 §5.1 / §8.5）
        "publish_count": getattr(cmd, "publish_count", 0) or 0,
        "boot_id": getattr(cmd, "boot_id", None),
        "session_id": getattr(cmd, "session_id", None),
        "command_seq": getattr(cmd, "command_seq", None),
        "ref_uptime_ms": getattr(cmd, "ref_uptime_ms", None),
        "ttl_ms": getattr(cmd, "ttl_ms", None),
        "result_boot_id": getattr(cmd, "result_boot_id", None),
        "result_session_id": getattr(cmd, "result_session_id", None),
    }
