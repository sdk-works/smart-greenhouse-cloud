"""T4 大棚设备与数据状态 —— 五组样例 + 跨公司隔离。

五组样例：**正常 / 过期 / 无效 / 补传 / 无数据**，分别对应页面必须能说清的五种情况。
断言全部基于接口返回的字段，跑在测试库 greenhouse_nightly_test（NIGHTLY_READONLY=1）。
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import (
    Device, DeviceStatus, DeviceType, MqttCredential, Telemetry, UserRole,
)
from tests.conftest import auth

API = settings.API_PREFIX


def _utc(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


async def _scene(iso, db, name):
    c = await iso.company(db, f"{name}-C")
    gh = await iso.greenhouse(db, c, f"{name}-gh")
    dev = await iso.device(db, gh, name=f"{name}-dev")
    u, token = await iso.user(db, UserRole.ADMIN, c)
    await db.commit()
    return gh, dev, token


async def _metric_defs_ready(db):
    """metric_definitions 由迁移 005 的种子填充；测试库 schema_ready 已应用。"""
    return True


def _find(metrics, code):
    return next((m for m in metrics if m["code"] == code), None)


async def test_status_normal_case(client, iso):
    """正常：有值、有单位、新鲜、归属可信。"""
    fx, db = iso
    gh, dev, token = await _scene(fx, db, "NM")
    now = datetime.now(timezone.utc)
    await fx.raw_telemetry(
        db, gh, dev, timestamp=now, created_at=now,
        sampled_at=now, time_quality=1, msg_id="NM/1/1", data_type="telemetry",
        temperature=24.5, humidity=60.0, company_id=gh.company_id,
        attribution_status="resolved", attribution_reason="backfill_sample_time",
        raw_data={"data": {"valid": [1, 1, 1, 1, 1, 1, 1, 1]}})
    await db.commit()

    r = await client.get(f"{API}/greenhouses/{gh.id}/status", headers=auth(token))
    assert r.status_code == 200, r.text
    d = r.json()
    temp = _find(d["metrics"], "temperature")
    assert temp is not None
    assert temp["value"] == 24.5 and temp["unit"] == "°C" and temp["name"] == "空气温度"
    assert temp["state"] == "ok" and temp["display_value"] == 24.5
    assert temp["last_updated_at"] and temp["age_seconds"] is not None
    assert temp["attribution"]["grade"] == "trusted"
    assert temp["device_reports_channel_invalid"] is False
    assert d["summary"]["metrics_ok"] >= 1
    assert d["devices"][0]["data"]["within_max_age"] is True
    assert not [a for a in d["anomalies"] if a["code"].startswith("sensor_invalid")]


async def test_status_expired_is_not_shown_as_live(client, iso):
    """过期：仍有数值但 display_value=null、state=expired，并给出"数据已过期"文案。"""
    fx, db = iso
    gh, dev, token = await _scene(fx, db, "EX")
    old = _utc(minutes=30)
    await fx.raw_telemetry(
        db, gh, dev, timestamp=old, created_at=old,
        sampled_at=old, time_quality=1, msg_id="EX/1/1", data_type="telemetry",
        temperature=30.0, humidity=50.0, company_id=gh.company_id,
        attribution_status="resolved", attribution_reason="backfill_sample_time")
    await db.commit()

    r = await client.get(f"{API}/greenhouses/{gh.id}/status?max_age_seconds=900",
                         headers=auth(token))
    assert r.status_code == 200, r.text
    d = r.json()
    temp = _find(d["metrics"], "temperature")
    assert temp["value"] == 30.0                 # 保留原值供排查
    assert temp["display_value"] is None         # 但页面不许再当实时值显示
    assert temp["state"] == "expired"
    assert "已过期" in temp["message"]
    assert d["devices"][0]["data"]["expired"] is True
    codes = {a["code"] for a in d["anomalies"]}
    assert "data_stalled" in codes


async def test_status_sensor_invalid_flagged(client, iso):
    """无效：设备自报有效位有 0 → 明确提示，且不猜是哪一路。"""
    fx, db = iso
    gh, dev, token = await _scene(fx, db, "IV")
    now = datetime.now(timezone.utc)
    await fx.raw_telemetry(
        db, gh, dev, timestamp=now, created_at=now,
        sampled_at=now, time_quality=1, msg_id="IV/1/1", data_type="telemetry",
        temperature=24.0, humidity=61.0, company_id=gh.company_id,
        attribution_status="resolved", attribution_reason="backfill_sample_time",
        raw_data={"data": {"valid": [1, 1, 0, 0, 1, 1, 1, 1]}})
    await db.commit()

    r = await client.get(f"{API}/greenhouses/{gh.id}/status", headers=auth(token))
    assert r.status_code == 200, r.text
    d = r.json()
    bad = [a for a in d["anomalies"] if a["code"] == "sensor_invalid"]
    assert len(bad) == 1 and "2 路" in bad[0]["message"], bad
    # 位序未定义，所以必须显式说明"不猜是哪一路"
    assert "不猜" in bad[0]["message"]
    for m in d["metrics"]:
        assert m["device_reports_channel_invalid"] is True


async def test_status_out_of_range_flagged(client, iso):
    """越界：使用 metric_definitions 里既有的合理区间，不新增农艺阈值。"""
    fx, db = iso
    gh, dev, token = await _scene(fx, db, "OR")
    now = datetime.now(timezone.utc)
    await fx.raw_telemetry(
        db, gh, dev, timestamp=now, created_at=now,
        sampled_at=now, time_quality=1, msg_id="OR/1/1", data_type="telemetry",
        temperature=999.0, company_id=gh.company_id,
        attribution_status="resolved", attribution_reason="backfill_sample_time")
    await db.commit()

    r = await client.get(f"{API}/greenhouses/{gh.id}/status", headers=auth(token))
    d = r.json()
    temp = _find(d["metrics"], "temperature")
    assert temp["state"] == "out_of_range"
    assert d["summary"]["metrics_out_of_range"] >= 1
    assert any(a["code"] == "value_out_of_range" for a in d["anomalies"])


async def test_status_backfill_does_not_overwrite_current(client, iso):
    """补传：晚到但描述更早时刻的数据只能进历史，不许顶掉更新的实时值。"""
    fx, db = iso
    gh, dev, token = await _scene(fx, db, "BF")
    now = datetime.now(timezone.utc)
    earlier = _utc(minutes=3)

    # 先到的"更新"数据（基准时刻 now）
    await fx.raw_telemetry(
        db, gh, dev, timestamp=now, created_at=now,
        sampled_at=now, time_quality=1, msg_id="BF/1/2", data_type="telemetry",
        temperature=26.0, company_id=gh.company_id,
        attribution_status="resolved", attribution_reason="backfill_sample_time")
    await db.commit()

    # 后到的"补传"数据：描述的是 3 分钟前，但入库时间更晚
    later_insert = datetime.now(timezone.utc)
    await fx.raw_telemetry(
        db, gh, dev, timestamp=earlier,
        created_at=later_insert, sampled_at=earlier, time_quality=1,
        msg_id="BF/1/1", data_type="telemetry",
        temperature=11.0, company_id=gh.company_id,
        attribution_status="resolved", attribution_reason="backfill_sample_time")
    await db.commit()

    r = await client.get(f"{API}/greenhouses/{gh.id}/status", headers=auth(token))
    d = r.json()
    temp = _find(d["metrics"], "temperature")
    # 当前值必须是"更新"的那条，不能被补传顶掉
    assert temp["value"] == 26.0, temp
    assert d["devices"][0]["backfilled_rows"] == 1
    # 补传数据仍然可见（进历史）
    hist_msg_ids = [h["msg_id"] for h in temp["history"]]
    assert "BF/1/1" in hist_msg_ids
    assert next(h for h in temp["history"] if h["msg_id"] == "BF/1/1")["is_backfill"] is True


async def test_status_no_data_device(client, iso):
    """无数据：不能显示成"正常"，必须给明确说明。"""
    fx, db = iso
    gh, dev, token = await _scene(fx, db, "ND")
    await db.commit()

    r = await client.get(f"{API}/greenhouses/{gh.id}/status", headers=auth(token))
    d = r.json()
    assert d["devices"][0]["data"]["has_data"] is False
    assert d["devices"][0]["data"]["within_max_age"] is False
    assert d["devices"][0]["note"]
    # 一个指标都没有可用数值
    assert d["summary"]["metrics_total"] == 0 or all(
        m["state"] == "missing" for m in d["metrics"])
    assert any(a["code"] == "credential_missing" for a in d["anomalies"])


async def test_status_mqtt_online_vs_data_separate(client, iso):
    """连接 ≠ 有数据：两栏独立取值，在线但无数据必须报警。"""
    fx, db = iso
    gh, dev, token = await _scene(fx, db, "OD")
    dev.status = DeviceStatus.ONLINE
    dev.last_seen = datetime.now(timezone.utc)
    await db.commit()

    r = await client.get(f"{API}/greenhouses/{gh.id}/status", headers=auth(token))
    d = r.json()
    item = d["devices"][0]
    assert set(["mqtt", "data"]) <= set(item)
    assert item["mqtt"]["db_status"] == "online"
    assert item["mqtt"]["source"] in ("redis_online_cache", "devices.status",
                                      "fallback_devices_status(redis_unavailable)")
    assert isinstance(item["mqtt"]["connected"], bool)
    assert item["data"]["has_data"] is False          # 没有数据
    codes = {a["code"] for a in d["anomalies"]}
    assert "mqtt_online_without_data" in codes or "data_stalled" in codes


async def test_status_attribution_grades_are_distinguishable(client, iso):
    """归属：可信 / 推定 / 待定 / 未判定 四种都能被识别，不混为一谈。"""
    fx, db = iso
    gh, dev, token = await _scene(fx, db, "AT")
    now = datetime.now(timezone.utc)
    await fx.raw_telemetry(
        db, gh, dev, timestamp=now, created_at=now,
        sampled_at=now, time_quality=1, msg_id="AT/1/1", data_type="telemetry",
        temperature=22.0, company_id=gh.company_id,
        attribution_status="resolved",
        attribution_reason="backfill_receive_time_estimated")
    await db.commit()

    r = await client.get(f"{API}/greenhouses/{gh.id}/status", headers=auth(token))
    d = r.json()
    grade = d["devices"][0]["attribution"]
    assert grade["grade"] == "inferred" and grade["label"] == "推定"
    assert _find(d["metrics"], "temperature")["attribution"]["grade"] == "inferred"

    # 改成待定，再看一次
    async with AsyncSessionLocal() as s2:
        row = (await s2.execute(
            __import__("sqlalchemy").select(Telemetry).where(
                Telemetry.msg_id == "AT/1/1"))).scalar_one()
        row.attribution_status = "pending"
        row.attribution_reason = "legacy_before_attribution"
        await s2.commit()
    r = await client.get(f"{API}/greenhouses/{gh.id}/status", headers=auth(token))
    assert r.json()["devices"][0]["attribution"]["grade"] == "pending"
    assert any(a["code"] == "attribution_pending" for a in r.json()["anomalies"])


async def test_status_cross_company_isolation(client, iso):
    """跨公司访问：业务用户 404；平台运营可看且必须落审计。"""
    fx, db = iso
    c1 = await fx.company(db, "XA")
    c2 = await fx.company(db, "XB")
    gh1 = await fx.greenhouse(db, c1, "XA棚")
    await fx.device(db, gh1, name="XA-dev")
    adminB, tokenB = await fx.user(db, UserRole.ADMIN, c2)
    # 平台运营放在 c2，看 c1 的棚才算跨公司
    platform, ptoken = await fx.user(db, UserRole.ADMIN, c2, platform=True)
    await db.commit()

    r = await client.get(f"{API}/greenhouses/{gh1.id}/status", headers=auth(tokenB))
    assert r.status_code == 404, r.text

    r = await client.get(f"{API}/greenhouses/{gh1.id}/status", headers=auth(ptoken))
    assert r.status_code == 200, r.text
    async with AsyncSessionLocal() as s2:
        from app.models.models import AuditLog
        from sqlalchemy import select
        logs = (await s2.execute(
            select(AuditLog).where(AuditLog.user_id == platform.id,
                                   AuditLog.resource == "greenhouse_status"))).scalars().all()
        assert logs, "平台运营跨公司查看状态未落审计"


async def test_status_identity_conflict_detected(client, iso):
    """身份冲突：同一个 msg_id 出现在两个设备下必须被识别。"""
    fx, db = iso
    gh, dev, token = await _scene(fx, db, "IC")
    dev2 = await fx.device(db, gh, name="IC-dev2")
    await db.commit()
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as s2:
        # 同一个 msg_id 被两个设备各写一条 —— 这就是"重复身份"
        shared_msg_id = "IC/shared/1"
        for d in (dev, dev2):
            s2.add(Telemetry(id=str(uuid.uuid4()), device_id=d.id, greenhouse_id=gh.id,
                             timestamp=now, created_at=now, data_type="telemetry",
                             msg_id=shared_msg_id, temperature=20.0,
                             company_id=gh.company_id))
        await s2.commit()

    r = await client.get(f"{API}/greenhouses/{gh.id}/status", headers=auth(token))
    d = r.json()
    bad = [a for a in d["anomalies"] if a["code"] == "identity_conflict"]
    assert bad, d["anomalies"]
    assert d["summary"]["errors"] >= 1


async def test_status_page_is_served(client):
    """静态页面必须能被取到，且包含页面要呈现的关键字段（避免接口与界面各说一套）。"""
    r = await client.get("/pages/greenhouse_status.html")
    assert r.status_code == 200, r.status_code
    html = r.text
    for must in ("MQTT 已连接", "最近有效数据", "数据是否新鲜", "数据已过期",
                 "补传", "数据归属", "greenhouses/", "/status?max_age_seconds="):
        assert must in html, f"页面缺少要素：{must}"
