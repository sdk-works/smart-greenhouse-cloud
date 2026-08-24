import logging
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.models import Telemetry, Device

logger = logging.getLogger(__name__)

def parse_timestamp(ts_str: str) -> datetime:
    """解析 ISO 8601 时间戳，处理带 Z 后缀的情况"""
    if not ts_str:
        return datetime.utcnow()
    
    try:
        # 处理带 Z 后缀的格式 (2026-04-01T10:54:00Z)
        if ts_str.endswith('Z'):
            ts_str = ts_str[:-1] + '+00:00'
        
        # 尝试解析
        return datetime.fromisoformat(ts_str)
    except ValueError:
        # 如果失败，尝试其他常见格式
        try:
            return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            logger.warning(f"无法解析时间戳: {ts_str}，使用当前时间")
            return datetime.utcnow()

async def process_telemetry(db: AsyncSession, device_id_str: str, payload: dict):
    """处理设备遥测数据 - 根据 device_id 字符串查询 UUID"""
    try:
        # 首先查询设备的 UUID（主键）
        result = await db.execute(
            select(Device).where(Device.device_id == device_id_str)
        )
        device = result.scalar_one_or_none()
        
        if not device:
            logger.warning(f"设备 {device_id_str} 不存在，跳过遥测数据")
            return
        
        # 解析数据
        data = payload.get("data", {})
        robot_status = payload.get("robot_status", {})
        position = robot_status.get("position", {})
        
        # 解析时间戳
        timestamp = parse_timestamp(payload.get("timestamp"))
        
        # 创建遥测记录，使用 device.id (UUID) 作为外键
        telemetry = Telemetry(
            device_id=device.id,  # 使用 UUID 主键，不是 device_id 字符串
            greenhouse_id=payload.get("greenhouse_id") or device.greenhouse_id,
            timestamp=timestamp,
            temperature=data.get("temperature"),
            humidity=data.get("humidity"),
            soil_moisture=data.get("soil_moisture"),
            light_intensity=data.get("light_intensity") or data.get("illumination"),  # 兼容 illumination 字段
            co2_level=data.get("co2"),
            ph_value=data.get("ph") or data.get("ph_value"),  # 兼容 ph_value 字段
            battery_level=robot_status.get("battery"),
            position_x=position.get("x"),
            position_y=position.get("y"),
            robot_status=robot_status,
            raw_data=payload
        )
        
        db.add(telemetry)
        await db.commit()
        logger.info(f"Telemetry saved for device {device_id_str} (UUID: {device.id})")
        
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to process telemetry: {e}")
        raise
