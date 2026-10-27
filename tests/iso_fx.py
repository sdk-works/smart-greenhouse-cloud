"""隔离测试夹具：一次性创建公司/大棚/设备/点位/证据等数据，测试结束整体清理。

所有测试共用同一套创建与清理逻辑，避免各测试文件各自维护一份，
也避免测试之间在共享的测试库里互相污染。
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis
from app.models.models import (
    ActuatorCommand, ActuatorProfile, AiAdviceLog, Alert, AuditLog, Company, Device, DeviceBinding, DeviceStatus, DeviceType,
    Greenhouse, Image, Incident, IncidentEvent, InspectionBatch,
    InspectionEvidence, InspectionPoint, InspectionVisit, MapVersion,
    MqttCredential, RobotMapAlert, RobotMapRegistry, RobotPointConfig, RobotPointMapping,
    CommandStatus, PlantingBatch, RobotTask, TaskStatus, TaskType,
    Telemetry, User,
)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class IsoFixture:
    def __init__(self):
        self.companies, self.greenhouses, self.devices = [], [], []
        self.tel_rows, self.images, self.tasks = [], [], []
        self.points, self.mappings, self.map_versions = [], [], []
        self.ev_rows, self.map_reg_rows, self.map_alerts = [], [], []
        self.bindings, self.incidents, self.alerts = [], [], []
        self.batches, self.plantings, self.ai_logs, self.commands = [], [], [], []
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


    # ── M3 对象 ──────────────────────────────────────────────
    async def binding(self, db, dev, gh, point=None, channel="default",
                      version=1, start_at=None, end_at=None):
        b = DeviceBinding(id=str(uuid.uuid4()), device_id=dev.id,
                          greenhouse_id=gh.id,
                          point_id=point.id if point else None,
                          channel=channel, binding_version=version,
                          start_at=start_at or datetime.now(timezone.utc),
                          end_at=end_at)
        db.add(b); self.bindings.append(b.id); await db.flush()
        return b

    async def raw_telemetry(self, db, gh, dev, **kw):
        t = Telemetry(id=str(uuid.uuid4()), device_id=dev.id, greenhouse_id=gh.id,
                      **kw)
        db.add(t); self.tel_rows.append(t.id); await db.flush()
        return t

    async def incident(self, db, gh, title="异常", status="pending_confirm",
                       company_id=None):
        inc = Incident(id=str(uuid.uuid4()), code=uid("INC"), title=title,
                       category="environment", severity="warning", status=status,
                       source="manual", greenhouse_id=gh.id if gh else None,
                       company_id=company_id)
        db.add(inc); self.incidents.append(inc.id); await db.flush()
        return inc

    async def alert(self, db, incident=None, gh=None, rule="r1",
                    company_id=None):
        a = Alert(id=str(uuid.uuid4()), incident_id=incident.id if incident else None,
                  rule_code=rule, greenhouse_id=gh.id if gh else None,
                  metric="temperature", value=40.0, threshold=35.0,
                  company_id=company_id)
        db.add(a); self.alerts.append(a.id); await db.flush()
        return a

    async def planting_batch(self, db, gh, code, crop="番茄", zone=None,
                             status="planned", company_id=None):
        b = PlantingBatch(id=str(uuid.uuid4()), code=code, greenhouse_id=gh.id,
                          zone=zone, crop_type=crop, status=status,
                          company_id=company_id)
        db.add(b); self.plantings.append(b.id); await db.flush()
        return b

    async def ai_log(self, db, gh, advice=None, company_id=None, **kw):
        row = AiAdviceLog(id=str(uuid.uuid4()), greenhouse_id=gh.id, kind="advice",
                          advice=advice or [], company_id=company_id, **kw)
        db.add(row); self.ai_logs.append(row.id); await db.flush()
        return row

    async def command(self, db, dev, gh, action="open", status="pending"):
        import uuid as _u
        c = ActuatorCommand(id=str(_u.uuid4()), command_id=str(_u.uuid4()),
                            device_id=dev.id, greenhouse_id=gh.id, action=action,
                            status=CommandStatus(status),
                            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
        db.add(c); self.commands.append(c.id); await db.flush()
        return c

    async def batch(self, db, robot_id, status="running", last_event_at=None):
        from app.models.models import InspectionBatch
        b = InspectionBatch(id=str(uuid.uuid4()), robot_id=robot_id, status=status,
                             started_at=datetime.now(timezone.utc),
                             last_event_at=last_event_at or datetime.now(timezone.utc))
        db.add(b); self.batches.append(b.id); await db.flush()
        return b


# 删除顺序：子表在前、父表在后，且每一层单独提交，
# 免得 ORM 的批量排序（依赖信息不全时不可靠）把父表删到子表前面。
_WIPE_ORDER = (
    (AiAdviceLog,       "ai_logs"),
    (ActuatorCommand,    "commands"),
    (PlantingBatch,      "plantings"),
    (Alert,             "alerts"),
    (Incident,          "incidents"),
    (InspectionEvidence, "ev_rows"),
    (Image,             "images"),
    (RobotTask,         "tasks"),
    (InspectionVisit,   None),
    (Telemetry,         "tel_rows"),
    (RobotPointMapping, "mappings"),
    (RobotPointConfig,  None),
    (InspectionPoint,   "points"),
    (MapVersion,        "map_versions"),
    (RobotMapRegistry,  "map_reg_rows"),
    (RobotMapAlert,     "map_alerts"),
    (DeviceBinding,      "bindings"),
    (Device,            "devices"),
    (InspectionBatch,   "batches"),
    (Greenhouse,        "greenhouses"),
)


async def _cleanup(fx: IsoFixture):
    async with AsyncSessionLocal() as db:
        # 没有 greenhouse_id 的子表，按父 id 先清
        if fx.incidents:
            for r in (await db.execute(select(IncidentEvent).where(
                    IncidentEvent.incident_id.in_(fx.incidents)))).scalars().all():
                await db.delete(r)
            await db.commit()
        if fx.batches:
            for r in (await db.execute(select(InspectionVisit).where(
                    InspectionVisit.batch_id.in_(fx.batches)))).scalars().all():
                await db.delete(r)
            await db.commit()

        # mqtt_credentials 不挂在 greenhouse 下，只能按 device_id 清。
        # 不清的话下一轮测试登记新凭据会撞 "一个设备一套凭据" 的唯一约束。
        if fx.devices:
            dev_rows = (await db.execute(
                select(Device).where(Device.id.in_(fx.devices)))).scalars().all()
            dev_ids = [d.device_id for d in dev_rows]
            if dev_ids:
                for row in (await db.execute(
                        select(MqttCredential).where(
                            MqttCredential.device_id.in_(dev_ids)))).scalars().all():
                    await db.delete(row)
                await db.commit()

        # 控制契约 v1（2026-10-27）：actuator_profiles 以 device_id 为主键，
        # 必须先于设备删除清掉，否则依赖规则会试图置空主键而报错。
        if fx.devices:
            for pid in fx.devices:
                p = await db.get(ActuatorProfile, pid)
                if p is not None:
                    await db.delete(p)
            await db.commit()

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
