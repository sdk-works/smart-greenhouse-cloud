"""巡检任务状态机（M2）。

状态：pending / running / paused / completed / partial / failed / cancelled
终态：completed / partial / failed / cancelled（终态不可再流转）

全部用**字符串**作为键与值：数据库列是 VARCHAR，历史数据与新状态共用一套表示，
避免新增状态还要去改数据库枚举类型。
"""
from datetime import datetime, timezone
from typing import Optional, Set

from fastapi import HTTPException

from app.models.models import TaskStatus

# 允许流转：当前状态 -> 可到达的状态集合（字符串）
TASK_TRANSITIONS = {
    "pending":   {"running", "cancelled", "failed"},
    "running":   {"paused", "completed", "partial", "failed", "cancelled"},
    "paused":    {"running", "completed", "partial", "cancelled"},
    "completed": set(),
    "partial":   set(),
    "failed":    set(),
    "cancelled": set(),
}

ALL_STATES = tuple(TASK_TRANSITIONS)
TERMINAL_STATES = {s for s, nxt in TASK_TRANSITIONS.items() if not nxt}


def _as_str(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, TaskStatus):
        return value.value
    return str(value)


def allowed_next(status) -> Set[str]:
    return set(TASK_TRANSITIONS.get(_as_str(status), set()))


def can_transition(current, target) -> bool:
    t = _as_str(target)
    return t in TASK_TRANSITIONS.get(_as_str(current), set())


def validate_transition(current, target) -> None:
    """非法流转 → 400。未指定目标或目标与当前一致时放行（幂等）。"""
    cur, tgt = _as_str(current), _as_str(target)
    if tgt is None or cur == tgt:
        return
    if tgt not in ALL_STATES:
        raise HTTPException(status_code=400, detail=f"未知任务状态：{tgt}")
    if tgt not in TASK_TRANSITIONS.get(cur, set()):
        raise HTTPException(status_code=400, detail=f"非法任务状态流转：{cur} → {tgt}")


def finish_side_effect(task, target) -> None:
    """进入终态时补 completed_at；回到非终态时清空。"""
    if _as_str(target) in TERMINAL_STATES:
        if getattr(task, "completed_at", None) is None:
            task.completed_at = datetime.now(timezone.utc)
    else:
        task.completed_at = None


def normalize_status(value) -> Optional[TaskStatus]:
    """把任意表示（字符串 / 枚举）归一为 TaskStatus；非法值 400。"""
    if value is None:
        return None
    if isinstance(value, TaskStatus):
        return value
    try:
        return TaskStatus(str(value))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"未知任务状态：{value}")


def raw_status(value) -> str:
    """写入数据库用的字符串表示。"""
    return _as_str(value)
