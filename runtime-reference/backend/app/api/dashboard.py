"""
dashboard.py - 仪表盘统计接口
修复:
  [#11] get_overview() N+1 查询优化：使用 JOIN + subquery 替代 for 循环多次 SELECT
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta, timezone

from app.core.database import get_db
from app.models.models import (
    Greenhouse, Device, Telemetry,
    RobotTask, Image, TaskStatus, DeviceStatus
)

router = APIRouter()

# A device is online only when it has reported recently.  Database status is
# retained for audit/history and must not keep a powered-off device online.
ONLINE_CUTOFF_SECONDS = 120


def _online_since():
    return datetime.now(timezone.utc) - timedelta(seconds=ONLINE_CUTOFF_SECONDS)


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    """平台整体统计（单次查询）"""
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return {
        "total_greenhouses": (
            await db.execute(
                select(func.count(Greenhouse.id))
                .where(Greenhouse.is_active == True)
            )
        ).scalar() or 0,

        "total_devices": (
            await db.execute(select(func.count(Device.id)))
        ).scalar() or 0,

        "online_devices": (
            await db.execute(
                select(func.count(Device.id))
                .where(Device.last_seen >= _online_since())
            )
        ).scalar() or 0,

        "active_tasks": (
            await db.execute(
                select(func.count(RobotTask.id))
                .where(RobotTask.status.in_([TaskStatus.RUNNING, TaskStatus.PENDING]))
            )
        ).scalar() or 0,

        "today_telemetry_count": (
            await db.execute(
                select(func.count(Telemetry.id))
                .where(Telemetry.created_at >= today)
            )
        ).scalar() or 0,

        "today_images_count": (
            await db.execute(
                select(func.count(Image.id))
                .where(Image.created_at >= today)
            )
        ).scalar() or 0,

        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/greenhouses/overview")
async def get_overview(db: AsyncSession = Depends(get_db)):
    """
    所有大棚状态概览（优化版，减少数据库查询次数）
    原版：每个大棚 3 次 SELECT（N+1 问题）
    优化：设备计数用一次批量查询，遥测用子查询
    """
    # 获取所有激活大棚
    gh_result = await db.execute(
        select(Greenhouse).where(Greenhouse.is_active == True)
    )
    greenhouses = gh_result.scalars().all()

    if not greenhouses:
        return []

    gh_ids = [gh.id for gh in greenhouses]

    # 批量查询：各大棚设备总数
    dev_counts_result = await db.execute(
        select(Device.greenhouse_id, func.count(Device.id).label("total"))
        .where(Device.greenhouse_id.in_(gh_ids))
        .group_by(Device.greenhouse_id)
    )
    dev_counts = {row.greenhouse_id: row.total for row in dev_counts_result}

    # 批量查询：各大棚在线设备数
    online_counts_result = await db.execute(
        select(Device.greenhouse_id, func.count(Device.id).label("online"))
        .where(
            and_(
                Device.greenhouse_id.in_(gh_ids),
                Device.last_seen >= _online_since()
            )
        )
        .group_by(Device.greenhouse_id)
    )
    online_counts = {row.greenhouse_id: row.online for row in online_counts_result}

    # 批量查询：各大棚最新遥测数据（取最近1小时均值）
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    avg_result = await db.execute(
        select(
            Telemetry.greenhouse_id,
            func.avg(Telemetry.temperature).label("avg_temp"),
            func.avg(Telemetry.humidity).label("avg_hum"),
        )
        .where(
            and_(
                Telemetry.greenhouse_id.in_(gh_ids),
                Telemetry.created_at >= since
            )
        )
        .group_by(Telemetry.greenhouse_id)
    )
    avg_data = {row.greenhouse_id: row for row in avg_result}

    # 组装结果
    out = []
    for gh in greenhouses:
        avgs = avg_data.get(gh.id)
        out.append({
            "id":           gh.id,
            "name":         gh.name,
            "crop_type":    gh.crop_type,
            "device_count": dev_counts.get(gh.id, 0),
            "online_count": online_counts.get(gh.id, 0),
            "temperature":  round(avgs.avg_temp, 2) if avgs and avgs.avg_temp else None,
            "humidity":     round(avgs.avg_hum, 2)  if avgs and avgs.avg_hum  else None,
        })
    return out
