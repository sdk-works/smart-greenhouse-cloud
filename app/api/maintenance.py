"""运维兜底接口（M3）：重启/掉线后的长时间 running 兜底标记。"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.database import get_db
from app.core.recovery import (
    BATCH_STALE_MINUTES, TASK_STALE_MINUTES, recover_all,
)
from app.models.models import User

router = APIRouter()


@router.post("/maintenance/recover")
async def run_recovery(db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """把长时间停留在 running 的任务/批次兜底标记，避免永久卡在执行中。"""
    check_permission(user, "manage_robots")
    return await recover_all(db)


@router.get("/maintenance/recovery-policy")
async def recovery_policy(_: User = Depends(get_current_user)):
    return {"task_timeout_minutes": TASK_STALE_MINUTES,
            "batch_timeout_minutes": BATCH_STALE_MINUTES,
            "task_target_status": "partial",
            "batch_target_status": "partial",
            "note": "兜底标记为 partial，表示'未收到结束事件'，不代表真实执行结果"}
