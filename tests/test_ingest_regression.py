"""
测试类别 4：现有采集链路回归（不许改原有采集逻辑，只做单测）

被测函数：app.mqtt.mqtt_service.MQTTService._handle_device_telemetry
（固定终端通道 agri/device/{device_id}/up，契约 v1.2 §3/§5/§7）

断言：
  - 喂一条 JY001 载荷 → telemetry 新增 1 行、device_receipts 新增 1 行、msg_id 正确
  - 同一条重复投递 → 不产生第二行（telemetry 仍 1 行、receipts 仍 1 行）
  - 缺 msg_id / topic 与 body 身份不一致 → 拒绝且不入库
  - 同 msg_id 异内容 → 冲突拒绝，不覆盖原行
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.core.database import AsyncSessionLocal
from app.models.models import Device, DeviceType, Greenhouse, Telemetry
from app.mqtt.mqtt_service import mqtt_service

DEVICE_SUFFIX = uuid.uuid4().hex[:8]
DEVICE_HW_ID = f"JY001-{DEVICE_SUFFIX}"
GH_NAME = f"ingest-{DEVICE_SUFFIX}"


def _payload(msg_id: str, temperature: float = 25.5) -> dict:
    sampled = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    return {
        "device_id": DEVICE_HW_ID,
        "msg_id": msg_id,
        "time_quality": 1,
        "sampled_at": sampled,
        "data": {
            "temperature": temperature,
            "humidity": 60.0,
            "soil_moisture": 33.0,
            "soil_temperature": 20.0,
            "soil_ec": 1.2,
            "light": 12000,
            "co2": 800,
            "ph": 6.5,
        },
    }


async def _counts(db, device_pk: str, msg_id: str):
    t = (await db.execute(select(Telemetry).where(
        Telemetry.device_id == device_pk))).scalars().all()
    r = (await db.execute(text(
        "SELECT COUNT(*) FROM device_receipts WHERE device_id=:d AND msg_id=:m"),
        {"d": DEVICE_HW_ID, "m": msg_id})).scalar()
    return len(t), int(r or 0)


async def _setup():
    async with AsyncSessionLocal() as db:
        gh = Greenhouse(name=GH_NAME)
        db.add(gh)
        await db.flush()
        dev = Device(device_id=DEVICE_HW_ID, name=DEVICE_HW_ID,
                     device_type=DeviceType.SENSOR, greenhouse_id=gh.id)
        db.add(dev)
        await db.commit()
        await db.refresh(dev)
        return gh.id, dev.id


async def _teardown(gh_id: str, dev_id: str, msg_ids):
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Telemetry).where(
            Telemetry.device_id == dev_id))).scalars().all()
        for r in rows:
            await db.delete(r)
        for m in msg_ids:
            await db.execute(text(
                "DELETE FROM device_receipts WHERE device_id=:d AND msg_id=:m"),
                {"d": DEVICE_HW_ID, "m": m})
        dev = await db.get(Device, dev_id)
        if dev:
            await db.delete(dev)
        gh = await db.get(Greenhouse, gh_id)
        if gh:
            await db.delete(gh)
        await db.commit()


async def test_jy001_single_delivery_and_duplicate(schema_ready):
    gh_id, dev_id = await _setup()
    msg_id = f"{DEVICE_HW_ID}/boot-night/1"
    try:
        async with AsyncSessionLocal() as db:
            before_t, before_r = await _counts(db, dev_id, msg_id)
        assert (before_t, before_r) == (0, 0), "前置环境必须干净"

        ok = await mqtt_service._handle_device_telemetry(DEVICE_HW_ID, _payload(msg_id))
        assert ok is True, "首次投递应入库成功"

        async with AsyncSessionLocal() as db:
            after_t, after_r = await _counts(db, dev_id, msg_id)
            assert after_t == 1, f"telemetry 应新增 1 行，实际 {after_t}"
            assert after_r == 1, f"device_receipts 应新增 1 行，实际 {after_r}"
            row = (await db.execute(select(Telemetry).where(
                Telemetry.device_id == dev_id))).scalar_one()
            assert row.msg_id == msg_id
            assert row.data_type == "telemetry"
            assert abs(row.temperature - 25.5) < 1e-6
            assert row.time_quality == 1
            assert row.sampled_at is not None, "time_quality=1 时应写入 sampled_at"

        # 重复投递：不产生第二行
        ok2 = await mqtt_service._handle_device_telemetry(DEVICE_HW_ID, _payload(msg_id))
        assert ok2 is True, "重复投递仍应回 stored"
        async with AsyncSessionLocal() as db:
            dup_t, dup_r = await _counts(db, dev_id, msg_id)
            assert dup_t == 1, f"重复投递后 telemetry 仍应为 1 行，实际 {dup_t}"
            assert dup_r == 1, f"重复投递后 receipts 仍应为 1 行，实际 {dup_r}"
    finally:
        await _teardown(gh_id, dev_id, [msg_id])


async def test_missing_msg_id_rejected(schema_ready):
    gh_id, dev_id = await _setup()
    try:
        payload = _payload("will-be-removed")
        payload.pop("msg_id")
        ok = await mqtt_service._handle_device_telemetry(DEVICE_HW_ID, payload)
        assert ok is False, "缺 msg_id 必须拒绝"
        async with AsyncSessionLocal() as db:
            t, _ = await _counts(db, dev_id, "will-be-removed")
            assert t == 0, "被拒绝的消息不得入库"
    finally:
        await _teardown(gh_id, dev_id, [])


async def test_topic_body_mismatch_rejected(schema_ready):
    gh_id, dev_id = await _setup()
    msg_id = f"{DEVICE_HW_ID}/boot-night/2"
    try:
        payload = _payload(msg_id)
        payload["device_id"] = "SOMEONE-ELSE"
        ok = await mqtt_service._handle_device_telemetry(DEVICE_HW_ID, payload)
        assert ok is False, "Topic 与 body 身份不一致必须拒绝"
        async with AsyncSessionLocal() as db:
            t, _ = await _counts(db, dev_id, msg_id)
            assert t == 0
    finally:
        await _teardown(gh_id, dev_id, [msg_id])


async def test_same_msg_id_different_content_rejected(schema_ready):
    gh_id, dev_id = await _setup()
    msg_id = f"{DEVICE_HW_ID}/boot-night/3"
    try:
        assert await mqtt_service._handle_device_telemetry(
            DEVICE_HW_ID, _payload(msg_id, 25.5)) is True
        ok = await mqtt_service._handle_device_telemetry(
            DEVICE_HW_ID, _payload(msg_id, 99.9))
        assert ok is False, "同 msg_id 异内容必须冲突拒绝"

        async with AsyncSessionLocal() as db:
            t, r = await _counts(db, dev_id, msg_id)
            assert t == 1 and r == 1, "冲突消息不得新增行"
            row = (await db.execute(select(Telemetry).where(
                Telemetry.device_id == dev_id))).scalar_one()
            assert abs(row.temperature - 25.5) < 1e-6, "原行不得被覆盖"
    finally:
        await _teardown(gh_id, dev_id, [msg_id])
