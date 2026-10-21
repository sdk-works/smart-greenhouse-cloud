"""MQTT 设备身份 / 主题约束（M1）。

**本模块只记录约束与提供校验，不修改 EMQX 配置、不建立 ACL、不下发任何东西。**
真正的强制点在 EMQX 侧（见 `docs/mqtt-topic-policy.md`），本模块负责在应用层
再做一次校验，尤其保证"控制下发必须校验设备归属"。

主题命名空间（与 mqtt_service 现状一致）：
    上行  agri/robot/{robot_id}/up             遥测/心跳/状态/图片通知/配置回执
    上行  agri/device/{device_id}/up           固定设备（DTU）上行
    下行  agri/robot/{robot_id}/command        机器人命令
    下行  agri/robot/{robot_id}/config         机器人点位配置（retain）
    下行  agri/device/{device_id}/command      固定设备命令
    下行  agri/device/{device_id}/ack          固定设备上行回执（stored）

约束（记录，供 EMQX 配置变更提案使用，本次不改 EMQX）：
1. 客户端连接必须使用 `device_<robot_id>` 形式的 client_id 与用户名；
   密码为每设备独立凭据，不得共用。
2. 每个设备只允许 pub 自己 `/up` 主题、sub 自己 `/command` 与 `/config` 主题，
   严禁 `#`、`+` 跨设备通配订阅，严禁订阅其它设备的 `/up`。
3. 云端只 sub 已注册设备的 `/up`；未注册设备一律丢弃并记录。
4. 控制下发前必须在应用层校验目标设备归属当前操作者的公司。
"""
from typing import Optional

from fastapi import HTTPException

from app.core.company_scope import can_access_company, is_platform

UPLINK_TOPIC = "agri/{kind}/{device_id}/up"
ROBOT_COMMAND_TOPIC = "agri/robot/{device_id}/command"
ROBOT_CONFIG_TOPIC = "agri/robot/{device_id}/config"
DEVICE_COMMAND_TOPIC = "agri/device/{device_id}/command"
DEVICE_ACK_TOPIC = "agri/device/{device_id}/ack"

# 约束 1：允许的客户端身份形态
ALLOWED_CLIENT_ID_PREFIX = "device_"

# 约束 2：云端允许订阅的上行主题白名单（只列形态，不含设备 id）
ALLOWED_UPLINK_TOPIC_TEMPLATES = (
    UPLINK_TOPIC.format(kind="robot", device_id="{device_id}"),
    UPLINK_TOPIC.format(kind="device", device_id="{device_id}"),
)


def uplink_topic(device_id: str, kind: str = "robot") -> str:
    return f"agri/{kind}/{device_id}/up"


def command_topic(device_id: str, kind: str = "robot") -> str:
    return f"agri/{kind}/{device_id}/command"


def validate_uplink_topic(topic: str, device_id: str) -> bool:
    """上行主题必须严格等于该设备的 /up 主题；拒绝通配与跨设备主题。"""
    if not topic or not device_id:
        return False
    if "#" in topic or "+" in topic:
        return False
    return topic in (
        uplink_topic(device_id, "robot"),
        uplink_topic(device_id, "device"),
    )


def validate_client_id(client_id: str, device_id: str) -> bool:
    """约束 1 的形态校验：client_id == device_<device_id>。"""
    return bool(client_id) and client_id == f"{ALLOWED_CLIENT_ID_PREFIX}{device_id}"


def assert_can_control(user, greenhouse_company_id: Optional[str], device_id: str) -> None:
    """控制下发前的设备归属校验（约束 4）。

    业务用户只能控制自己公司名下的设备；平台运营可跨公司，但调用方需自行落审计。
    """
    if is_platform(user):
        return
    if not can_access_company(user, greenhouse_company_id):
        raise HTTPException(
            status_code=403,
            detail=f"设备 {device_id} 不属于你所在公司，无法下发控制指令",
        )
