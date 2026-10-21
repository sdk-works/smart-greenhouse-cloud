"""
main.py - FastAPI 应用入口
修复: 使用 settings.get_cors_origins() 获取正确解析的 CORS 列表
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import init_db
from app.api import api_router
from app.mqtt.mqtt_service import mqtt_service
from app.api.ws import redis_listener

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 50)
    logger.info("  Smart Greenhouse Platform 启动中...")
    logger.info("=" * 50)

    await init_db()
    logger.info("✅ 数据库初始化完成")

    # M3：启动时把长时间停留在 running 的任务/批次做兜底标记（只标记，不假装知道结果）
    try:
        from app.core.database import AsyncSessionLocal as _SessionLocal
        from app.core.recovery import recover_all as _recover_all
        async with _SessionLocal() as _db:
            res = await _recover_all(_db)
        if res["tasks_recovered"] or res["batches_recovered"]:
            logger.warning(f"⚠️ 启动恢复：兜底标记 {res}")
    except Exception as e:
        logger.error(f"[RECOVERY] 启动恢复失败（不影响服务启动）: {e}")

    # ── 夜间只读实例守卫 ──────────────────────────────────────
    # NIGHTLY_READONLY=1 时本实例不得接入 MQTT、不得启动任何后台任务，
    # 仅用于 worktree 内起临时实例跑接口/测试。
    readonly = os.getenv("NIGHTLY_READONLY") == "1"
    mqtt_task = None
    ws_task = None

    if readonly:
        logger.warning("⚠️ NIGHTLY_READONLY=1：已跳过 MQTT 连接与 Redis/WS 广播器启动（无任何后台任务）")
    else:
        # 单 worker 模式：MQTT 和 WebSocket 在同一进程共享状态
        mqtt_task = asyncio.create_task(mqtt_service.connect())
        ws_task   = asyncio.create_task(redis_listener())
        logger.info("✅ MQTT 服务和 WebSocket 广播器已启动")

    yield

    # 停止服务
    if mqtt_task is not None:
        mqtt_task.cancel()
    if ws_task is not None:
        ws_task.cancel()
    if not readonly:
        await mqtt_service.disconnect()
    logger.info("👋 平台已停止")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="智慧农业巡检机器人物联网云端平台",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS（使用解析方法获取列表）
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# 静态文件服务（图片上传）
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

app.include_router(api_router, prefix=settings.API_PREFIX)


@app.get("/health")
async def health():
    return {
        "status":  "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
    }


if __name__ == "__main__":
    import uvicorn
    # 必须单 worker，多 worker 会导致 MQTT/WebSocket 状态不共享
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        workers=1,
        reload=settings.DEBUG
    )
