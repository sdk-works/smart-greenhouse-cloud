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

    # 单 worker 模式：MQTT 和 WebSocket 在同一进程共享状态
    mqtt_task = asyncio.create_task(mqtt_service.connect())
    ws_task   = asyncio.create_task(redis_listener())
    logger.info("✅ MQTT 服务和 WebSocket 广播器已启动")

    yield

    # 停止服务
    mqtt_task.cancel()
    ws_task.cancel()
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
