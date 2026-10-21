"""
tasks.py - 任务调度接口
修复 [#7]: publish_command 返回 bool，调用方正确处理未连接情况
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.mqtt_policy import assert_can_control
from app.core.photo_service import latest_map_version
from app.core.task_state import (
    ALL_STATES, TERMINAL_STATES, TASK_TRANSITIONS, finish_side_effect,
    normalize_status, raw_status, validate_transition,
)
from app.api.auth import check_permission, get_current_user
from app.models.models import RobotTask, Device, TaskStatus
from app.models.models import Greenhouse, User
from app.schemas.schemas import RobotTaskCreate, RobotTaskUpdate, RobotTaskResponse
from app.mqtt.mqtt_service import mqtt_service

router = APIRouter()


class TaskStatusIn(BaseModel):
    status: str = Field(min_length=1, max_length=20)
    reason: Optional[str] = Field(default=None, max_length=500)


@router.get("/task-state-machine")
async def task_state_machine(_: User = Depends(get_current_user)):
    """巡检任务状态机定义（供前端/联调查询允许流转）。"""
    return {
        "states": list(ALL_STATES),
        "terminal": sorted(TERMINAL_STATES),
        "transitions": {k: sorted(v) for k, v in TASK_TRANSITIONS.items()},
    }


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
    # M2：任务快照 —— 创建时冻结地图版本 + 点位列表 + 采集参数
    mv = await latest_map_version(db, payload.greenhouse_id)
    snapshot = {
        "map_version_id": mv.id if mv else None,
        "map_version":    mv.version if mv else None,
        "points":         [w.model_dump() for w in payload.waypoints],
        "params":         payload.task_config or {},
        "captured_at":    datetime.now(timezone.utc).isoformat(),
    }
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
        snapshot    = snapshot,
        map_version_id = mv.id if mv else None,
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
    """更新任务。M2：状态变更走状态机校验，非法流转 400；snapshot 永不被改写。"""
    check_permission(user, "manage_robots")
    result = await db.execute(select(RobotTask).where(RobotTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    data = payload.model_dump(exclude_unset=True)
    if "status" in data:
        target = normalize_status(data.pop("status"))
        validate_transition(task.status, target)
        task.status = raw_status(target)
        finish_side_effect(task, target)
    for k, v in data.items():
        if k in ("snapshot", "map_version_id"):
            continue          # 快照与地图版本是创建时冻结的，不允许编辑改写
        setattr(task, k, v)
    await db.commit()
    await db.refresh(task)
    return task


@router.post("/{task_id}/status")
async def set_task_status(
    task_id: str,
    payload: TaskStatusIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """专用的任务状态流转端点（非法流转 400）。"""
    check_permission(user, "manage_robots")
    result = await db.execute(select(RobotTask).where(RobotTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    target = normalize_status(payload.status)
    validate_transition(task.status, target)
    task.status = raw_status(target)
    finish_side_effect(task, target)
    if payload.reason:
        task.error_message = payload.reason if target in (TaskStatus.FAILED,
                                                          TaskStatus.CANCELLED) \
            else task.error_message
    await db.commit()
    return {"success": True, "task_id": task.id, "status": task.status}


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
    # M2：状态机 —— 只有 pending/paused 的任务可以再次下发
    validate_transition(task.status, TaskStatus.RUNNING)

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

    task.status     = TaskStatus.RUNNING.value
    task.started_at = datetime.now(timezone.utc)
    task.completed_at = None
    await db.commit()
    return {"success": True, "task_id": task.id, "status": task.status,
            "message": f"任务已下发到 {device.device_id}"}


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    check_permission(user, "manage_robots")
    result = await db.execute(select(RobotTask).where(RobotTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    validate_transition(task.status, TaskStatus.CANCELLED)

    if task.device_id:
        dev = await db.execute(select(Device).where(Device.id == task.device_id))
        device = dev.scalar_one_or_none()
        if device:
            await mqtt_service.publish_command(
                device.device_id,
                {"command": "cancel_task", "task_id": task_id}
            )

    task.status = TaskStatus.CANCELLED.value
    finish_side_effect(task, TaskStatus.CANCELLED)
    await db.commit()
    return {"success": True, "task_id": task.id, "status": task.status}
