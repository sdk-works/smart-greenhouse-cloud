import logging
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.models import Device
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

async def update_device_status(db: AsyncSession, device_id_str: str, payload: dict):
    """更新设备在线状态 - 根据 device_id 字符串查询"""
    try:
        # 查找设备（通过 device_id 字符串）
        result = await db.execute(
            select(Device).where(Device.device_id == device_id_str)
        )
        device = result.scalar_one_or_none()
        
        if not device:
            logger.warning(f"设备 {device_id_str} 不存在，无法更新状态")
            return
        
        is_online = payload.get("online", False)
        device.status = "online" if is_online else "offline"
        device.last_seen = datetime.utcnow()
        if payload.get("ip_address"):
            device.ip_address = payload.get("ip_address")
        
        await db.commit()
        
        # 更新 Redis 缓存
        try:
            redis = await get_redis()
            await redis.set(f"device:{device.id}:status", device.status, ex=120)
            await redis.set(f"device:online:{device.device_id}", "1" if is_online else "0", ex=120)
        except Exception as redis_err:
            logger.warning(f"Redis 更新失败: {redis_err}")
        
        logger.info(f"Device {device_id_str} status updated to {device.status}")
            
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to update device status: {e}")
        raise
