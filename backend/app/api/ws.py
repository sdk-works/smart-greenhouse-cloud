"""
ws.py - WebSocket 实时推送服务
修复:
  [#8] redis_listener finally 块 NameError（pubsub/r 未定义时的安全关闭）
  [#9] ConnectionManager.broadcast 使用列表副本迭代，防止并发修改
  [#10] WebSocket 收到非 ping 消息时正常处理不卡住
"""
import asyncio
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import redis.asyncio as aioredis
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active: list = []  # Python 3.10 兼容写法（不用 list[WebSocket]）
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self.active.append(ws)
        logger.info(f"[WS] 客户端连接，当前连接数: {len(self.active)}")

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)
        logger.info(f"[WS] 客户端断开，当前连接数: {len(self.active)}")

    async def broadcast(self, message: str):
        """
        广播消息给所有连接的客户端。
        使用列表副本迭代，防止并发修改列表导致 RuntimeError。
        """
        if not self.active:
            return
        # 创建副本防止迭代中修改
        async with self._lock:
            clients = list(self.active)

        dead = []
        for ws in clients:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        # 清理断开的连接
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self.active:
                        self.active.remove(ws)


manager = ConnectionManager()


async def redis_listener():
    """
    持续监听 Redis Pub/Sub，将遥测消息广播给所有 WebSocket 客户端。
    修复：finally 块变量安全处理，pubsub/r 可能未定义时不报 NameError。
    """
    while True:
        r = None
        pubsub = None
        try:
            r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe("telemetry_updates")
            logger.info("[WS] Redis Pub/Sub 监听器已启动")

            async for msg in pubsub.listen():
                if msg["type"] == "message":
                    await manager.broadcast(msg["data"])

        except asyncio.CancelledError:
            logger.info("[WS] Redis 监听器收到停止信号")
            break
        except Exception as e:
            logger.error(f"[WS] Redis 监听器异常: {e}，3s 后重启...")
            await asyncio.sleep(3)
        finally:
            # 安全关闭（变量可能未定义）
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
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点：接收连接，等待断开"""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            # 其他消息静默忽略（不卡住）
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except Exception as e:
        logger.debug(f"[WS] WebSocket 异常断开: {e}")
        await manager.disconnect(websocket)
