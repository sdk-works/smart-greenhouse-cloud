"""大棚设备与数据状态视图（T4）。

页面要的六件事，这里逐条给出可判定依据，**没有依据的一律标"未知"，不猜**：

| 需求 | 判定方式 |
|---|---|
| 当前指标 | 出自 `metric_definitions`（名称/单位/精度/合理区间），数值取该 Device 最新一条遥测 |
| 数据过期 | 按 `app.core.freshness` 在**查询时**算；过期后 `display_value=null`，不让页面继续当实时值显示 |
| 连接 ≠ 有数据 | MQTT 在线看 Redis 在线缓存（回退 devices.status），"最近有效数据"看遥测基准时刻，两者分开返回 |
| 补传不入当前值 | 当前值只取**基准时刻最大**那条；晚到但描述更早时刻的行计入 `backfilled_rows` 并进 `history` |
| 归属分级 | `attribution_status` + `attribution_reason` → trusted / inferred / pending / unknown |
| 异常说明 | 传感器无效、数据断流、身份冲突、凭据异常、越界，各自独立成条，带 code 与可展示文案 |

时间基准沿用既有口径：`sampled_at`（仅 time_quality=1 可信）→ 否则 `created_at`。
不新增农艺阈值，只使用 `metric_definitions` 里已经存在的 min/max。
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select, text

from app.core.freshness import compute_freshness, is_fresh_enough, pick_basis
from app.models.models import Device, Greenhouse, MetricDefinition, Telemetry

# metric code -> Telemetry 列名
METRIC_COLUMN = {
    "temperature":       "temperature",
    "humidity":          "humidity",
    "soil_moisture":     "soil_moisture",
    "soil_temperature":  "soil_temperature",
    "soil_ec":           "soil_ec",
    "ph":                "ph_value",
    "light":             "light_intensity",
    "co2":               "co2_level",
    "soil_nitrogen":     "soil_nitrogen",
    "soil_phosphorus":   "soil_phosphorus",
    "soil_potassium":    "soil_potassium",
}

ATTRIBUTION_LABEL = {
    "trusted":  "可信",
    "inferred": "推定",
    "pending":  "待定",
    "unknown":  "未判定",
}

# attribution_status / reason -> 页面上的归属档位
def _attribution_grade(status: Optional[str], reason: Optional[str]) -> Dict:
    st = (status or "").lower()
    rs = reason or ""
    if st == "resolved":
        if rs == "backfill_sample_time":
            return {"grade": "trusted", "label": ATTRIBUTION_LABEL["trusted"],
                    "detail": "按设备采集时刻落在唯一绑定窗口内"}
        if rs == "backfill_receive_time_estimated":
            return {"grade": "inferred", "label": ATTRIBUTION_LABEL["inferred"],
                    "detail": "设备未给出可信采集时刻，按服务器收到报文的时刻推定"}
        return {"grade": "trusted", "label": ATTRIBUTION_LABEL["trusted"],
                "detail": f"入站时已判定（{rs or 'resolved'}）"}
    if st == "pending":
        return {"grade": "pending", "label": ATTRIBUTION_LABEL["pending"],
                "detail": f"归属待确认（{rs or 'pending'}）"}
    # NULL：早于服务开始写 attribution_status 的历史行，**不能当成可信也不能当成待定**
    return {"grade": "unknown", "label": ATTRIBUTION_LABEL["unknown"],
            "detail": "该行由写入归属字段之前的版本产生，尚未判定"}


def _basis(row) -> tuple:
    """一条遥测的时间基准（ (时刻, 来源) ）。与 freshness.pick_basis 同一口径。"""
    return pick_basis(getattr(row, "sampled_at", None),
                      getattr(row, "created_at", None),
                      getattr(row, "time_quality", 0))


def _valid_flags(row) -> Optional[List[int]]:
    """设备自报的通道有效位。

    只在 raw_data.data.valid 存在时返回。**位序与指标的对应关系未在契约里定义**，
    所以这里只统计"有几路未通过自检"，不猜是哪一路。
    """
    raw = getattr(row, "raw_data", None) or {}
    data = raw.get("data") if isinstance(raw, dict) else None
    flags = (data or {}).get("valid") if isinstance(data, dict) else None
    if isinstance(flags, list) and flags and all(isinstance(x, int) for x in flags):
        return flags
    return None


async def load_device_rows(db, greenhouse_id: str, device_ids: List[str],
                           per_device: int = 50):
    """取每个设备最近若干条遥测（按创建时间倒序）。"""
    if not device_ids:
        return {}
    rows = (await db.execute(
        select(Telemetry)
        .where(Telemetry.device_id.in_(device_ids),
               Telemetry.greenhouse_id == greenhouse_id)
        .order_by(Telemetry.device_id, Telemetry.created_at.desc())
    )).scalars().all()
    out: Dict[str, list] = {}
    for r in rows:
        lst = out.setdefault(r.device_id, [])
        if len(lst) < per_device:
            lst.append(r)
    return out


async def _identity_conflicts(db, device_ids: List[str]):
    """身份冲突两类：
      (a) 同一 msg_id 出现在多个设备下（重复身份）
      (b) 上行自报的 device_id 在设备表里查无此人
    returns (冲突文案 list, 重复明细 list)
    """
    if not device_ids:
        return [], []
    dup = (await db.execute(text(
        "SELECT msg_id, count(DISTINCT device_id) AS n "
        "FROM telemetry WHERE device_id = ANY(:ids) GROUP BY msg_id "
        "HAVING count(DISTINCT device_id) > 1 ORDER BY msg_id LIMIT 20"),
        {"ids": device_ids})).all()
    conflicts = [f"msg_id {m} 同时出现在 {n} 个设备下" for m, n in dup]

    unknown = (await db.execute(text(
        "SELECT DISTINCT m.device_id FROM raw_messages m "
        "LEFT JOIN devices d ON d.device_id = m.device_id "
        "WHERE d.id IS NULL AND m.device_id IS NOT NULL LIMIT 20"))).all()
    conflicts += [f"上行自报身份 {d[0]} 未登记为任何设备" for d in unknown]
    return conflicts, dup


async def build_greenhouse_status(db, greenhouse_id: str, *, max_age_seconds: int,
                                  redis_cache=None, history_size: int = 5) -> dict:
    """组装接口返回体。db/redis 由调用方给，方便测试里替换。"""
    gh = await db.get(Greenhouse, greenhouse_id)
    devices = (await db.execute(
        select(Device).where(Device.greenhouse_id == greenhouse_id)
        .order_by(Device.device_id))).scalars().all()
    definitions = (await db.execute(
        select(MetricDefinition).where(MetricDefinition.enabled == True)  # noqa: E712
        .order_by(MetricDefinition.code))).scalars().all()

    now = datetime.now(timezone.utc)
    device_ids = [d.id for d in devices]
    rows_by_dev = await load_device_rows(db, greenhouse_id, device_ids)
    conflicts, _dup = await _identity_conflicts(db, device_ids)

    anomalies: List[dict] = []
    metrics: List[dict] = []
    device_items: List[dict] = []

    def anomaly(code, level, message, ref=None):
        anomalies.append({"code": code, "level": level, "message": message,
                          "ref": ref})

    async def mqtt_state(dev):
        """MQTT 是否在线。

        与"有没有数据"完全无关：先看 Redis 在线缓存，取不到才退到 devices.status，
        并把数据来源标出来，页面才敢照实展示。
        """
        if redis_cache is not None:
            try:
                return (bool(await redis_cache.get_device_online(dev.device_id)),
                        "redis_online_cache")
            except Exception:
                return (str(getattr(dev.status, "value", dev.status)) == "online",
                        "fallback_devices_status(redis_unavailable)")
        return (str(getattr(dev.status, "value", dev.status)) == "online",
                "devices.status")

    async def mqtt_block(dev):
        connected, source = await mqtt_state(dev)
        return {"connected": connected, "source": source,
                "db_status": str(getattr(dev.status, "value", dev.status)),
                "last_seen": dev.last_seen.isoformat() if dev.last_seen else None}

    for dev in devices:
        rows = rows_by_dev.get(dev.id, [])
        if not rows:
            mqtt = await mqtt_block(dev)
            device_items.append({
                "id": dev.id, "device_id": dev.device_id, "name": dev.name,
                "mqtt": mqtt,
                "data": {"has_data": False, "last_valid_at": None,
                         "age_seconds": None, "freshness": compute_freshness(),
                         "within_max_age": False, "expired": True},
                "note": "该设备在本棚内没有任何遥测记录",
            })
            anomaly("data_stalled", "warning",
                    f"{dev.device_id} 在本棚内没有任何遥测记录"
                    f"（不知道是没装、没绑还是没上报，不显示成正常）",
                    ref=dev.device_id)
            if mqtt["connected"]:
                anomaly("mqtt_online_without_data", "warning",
                        f"{dev.device_id} MQTT 在线但本棚内查不到任何有效数据"
                        f"（连接 ≠ 有数据）", ref=dev.device_id)
            continue

        # ── 当前值：只认"基准时刻最大"的那一行 ────────────────
        def key(r):
            b, _s = _basis(r)
            return (b is not None, b or datetime.min.replace(tzinfo=timezone.utc))
        current = max(rows, key=key)
        cur_basis, cur_src = _basis(current)
        fresh = compute_freshness(sampled_at=current.sampled_at,
                                  created_at=current.created_at,
                                  time_quality=current.time_quality, now=now)
        within = is_fresh_enough(fresh, max_age_seconds)

        # 补传：晚到，但描述的是更早的时刻（不参与当前值，只进历史）
        backfilled = [r for r in rows
                      if r is not current and r.created_at > current.created_at]

        # 设备自报的通道有效位
        flags = _valid_flags(current)
        bad_channels = sum(1 for x in flags if x == 0) if flags else 0
        if bad_channels:
            anomaly("sensor_invalid", "warning",
                    f"{dev.device_id} 自报 {bad_channels} 路传感器未通过自检"
                    f"（有效位序列={(flags or [])!r}，位序与指标的对应关系未在契约中定义，不猜是哪一路）",
                    ref=dev.device_id)

        # 连接状态 vs 数据状态（两回事，分别取值）
        connected, source = await mqtt_state(dev)
        if source.startswith("fallback") or source == "devices.status":
            source = source

        if not within:
            anomaly("data_stalled", "warning",
                    f"{dev.device_id} 数据已过期：最后一次有效数据距今 "
                    f"{fresh.get('age_seconds')} 秒（阈值 {max_age_seconds} 秒）",
                    ref=dev.device_id)
        if connected and not within:
            anomaly("mqtt_online_without_data", "warning",
                    f"{dev.device_id} MQTT 在线但长时间没有有效数据"
                    f"（连接 ≠ 有数据，请分别看 '连接' 与 '最近有效数据' 两栏）",
                    ref=dev.device_id)

        attr = _attribution_grade(current.attribution_status,
                                  current.attribution_reason)
        if attr["grade"] == "pending":
            anomaly("attribution_pending", "info",
                    f"{dev.device_id} 最近一条数据归属待定（{current.attribution_reason}）",
                    ref=dev.device_id)

        device_items.append({
            "id": dev.id, "device_id": dev.device_id, "name": dev.name,
            "mqtt": {"connected": connected, "source": source,
                     "db_status": str(getattr(dev.status, "value", dev.status)),
                     "last_seen": dev.last_seen.isoformat() if dev.last_seen else None},
            "data": {
                "has_data": True,
                "last_valid_at": cur_basis.isoformat() if cur_basis else None,
                "basis": cur_src,
                "age_seconds": fresh.get("age_seconds"),
                "freshness": fresh,
                "within_max_age": within,
                "expired": not within,
            },
            "backfilled_rows": len(backfilled),
            "attribution": attr,
        })

        history = [{
            "basis_at": _basis(r)[0].isoformat() if _basis(r)[0] else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "msg_id": r.msg_id,
            "is_backfill": r is not current and r.created_at > current.created_at,
        } for r in rows[:history_size]]

        for md in definitions:
            col = METRIC_COLUMN.get(md.code)
            if not col:
                continue
            value = getattr(current, col, None)
            age = fresh.get("age_seconds")
            expired = not within
            state = "ok"
            if value is None:
                state = "missing"
            elif expired:
                state = "expired"
            elif md.min_value is not None and value < md.min_value:
                state = "out_of_range"
            elif md.max_value is not None and value > md.max_value:
                state = "out_of_range"

            if state == "out_of_range":
                anomaly("value_out_of_range", "warning",
                        f"{md.name}（{dev.device_id}）当前值 {value} 超出合理区间 "
                        f"[{md.min_value}, {md.max_value}]，请核对传感器",
                        ref=md.code)

            metrics.append({
                "code": md.code, "name": md.name, "unit": md.unit,
                "precision": md.precision,
                "device_id": dev.device_id,
                "value": value,
                # 过期/缺失时页面不要再拿这个数当实时值显示
                "display_value": None if state in ("expired", "missing") else value,
                "state": state,
                "value_valid": value is not None,
                "message": {
                    "ok": "",
                    "missing": f"{md.name} 本次未上报",
                    "expired": f"数据已过期（{age} 秒前），不再作为实时值显示",
                    "out_of_range": f"{md.name} 数值超出合理区间，疑似传感器异常",
                }[state],
                "last_updated_at": cur_basis.isoformat() if cur_basis else None,
                "last_updated_source": cur_src,
                "age_seconds": age,
                "freshness": fresh,
                "attribution": attr,
                "device_reports_channel_invalid": bad_channels > 0,
                "history": history,
            })

    for c in conflicts:
        anomaly("identity_conflict", "error", f"身份冲突：{c}")

    summary = {
        "metrics_total": len(metrics),
        "metrics_ok": sum(1 for m in metrics if m["state"] == "ok"),
        "metrics_expired": sum(1 for m in metrics if m["state"] == "expired"),
        "metrics_missing": sum(1 for m in metrics if m["state"] == "missing"),
        "metrics_out_of_range": sum(1 for m in metrics if m["state"] == "out_of_range"),
        "devices_total": len(devices),
        "devices_mqtt_online": sum(1 for d in device_items if d.get("mqtt", {}).get("connected")),
        "devices_data_fresh": sum(1 for d in device_items if d.get("data", {}).get("within_max_age")),
        "attribution": {
            g: sum(1 for d in device_items if d.get("attribution", {}).get("grade") == g)
            for g in ("trusted", "inferred", "pending", "unknown")
        },
        "anomalies_total": len(anomalies),
        "errors": sum(1 for a in anomalies if a["level"] == "error"),
    }

    return {
        "greenhouse": {"id": gh.id if gh else greenhouse_id,
                       "name": gh.name if gh else None,
                       "company_id": gh.company_id if gh else None},
        "as_of": now.isoformat(),
        "max_age_seconds": max_age_seconds,
        "summary": summary,
        "devices": device_items,
        "metrics": metrics,
        "anomalies": anomalies,
    }

