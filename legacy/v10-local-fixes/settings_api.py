"""
settings_api.py - 系统配置管理接口
提供:
  - 大棚配置（地图点位、尺寸）
  - 机器人配置（巡检点位、行为）
  - MQTT 连接配置（运行时动态修改）
  - 系统全局配置
"""
import json
import os
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db
from app.core.config import settings
from app.models.models import Greenhouse, Device, DeviceType

router = APIRouter()


# ── Pydantic 模型 ─────────────────────────────────────────────

class WaypointConfig(BaseModel):
    id: int
    x: float
    y: float
    label: Optional[str] = None
    actions: List[str] = []


class GreenhouseMapConfig(BaseModel):
    width: float = 100.0
    height: float = 60.0
    waypoints: List[WaypointConfig] = []
    sensor_positions: List[Dict] = []


class RobotConfig(BaseModel):
    patrol_speed: float = 0.5          # m/s
    capture_interval: int = 5          # 每隔几个点位拍照
    return_battery_threshold: int = 20 # 电量低于此值返回充电
    auto_patrol: bool = False
    patrol_waypoints: List[WaypointConfig] = []


class MQTTConfig(BaseModel):
    broker_host: str
    broker_port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    topic_prefix: str = "agri"
    keepalive: int = 60


class SystemConfig(BaseModel):
    platform_name: str = "Smart Greenhouse Platform"
    data_retention_days: int = 30
    alert_temperature_max: float = 35.0
    alert_temperature_min: float = 10.0
    alert_humidity_max: float = 90.0
    alert_co2_max: float = 1000.0
    upload_max_size_mb: int = 20


# ── 配置文件路径 ──────────────────────────────────────────────
CONFIG_FILE = "/opt/greenhouse/system_config.json"


def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(data: dict):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 大棚地图配置 ──────────────────────────────────────────────

@router.get("/greenhouse/{greenhouse_id}/map", response_model=GreenhouseMapConfig)
async def get_greenhouse_map(greenhouse_id: str, db: AsyncSession = Depends(get_db)):
    """获取大棚地图配置（巡检点位、传感器位置）"""
    result = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
    gh = result.scalar_one_or_none()
    if not gh:
        raise HTTPException(status_code=404, detail="大棚不存在")

    map_data = gh.map_data or {}
    return GreenhouseMapConfig(
        width=map_data.get("width", 100.0),
        height=map_data.get("height", 60.0),
        waypoints=[WaypointConfig(**w) for w in map_data.get("waypoints", [])],
        sensor_positions=map_data.get("sensor_positions", []),
    )


@router.put("/greenhouse/{greenhouse_id}/map")
async def update_greenhouse_map(
    greenhouse_id: str,
    config: GreenhouseMapConfig,
    db: AsyncSession = Depends(get_db),
):
    """更新大棚地图配置（可视化拖拽后保存）"""
    result = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
    gh = result.scalar_one_or_none()
    if not gh:
        raise HTTPException(status_code=404, detail="大棚不存在")

    gh.map_data = {
        "width": config.width,
        "height": config.height,
        "waypoints": [w.model_dump() for w in config.waypoints],
        "sensor_positions": config.sensor_positions,
    }
    await db.commit()
    return {"success": True, "message": "地图配置已保存"}


@router.post("/greenhouse/{greenhouse_id}/map/waypoints")
async def add_waypoint(
    greenhouse_id: str,
    waypoint: WaypointConfig,
    db: AsyncSession = Depends(get_db),
):
    """添加单个巡检点位"""
    result = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
    gh = result.scalar_one_or_none()
    if not gh:
        raise HTTPException(status_code=404, detail="大棚不存在")

    map_data = gh.map_data or {"width": 100, "height": 60, "waypoints": []}
    waypoints = map_data.get("waypoints", [])
    # 自动分配 ID
    max_id = max((w.get("id", 0) for w in waypoints), default=0)
    wp = waypoint.model_dump()
    wp["id"] = max_id + 1
    waypoints.append(wp)
    map_data["waypoints"] = waypoints
    gh.map_data = map_data
    await db.commit()
    return {"success": True, "waypoint": wp, "total": len(waypoints)}


@router.delete("/greenhouse/{greenhouse_id}/map/waypoints/{waypoint_id}")
async def delete_waypoint(
    greenhouse_id: str,
    waypoint_id: int,
    db: AsyncSession = Depends(get_db),
):
    """删除指定巡检点位"""
    result = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
    gh = result.scalar_one_or_none()
    if not gh:
        raise HTTPException(status_code=404, detail="大棚不存在")

    map_data = gh.map_data or {}
    waypoints = [w for w in map_data.get("waypoints", []) if w.get("id") != waypoint_id]
    map_data["waypoints"] = waypoints
    gh.map_data = map_data
    await db.commit()
    return {"success": True, "remaining": len(waypoints)}


# ── 机器人配置 ────────────────────────────────────────────────

@router.get("/robot/{device_id}/config", response_model=RobotConfig)
async def get_robot_config(device_id: str, db: AsyncSession = Depends(get_db)):
    """获取机器人巡检配置"""
    result = await db.execute(select(Device).where(Device.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device or device.device_type != DeviceType.ROBOT:
        raise HTTPException(status_code=404, detail="机器人设备不存在")

    cfg = device.config or {}
    return RobotConfig(**cfg) if cfg else RobotConfig()


@router.put("/robot/{device_id}/config")
async def update_robot_config(
    device_id: str,
    config: RobotConfig,
    db: AsyncSession = Depends(get_db),
):
    """更新机器人配置"""
    result = await db.execute(select(Device).where(Device.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device or device.device_type != DeviceType.ROBOT:
        raise HTTPException(status_code=404, detail="机器人设备不存在")

    device.config = config.model_dump()
    await db.commit()
    return {"success": True, "message": "机器人配置已保存"}


@router.post("/robot/{device_id}/assign-greenhouse/{greenhouse_id}")
async def assign_robot_to_greenhouse(
    device_id: str,
    greenhouse_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    将机器人分配到大棚（一个机器人可服务多个大棚）
    配置机器人使用该大棚的巡检点位
    """
    dev_result = await db.execute(select(Device).where(Device.device_id == device_id))
    device = dev_result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    gh_result = await db.execute(select(Greenhouse).where(Greenhouse.id == greenhouse_id))
    gh = gh_result.scalar_one_or_none()
    if not gh:
        raise HTTPException(status_code=404, detail="大棚不存在")

    # 将机器人的当前大棚设为指定大棚
    device.greenhouse_id = greenhouse_id

    # 如果大棚有配置巡检点位，自动同步到机器人配置
    if gh.map_data and gh.map_data.get("waypoints"):
        cfg = device.config or {}
        cfg["patrol_waypoints"] = gh.map_data["waypoints"]
        device.config = cfg

    await db.commit()
    return {
        "success": True,
        "message": f"机器人 {device_id} 已分配到 {gh.name}",
        "waypoints_synced": len(gh.map_data.get("waypoints", [])) if gh.map_data else 0,
    }


# ── MQTT 配置 ─────────────────────────────────────────────────

@router.get("/mqtt")
async def get_mqtt_config():
    """获取当前 MQTT 配置（密码脱敏）"""
    return {
        "broker_host":  settings.MQTT_BROKER_HOST,
        "broker_port":  settings.MQTT_BROKER_PORT,
        "username":     settings.MQTT_USERNAME,
        "password":     "***" if settings.MQTT_PASSWORD else "",
        "topic_prefix": settings.MQTT_TOPIC_PREFIX,
        "client_id":    settings.MQTT_CLIENT_ID,
    }


@router.put("/mqtt")
async def update_mqtt_config(config: MQTTConfig):
    """
    更新 MQTT 配置（写入 .env 文件，需重启服务生效）
    注意：重启后才会连接新的 Broker
    """
    env_path = "/opt/greenhouse/.env"
    lines = []

    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()

    updates = {
        "MQTT_BROKER_HOST": config.broker_host,
        "MQTT_BROKER_PORT": str(config.broker_port),
        "MQTT_TOPIC_PREFIX": config.topic_prefix,
    }
    if config.username:
        updates["MQTT_USERNAME"] = config.username
    if config.password and config.password != "***":
        updates["MQTT_PASSWORD"] = config.password

    # 更新已有行
    new_lines = []
    updated_keys = set()
    for line in lines:
        key = line.split("=")[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    # 追加新增的 key
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")

    try:
        with open(env_path, "w") as f:
            f.writelines(new_lines)
        return {
            "success": True,
            "message": "MQTT 配置已保存，请重启服务使配置生效",
            "restart_command": "sudo systemctl restart greenhouse",
        }
    except PermissionError:
        raise HTTPException(status_code=500, detail="无权限写入配置文件")


# ── 系统全局配置 ──────────────────────────────────────────────

@router.get("/system", response_model=SystemConfig)
async def get_system_config():
    """获取系统全局配置（告警阈值等）"""
    stored = load_config().get("system", {})
    return SystemConfig(**stored) if stored else SystemConfig()


@router.put("/system")
async def update_system_config(config: SystemConfig):
    """更新系统全局配置"""
    cfg = load_config()
    cfg["system"] = config.model_dump()
    save_config(cfg)
    return {"success": True, "message": "系统配置已保存"}


@router.get("/overview")
async def get_all_config(db: AsyncSession = Depends(get_db)):
    """配置总览（用于配置中心首页）"""
    gh_count = (await db.execute(
        select(Greenhouse).where(Greenhouse.is_active == True)
    )).scalars().all()
    robot_count = (await db.execute(
        select(Device).where(Device.device_type == DeviceType.ROBOT)
    )).scalars().all()

    return {
        "mqtt": {
            "broker_host": settings.MQTT_BROKER_HOST,
            "broker_port": settings.MQTT_BROKER_PORT,
            "username":    settings.MQTT_USERNAME,
            "topic_prefix":settings.MQTT_TOPIC_PREFIX,
        },
        "greenhouses": len(gh_count),
        "robots":      len(robot_count),
        "system":      load_config().get("system", SystemConfig().model_dump()),
    }
