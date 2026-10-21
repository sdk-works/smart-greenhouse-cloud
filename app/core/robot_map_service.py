"""机器人地图版本核对（M2）。

规则：
- 平台侧在 robot_map_registry 登记期望地图版本。
- 机器人回传（config_ack / 遥测）里带 robot_map_version 时做比对。
- **不一致只产生告警记录（robot_map_alerts），绝不自动改任何配置或数据。**
- 未登记期望值时不做任何判断（不能凭空判定不一致）。
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import RobotMapAlert, RobotMapRegistry


async def expected_map_version(db: AsyncSession, robot_id: str) -> Optional[str]:
    row = (await db.execute(select(RobotMapRegistry).where(
        RobotMapRegistry.robot_id == robot_id,
        RobotMapRegistry.active == True,
    ).order_by(RobotMapRegistry.created_at.desc()).limit(1))).scalar_one_or_none()
    return row.map_version if row else None


async def check_robot_map_version(
    db: AsyncSession,
    robot_id: str,
    reported: Optional[str],
    source: str = "config_ack",
) -> dict:
    """比对并在不一致时落一条 open 告警；返回比对结果。

    reported 为空 / 未登记期望值 → 返回 status='unknown'，不产生告警。
    """
    reported = (reported or "").strip()
    expected = await expected_map_version(db, robot_id)
    if not reported or not expected:
        return {"status": "unknown", "expected": expected, "reported": reported or None}

    if reported == expected:
        return {"status": "match", "expected": expected, "reported": reported}

    # 同一对版本已有未闭环告警时不重复刷屏
    existing = (await db.execute(select(RobotMapAlert).where(
        RobotMapAlert.robot_id == robot_id,
        RobotMapAlert.expected_map_version == expected,
        RobotMapAlert.reported_map_version == reported,
        RobotMapAlert.status == "open",
    ).limit(1))).scalar_one_or_none()
    if existing:
        return {"status": "mismatch", "expected": expected, "reported": reported,
                "alert_id": existing.id, "duplicated": True}

    alert = RobotMapAlert(robot_id=robot_id, expected_map_version=expected,
                          reported_map_version=reported, source=source,
                          status="open",
                          detail={"note": "机器人回传地图版本与平台登记不一致，需人工核对"})
    db.add(alert)
    await db.flush()
    return {"status": "mismatch", "expected": expected, "reported": reported,
            "alert_id": alert.id, "duplicated": False}


async def resolve_alert(db: AsyncSession, alert_id: str, *, synced: bool) -> Optional[RobotMapAlert]:
    """人工闭环告警。synced=True 表示已确认一致；否则只是关闭记录。"""
    from datetime import datetime, timezone
    alert = await db.get(RobotMapAlert, alert_id)
    if alert is None:
        return None
    alert.status = "resolved" if synced else "closed"
    alert.resolved_at = datetime.now(timezone.utc)
    await db.flush()
    return alert
