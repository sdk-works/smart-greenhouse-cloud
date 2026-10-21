"""AI 受控接口（M4）。

约束：
  1. AI **不直连数据库**：数据由本接口层（后端）按公司范围取好后，
     以只读快照传给 ai_advisor（纯函数模块，无任何 IO 能力）。
  2. AI **不发 MQTT**：全程不碰 mqtt_service，产出也没有控制动作。
  3. 请求/建议落 ai_advice_logs 留痕（db_access_used=False / control_issued=False）。
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import check_permission, get_current_user
from app.core.ai_advisor import assert_no_control_action, generate_advice
from app.core.company_scope import can_access_company
from app.core.database import get_db
from app.models.models import (
    AiAdviceLog, Greenhouse, MetricDefinition, Telemetry, User,
)

router = APIRouter()

METRIC_COLUMNS = {
    "temperature": Telemetry.temperature,
    "humidity": Telemetry.humidity,
    "soil_moisture": Telemetry.soil_moisture,
    "soil_temperature": Telemetry.soil_temperature,
    "soil_ec": Telemetry.soil_ec,
    "ph": Telemetry.ph_value,
    "light": Telemetry.light_intensity,
    "co2": Telemetry.co2_level,
}


class AdviceIn(BaseModel):
    greenhouse_id: str
    hours: int = Field(default=1, ge=1, le=72)
    metrics: Optional[list] = Field(default=None)


@router.get("/ai/capabilities")
async def ai_capabilities(_: User = Depends(get_current_user)):
    """对外声明 AI 的能力边界，便于联调与验收。"""
    return {
        "can_read_database_directly": False,
        "can_publish_mqtt": False,
        "can_issue_control": False,
        "input": "后端接口层组装的只读快照",
        "output_kinds": ["observation", "suggestion", "risk", "inspection_request"],
        "note": "AI 只产出建议；任何控制动作都必须由人工在控制接口发起",
    }


@router.post("/ai/advice")
async def ai_advice(payload: AdviceIn, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    gh = await db.get(Greenhouse, payload.greenhouse_id)
    if not gh or not can_access_company(user, gh.company_id):
        raise HTTPException(404, "大棚不存在")

    # ── 后端侧取数（AI 自己拿不到 db） ──────────────────────
    since = datetime.now(timezone.utc) - timedelta(hours=payload.hours)
    metrics = {}
    row = (await db.execute(
        select(*[METRIC_COLUMNS[m] for m in METRIC_COLUMNS])
        .where(Telemetry.greenhouse_id == gh.id, Telemetry.created_at >= since)
        .order_by(Telemetry.created_at.desc()).limit(1))).first()
    if row is not None:
        for i, code in enumerate(METRIC_COLUMNS):
            if row[i] is not None:
                metrics[code] = row[i]

    wanted = payload.metrics or list(METRIC_COLUMNS)
    defs = (await db.execute(select(MetricDefinition).where(
        MetricDefinition.code.in_(list(wanted)),
        MetricDefinition.enabled == True))).scalars().all()
    # 物理合理区间（min/max）用于质量校验；AI 判读用 quality_rules 的目标区间
    thresholds = {d.code: {
        "min": (d.quality_rules or {}).get("target_min", d.min_value),
        "max": (d.quality_rules or {}).get("target_max", d.max_value),
        "unit": d.unit,
    } for d in defs}

    snapshot = {"greenhouse_id": gh.id, "metrics": metrics,
                "thresholds": thresholds,
                "as_of": datetime.now(timezone.utc).isoformat()}
    result = generate_advice(snapshot)
    try:
        assert_no_control_action(result["advice"])
    except ValueError as e:
        raise HTTPException(500, str(e))

    log = AiAdviceLog(greenhouse_id=gh.id, kind="advice",
                      request=payload.model_dump(), advice=result["advice"],
                      basis=result["basis"], db_access_used=False,
                      control_issued=False, requested_by=user.id,
                      company_id=gh.company_id)
    db.add(log)
    await db.commit()

    return {"greenhouse_id": gh.id, "advice": result["advice"],
            "basis": result["basis"], "limits": result["limits"],
            "log_id": log.id,
            "note": "建议仅供参考；如需控制设备请人工走 /actuator-commands"}


@router.get("/ai/advice/logs")
async def ai_advice_logs(greenhouse_id: Optional[str] = None,
                         limit: int = 20,
                         db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    from app.core.company_scope import is_platform, resolve_company_id
    q = select(AiAdviceLog)
    if not is_platform(user):
        q = q.where(AiAdviceLog.company_id == resolve_company_id(user))
    if greenhouse_id:
        q = q.where(AiAdviceLog.greenhouse_id == greenhouse_id)
    rows = (await db.execute(
        q.order_by(AiAdviceLog.created_at.desc()).limit(limit))).scalars().all()
    return {"items": [{
        "id": r.id, "greenhouse_id": r.greenhouse_id, "kind": r.kind,
        "advice": r.advice, "db_access_used": r.db_access_used,
        "control_issued": r.control_issued, "created_at": r.created_at,
    } for r in rows], "total": len(rows)}


@router.get("/metric-definitions")
async def list_metric_definitions(db: AsyncSession = Depends(get_db),
                                  _: User = Depends(get_current_user)):
    rows = (await db.execute(select(MetricDefinition).where(
        MetricDefinition.enabled == True).order_by(MetricDefinition.code))
    ).scalars().all()
    return {"items": [{
        "id": r.id, "code": r.code, "name": r.name, "unit": r.unit,
        "value_type": r.value_type, "min_value": r.min_value,
        "max_value": r.max_value, "precision": r.precision,
        "quality_rules": r.quality_rules, "source": r.source,
    } for r in rows], "total": len(rows)}


@router.post("/metric-definitions", status_code=201)
async def create_metric_definition(payload: dict,
                                   db: AsyncSession = Depends(get_db),
                                   user: User = Depends(get_current_user)):
    check_permission(user, "manage_robots")
    code = (payload.get("code") or "").strip()
    if not code:
        raise HTTPException(400, "缺少 code")
    existing = (await db.execute(select(MetricDefinition).where(
        MetricDefinition.code == code))).scalar_one_or_none()
    if existing:
        raise HTTPException(409, "指标编码已存在")
    row = MetricDefinition(
        id=str(uuid.uuid4()), code=code,
        name=payload.get("name") or code, unit=payload.get("unit"),
        value_type=payload.get("value_type") or "float",
        min_value=payload.get("min_value"), max_value=payload.get("max_value"),
        precision=int(payload.get("precision") or 2),
        source=payload.get("source") or "sensor",
        quality_rules=payload.get("quality_rules") or {})
    db.add(row)
    await db.commit()
    return {"id": row.id, "code": row.code, "name": row.name}
