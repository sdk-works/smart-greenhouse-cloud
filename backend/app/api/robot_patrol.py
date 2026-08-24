"""
robot_patrol.py — 机器人多大棚巡逻配置 API (V8新增)
一个机器人可以配置多个大棚的巡逻任务
巡逻配置存储在 Device.config["patrol_greenhouses"]
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.core.database import get_db
from app.models.models import Device, Greenhouse, DeviceType

router = APIRouter()


class PatrolGreenhouseIn(BaseModel):
    greenhouse_id: str
    priority: int = 1          # 巡逻优先级（1=最高）
    enabled: bool = True       # 是否启用
    repeat_interval_min: int = 60  # 巡逻间隔（分钟）


class PatrolGreenhouseOut(PatrolGreenhouseIn):
    greenhouse_name: Optional[str] = None
    greenhouse_crop: Optional[str] = None
    waypoint_count: int = 0


class PatrolConfigOut(BaseModel):
    device_id: str
    robot_name: str
    patrol_greenhouses: List[PatrolGreenhouseOut] = []
    patrol_speed: float = 0.5
    capture_interval: int = 5
    return_battery_threshold: int = 20
    auto_patrol: bool = False


def _get_cfg(device: Device) -> dict:
    return dict(device.config or {})


@router.get("/{device_id}/patrol-config", response_model=PatrolConfigOut)
async def get_patrol_config(device_id: str, db: AsyncSession = Depends(get_db)):
    """获取机器人巡逻配置（含所有关联大棚）"""
    r = await db.execute(select(Device).where(Device.id == device_id))
    dev = r.scalar_one_or_none()
    if not dev or dev.device_type != DeviceType.ROBOT:
        raise HTTPException(status_code=404, detail="机器人不存在")

    cfg = _get_cfg(dev)
    patrol_ghs = cfg.get("patrol_greenhouses", [])

    # 补充大棚名称
    enriched = []
    for pg in patrol_ghs:
        gh_r = await db.execute(select(Greenhouse).where(Greenhouse.id == pg.get("greenhouse_id")))
        gh = gh_r.scalar_one_or_none()
        wps = (gh.map_data or {}).get("waypoints", []) if gh else []
        enriched.append(PatrolGreenhouseOut(
            **pg,
            greenhouse_name=gh.name if gh else "（已删除）",
            greenhouse_crop=gh.crop_type if gh else None,
            waypoint_count=len(wps),
        ))

    return PatrolConfigOut(
        device_id=dev.device_id,
        robot_name=dev.name,
        patrol_greenhouses=enriched,
        patrol_speed=cfg.get("patrol_speed", 0.5),
        capture_interval=cfg.get("capture_interval", 5),
        return_battery_threshold=cfg.get("return_battery_threshold", 20),
        auto_patrol=cfg.get("auto_patrol", False),
    )


@router.post("/{device_id}/patrol-greenhouses")
async def add_patrol_greenhouse(
    device_id: str,
    payload: PatrolGreenhouseIn,
    db: AsyncSession = Depends(get_db),
):
    """为机器人添加一个巡逻大棚"""
    r = await db.execute(select(Device).where(Device.id == device_id))
    dev = r.scalar_one_or_none()
    if not dev:
        raise HTTPException(status_code=404, detail="机器人不存在")

    # 验证大棚存在
    gh_r = await db.execute(select(Greenhouse).where(Greenhouse.id == payload.greenhouse_id))
    gh = gh_r.scalar_one_or_none()
    if not gh:
        raise HTTPException(status_code=404, detail="大棚不存在")

    cfg = _get_cfg(dev)
    patrol_ghs = cfg.get("patrol_greenhouses", [])

    # 检查是否已添加
    if any(pg["greenhouse_id"] == payload.greenhouse_id for pg in patrol_ghs):
        raise HTTPException(status_code=409, detail=f"大棚 '{gh.name}' 已在巡逻列表中")

    patrol_ghs.append(payload.model_dump())
    # 按优先级排序
    patrol_ghs.sort(key=lambda x: x.get("priority", 99))
    cfg["patrol_greenhouses"] = patrol_ghs
    dev.config = cfg
    await db.commit()

    return {
        "success": True,
        "message": f"已将 '{gh.name}' 添加到 {dev.name} 的巡逻列表",
        "total": len(patrol_ghs),
    }


@router.put("/{device_id}/patrol-greenhouses/{greenhouse_id}")
async def update_patrol_greenhouse(
    device_id: str,
    greenhouse_id: str,
    payload: PatrolGreenhouseIn,
    db: AsyncSession = Depends(get_db),
):
    """修改巡逻大棚配置"""
    r = await db.execute(select(Device).where(Device.id == device_id))
    dev = r.scalar_one_or_none()
    if not dev:
        raise HTTPException(status_code=404, detail="机器人不存在")

    cfg = _get_cfg(dev)
    patrol_ghs = cfg.get("patrol_greenhouses", [])
    idx = next((i for i, pg in enumerate(patrol_ghs) if pg["greenhouse_id"] == greenhouse_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="该大棚不在巡逻列表中")

    patrol_ghs[idx] = payload.model_dump()
    patrol_ghs.sort(key=lambda x: x.get("priority", 99))
    cfg["patrol_greenhouses"] = patrol_ghs
    dev.config = cfg
    await db.commit()
    return {"success": True}


@router.delete("/{device_id}/patrol-greenhouses/{greenhouse_id}", status_code=204)
async def remove_patrol_greenhouse(
    device_id: str,
    greenhouse_id: str,
    db: AsyncSession = Depends(get_db),
):
    """从巡逻列表移除大棚"""
    r = await db.execute(select(Device).where(Device.id == device_id))
    dev = r.scalar_one_or_none()
    if not dev:
        raise HTTPException(status_code=404, detail="机器人不存在")

    cfg = _get_cfg(dev)
    cfg["patrol_greenhouses"] = [
        pg for pg in cfg.get("patrol_greenhouses", [])
        if pg["greenhouse_id"] != greenhouse_id
    ]
    dev.config = cfg
    await db.commit()


@router.put("/{device_id}/patrol-params")
async def update_patrol_params(
    device_id: str,
    patrol_speed: float = 0.5,
    capture_interval: int = 5,
    return_battery_threshold: int = 20,
    auto_patrol: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """更新巡逻参数（速度、拍照间隔等）"""
    r = await db.execute(select(Device).where(Device.id == device_id))
    dev = r.scalar_one_or_none()
    if not dev:
        raise HTTPException(status_code=404, detail="机器人不存在")

    cfg = _get_cfg(dev)
    cfg.update({
        "patrol_speed": patrol_speed,
        "capture_interval": capture_interval,
        "return_battery_threshold": return_battery_threshold,
        "auto_patrol": auto_patrol,
    })
    dev.config = cfg
    await db.commit()
    return {"success": True, "message": "巡逻参数已更新"}
