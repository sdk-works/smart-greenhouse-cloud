"""大棚设备与数据状态（T4）。

只做一件事：给"现有大棚详情页"提供一份**能直接渲染**的状态数据，
把以前混在一起的几件事分开说清楚：

- MQTT 已连接 vs 最近收到有效数据（两回事，页面必须分开显示）
- 数据过期：过期后 `display_value=null` + `state=expired`，不许继续当实时值显示
- 补传数据：能进 `history`（历史可见），但**不参与当前值**的选取
- 归属分级：可信 / 推定 / 待定 / 未判定，四种各自可识别，不混成一种"正常"
- 异常：传感器无效、数据断流、身份冲突、凭据异常、越界，各带 code 与成句文案

**没有做**：全站仪表盘重做、未经确认的农艺阈值、把未知设备状态显示成"正常"。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.core.audit import record_cross_company_access
from app.core.company_scope import can_access_company, is_platform
from app.core.database import get_db
from app.core.redis import CacheService, get_redis
from app.core.status_view import build_greenhouse_status
from app.models.models import Greenhouse, User
from app.services import mqtt_credential_service as creds

router = APIRouter(tags=["GreenhouseStatus 大棚设备与数据状态"])

DEFAULT_MAX_AGE = 900          # 15 分钟，与 freshness.STALE_SECONDS 同口径


@router.get("/greenhouses/{greenhouse_id}/status")
async def greenhouse_status(
    greenhouse_id: str,
    max_age_seconds: int = Query(DEFAULT_MAX_AGE, ge=1, le=86400),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    gh = await db.get(Greenhouse, greenhouse_id)
    if not gh or not can_access_company(user, gh.company_id):
        raise HTTPException(404, "大棚不存在")
    if is_platform(user) and gh.company_id:
        await record_cross_company_access(
            db, user, gh.company_id, "greenhouse_status", gh.id)

    redis = await get_redis()
    cache = CacheService(redis, company_id=gh.company_id)
    data = await build_greenhouse_status(
        db, greenhouse_id, max_age_seconds=max_age_seconds, redis_cache=cache)

    # 凭据状态并入设备条目（T3 的产物），页面据此提示"凭据已停用/未登记"
    for item in data["devices"]:
        cred = await creds.get_by_device(db, item["device_id"])
        item["credential"] = {
            "registered": cred is not None,
            "enabled": cred.enabled if cred else None,
            "username": cred.username if cred else None,
            "asset_no": cred.asset_no if cred else None,
        }
        if cred is None:
            data["anomalies"].append({
                "code": "credential_missing", "level": "info",
                "message": f"{item['device_id']} 尚未登记 MQTT 凭据（EMQX authn 启用后无法连接）",
                "ref": item["device_id"],
            })
        elif not cred.enabled:
            data["anomalies"].append({
                "code": "credential_disabled", "level": "error",
                "message": f"{item['device_id']} 的 MQTT 凭据已停用，设备无法再次连接",
                "ref": item["device_id"],
            })
    data["summary"]["anomalies_total"] = len(data["anomalies"])
    data["summary"]["errors"] = sum(1 for a in data["anomalies"] if a["level"] == "error")
    return data
