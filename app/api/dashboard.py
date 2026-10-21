"""
dashboard.py - 仪表盘统计接口

M1 隔离收尾：
  - `/dashboard/stats` 全部计数按公司范围过滤（大棚/设备/在线/任务/遥测/图片）。
  - 平台运营可用 `?company_id=` 查看指定公司，该次访问落审计日志。
  - 业务用户传其它公司 → 403（ensure_company_access）。
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.api.auth import get_current_user
from app.core.audit import ensure_company_access
from app.core.company_scope import (
    company_filter, is_platform, resolve_company_id, scoped_greenhouse_ids,
)
from app.core.database import get_db
from app.models.models import (
    Greenhouse, Device, Telemetry,
    RobotTask, Image, TaskStatus, DeviceStatus, User,
)

router = APIRouter()


def _day_start() -> datetime:
    return datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


async def _scoped_counts(
    db: AsyncSession, user: User, company_id: Optional[str], today: datetime
) -> dict:
    ghq = scoped_greenhouse_ids(user, company_id)

    def _gh_cond(column):
        """ghq 为 None 表示平台运营的全平台视角，不加条件。"""
        return [] if ghq is None else [column.in_(ghq)]

    total_greenhouses = (await db.execute(
        select(func.count(Greenhouse.id)).where(
            Greenhouse.is_active == True, *_gh_cond(Greenhouse.id)
        )
    )).scalar() or 0

    total_devices = (await db.execute(
        select(func.count(Device.id)).where(*_gh_cond(Device.greenhouse_id))
    )).scalar() or 0

    online_devices = (await db.execute(
        select(func.count(Device.id)).where(
            Device.status == DeviceStatus.ONLINE, *_gh_cond(Device.greenhouse_id)
        )
    )).scalar() or 0

    active_tasks = (await db.execute(
        select(func.count(RobotTask.id)).where(
            RobotTask.status.in_([TaskStatus.RUNNING, TaskStatus.PENDING]),
            *_gh_cond(RobotTask.greenhouse_id)
        )
    )).scalar() or 0

    if is_platform(user) and company_id:
        tel_cond = [Telemetry.company_id == company_id]
    elif ghq is None:
        tel_cond = []
    else:
        tel_cond = [company_filter(user, Telemetry.company_id)]
    today_telemetry_count = (await db.execute(
        select(func.count(Telemetry.id)).where(
            Telemetry.created_at >= today, *tel_cond
        )
    )).scalar() or 0

    today_images_count = (await db.execute(
        select(func.count(Image.id)).where(
            Image.created_at >= today, *_gh_cond(Image.greenhouse_id)
        )
    )).scalar() or 0

    return {
        "total_greenhouses": int(total_greenhouses),
        "total_devices": int(total_devices),
        "online_devices": int(online_devices),
        "active_tasks": int(active_tasks),
        "today_telemetry_count": int(today_telemetry_count),
        "today_images_count": int(today_images_count),
    }


@router.get("/stats")
async def get_stats(
    company_id: Optional[str] = Query(
        None, description="仅平台运营可用：查看指定公司的统计（会落审计日志）"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """平台整体统计（按公司范围聚合）。"""
    # 先鉴权后审计：业务用户跨公司 403，平台运营跨公司落审计日志
    await ensure_company_access(db, user, company_id, resource="dashboard.stats",
                                action="read")
    counts = await _scoped_counts(db, user, company_id, _day_start())
    await db.commit()
    return {
        **counts,
        "scope": {
            "company_id": company_id or (None if is_platform(user)
                                         else resolve_company_id(user)),
            "platform_view": is_platform(user) and company_id is None,
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/greenhouses/overview")
async def get_overview(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    所有大棚状态概览（按公司范围过滤；设备计数用一次批量查询，遥测用子查询）
    """
    ghq = scoped_greenhouse_ids(user, None)
    gh_query = select(Greenhouse).where(Greenhouse.is_active == True)
    if ghq is not None:
        gh_query = gh_query.where(Greenhouse.id.in_(ghq))
    gh_result = await db.execute(gh_query)
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
                Device.status == DeviceStatus.ONLINE
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
