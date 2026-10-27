"""卷帘机控制闭环云端侧（控制契约 v1 rev.2，JY_EnvMon_v2@5acb426 docs/control）。

本模块只实现云端最小闭环的三个契约要点，不部署、不改 EMQX、不碰生产库：

1. 命令信封（契约 §5）：扁平单行 JSON ≤320 B；
   boot_id / session_id / ref_uptime_ms **只**来自该设备最近一次可信
   `device_state`（§8.5：ref_uptime_ms = 下达命令所依据的上行报文 uptime_ms）。
   客户端自报、服务器运行时间替代、缺字段填零——都不允许；无可信状态直接拒绝。

2. 回报归属（契约 §2 / §11.2）：/up 上的 command_result / device_state / device_info
   做三层归属校验——MQTT 主题设备 == payload 自报设备 == 数据库设备；
   command_result 还要求 (command_id, device_id) 命中、命令大棚与设备大棚一致、
   boot_id / session_id 与命令代次一致。任何一层不匹配：记录、不改命令状态。

3. 生命周期（契约 §6.3 / §11.2）：状态迁移由 command_state.COMMAND_TRANSITIONS 约束；
   更高 boot_id 的 device_state 使旧未终态命令 timeout(BOOT_CHANGED)；
   旧 boot 的状态与结果直接丢弃。

草案参数（非安全值，与固件 docs/control 保持同一套草案口径）：
  ONLINE_WINDOW_SECONDS = 60   （§7.2：启动命令只在设备状态新鲜时允许）
  MAX_CMD_STALE_MS      = 60000（§8.5：ref_uptime_ms 允许的最大滞留）
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select, update

from app.core.command_state import try_command_transition
from app.core.database import AsyncSessionLocal
from app.models.models import (
    ActuatorCommand, ActuatorProfile, CommandStatus, Device, DeviceStatus,
)

logger = logging.getLogger(__name__)

# ── 草案参数（与固件 docs/control 同源，硬件确认前不是安全值） ──
ONLINE_WINDOW_SECONDS = 60
MAX_CMD_STALE_MS = 60000
CHANNEL_V1 = "curtain_1"
SCHEMA_VERSION = 1

ACTIONS_V1 = ("move_up", "move_down", "stop", "get_state")
START_ACTIONS = ("move_up", "move_down")          # 启动类：要求新鲜状态
SAFE_ACTIONS = ("stop", "get_state")              # 安全/只读：状态过期仍可尝试
TTL_MIN_MS, TTL_MAX_MS = 1000, 60000

ENVELOPE_MAX_BYTES = 320                          # 契约 §2.2：command ≤320 B（含 \n）

RESULT_STATUS_MAP = {
    "accepted": CommandStatus.ACCEPTED,
    "executing": CommandStatus.EXECUTING,
    "executed": CommandStatus.EXECUTED,
    "rejected": CommandStatus.REJECTED,
    "failed": CommandStatus.FAILED,
}


# ────────────────────────── 设备状态与新鲜度 ──────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def state_age_seconds(profile: Optional[ActuatorProfile]) -> Optional[float]:
    if profile is None or profile.state_received_at is None:
        return None
    return (_now() - profile.state_received_at).total_seconds()


def require_trusted_state(profile: Optional[ActuatorProfile]) -> ActuatorProfile:
    """所有命令的共同前提：存在含 boot_id/session_id/uptime 的可信状态。

    缺任何一项都拒绝（契约禁止填零/猜测），绝不降级发送。
    """
    if profile is None or profile.boot_id is None \
            or not profile.session_id or profile.uptime_ms is None:
        raise HTTPException(
            409, "device_offline: 设备无可信控制状态（缺 device_state 的 "
                 "boot_id/session_id/uptime_ms），先等待设备上线并上报状态")
    return profile


def require_fresh_state_for_start(profile: ActuatorProfile) -> None:
    """启动类命令（move_up/move_down）：状态必须新鲜（§7.2 规则 1/2）。"""
    age = state_age_seconds(profile)
    if age is None or age > ONLINE_WINDOW_SECONDS:
        raise HTTPException(
            409, f"device_offline: 设备状态已过期（{age if age is not None else '无'}s "
                 f"> 窗口 {ONLINE_WINDOW_SECONDS}s），启动命令不入离线队列，"
                 f"先 get_state 或等待 device_state")


async def load_profile(db, device_uuid: str) -> Optional[ActuatorProfile]:
    return await db.get(ActuatorProfile, device_uuid)


async def allocate_command_seq(db, device_uuid: str,
                               profile: ActuatorProfile) -> int:
    """契约 §7.2 规则 6 / §11.2：seq = max(已分配序号, 设备 last_command_seq) + 1。"""
    row = (await db.execute(
        select(ActuatorCommand.command_seq)
        .where(ActuatorCommand.device_id == device_uuid,
               ActuatorCommand.command_seq.isnot(None))
        .order_by(ActuatorCommand.command_seq.desc())
        .limit(1))).scalar_one_or_none()
    allocated = row or 0
    device_hi = profile.last_command_seq or 0
    return max(allocated, device_hi) + 1


# ────────────────────────── 命令信封（契约 §5） ──────────────────────────

def build_command_envelope(*, command_id: str, device_hw_id: str,
                           channel_id: str, action: str,
                           session_id: str, boot_id: int,
                           ttl_ms: int, command_seq: int,
                           ref_uptime_ms: Optional[int]) -> dict:
    """构造扁平命令信封并校验长度。所有代次字段必须来自可信设备状态。

    ref_uptime_ms：§8.5 下行可选字段；云端有可信 uptime 时必须携带（不填零），
    供设备做 COMMAND_STALE 判定。
    """
    if action not in ACTIONS_V1:
        raise HTTPException(400, f"ACTION_UNKNOWN: 未知动作 {action!r}，"
                                 f"v1 仅支持 {ACTIONS_V1}")
    if not (TTL_MIN_MS <= ttl_ms <= TTL_MAX_MS):
        raise HTTPException(400, f"BAD_TTL: ttl_ms 须在 {TTL_MIN_MS}..{TTL_MAX_MS}")
    envelope = {
        "type": "command",
        "schema_version": SCHEMA_VERSION,
        "command_id": command_id,
        "device_id": device_hw_id,
        "channel_id": channel_id,
        "action": action,
        "session_id": session_id,
        "boot_id": boot_id,
        "ttl_ms": ttl_ms,
        "command_seq": command_seq,
    }
    if ref_uptime_ms is not None:
        envelope["ref_uptime_ms"] = ref_uptime_ms
    wire = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    if len(wire.encode("utf-8")) > ENVELOPE_MAX_BYTES:
        raise HTTPException(500, "命令信封超过 320 B（契约 §2.2），拒绝发布")
    return envelope


def envelope_wire(envelope: dict) -> str:
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n"


# ────────────────────────── /up 上行处理（归属校验） ──────────────────────────

def _topic_matches_payload(topic_device_id: str, payload: dict) -> bool:
    """归属第一层：主题设备必须与 payload 自报设备一致（契约 §2）。"""
    return bool(payload.get("device_id")) and payload.get("device_id") == topic_device_id


async def handle_up_message(hardware_id: str, msg_type: str, payload: dict) -> bool:
    """MQTT /up 上 command_result / device_state / device_info 的统一入口。

    返回 True 表示消息已按契约处理（含"合法丢弃"）；False 表示未处理。
    归属不匹配的消息：记录日志、不改任何命令状态（§11.2：跨设备结果拒绝并审计）。
    """
    if not isinstance(payload, dict):
        return False
    if not _topic_matches_payload(hardware_id, payload):
        logger.warning("[CURTAIN-V1] 归属拒绝：主题设备 %r != payload 设备 %r (type=%s)",
                       hardware_id, payload.get("device_id"), msg_type)
        return False

    async with AsyncSessionLocal() as db:
        device = (await db.execute(
            select(Device).where(Device.device_id == hardware_id))).scalar_one_or_none()
        if device is None:
            logger.warning("[CURTAIN-V1] 未注册设备上行：device_id=%r type=%s",
                           hardware_id, msg_type)
            return False
        # 归属第二层：数据库设备存在且 greenhouse 一致性由命令侧再核（第三层）
        device.status = DeviceStatus.ONLINE
        device.last_seen = _now()

        if msg_type == "device_state":
            await _ingest_device_state(db, device, payload)
        elif msg_type == "command_result":
            await _ingest_command_result(db, device, payload)
        elif msg_type == "device_info":
            logger.info("[CURTAIN-V1] device_info device=%s payload_keys=%s",
                        hardware_id, sorted(payload.keys()))
        else:
            await db.commit()
            return False
        await db.commit()
    return True


def _curtain_fields_ok(payload: dict) -> bool:
    """device_state / command_result 的最低字段门槛（不满足按坏帧丢弃）。"""
    return isinstance(payload.get("boot_id"), int) and \
        isinstance(payload.get("state_seq"), int)


async def _expire_inflight_for_boot(db, device, old_boot_id: Optional[int],
                                    reason: str) -> int:
    """旧代次的未终态命令收敛为 timeout（契约 §8.3：更高 boot 使旧命令超时）。"""
    if old_boot_id is None:
        return 0
    res = await db.execute(
        update(ActuatorCommand)
        .where(ActuatorCommand.device_id == device.id,
               ActuatorCommand.boot_id == str(old_boot_id),
               ActuatorCommand.status.in_([
                   CommandStatus.PENDING, CommandStatus.SENT,
                   CommandStatus.ACCEPTED, CommandStatus.EXECUTING,
                   CommandStatus.UNKNOWN]))
        .values(status=CommandStatus.TIMEOUT,
                error_message=f"BOOT_CHANGED: 设备已重启进入 boot 新代次（{reason}）",
                completed_at=_now()))
    return res.rowcount or 0


async def _ingest_device_state(db, device, payload: dict) -> None:
    """契约 §8.3：按 (device_id, boot_id, state_seq) 排序；旧 boot 丢弃；新 boot 收敛旧命令。"""
    if not _curtain_fields_ok(payload):
        logger.warning("[CURTAIN-V1] device_state 缺代次字段，丢弃：device=%s", device.id)
        return
    profile = await db.get(ActuatorProfile, device.id)
    if profile is None:
        profile = ActuatorProfile(device_id=device.id)
        db.add(profile)

    boot_id = payload["boot_id"]
    state_seq = payload["state_seq"]

    if profile.boot_id is not None and boot_id < profile.boot_id:
        logger.warning("[CURTAIN-V1] 旧 boot 状态丢弃：device=%s got=%s cur=%s",
                       device.id, boot_id, profile.boot_id)
        return

    if profile.boot_id is None or boot_id > profile.boot_id:
        old = profile.boot_id
        n = await _expire_inflight_for_boot(db, device, old, f"boot {old}→{boot_id}")
        if n:
            logger.warning("[CURTAIN-V1] 新 boot %s→%s：旧代次 %s 条未终态命令置 timeout/BOOT_CHANGED",
                           old, boot_id, n)
        profile.boot_id = boot_id
        profile.state_seq = -1  # 新代次从新序号开始

    if state_seq <= (profile.state_seq or -1):
        return  # 同 boot 旧 state_seq 忽略（§8.3）

    profile.state_seq = state_seq
    profile.session_id = str(payload.get("session_id") or "")[:12] or None
    profile.last_command_seq = payload.get("last_command_seq") \
        if isinstance(payload.get("last_command_seq"), int) else profile.last_command_seq
    profile.mode = payload.get("mode")
    profile.motion = payload.get("motion")
    profile.fault = payload.get("fault")
    profile.limit_up = bool(payload.get("limit_up"))
    profile.limit_down = bool(payload.get("limit_down"))
    profile.estop = bool(payload.get("estop"))
    profile.position_raw = payload.get("position_raw") \
        if isinstance(payload.get("position_raw"), int) else None
    profile.position_valid = bool(payload.get("position_valid"))
    profile.uptime_ms = payload.get("uptime_ms") \
        if isinstance(payload.get("uptime_ms"), int) else profile.uptime_ms
    profile.state_received_at = _now()


async def _ingest_command_result(db, device, payload: dict) -> None:
    """契约 §11.2：command_id + device_id 匹配；校验归属、代次与会话；乱序不回退。

    归属第三层（命令 ↔ 设备）：查询按 device_id 过滤，跨设备结果根本命中不了；
    命中后再核命令大棚与设备大棚一致、boot/session 与命令代次一致。
    """
    if not _curtain_fields_ok(payload):
        logger.warning("[CURTAIN-V1] command_result 缺代次字段，丢弃：device=%s", device.id)
        return
    command_id = payload.get("command_id")
    if not command_id or not isinstance(command_id, str):
        logger.warning("[CURTAIN-V1] command_result 缺 command_id：device=%s", device.id)
        return

    cmd = (await db.execute(
        select(ActuatorCommand)
        .where(ActuatorCommand.command_id == command_id,
               ActuatorCommand.device_id == device.id))).scalar_one_or_none()
    if cmd is None:
        # 要么未知命令、要么跨设备重放：都不改状态，只记录
        logger.warning("[CURTAIN-V1] 结果归属失败（未命中 command_id+device_id）："
                       "device=%s command_id=%r", device.id, command_id)
        return
    if cmd.greenhouse_id and device.greenhouse_id \
            and cmd.greenhouse_id != device.greenhouse_id:
        logger.warning("[CURTAIN-V1] 结果归属失败（命令大棚 %s != 设备大棚 %s）：command=%s",
                       cmd.greenhouse_id, device.greenhouse_id, cmd.command_id)
        return

    result_boot = payload["boot_id"]
    result_session = str(payload.get("session_id") or "")

    # 代次校验：结果来自另一代 boot → 不改结论（与夜间 HTTP 语义一致）
    if cmd.boot_id and str(result_boot) != str(cmd.boot_id):
        cmd.result_boot_id = str(result_boot)
        cmd.error_message = (f"结果代次不匹配：命令 boot={cmd.boot_id}，"
                             f"结果 boot={result_boot}")
        logger.warning("[CURTAIN-V1] %s", cmd.error_message)
        return

    # 会话校验（契约 §3：session_id 用于拒绝旧会话命令；结果同理必须来自命令会话）
    if cmd.session_id and result_session and result_session != cmd.session_id:
        cmd.result_session_id = result_session[:64]
        cmd.error_message = (f"结果会话不匹配：命令 session={cmd.session_id}，"
                             f"结果 session={result_session}")
        logger.warning("[CURTAIN-V1] %s", cmd.error_message)
        return

    target = RESULT_STATUS_MAP.get(payload.get("status"))
    if target is None:
        logger.warning("[CURTAIN-V1] 未知结果状态 %r：command=%s",
                       payload.get("status"), cmd.command_id)
        return
    if not try_command_transition(cmd.status, target):
        logger.info("[CURTAIN-V1] 非法/重复结果流转 %s→%s 忽略：command=%s",
                    cmd.status, target.value, cmd.command_id)
        return

    cmd.status = target
    motion = payload.get("motion")
    if isinstance(motion, str):
        cmd.actual_state = {
            "motion": motion,
            "motion_confirmed": bool(payload.get("motion_confirmed")),
            "limit_up": bool(payload.get("limit_up")),
            "limit_down": bool(payload.get("limit_down")),
            "estop": bool(payload.get("estop")),
            "state_seq": payload["state_seq"],
            "uptime_ms": payload.get("uptime_ms"),
        }
    if payload.get("error_code"):
        cmd.error_message = f"error_code={payload['error_code']}"
    if target in (CommandStatus.EXECUTED, CommandStatus.REJECTED, CommandStatus.FAILED):
        cmd.completed_at = _now()
    if target == CommandStatus.ACCEPTED and cmd.accepted_at is None:
        cmd.accepted_at = _now()
