"""隔离测试夹具：一次性创建公司/大棚/设备/点位/证据等数据，测试结束整体清理。

所有测试共用同一套创建与清理逻辑，避免各测试文件各自维护一份，
也避免测试之间在共享的测试库里互相污染。
"""
import secrets
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis
from app.models.models import (
    AuditLog, Company, Device, DeviceStatus, DeviceType, Greenhouse, Image,
    InspectionEvidence, InspectionPoint, MapVersion, RobotMapAlert,
    RobotMapRegistry, RobotPointConfig, RobotPointMapping, RobotTask,
    TaskStatus, TaskType, Telemetry, User,
)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class IsoFixture:
    def __init__(self):
        self.companies, self.greenhouses, self.devices = [], [], []
        self.tel_rows, self.images, self.tasks = [], [], []
        self.points, self.mappings, self.map_versions = [], [], []
        self.ev_rows, self.map_reg_rows, self.map_alerts = [], [], []
        self.users, self.tokens = [], []

    # ── 基础对象 ─────────────────────────────────────────────
    async def company(self, db, name):
        c = Company(id=str(uuid.uuid4()), name=name)
        db.add(c); self.companies.append(c.id); await db.flush()
        return c.id

    async def greenhouse(self, db, cid, name):
        gh = Greenhouse(id=str(uuid.uuid4()), name=name, company_id=cid, is_active=True)
        db.add(gh); self.greenhouses.append(gh.id); await db.flush()
        return gh

    async def device(self, db, gh, kind=DeviceType.SENSOR, name="fx-dev"):
        d = Device(id=str(uuid.uuid4()), device_id=uid("dev"), name=name,
                   device_type=kind, greenhouse_id=gh.id, status=DeviceStatus.ONLINE)
        db.add(d); self.devices.append(d.id); await db.flush()
        return d

    async def telemetry(self, db, gh, dev, cid):
        t = Telemetry(id=str(uuid.uuid4()), device_id=dev.id, greenhouse_id=gh.id,
                      timestamp=datetime.now(timezone.utc), data_type="telemetry",
                      temperature=20.0, company_id=cid)
        db.add(t); self.tel_rows.append(t.id); await db.flush()
        return t

    async def image(self, db, gh, dev, point_id=None, url=None):
        img = Image(id=str(uuid.uuid4()), device_id=dev.id, greenhouse_id=gh.id,
                    storage_url=url or f"http://x/{uuid.uuid4().hex}.jpg",
                    captured_at=datetime.now(timezone.utc), point_id=point_id)
        db.add(img); self.images.append(img.id); await db.flush()
        return img

    async def task(self, db, gh, dev, status=TaskStatus.PENDING.value):
        t = RobotTask(id=str(uuid.uuid4()), task_name="fx-task",
                      task_type=TaskType.INSPECTION, greenhouse_id=gh.id,
                      device_id=dev.id, status=status)
        db.add(t); self.tasks.append(t.id); await db.flush()
        return t

    async def user(self, db, role, company_id=None, platform=False):
        u = User(id=str(uuid.uuid4()), username=uid("u"), password_hash="fx-not-used",
                 role=role, is_active=True, company_id=company_id,
                 is_platform_operator=platform)
        db.add(u); self.users.append(u.id); await db.flush()
        token = secrets.token_urlsafe(24)
        redis = await get_redis()
        await redis.set(f"auth:token:{token}", u.id, ex=3600)
        self.tokens.append(token)
        return u, token

    # ── M2 对象 ──────────────────────────────────────────────
    async def point(self, db, gh, name, code=None):
        p = InspectionPoint(id=str(uuid.uuid4()), greenhouse_id=gh.id,
                            code=code or uid("P"), name=name)
        db.add(p); self.points.append(p.id); await db.flush()
        return p

    async def robot_mapping(self, db, robot_id, nav_code, point, gh):
        m = RobotPointMapping(id=str(uuid.uuid4()), robot_id=robot_id,
                              nav_point_code=nav_code, point_id=point.id,
                              greenhouse_id=gh.id, status="active", enabled=True)
        db.add(m); self.mappings.append(m.id); await db.flush()
        return m

    async def map_version(self, db, gh, version, points_snapshot=None, layout=None):
        mv = MapVersion(id=str(uuid.uuid4()), greenhouse_id=gh.id, version=version,
                        layout=layout or {"width": 10}, defaults={},
                        points_snapshot=points_snapshot or [], immutable=True)
        db.add(mv); self.map_versions.append(mv.id); await db.flush()
        return mv

    async def evidence(self, db, **kw):
        ev = InspectionEvidence(id=str(uuid.uuid4()), **kw)
        db.add(ev); self.ev_rows.append(ev.id); await db.flush()
        return ev

    async def map_registry(self, db, robot_id, map_version, gh=None):
        r = RobotMapRegistry(id=str(uuid.uuid4()), robot_id=robot_id,
                             greenhouse_id=gh.id if gh else None,
                             map_version=map_version, active=True)
        db.add(r); self.map_reg_rows.append(r.id); await db.flush()
        return r

    async def map_alert(self, db, robot_id, expected, reported, status="open"):
        a = RobotMapAlert(id=str(uuid.uuid4()), robot_id=robot_id,
                          expected_map_version=expected,
                          reported_map_version=reported, status=status)
        db.add(a); self.map_alerts.append(a.id); await db.flush()
        return a


# 删除顺序：子表在前、父表在后，且每一层单独提交，
# 免得 ORM 的批量排序（依赖信息不全时不可靠）把父表删到子表前面。
_WIPE_ORDER = (
    (InspectionEvidence, "ev_rows"),
    (Image,             "images"),
    (RobotTask,         "tasks"),
    (Telemetry,         "tel_rows"),
    (RobotPointMapping, "mappings"),
    (RobotPointConfig,  None),
    (InspectionPoint,   "points"),
    (MapVersion,        "map_versions"),
    (RobotMapRegistry,  "map_reg_rows"),
    (RobotMapAlert,     "map_alerts"),
    (Device,            "devices"),
    (Greenhouse,        "greenhouses"),
)


async def _cleanup(fx: IsoFixture):
    async with AsyncSessionLocal() as db:
        for model, attr in _WIPE_ORDER:
            ids = list(getattr(fx, attr)) if attr else []
            ghs = list(fx.greenhouses) if hasattr(model, "greenhouse_id") else []
            rows = []
            if ids:
                rows += (await db.execute(
                    select(model).where(model.id.in_(ids)))).scalars().all()
            for gid in ghs:
                rows += (await db.execute(
                    select(model).where(model.greenhouse_id == gid))).scalars().all()
            for row in rows:
                await db.delete(row)
            if rows:
                await db.commit()

        # users：其名下的 map_registry 等已在上面清掉，现在才能删
        for uid_ in fx.users:
            for r in (await db.execute(
                    select(AuditLog).where(AuditLog.user_id == uid_))).scalars().all():
                await db.delete(r)
            row = await db.get(User, uid_)
            if row: await db.delete(row)
        if fx.users:
            await db.commit()

        for cid in fx.companies:
            row = await db.get(Company, cid)
            if row: await db.delete(row)
        if fx.companies:
            await db.commit()
    redis = await get_redis()
    for t in fx.tokens:
        await redis.delete(f"auth:token:{t}")


@pytest.fixture
async def iso(schema_ready):
    fx = IsoFixture()
    async with AsyncSessionLocal() as db:
        try:
            yield fx, db
        finally:
            await db.rollback()
            await _cleanup(fx)
