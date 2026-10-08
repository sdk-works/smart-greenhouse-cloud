"""
devices.py — 设备管理 API
v6 新增：
  - 注册设备后自动触发 MQTT 动态订阅
  - 返回完整的 MQTT topic 信息
  - 支持批量注册
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional

from app.core.database import get_db
from app.core.redis import CacheService, get_redis
from app.models.models import Device, DeviceLog, Greenhouse
from app.schemas.schemas import (
    DeviceCreate, DeviceUpdate, DeviceResponse,
    DeviceCommand, DeviceLogResponse
)
from app.mqtt.mqtt_service import mqtt_service

router = APIRouter()


def _build_mqtt_topics(device_id: str, greenhouse_id: str = "", prefix: str = "agri") -> dict:
    """构建设备的标准 MQTT topic 信息"""
    if greenhouse_id:
        base = f"{prefix}/{greenhouse_id}/{device_id}"
    else:
        base = f"{prefix}/{device_id}"
    return {
        "telemetry": f"{base}/telemetry",
        "status":    f"{base}/status",
        "command":   f"{base}/command",
        "base":      base,
    }


@router.get("/", response_model=List[DeviceResponse])
async def list_devices(
    greenhouse_id: Optional[str] = Query(None),
    device_type:   Optional[str] = Query(None),
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    query = select(Device)
    if greenhouse_id:
        query = query.where(Device.greenhouse_id == greenhouse_id)
    if device_type:
        query = query.where(Device.device_type == device_type)
    result  = await db.execute(query.offset(skip).limit(limit))
    devices = result.scalars().all()

    redis = await get_redis()
    cache = CacheService(redis)
    out = []
    for d in devices:
        r = DeviceResponse.model_validate(d)
        r.is_online = await cache.get_device_online(d.device_id)
        out.append(r)
    return out


@router.post("/", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
async def register_device(payload: DeviceCreate, db: AsyncSession = Depends(get_db)):
    """
    注册新设备
    注册成功后自动：
      1. 触发 MQTT 动态订阅（无需重启服务）
      2. 返回该设备的完整 MQTT topic 信息
    """
    # 检查 device_id 唯一性
    existing = await db.execute(select(Device).where(Device.device_id == payload.device_id))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"设备ID '{payload.device_id}' 已存在，请使用不同的 device_id"
        )

    # 验证大棚存在（如果提供了 greenhouse_id）
    if payload.greenhouse_id:
        gh = await db.execute(select(Greenhouse).where(Greenhouse.id == payload.greenhouse_id))
        if not gh.scalar_one_or_none():
            raise HTTPException(
                status_code=404,
                detail=f"大棚 '{payload.greenhouse_id}' 不存在，请先创建大棚"
            )

    device = Device(**payload.model_dump())
    db.add(device)
    await db.commit()
    await db.refresh(device)

    # ── 关键：注册后自动订阅该设备的 MQTT topic ──────────────
    asyncio.create_task(
        mqtt_service.subscribe_device(device.device_id, device.greenhouse_id or "")
    )

    resp = DeviceResponse.model_validate(device)
    resp.is_online = False
    # 附加 MQTT topic 信息（方便前端/机器人开发者参考）
    resp.mqtt_topics = _build_mqtt_topics(
        device.device_id,
        device.greenhouse_id or "",
        "agri"
    )
    return resp


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    redis = await get_redis()
    cache = CacheService(redis)
    r = DeviceResponse.model_validate(device)
    r.is_online = await cache.get_device_online(device.device_id)
    r.mqtt_topics = _build_mqtt_topics(device.device_id, device.greenhouse_id or "")
    return r


@router.put("/{device_id}", response_model=DeviceResponse)
async def update_device(device_id: str, payload: DeviceUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    old_gh = device.greenhouse_id
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(device, k, v)
    await db.commit()
    await db.refresh(device)

    # 如果更换了大棚，重新订阅新 topic
    if payload.greenhouse_id and payload.greenhouse_id != old_gh:
        asyncio.create_task(
            mqtt_service.subscribe_device(device.device_id, device.greenhouse_id or "")
        )

    return DeviceResponse.model_validate(device)


@router.delete("/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(device_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    await db.delete(device)
    await db.commit()


@router.post("/{device_id}/command")
async def send_command(device_id: str, command: DeviceCommand, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    cmd = command.model_dump(exclude_none=True)
    sent = await mqtt_service.publish_command(device.device_id, cmd)
    if not sent:
        raise HTTPException(status_code=503, detail="MQTT 未连接，指令发送失败")

    log = DeviceLog(
        device_id=device.id, level="info",
        event="command_sent",
        message=f"指令 '{command.command}' 已发送",
        meta_data=cmd,
    )
    db.add(log)
    await db.commit()
    return {"success": True, "message": f"指令已发送到 {device.device_id}"}


@router.get("/{device_id}/logs", response_model=List[DeviceLogResponse])
async def get_device_logs(
    device_id: str,
    skip: int = 0, limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="设备不存在")
    logs = await db.execute(
        select(DeviceLog).where(DeviceLog.device_id == device_id)
        .order_by(DeviceLog.created_at.desc()).offset(skip).limit(limit)
    )
    return logs.scalars().all()


@router.get("/{device_id}/mqtt-topics")
async def get_device_mqtt_topics(device_id: str, db: AsyncSession = Depends(get_db)):
    """获取设备的 MQTT topic 信息（供机器人端参考）"""
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    return {
        "device_id":    device.device_id,
        "greenhouse_id":device.greenhouse_id,
        "topics":       _build_mqtt_topics(device.device_id, device.greenhouse_id or ""),
        "broker":       f"{__import__('app.core.config', fromlist=['settings']).settings.MQTT_BROKER_HOST}:1883",
        "qos_telemetry": 0,
        "qos_status":    1,
        "qos_command":   1,
    }


@router.get("/{device_id}/status")
async def get_device_realtime_status(device_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    redis = await get_redis()
    cache = CacheService(redis)
    return {
        "device_id":  device.device_id,
        "is_online":  await cache.get_device_online(device.device_id),
        "last_status":await cache.get_device_status(device.device_id),
        "db_status":  device.status,
        "last_seen":  device.last_seen.isoformat() if device.last_seen else None,
    }


import asyncio
