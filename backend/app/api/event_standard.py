from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.database import get_db
import time

router = APIRouter()

# 1. 事件存储（规范1）
@router.post("/save")
async def save_event(
    event_type: str = Query(...),
    event_source: str = Query(...),
    raw_content: str = Query(...),
    db: AsyncSession = Depends(get_db)
):
    event_no = f"EVT{int(time.time())}"

    try:
        await db.execute(
            text("""
                INSERT INTO event_standard_log (event_no, event_type, event_source, raw_content)
                VALUES (:a, :b, :c, :d)
            """),
            {"a": event_no, "b": event_type, "c": event_source, "d": raw_content}
        )
        await db.commit()
    except Exception as e:
        return {"code": 500, "msg": f"存储失败：{str(e)}"}

    return {"code": 0, "msg": "事件存储成功", "event_no": event_no}

# 2. 因果链挂载（规范2）
@router.post("/bind-chain")
async def bind_chain(
    event_no: str = Query(...),
    chain_id: str = Query(...),
    parent_event_no: str = Query(...),
    db: AsyncSession = Depends(get_db)
):
    await db.execute(
        text("""
            UPDATE event_standard_log
            SET chain_id = :c, parent_event_no = :p
            WHERE event_no = :e
        """),
        {"c": chain_id, "p": parent_event_no, "e": event_no}
    )
    await db.commit()
    return {"code": 0, "msg": "因果链已挂载"}

# 3. 状态快照绑定（规范4）
@router.post("/bind-snapshot")
async def bind_snapshot(
    event_no: str = Query(...),
    db: AsyncSession = Depends(get_db)
):
    snapshot_id = f"SNAP{int(time.time())}"
    await db.execute(
        text("""
            UPDATE event_standard_log
            SET snapshot_id = :s
            WHERE event_no = :e
        """),
        {"s": snapshot_id, "e": event_no}
    )
    await db.commit()
    return {"code": 0, "msg": "快照已绑定", "snapshot_id": snapshot_id}