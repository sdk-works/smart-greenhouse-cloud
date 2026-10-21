"""重启/掉线后的兜底恢复（M3）。

问题：任务与巡检批次可能长时间停留在 running（进程重启、机器人掉线、
没有收到完成事件），永久卡在执行中，前端一直显示"进行中"。

策略：**只做兜底标记，不假装知道真实结果**。
  - 任务 running 超过 TASK_STALE_MINUTES → 标记 partial（部分完成），
    并写入 error_message 说明这是系统兜底标记、实际结果未知。
  - 批次 running 超过 BATCH_STALE_MINUTES 无新事件 → 标记 partial +
    close_reason=stale_running_recovery。
  - 终态任务/批次不动。

调用点：
  - 服务启动（main.lifespan）后跑一次
  - POST /api/v1/maintenance/recover 手工触发
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.task_state import TERMINAL_STATES, TASK_TRANSITIONS
from app.models.models import InspectionBatch, RobotTask

TASK_STALE_MINUTES = 120
BATCH_STALE_MINUTES = 15

RECOVERY_NOTE = "系统兜底标记：running 超时未见结束事件，实际执行结果未知"


async def recover_stale_tasks(db: AsyncSession, now: Optional[datetime] = None,
                              timeout_minutes: int = TASK_STALE_MINUTES) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=timeout_minutes)
    rows = (await db.execute(select(RobotTask).where(
        RobotTask.status == "running",
        or_(RobotTask.started_at < cutoff,
            RobotTask.started_at.is_(None) & (RobotTask.created_at < cutoff)),
    ))).scalars().all()
    for t in rows:
        t.status = "partial"
        t.completed_at = t.started_at or t.created_at
        t.error_message = RECOVERY_NOTE
    if rows:
        await db.flush()
    return len(rows)


async def recover_stale_batches(db: AsyncSession, now: Optional[datetime] = None,
                                timeout_minutes: int = BATCH_STALE_MINUTES) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=timeout_minutes)
    rows = (await db.execute(select(InspectionBatch).where(
        InspectionBatch.status == "running",
        InspectionBatch.last_event_at < cutoff,
    ))).scalars().all()
    for b in rows:
        b.status = "partial"
        b.ended_at = b.last_event_at
        b.close_reason = "stale_running_recovery"
    if rows:
        await db.flush()
    return len(rows)


async def recover_all(db: AsyncSession, now: Optional[datetime] = None) -> dict:
    tasks = await recover_stale_tasks(db, now)
    batches = await recover_stale_batches(db, now)
    if tasks or batches:
        await db.commit()
    return {"tasks_recovered": tasks, "batches_recovered": batches,
            "task_timeout_minutes": TASK_STALE_MINUTES,
            "batch_timeout_minutes": BATCH_STALE_MINUTES,
            "at": (now or datetime.now(timezone.utc)).isoformat()}
