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


UNKNOWN_COMPANY = "unknown"


class CacheService:
    """缓存服务。

    M1：所有缓存键统一加 `c:<company_id>:` 前缀，保证不同公司的缓存互不覆盖。
    company_id 缺失时落到 `c:unknown:` —— 宁可不命中，也不能让 A 公司读到 B 公司的缓存。
    """

    def __init__(self, redis: aioredis.Redis, company_id: Optional[str] = None):
        self.redis = redis
        self.company_id = company_id or UNKNOWN_COMPANY

    # ── 键构造 ───────────────────────────────────────────────
    def prefix(self) -> str:
        return f"c:{self.company_id}"

    def make_key(self, key: str) -> str:
        return f"c:{self.company_id}:{key}"

    # ── 基础读写 ─────────────────────────────────────────────
    async def set(self, key: str, value, expire: int = 3600):
        await self.redis.set(self.make_key(key), json.dumps(value, default=str), ex=expire)

    async def get(self, key: str):
        data = await self.redis.get(self.make_key(key))
        return json.loads(data) if data else None

    async def delete(self, key: str):
        await self.redis.delete(self.make_key(key))

    async def set_device_online(self, device_id: str, online: bool):
        await self.redis.set(
            self.make_key(f"device:online:{device_id}"), "1" if online else "0", ex=120
        )

    async def get_device_online(self, device_id: str) -> bool:
        val = await self.redis.get(self.make_key(f"device:online:{device_id}"))
        return val == "1" if val else False

    async def set_device_status(self, device_id: str, status: dict):
        await self.set(f"device:status:{device_id}", status, expire=300)

    async def get_device_status(self, device_id: str):
        return await self.get(f"device:status:{device_id}")

    async def publish(self, channel: str, message: dict):
        await self.redis.publish(channel, json.dumps(message, default=str))
