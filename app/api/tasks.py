"""
tasks.py - 任务调度接口
修复 [#7]: publish_command 返回 bool，调用方正确处理未连接情况
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.mqtt_policy import assert_can_control
from app.api.auth import check_permission, get_current_user
from app.models.models import RobotTask, Device, TaskStatus
from app.models.models import Greenhouse, User
from app.schemas.schemas import RobotTaskCreate, RobotTaskUpdate, RobotTaskResponse
from app.mqtt.mqtt_service import mqtt_service

router = APIRouter()


@router.get("/", response_model=List[RobotTaskResponse])
async def list_tasks(
    greenhouse_id: Optional[str] = Query(None),
    task_status: Optional[str] = Query(None),
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    query = select(RobotTask)
    if greenhouse_id:
        query = query.where(RobotTask.greenhouse_id == greenhouse_id)
    if task_status:
        query = query.where(RobotTask.status == task_status)
    result = await db.execute(
        query.order_by(RobotTask.created_at.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()


@router.post("/", response_model=RobotTaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(payload: RobotTaskCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    check_permission(user, "manage_robots")
    task = RobotTask(
        task_name   = payload.task_name,
        task_type   = payload.task_type,
        greenhouse_id = payload.greenhouse_id,
        device_id   = payload.device_id,
        priority    = payload.priority,
        scheduled_at = payload.scheduled_at,
        waypoints   = [w.model_dump() for w in payload.waypoints],
        actions     = [a.model_dump() for a in payload.actions],
        task_config = payload.task_config,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    return task


@router.get("/{task_id}", response_model=RobotTaskResponse)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(RobotTask).where(RobotTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}", response_model=RobotTaskResponse)
async def update_task(
    task_id: str,
    payload: RobotTaskUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    check_permission(user, "manage_robots")
    result = await db.execute(select(RobotTask).where(RobotTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(task, k, v)
    await db.commit()
    await db.refresh(task)
    return task


@router.post("/{task_id}/dispatch")
async def dispatch_task(task_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    check_permission(user, "manage_robots")
    """下发任务到机器人（修复：publish_command 返回 bool，不再 raise RuntimeError）"""
    result = await db.execute(select(RobotTask).where(RobotTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if not task.device_id:
        raise HTTPException(status_code=400, detail="任务未分配设备")

    dev = await db.execute(select(Device).where(Device.id == task.device_id))
    device = dev.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # M1：控制/任务下发必须校验设备归属（MQTT 主题约束 §约束4）
    gh = await db.get(Greenhouse, device.greenhouse_id) if device.greenhouse_id else None
    assert_can_control(user, gh.company_id if gh else None, device.device_id)

    command = {
        "command": "start_task",
        "task_id": task.id,
        "task": {
            "type":         task.task_type,
            "greenhouse_id":task.greenhouse_id,
            "points":       task.waypoints,
            "actions":      task.actions,
        }
    }

    # publish_command 返回 bool，不抛异常
    sent = await mqtt_service.publish_command(device.device_id, command)
    if not sent:
        raise HTTPException(
            status_code=503,
            detail="MQTT 服务当前未连接，请稍后重试"
        )

    task.status     = TaskStatus.RUNNING
    task.started_at = datetime.now(timezone.utc)
    await db.commit()
    return {"success": True, "message": f"任务已下发到 {device.device_id}"}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    check_permission(user, "manage_robots")
    result = await db.execute(select(RobotTask).where(RobotTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.device_id:
        dev = await db.execute(select(Device).where(Device.id == task.device_id))
        device = dev.scalar_one_or_none()
        if device:
            await mqtt_service.publish_command(
                device.device_id,
                {"command": "cancel_task", "task_id": task_id}
            )

    task.status = TaskStatus.CANCELLED
    await db.commit()
    return {"success": True}
