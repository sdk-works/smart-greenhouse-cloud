import json
from typing import Optional
import redis.asyncio as aioredis
from app.core.config import settings

_redis_client = None


async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_client


async def close_redis():
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None


# ===== 实时推送：只给“消息频道”加公司作用域，缓存键行为保持不变 =====
UNKNOWN_COMPANY = "unknown"
REALTIME_CHANNEL_BASES = ("telemetry_updates", "config_ack_updates")


def realtime_channel(base: str, company_id: Optional[str]) -> str:
    """构造按公司隔离的频道名。company_id 缺失落 unknown，监听侧会丢弃。"""
    return f"{base}:{company_id or UNKNOWN_COMPANY}"


class CacheService:
    def __init__(self, redis: aioredis.Redis, company_id: Optional[str] = None):
        self.redis = redis
        # 仅用于 publish 的频道作用域；不参与 set/get 的键构造
        self.company_id = company_id

    async def set(self, key: str, value, expire: int = 3600):
        await self.redis.set(key, json.dumps(value, default=str), ex=expire)

    async def get(self, key: str):
        data = await self.redis.get(key)
        return json.loads(data) if data else None

    async def delete(self, key: str):
        await self.redis.delete(key)

    async def set_device_online(self, device_id: str, online: bool):
        await self.redis.set(f"device:online:{device_id}", "1" if online else "0", ex=120)

    async def get_device_online(self, device_id: str) -> bool:
        val = await self.redis.get(f"device:online:{device_id}")
        return val == "1" if val else False

    async def set_device_status(self, device_id: str, status: dict):
        await self.set(f"device:status:{device_id}", status, expire=300)

    async def get_device_status(self, device_id: str):
        return await self.get(f"device:status:{device_id}")

    async def publish(self, channel: str, message: dict):
        """发到本实例公司作用域的频道；不再发裸频道（会串公司）。"""
        await self.redis.publish(
            realtime_channel(channel, self.company_id),
            json.dumps(message, default=str),
        )
