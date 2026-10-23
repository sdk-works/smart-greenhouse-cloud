"""
ws.py - WebSocket 实时推送服务

历史修复：
  [#8] redis_listener finally 块 NameError（pubsub/r 未定义时的安全关闭）
  [#9] ConnectionManager.broadcast 使用列表副本迭代，防止并发修改
  [#10] WebSocket 收到非 ping 消息时正常处理不卡住

2026-10-22 安全修复（M6a）：
  [1] **连接必须带有效 token**。原来 /realtime 完全不校验身份，
      任何人打开页面（甚至直接连一下）都能收到全平台实时遥测。
      浏览器 WebSocket 无法自定义 Header，所以用 `?token=`（前端已按此传参）。
  [2] **频道按公司隔离**。原来订阅裸频道 `telemetry_updates` 并广播给所有连接，
      A 公司浏览器会收到 B 公司的遥测。现在发布侧写 `telemetry_updates:<company>`，
      监听侧按频道后缀把消息只投给同一公司的连接。
  [3] 未归属（unknown 公司）的消息一律**不推送**，只记 warning —— 宁可这条实时
      消息丢，也不能推给错的公司；历史数据仍可在 REST 接口按权限查到。
"""
import asyncio
import logging
import os

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
import redis.asyncio as aioredis

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis import (REALTIME_CHANNEL_BASES, UNKNOWN_COMPANY,
                            get_redis)
from app.models.models import User

router = APIRouter()
logger = logging.getLogger(__name__)

# 与 HTTP 侧保持一致：token 在 Redis 里是滑动过期，WS 连上也算一次活跃
TOKEN_TTL_SECONDS = 8 * 60 * 60

# 已建立连接的权限复查间隔（独立定时任务，不依赖客户端消息）
WS_AUTH_RECHECK_SECONDS = int(os.environ.get("WS_AUTH_RECHECK_SECONDS", "30"))


class ConnectionManager:
    """按公司分组的连接池。广播时必须给公司，不存在"发给所有人"的接口。"""

    def __init__(self):
        self.active = {}          # WebSocket -> company_id
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, company_id: str):
        await ws.accept()
        async with self._lock:
            self.active[ws] = company_id
        logger.info(f"[WS] 客户端连接 company={company_id}，当前连接数: {len(self.active)}")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self.active.pop(ws, None)
        logger.info(f"[WS] 客户端断开，当前连接数: {len(self.active)}")

    async def broadcast(self, company_id: str, message: str):
        """只发给同一公司的连接；跨公司一条都不发。"""
        async with self._lock:
            targets = [w for w, c in self.active.items() if c == company_id]

        dead = []
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self.active.pop(ws, None)

    def count(self, company_id: str = None) -> int:
        if company_id is None:
            return len(self.active)
        return sum(1 for c in self.active.values() if c == company_id)


manager = ConnectionManager()


def channel_company(channel: str) -> str:
    """从 `<base>:<company_id>` 取公司；未归属/格式不对返回空串（调用方据此丢弃）。

    单独抽成函数是为了能被单测直接钉住：隔离失效最典型的方式就是这里"取不到
    公司却仍然广播给所有人"。空串必须等价于"不推给任何人"。
    """
    base, _, company_id = channel.partition(":")
    if not company_id or company_id == UNKNOWN_COMPANY:
        return ""
    return company_id


async def _resolve_user(token: str):
    """token → User。与 app/api/auth.get_current_user 同一份凭据（Redis 里存 user_id）。"""
    if not token:
        return None
    redis = await get_redis()
    user_id = await redis.get(f"auth:token:{token}")
    if not user_id:
        return None
    await redis.expire(f"auth:token:{token}", TOKEN_TTL_SECONDS)
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
    if not user or not user.is_active:
        return None
    return user


async def _session_still_valid(token: str, user_id, company_id) -> bool:
    """复查：token 未过期/未吊销、用户仍启用、公司归属未变。任一不满足即失效。"""
    redis = await get_redis()
    cur = await redis.get(f"auth:token:{token}")
    if not cur or str(cur) != str(user_id):
        return False
    await redis.expire(f"auth:token:{token}", TOKEN_TTL_SECONDS)
    async with AsyncSessionLocal() as db:
        u = await db.get(User, user_id)
        if not u or not u.is_active:
            return False
        if str(getattr(u, "company_id", None)) != str(company_id):
            return False
    return True


async def redis_listener():
    """监听所有公司的实时频道，按频道后缀分流到对应公司的连接。

    用 psubscribe 而不是为每个公司开一个监听器：连接数远比公司数多，
    而公司数增长时频道数增长是可控的；一个监听器 + 内存路由也避免 N 个
    Redis 连接各占一个 pubsub。
    """
    while True:
        r = None
        pubsub = None
        try:
            r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            for base in REALTIME_CHANNEL_BASES:
                await pubsub.psubscribe(base + ":*")
            logger.info("[WS] Redis Pub/Sub 监听器已启动（按公司频道分流）")

            async for msg in pubsub.listen():
                if msg.get("type") != "pmessage":
                    continue
                channel = msg.get("channel") or ""
                company_id = channel_company(channel)
                if not company_id:
                    logger.warning(f"[WS] 丢弃未归属频道的消息: {channel}")
                    continue
                await manager.broadcast(company_id, msg.get("data") or "")

        except asyncio.CancelledError:
            logger.info("[WS] Redis 监听器收到停止信号")
            break
        except Exception as e:
            logger.error(f"[WS] Redis 监听器异常: {e}，3s 后重启...")
            await asyncio.sleep(3)
        finally:
            if pubsub is not None:
                try:
                    await pubsub.close()
                except Exception:
                    pass
            if r is not None:
                try:
                    await r.close()
                except Exception:
                    pass


@router.websocket("/realtime")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(None)):
    """WebSocket 端点：必须带 token，且只能收到本公司消息。

    先 accept 再 close(4401)：客户端能读到具体关闭码，前端可据此跳登录页；
    若在 accept 前 close，浏览器只会看到一次握手失败，无法区分"token 过期"。
    """
    user = await _resolve_user(token)
    if user is None:
        await websocket.accept()
        await websocket.close(code=4401)      # 未认证 / token 无效
        logger.info("[WS] 拒绝未认证连接")
        return

    company_id = getattr(user, "company_id", None)
    if not company_id:
        await websocket.accept()
        await websocket.close(code=4403)      # 账号未绑定公司：没有可推送的数据范围
        logger.warning(f"[WS] 账号无公司归属，拒绝 user={getattr(user, 'username', '?')}")
        return

    user_id = user.id
    await manager.connect(websocket, company_id)

    async def _recheck():
        """独立定时复查：空闲连接也会被检查，不看客户端是否发消息。"""
        while True:
            await asyncio.sleep(WS_AUTH_RECHECK_SECONDS)
            try:
                ok = await _session_still_valid(token, user_id, company_id)
            except Exception as e:
                # 查询失败也不能无限沿用旧权限：按失效处理（fail-closed）
                logger.error(f"[WS] 权限复查失败，按失效断开 user={user_id}: {e}")
                ok = False
            if not ok:
                logger.info(f"[WS] 会话已失效，断开 user={user_id} company={company_id}")
                try:
                    await websocket.close(code=4401)
                except Exception:
                    pass
                return

    recheck_task = asyncio.create_task(_recheck())
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            # 其他消息静默忽略；权限复查由独立任务保证
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"[WS] WebSocket 异常断开: {e}")
    finally:
        recheck_task.cancel()
        try:
            await recheck_task
        except (asyncio.CancelledError, Exception):
            pass
        await manager.disconnect(websocket)
