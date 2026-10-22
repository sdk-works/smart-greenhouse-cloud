"""集成联调（I）：把 T3/T4/T5 的产物串成一条链跑一遍。

链路：登记凭据(T3) → 模拟器上行入库(T5 的测试 broker + 真实 MQTT 报文)
      → 状态页接口读回(T4) → 停用凭据(T3) → 状态页出现 credential_disabled

**只联调，不重复开发**：三个模块的实现都在各自分支上，这里只证明它们合起来能用，
特别是"凭据状态一变，状态页立刻反映出来"这条跨模块因果。

全部跑在测试库 + 进程内测试 broker 上，未连生产 EMQX、未碰真实设备。
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import asyncio
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import AuditLog, MqttCredential, Telemetry, UserRole
from app.mqtt.mqtt_service import mqtt_service
from app.services import mqtt_credential_service as svc
from tests.conftest import auth
from tests.mqtt_test_broker import TestBroker

API = settings.API_PREFIX

try:
    import paho.mqtt.client as paho
except ImportError:
    paho = None


def _utc(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


async def _auth_fn(username, password, clientid):
    async with AsyncSessionLocal() as db:
        return await svc.authenticate(db, username=username,
                                      password=password, clientid=clientid)


@pytest.mark.skipif(paho is None, reason="缺少 paho-mqtt")
async def test_onboarding_then_telemetry_then_status_then_disable(client, iso):
    fx, db = iso
    c = await fx.company(db, "INT-C")
    gh = await fx.greenhouse(db, c, "INT-棚")
    dev = await fx.device(db, gh, name="INT-DTU")
    admin, token = await fx.user(db, UserRole.ADMIN, c)
    await fx.binding(db, dev, gh, start_at=_utc(days=1))
    await db.commit()

    # ── T3：登记凭据 ──────────────────────────────────────────
    r = await client.post(f"{API}/devices/{dev.id}/credential",
                          json={"asset_no": "DTU-INT-0001",
                                "clientid": "INT00000000000001"},
                          headers=auth(token))
    assert r.status_code == 201, r.text
    username = r.json()["credential"]["username"]
    password = r.json()["password"]

    # ── T5：用测试 broker + 真实 MQTT 报文上行一条遥测 ────────
    async def on_publish(topic, payload):
        await mqtt_service._dispatch_payload(
            SimpleNamespace(topic=topic, payload=payload))

    class _AckSink:
        def __init__(self, brk):
            self.broker, self.sent = brk, []

        async def publish(self, topic, payload, qos=0, retain=False):
            body = payload.encode() if isinstance(payload, str) else payload
            self.sent.append(topic)
            await self.broker.deliver(topic, body)

    broker = TestBroker(authenticate=_auth_fn, on_publish=on_publish)
    await broker.start()
    sink = _AckSink(broker)
    saved = getattr(mqtt_service, "_client", None)
    mqtt_service._client = sink
    acks = []
    try:
        def _run():
            import time as _t
            cl = paho.Client(client_id="INT00000000000001", clean_session=True)
            cl.username_pw_set(username, password)

            rc = [None]

            def on_connect(cli, u, f, r):
                rc[0] = r
                if r == 0:
                    cli.subscribe(f"agri/device/{dev.device_id}/ack", qos=0)

            def on_message(cli, u, msg):
                acks.append(json.loads(msg.payload))

            cl.on_connect, cl.on_message = on_connect, on_message
            cl.connect("127.0.0.1", broker.port, 10)
            cl.loop_start()
            deadline = _t.time() + 6
            sent = False
            while _t.time() < deadline:
                # 必须等 CONNACK(rc==0) 再发：否则订阅还没登记，服务端回的 ACK 没人接
                if rc[0] == 0 and not sent:
                    _t.sleep(0.3)
                    cl.publish(f"agri/device/{dev.device_id}/up", json.dumps({
                        "type": "telemetry", "schema_version": 1,
                        "device_id": dev.device_id,
                        "msg_id": f"{dev.device_id}/1/1", "boot_id": 1, "seq": 1,
                        "sampled_at": 0, "time_quality": 0,
                        "data": {"temperature": 23.4, "humidity": 55.0},
                    }), qos=0)
                    sent = True
                if acks:
                    break
                _t.sleep(0.2)

            cl.loop_stop(); cl.disconnect()

        await asyncio.to_thread(_run)
    finally:
        mqtt_service._client = saved
        await broker.stop()

    assert acks and acks[0]["status"] == "stored", acks
    async with AsyncSessionLocal() as s:
        row = (await s.execute(select(Telemetry).where(
            Telemetry.msg_id == f"{dev.device_id}/1/1"))).scalar_one_or_none()
        assert row is not None, "遥测未入库"

    # ── T4：状态页接口读回，且能看到 T3 登记的凭据 ────────────
    r = await client.get(f"{API}/greenhouses/{gh.id}/status", headers=auth(token))
    assert r.status_code == 200, r.text
    data = r.json()
    item = next(d for d in data["devices"] if d["device_id"] == dev.device_id)
    assert item["data"]["has_data"] is True
    assert item["credential"]["registered"] is True
    assert item["credential"]["enabled"] is True
    assert item["credential"]["username"] == username
    temp = next(m for m in data["metrics"]
                if m["code"] == "temperature" and m["device_id"] == dev.device_id)
    assert temp["value"] == 23.4 and temp["state"] == "ok"

    # ── T3：停用凭据 → T4：状态页必须立刻反映 ─────────────────
    r = await client.patch(f"{API}/devices/{dev.id}/credential/enabled",
                           json={"enabled": False, "reason": "集成联调回收"},
                           headers=auth(token))
    assert r.status_code == 200, r.text
    r = await client.get(f"{API}/greenhouses/{gh.id}/status", headers=auth(token))
    data = r.json()
    item = next(d for d in data["devices"] if d["device_id"] == dev.device_id)
    assert item["credential"]["enabled"] is False, "停用后状态页仍显示启用"
    codes = {a["code"] for a in data["anomalies"]}
    assert "credential_disabled" in codes, data["anomalies"]

    # 审计：整个链路里 T3 的两次写操作都有记录
    async with AsyncSessionLocal() as s:
        logs = (await s.execute(select(AuditLog).where(
            AuditLog.resource == "mqtt_credential"))).scalars().all()
        assert {l.action for l in logs} >= {"create", "disable"}, [l.action for l in logs]
