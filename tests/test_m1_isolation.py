"""M1 隔离收尾契约测试。

覆盖：
  1. /dashboard/stats 各项计数按公司范围聚合——A 公司新增数据只影响 A，不影响 B。
  2. 平台运营跨公司访问（?company_id=）落 audit_logs；访问自己公司不落。
  3. 业务用户传其它公司 company_id → 403。
  4. Redis 缓存键带公司前缀，同 device_id 在两家公司互不串数据。
  5. 控制/任务下发必须校验设备归属。
  6. MQTT 主题/身份约束函数的形态校验（纯函数，不连 EMQX）。
"""
import secrets
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.mqtt_policy import (
    command_topic, uplink_topic, validate_client_id, validate_uplink_topic,
)
from app.core.redis import CacheService, get_redis
from app.models.models import (
    Company, Device, DeviceStatus, DeviceType, Greenhouse, Image, RobotTask,
    TaskStatus, TaskType, Telemetry, User, UserRole, AuditLog,
)
from tests.conftest import auth

API = settings.API_PREFIX
STATS = f"{API}/dashboard/stats"

_KEYS = ("total_greenhouses", "total_devices", "online_devices", "active_tasks",
         "today_telemetry_count", "today_images_count")


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class _Fixture:
    """一次性创建的隔离数据，测试结束后整体清理。"""

    def __init__(self):
        self.companies = []
        self.greenhouses = []
        self.devices = []
        self.tel_rows = []
        self.images = []
        self.tasks = []
        self.users = []
        self.tokens = []

    async def company(self, db, name):
        c = Company(id=str(uuid.uuid4()), name=name)
        db.add(c)
        self.companies.append(c.id)
        await db.flush()
        return c.id

    async def greenhouse(self, db, cid, name):
        gh = Greenhouse(id=str(uuid.uuid4()), name=name, company_id=cid, is_active=True)
        db.add(gh)
        self.greenhouses.append(gh.id)
        await db.flush()
        return gh

    async def device(self, db, gh, kind=DeviceType.SENSOR):
        d = Device(id=str(uuid.uuid4()), device_id=_uid("dev"), name="m1", device_type=kind,
                   greenhouse_id=gh.id, status=DeviceStatus.ONLINE)
        db.add(d)
        self.devices.append(d.id)
        await db.flush()
        return d

    async def telemetry(self, db, gh, dev, cid):
        t = Telemetry(id=str(uuid.uuid4()), device_id=dev.id, greenhouse_id=gh.id,
                      timestamp=datetime.now(timezone.utc), data_type="telemetry",
                      temperature=20.0, company_id=cid)
        db.add(t)
        self.tel_rows.append(t.id)
        await db.flush()
        return t

    async def image(self, db, gh, dev):
        img = Image(id=str(uuid.uuid4()), device_id=dev.id, greenhouse_id=gh.id,
                    storage_url=f"http://x/{uuid.uuid4().hex}.jpg",
                    captured_at=datetime.now(timezone.utc))
        db.add(img)
        self.images.append(img.id)
        await db.flush()
        return img

    async def task(self, db, gh, dev):
        t = RobotTask(id=str(uuid.uuid4()), task_name="m1", task_type=TaskType.INSPECTION,
                      greenhouse_id=gh.id, device_id=dev.id, status=TaskStatus.PENDING)
        db.add(t)
        self.tasks.append(t.id)
        await db.flush()
        return t

    async def user(self, db, role=UserRole.ADMIN, company_id=None, platform=False):
        u = User(id=str(uuid.uuid4()), username=_uid("u"), password_hash="m1-not-used",
                 role=role, is_active=True, company_id=company_id,
                 is_platform_operator=platform)
        db.add(u)
        self.users.append(u.id)
        await db.flush()
        token = secrets.token_urlsafe(24)
        redis = await get_redis()
        await redis.set(f"auth:token:{token}", u.id, ex=3600)
        self.tokens.append(token)
        return u, token


@pytest.fixture
async def iso():
    fx = _Fixture()
    async with AsyncSessionLocal() as db:
        try:
            yield fx, db
        finally:
            await db.rollback()
            await _cleanup(fx)


async def _cleanup(fx):
    async with AsyncSessionLocal() as db:
        for tid in fx.tasks:
            row = await db.get(RobotTask, tid)
            if row: await db.delete(row)
        for iid in fx.images:
            row = await db.get(Image, iid)
            if row: await db.delete(row)
        for tid in fx.tel_rows:
            row = await db.get(Telemetry, tid)
            if row: await db.delete(row)
        for did in fx.devices:
            row = await db.get(Device, did)
            if row: await db.delete(row)
        for gid in fx.greenhouses:
            row = await db.get(Greenhouse, gid)
            if row: await db.delete(row)
        for uid in fx.users:
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.user_id == uid))).scalars().all()
            for r in rows:
                await db.delete(r)
            row = await db.get(User, uid)
            if row: await db.delete(row)
        await db.commit()
        for cid in fx.companies:
            row = await db.get(Company, cid)
            if row: await db.delete(row)
        await db.commit()
    redis = await get_redis()
    for t in fx.tokens:
        await redis.delete(f"auth:token:{t}")


async def _seed(fx, db, cid, prefix):
    """在某公司下放一套完整数据：大棚 + 设备 + 遥测 + 图片 + 任务。"""
    gh = await fx.greenhouse(db, cid, f"{prefix}-gh")
    dev = await fx.device(db, gh)
    await fx.telemetry(db, gh, dev, cid)
    await fx.image(db, gh, dev)
    await fx.task(db, gh, dev)
    await db.commit()
    return gh, dev


# ── 1. 仪表盘按公司聚合 ──────────────────────────────────────
async def test_stats_counts_are_company_scoped(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "A")
    c_b = await fx.company(db, "B")
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    u_b, t_b = await fx.user(db, UserRole.ADMIN, c_b)
    await db.commit()

    r_a0 = (await client.get(STATS, headers=auth(t_a))).json()
    r_b0 = (await client.get(STATS, headers=auth(t_b))).json()

    # 给 A 公司新增一整套数据
    await _seed(fx, db, c_a, "A")

    r_a1 = (await client.get(STATS, headers=auth(t_a))).json()
    r_b1 = (await client.get(STATS, headers=auth(t_b))).json()

    for k in _KEYS:
        assert r_a1[k] == r_a0[k] + 1, f"A 公司新增后 {k} 应 +1：{r_a0[k]} -> {r_a1[k]}"
        assert r_b1[k] == r_b0[k], f"B 公司统计不应受 A 数据影响：{k} {r_b0[k]} -> {r_b1[k]}"

    # 再给 B 加一套，A 不受影响
    await _seed(fx, db, c_b, "B")
    r_a2 = (await client.get(STATS, headers=auth(t_a))).json()
    r_b2 = (await client.get(STATS, headers=auth(t_b))).json()
    for k in _KEYS:
        assert r_a2[k] == r_a1[k], f"A 统计不应受 B 数据影响：{k}"
        assert r_b2[k] == r_b1[k] + 1, f"B 公司新增后 {k} 应 +1"


async def test_platform_sees_at_least_as_much_as_tenant(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "A")
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    u_p, t_p = await fx.user(db, UserRole.ADMIN, None, platform=True)
    await db.commit()

    sa = (await client.get(STATS, headers=auth(t_a))).json()
    sp = (await client.get(STATS, headers=auth(t_p))).json()
    for k in _KEYS:
        assert sp[k] >= sa[k], f"平台视角 {k} 不应少于租户视角"
    assert sp["scope"]["platform_view"] is True


# ── 2/3. 跨公司审计 ──────────────────────────────────────────
async def test_platform_cross_company_access_writes_audit_log(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "A")
    c_b = await fx.company(db, "B")
    u_p, t_p = await fx.user(db, UserRole.ADMIN, c_a, platform=True)
    await db.commit()

    r = await client.get(f"{STATS}?company_id={c_b}", headers=auth(t_p))
    assert r.status_code == 200, r.text

    rows = (await db.execute(
        select(AuditLog).where(AuditLog.user_id == u_p.id))).scalars().all()
    assert len(rows) == 1, f"跨公司访问应落 1 条审计，实际 {len(rows)}"
    log = rows[0]
    assert log.action == "cross_company_read", log.action
    assert log.resource == "dashboard.stats"
    assert log.detail["target_company_id"] == c_b
    assert log.detail["actor_company_id"] == c_a
    assert log.created_at is not None


async def test_platform_viewing_own_company_writes_no_audit(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "A")
    u_p, t_p = await fx.user(db, UserRole.ADMIN, c_a, platform=True)
    await db.commit()

    r = await client.get(f"{STATS}?company_id={c_a}", headers=auth(t_p))
    assert r.status_code == 200, r.text
    rows = (await db.execute(
        select(AuditLog).where(AuditLog.user_id == u_p.id))).scalars().all()
    assert rows == [], "访问自己公司不应落跨公司审计"


async def test_tenant_cannot_read_other_company_stats(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "A")
    c_b = await fx.company(db, "B")
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    await db.commit()

    r = await client.get(f"{STATS}?company_id={c_b}", headers=auth(t_a))
    assert r.status_code == 403, r.text
    assert "无权访问其它公司" in r.json()["detail"]

    ok = await client.get(f"{STATS}?company_id={c_a}", headers=auth(t_a))
    assert ok.status_code == 200, ok.text


# ── 4. Redis 缓存键隔离 ──────────────────────────────────────
async def test_cache_keys_are_company_prefixed(schema_ready):
    c_a, c_b = str(uuid.uuid4()), str(uuid.uuid4())
    redis = await get_redis()
    ca, cb = CacheService(redis, company_id=c_a), CacheService(redis, company_id=c_b)
    dev = _uid("dev")

    assert ca.make_key(f"device:online:{dev}") == f"c:{c_a}:device:online:{dev}"
    assert cb.make_key(f"device:online:{dev}") == f"c:{c_b}:device:online:{dev}"
    assert ca.make_key("x") != cb.make_key("x")

    await ca.set_device_online(dev, True)
    assert await ca.get_device_online(dev) is True
    assert await cb.get_device_online(dev) is False, "B 公司不应读到 A 公司的在线缓存"

    await ca.set_device_status(dev, {"temperature": 25})
    assert await ca.get_device_status(dev) == {"temperature": 25}
    assert await cb.get_device_status(dev) is None

    await ca.delete(f"device:online:{dev}")


async def test_device_status_cache_read_uses_device_company(client, iso):
    """同一个 device_id 的在线缓存在两家公司下读到的值互不干扰。"""
    fx, db = iso
    c_a = await fx.company(db, "A")
    c_b = await fx.company(db, "B")
    redis = await get_redis()
    dev = _uid("dev")
    await CacheService(redis, company_id=c_a).set_device_online(dev, True)

    gh_a = await fx.greenhouse(db, c_a, "A-gh")
    d_a = await fx.device(db, gh_a)
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    gh_b = await fx.greenhouse(db, c_b, "B-gh")
    d_b = await fx.device(db, gh_b)
    u_b, t_b = await fx.user(db, UserRole.ADMIN, c_b)
    await db.commit()

    # 用真实设备 id 覆盖，验证读路径确实用了"设备所属公司"作为前缀
    await CacheService(redis, company_id=c_a).set_device_online(d_a.device_id, True)
    ra = (await client.get(f"{API}/devices/{d_a.id}", headers=auth(t_a))).json()
    rb = (await client.get(f"{API}/devices/{d_b.id}", headers=auth(t_b))).json()
    assert ra["is_online"] is True, "A 公司设备应能读到本公司的在线缓存"
    assert rb["is_online"] is False, "B 公司设备不应读到 A 公司的在线缓存"


# ── 5. 控制下发归属校验 ──────────────────────────────────────
async def test_command_to_foreign_device_is_rejected(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "A")
    c_b = await fx.company(db, "B")
    gh_b = await fx.greenhouse(db, c_b, "B-gh")
    d_b = await fx.device(db, gh_b)
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    u_b, t_b = await fx.user(db, UserRole.ADMIN, c_b)
    u_p, t_p = await fx.user(db, UserRole.ADMIN, c_a, platform=True)
    await db.commit()

    r = await client.post(f"{API}/devices/{d_b.id}/command",
                          json={"command": "start"}, headers=auth(t_a))
    assert r.status_code == 403, r.text
    assert "不属于你所在公司" in r.json()["detail"]

    # 本公司设备放行到 MQTT 层（测试环境 MQTT 未连接 → 503），不是 403
    own = await client.post(f"{API}/devices/{d_b.id}/command",
                            json={"command": "start"}, headers=auth(t_b))
    assert own.status_code == 503, own.text

    # 平台运营可跨公司（同样走到 503）
    plat = await client.post(f"{API}/devices/{d_b.id}/command",
                             json={"command": "start"}, headers=auth(t_p))
    assert plat.status_code == 503, plat.text


async def test_task_dispatch_to_foreign_device_is_rejected(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "A")
    c_b = await fx.company(db, "B")
    gh_b = await fx.greenhouse(db, c_b, "B-gh")
    d_b = await fx.device(db, gh_b)
    task = await fx.task(db, gh_b, d_b)
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    u_b, t_b = await fx.user(db, UserRole.ADMIN, c_b)
    await db.commit()

    r = await client.post(f"{API}/tasks/{task.id}/dispatch", headers=auth(t_a))
    assert r.status_code == 403, r.text
    assert "不属于你所在公司" in r.json()["detail"]

    own = await client.post(f"{API}/tasks/{task.id}/dispatch", headers=auth(t_b))
    assert own.status_code == 503, own.text


# ── 6. MQTT 主题/身份约束（纯函数） ──────────────────────────
def test_mqtt_topic_constraints():
    assert uplink_topic("ROB1") == "agri/robot/ROB1/up"
    assert command_topic("ROB1") == "agri/robot/ROB1/command"
    assert validate_uplink_topic("agri/robot/ROB1/up", "ROB1") is True
    assert validate_uplink_topic("agri/device/D1/up", "D1") is True
    assert validate_uplink_topic("agri/robot/ROB2/up", "ROB1") is False
    assert validate_uplink_topic("agri/robot/#", "ROB1") is False
    assert validate_uplink_topic("agri/robot/+/up", "ROB1") is False
    assert validate_uplink_topic("", "ROB1") is False
    assert validate_client_id("device_ROB1", "ROB1") is True
    assert validate_client_id("ROB1", "ROB1") is False
    assert validate_client_id("device_ROB2", "ROB1") is False
