"""T3 设备入网与凭据管理闭环 —— 契约测试。

声明范围：走的是**测试库 + 进程内测试 broker**（tests/mqtt_test_broker.py），
没有连生产 EMQX、没有连真实设备、没有改任何 EMQX 配置。
所以这里通过的是"自动测试通过 / 模拟联调通过"，**不是真实环境验证**。

被测链路：登记 → 生成凭据 →（真实 MQTT 报文）连接 → 上传 → 收到 ACK →
          停用 → 再次连接被 broker 拒绝。
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import (
    AuditLog, Device, MqttCredential, Telemetry, UserRole,
)
from app.mqtt.mqtt_service import mqtt_service
from app.services import mqtt_credential_service as svc
from tests.conftest import auth
from tests.mqtt_test_broker import TestBroker

API = settings.API_PREFIX

# paho 是同步库，放在线程里跑；这里统一封装，测试只关心结果
try:
    import paho.mqtt.client as paho
except ImportError:      # pragma: no cover
    paho = None


def _utc(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


async def _auth_fn(username, password, clientid):
    """给测试 broker 用的鉴权回调，直接走生产那同一份判定逻辑。"""
    async with AsyncSessionLocal() as db:
        return await svc.authenticate(db, username=username,
                                      password=password, clientid=clientid)


class _DeviceSim:
    """极简 DTU 模拟：连 broker、订阅 ack、发一条遥测、等回执。"""

    def __init__(self, username, password, clientid, device_id):
        self.username, self.password = username, password
        self.clientid, self.device_id = clientid, device_id
        self.acks = []
        self.rc = None

    async def run(self, port, msg_id, wait=6.0):
        loop = asyncio.get_running_loop()

        def _thread():
            c = paho.Client(client_id=self.clientid, clean_session=True)
            c.username_pw_set(self.username, self.password)

            def on_connect(cli, u, f, rc):
                self.rc = rc
                if rc == 0:
                    cli.subscribe(f"agri/device/{self.device_id}/ack", qos=0)

            def on_message(cli, u, msg):
                try:
                    self.acks.append(json.loads(msg.payload))
                except Exception:
                    self.acks.append({"raw": msg.payload.decode("utf-8", "replace")})

            c.on_connect, c.on_message = on_connect, on_message
            c.connect("127.0.0.1", port, 10)
            c.loop_start()
            deadline = loop.time() + wait
            while loop.time() < deadline:
                if self.rc is not None and self.rc != 0:
                    break
                if self.rc == 0 and len(self.acks) == 0:
                    payload = {
                        "type": "telemetry", "schema_version": 1,
                        "device_id": self.device_id, "msg_id": msg_id,
                        "boot_id": 1, "seq": 1, "sampled_at": 0,
                        "time_quality": 0,
                        "data": {"temperature": 24.5, "humidity": 60.0},
                    }
                    c.publish(f"agri/device/{self.device_id}/up",
                              json.dumps(payload), qos=0)
                if self.acks:
                    break
                import time as _t
                _t.sleep(0.2)
            c.loop_stop()
            c.disconnect()

        await asyncio.to_thread(_thread)
        return self.rc, self.acks


@pytest.fixture
async def broker():
    """测试 broker：上行报文直接交给**被测服务现有的人口**（同一段代码路径）。"""
    async def _on_publish(topic: str, payload: bytes):
        parts = topic.split("/")
        if len(parts) != 4 or parts[0] != "agri" or parts[3] != "up":
            return
        try:
            body = json.loads(payload.decode("utf-8", "replace"))
        except Exception:
            return                       # 非 JSON 上行（生产上有 www.usr.cn 这类）
        if parts[1] == "device":
            await mqtt_service._handle_device_telemetry(parts[2], body)

    b = TestBroker(authenticate=_auth_fn, on_publish=_on_publish)
    await b.start()
    yield b
    await b.stop()


@pytest.fixture
def bridge_publish(broker):
    """把服务端要发的 ACK 接回测试 broker，让"设备侧真能收到 ACK"。"""
    class _ClientStub:
        async def publish(self, topic, payload, qos=0, retain=False):
            await broker.deliver(topic, payload.encode() if isinstance(payload, str) else payload)

    saved = getattr(mqtt_service, "_client", None)
    mqtt_service._client = _ClientStub()
    yield
    mqtt_service._client = saved


@pytest.mark.skipif(paho is None, reason="缺少 paho-mqtt，无法跑真实 MQTT 报文链路")
async def test_onboarding_full_loop(client, iso, broker, bridge_publish):
    """登记 → 凭据 → 连接 → 上传 → ACK → 停用 → 再连接被拒。"""
    fx, db = iso
    c = await fx.company(db, "OB-C")
    gh = await fx.greenhouse(db, c, "OB-棚")
    dev = await fx.device(db, gh, name="OB-TEST-DTU")
    admin, token = await fx.user(db, UserRole.ADMIN, c)
    await fx.binding(db, dev, gh, start_at=_utc(days=1))
    await db.commit()

    # ── 1) 登记（尚未生成凭据） ──────────────────────────────
    r = await client.get(f"{API}/devices/{dev.id}/credential", headers=auth(token))
    assert r.status_code == 200 and r.json()["has_credential"] is False

    # ── 2) 生成凭据：明文只出现这一次 ────────────────────────
    r = await client.post(f"{API}/devices/{dev.id}/credential",
                          json={"asset_no": "DTU-TEST-0001",
                                "clientid": "TEST00000000000001",
                                "username": "dev-dtu-test-0001"},
                          headers=auth(token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["password_shown_once"] is True
    plain = body["password"]
    assert len(plain) >= 20
    assert plain not in json.dumps(body["credential"], ensure_ascii=False)

    # 明文没有落到库里（库里只有哈希）
    async with AsyncSessionLocal() as s2:
        cred = (await s2.execute(
            select(MqttCredential).where(
                MqttCredential.username == "dev-dtu-test-0001"))).scalar_one()
        assert cred.password_hash != plain
        assert plain not in cred.password_hash
        assert cred.enabled is True
        assert cred.asset_no == "DTU-TEST-0001"
        assert cred.clientid == "TEST00000000000001"
        # JY001 那套"兼容身份"要保留：这是同一台采集终端的两种叫法
        assert cred.compat_identity == dev.device_id

    # 再来拿详情：只有摘要，没有口令
    r = await client.get(f"{API}/devices/{dev.id}/credential", headers=auth(token))
    assert r.status_code == 200
    snap = r.json()["credential"]
    assert "password" not in snap and "password_hash" not in snap

    # ── 3) 用真实 MQTT 报文连接 + 上传 + 收 ACK ─────────────
    sim = _DeviceSim("dev-dtu-test-0001", plain, "TEST00000000000001", dev.device_id)
    msg_id = f"{dev.device_id}/1/1"
    rc, acks = await sim.run(broker.port, msg_id)
    assert rc == 0, f"连接被拒 rc={rc}"
    assert acks and acks[0].get("msg_id") == msg_id and acks[0].get("status") == "stored", acks

    # 数据真的入库了
    async with AsyncSessionLocal() as s3:
        row = (await s3.execute(
            select(Telemetry).where(Telemetry.msg_id == msg_id))).scalar_one_or_none()
        assert row is not None, "遥测未入库"

    # ── 4) 换密：旧口令立刻失效 ──────────────────────────────
    r = await client.post(f"{API}/devices/{dev.id}/credential/rotate",
                          headers=auth(token))
    assert r.status_code == 200, r.text
    new_plain = r.json()["password"]
    assert new_plain != plain
    async with AsyncSessionLocal() as s4:
        ok_old, why = await svc.authenticate(
            s4, username="dev-dtu-test-0001", password=plain,
            clientid="TEST00000000000001")
        assert ok_old is False and why == svc.DENY_BAD_PASSWORD
        ok_new, _ = await svc.authenticate(
            s4, username="dev-dtu-test-0001", password=new_plain,
            clientid="TEST00000000000001")
        assert ok_new is True

    # ── 5) 停用后无法再次连接 ────────────────────────────────
    r = await client.patch(f"{API}/devices/{dev.id}/credential/enabled",
                           json={"enabled": False, "reason": "测试结束回收"},
                           headers=auth(token))
    assert r.status_code == 200 and r.json()["credential"]["enabled"] is False

    sim2 = _DeviceSim("dev-dtu-test-0001", new_plain, "TEST00000000000001",
                      dev.device_id)
    rc2, acks2 = await sim2.run(broker.port, f"{dev.device_id}/1/2")
    assert rc2 == 5, f"停用后仍被接受 rc={rc2}"
    assert acks2 == []

    # ── 6) 重新启用后可以再连 ────────────────────────────────
    await client.patch(f"{API}/devices/{dev.id}/credential/enabled",
                       json={"enabled": True}, headers=auth(token))
    sim3 = _DeviceSim("dev-dtu-test-0001", new_plain, "TEST00000000000001",
                      dev.device_id)
    rc3, acks3 = await sim3.run(broker.port, f"{dev.device_id}/1/3")
    assert rc3 == 0 and acks3, "启用后仍连不上"


async def test_credential_rejects_mismatch_and_wrong_password(client, iso, broker):
    """clientid 不符 / 口令错 / 账号不存在：全部拒绝，且原因码可区分。"""
    fx, db = iso
    c = await fx.company(db, "OB-C2")
    gh = await fx.greenhouse(db, c, "OB-棚2")
    dev = await fx.device(db, gh, name="OB-DTU2")
    admin, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()

    r = await client.post(f"{API}/devices/{dev.id}/credential",
                          json={"asset_no": "DTU-TEST-0002",
                                "clientid": "TEST00000000000002"},
                          headers=auth(token))
    assert r.status_code == 201, r.text
    plain = r.json()["password"]
    username = r.json()["credential"]["username"]

    async with AsyncSessionLocal() as s:
        for kwargs, expect in (
            ({"username": username, "password": plain,
              "clientid": "OTHER-SERIAL"}, svc.DENY_CLIENTID),
            ({"username": username, "password": "wrong-one",
              "clientid": "TEST00000000000002"}, svc.DENY_BAD_PASSWORD),
            ({"username": "no-such-user", "password": plain,
              "clientid": "TEST00000000000002"}, svc.DENY_NO_USER),
            ({"username": "", "password": plain, "clientid": ""}, svc.DENY_NO_USER),
        ):
            ok, why = await svc.authenticate(s, **kwargs)
            assert ok is False and why == expect, (kwargs, why, expect)


async def test_credential_endpoints_permission_and_isolation(client, iso):
    """权限与公司隔离：越权一律拒绝，跨公司看不到。"""
    fx, db = iso
    c1 = await fx.company(db, "OB-A")
    c2 = await fx.company(db, "OB-B")
    gh1 = await fx.greenhouse(db, c1, "A棚")
    gh2 = await fx.greenhouse(db, c2, "B棚")
    dev1 = await fx.device(db, gh1, name="A-DTU")
    admin1, token1 = await fx.user(db, UserRole.ADMIN, c1)
    adminB, tokenB = await fx.user(db, UserRole.ADMIN, c2)
    viewer, vtoken = await fx.user(db, UserRole.VIEWER, c1)
    platform, ptoken = await fx.user(db, UserRole.ADMIN, c1, platform=True)
    await db.commit()

    # 只读角色不能登记凭据
    r = await client.post(f"{API}/devices/{dev1.id}/credential",
                          json={"asset_no": "X", "clientid": "CID-X"},
                          headers=auth(vtoken))
    assert r.status_code == 403, r.text

    r = await client.post(f"{API}/devices/{dev1.id}/credential",
                          json={"asset_no": "DTU-0003", "clientid": "TEST00000000000003"},
                          headers=auth(token1))
    assert r.status_code == 201, r.text

    # 另一家公司的管理员：既看到不到也不能操作
    for url in (f"{API}/devices/{dev1.id}/credential",
                f"{API}/devices/{dev1.id}/credential/install-sheet"):
        r = await client.get(url, headers=auth(tokenB))
        assert r.status_code == 404, (url, r.status_code, r.text)
    r = await client.post(f"{API}/devices/{dev1.id}/credential/rotate",
                          headers=auth(tokenB))
    assert r.status_code == 404, r.text

    # 重复登记被拒（一个设备一套凭据）
    r = await client.post(f"{API}/devices/{dev1.id}/credential",
                          json={"asset_no": "DTU-0003b", "clientid": "TEST00000000000003b"},
                          headers=auth(token1))
    assert r.status_code == 409, r.text


async def test_install_sheet_contains_no_secret(client, iso):
    """安装清单可以给安装人员打印，但绝不能含明文口令。"""
    fx, db = iso
    c = await fx.company(db, "OB-S")
    gh = await fx.greenhouse(db, c, "S棚")
    dev = await fx.device(db, gh, name="S-DTU")
    admin, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()

    r = await client.post(f"{API}/devices/{dev.id}/credential",
                          json={"asset_no": "DTU-0004", "clientid": "TEST00000000000004"},
                          headers=auth(token))
    assert r.status_code == 201, r.text
    secret = r.json()["password"]

    j = await client.get(f"{API}/devices/{dev.id}/credential/install-sheet",
                         headers=auth(token))
    assert j.status_code == 200
    data = j.json()
    assert data["mqtt"]["clientid"] == "TEST00000000000004"
    assert data["mqtt"]["username"]
    assert secret not in json.dumps(data, ensure_ascii=False)
    assert data["mqtt"]["topics"]["uplink"] == f"agri/device/{dev.device_id}/up"

    t = await client.get(f"{API}/devices/{dev.id}/credential/install-sheet?fmt=text",
                         headers=auth(token))
    assert t.status_code == 200
    content = t.json()["content"]
    assert "DTU-0004" in content and secret not in content


async def test_all_writes_are_audited(client, iso):
    """创建 / 换密 / 停用：三次写操作都必须在 audit_logs 有记录，且明细不含明文。"""
    fx, db = iso
    c = await fx.company(db, "OB-AU")
    gh = await fx.greenhouse(db, c, "AU棚")
    dev = await fx.device(db, gh, name="AU-DTU")
    admin, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()

    created = await client.post(
        f"{API}/devices/{dev.id}/credential",
        json={"asset_no": "DTU-0005", "clientid": "TEST00000000000005"},
        headers=auth(token))
    assert created.status_code == 201
    secret = created.json()["password"]
    await client.post(f"{API}/devices/{dev.id}/credential/rotate", headers=auth(token))
    await client.patch(f"{API}/devices/{dev.id}/credential/enabled",
                       json={"enabled": False}, headers=auth(token))

    async with AsyncSessionLocal() as s:
        logs = (await s.execute(
            select(AuditLog).where(AuditLog.resource == "mqtt_credential")
            .order_by(AuditLog.created_at))).scalars().all()
        actions = [l.action for l in logs]
        assert actions == ["create", "rotate", "disable"], actions
        # 审计明细可以有指纹，但不能有明文
        blob = json.dumps([l.detail or {} for l in logs], ensure_ascii=False)
        assert secret not in blob


async def test_emqx_authn_hook_fail_closed(client, iso):
    """EMQX 钩子：没配共享密钥时必须 503，配了才判 allows/deny。"""
    fx, db = iso
    c = await fx.company(db, "OB-H")
    gh = await fx.greenhouse(db, c, "H棚")
    dev = await fx.device(db, gh, name="H-DTU")
    admin, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()
    created = await client.post(
        f"{API}/devices/{dev.id}/credential",
        json={"asset_no": "DTU-0006", "clientid": "TEST00000000000006"},
        headers=auth(token))
    secret = created.json()["password"]
    username = created.json()["credential"]["username"]

    # 默认没配 → 503，不允许裸奔
    r = await client.post(f"{API}/emqx/authn",
                          json={"username": username, "password": secret,
                                "clientid": "TEST00000000000006"})
    assert r.status_code == 503, r.text

    settings.EMQX_AUTHN_TOKEN = "t3-test-token"
    try:
        good = await client.post(f"{API}/emqx/authn",
                                 json={"username": username, "password": secret,
                                       "clientid": "TEST00000000000006"},
                                 headers={"X-Authn-Token": "t3-test-token"})
        assert good.status_code == 200 and good.json()["result"] == "allow", good.text

        bad_token = await client.post(f"{API}/emqx/authn",
                                      json={"username": username, "password": secret,
                                            "clientid": "TEST00000000000006"},
                                      headers={"X-Authn-Token": "wrong"})
        assert bad_token.json()["result"] == "deny"

        bad_pwd = await client.post(f"{API}/emqx/authn",
                                    json={"username": username, "password": "nope",
                                          "clientid": "TEST00000000000006"},
                                    headers={"X-Authn-Token": "t3-test-token"})
        assert bad_pwd.json()["result"] == "deny"

        await client.patch(f"{API}/devices/{dev.id}/credential/enabled",
                           json={"enabled": False}, headers=auth(token))
        disabled = await client.post(f"{API}/emqx/authn",
                                     json={"username": username, "password": secret,
                                           "clientid": "TEST00000000000006"},
                                     headers={"X-Authn-Token": "t3-test-token"})
        assert disabled.json()["result"] == "deny"
    finally:
        settings.EMQX_AUTHN_TOKEN = ""
