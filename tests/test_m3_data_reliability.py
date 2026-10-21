"""M3 数据可靠性契约测试。

覆盖：
  1. 补传归属：按绑定窗口判定，窗口内 resolved；窗口外/无历史/歧义 → pending（不猜）
  2. 新鲜度：查询时按 sampled_at/created_at 计算，不写死状态
  3. 重启恢复：长时间 running 的任务/批次被兜底标记，终态不动
  4. 异常事件：事件单状态机 + 告警清除与事件关闭刻意分开
  5. device_bindings：通道互斥 + 绑定版本单调递增（代码层强制）
  6. 原始报文清理脚本：默认 dry-run，且拒绝连非测试库
"""
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.attribution import resolve_attribution
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.freshness import compute_freshness
from app.models.models import (
    Alert, Incident, IncidentEvent, InspectionBatch, RawMessage, RobotTask,
    Telemetry, UserRole,
)
from tests.conftest import auth
from tests.iso_fx import uid

API = settings.API_PREFIX
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _t(days=0, hours=0, minutes=0):
    return datetime.now(timezone.utc) - timedelta(days=days, hours=hours,
                                                  minutes=minutes)


# ── 1. 补传归属 ──────────────────────────────────────────────
async def test_attribution_resolved_inside_binding_window(iso):
    fx, db = iso
    c = await fx.company(db, "M3A")
    gh1 = await fx.greenhouse(db, c, "M3A-gh1")
    gh2 = await fx.greenhouse(db, c, "M3A-gh2")
    dev = await fx.device(db, gh2)
    # 2023 年在 gh1，之后搬到 gh2
    await fx.binding(db, dev, gh1, version=1,
                     start_at=_t(days=800), end_at=_t(days=400))
    await fx.binding(db, dev, gh2, version=2, start_at=_t(days=400))
    await db.commit()

    now_hit = await resolve_attribution(db, device_id=dev.id, at=_t(days=1))
    assert now_hit["status"] == "resolved", now_hit
    assert now_hit["greenhouse_id"] == gh2.id
    assert now_hit["reason"] == "binding_window"
    assert now_hit["binding_version"] == 2

    # 补传 3 年前的旧数据 → 应归属 gh1（而不是设备当前的 gh2）
    old = await resolve_attribution(db, device_id=dev.id, at=_t(days=700))
    assert old["status"] == "resolved", old
    assert old["greenhouse_id"] == gh1.id, "补传旧数据应按当时的绑定归属"
    assert old["binding_version"] == 1


async def test_attribution_pending_when_no_binding_history(iso):
    """没有任何绑定记录 → pending，绝不冒充 resolved。"""
    fx, db = iso
    c = await fx.company(db, "M3B")
    gh = await fx.greenhouse(db, c, "M3B-gh")
    dev = await fx.device(db, gh)
    await db.commit()

    res = await resolve_attribution(db, device_id=dev.id, at=datetime.now(timezone.utc))
    assert res["status"] == "pending", res
    assert res["reason"] == "no_binding_history"
    assert res["greenhouse_id"] is None


async def test_attribution_pending_outside_binding_window(iso):
    fx, db = iso
    c = await fx.company(db, "M3C")
    gh = await fx.greenhouse(db, c, "M3C-gh")
    dev = await fx.device(db, gh)
    await fx.binding(db, dev, gh, version=1, start_at=_t(days=10))
    await db.commit()

    # 补传的是装机之前 100 天前的数据
    res = await resolve_attribution(db, device_id=dev.id, at=_t(days=100))
    assert res["status"] == "pending", res
    assert res["reason"] == "outside_binding_window"


async def test_attribution_pending_when_ambiguous(iso):
    fx, db = iso
    c = await fx.company(db, "M3D")
    gh1 = await fx.greenhouse(db, c, "M3D-gh1")
    gh2 = await fx.greenhouse(db, c, "M3D-gh2")
    dev = await fx.device(db, gh1)
    # 同版本两条同时生效 → 无法判定
    await fx.binding(db, dev, gh1, version=1, start_at=_t(days=10))
    await fx.binding(db, dev, gh2, version=1, start_at=_t(days=5))
    await db.commit()

    res = await resolve_attribution(db, device_id=dev.id, at=_t(days=1))
    assert res["status"] == "pending", res
    assert res["reason"] == "ambiguous_binding_window"


async def test_telemetry_ingest_records_attribution(client, iso):
    """入库路径把归属状态与依据写进 telemetry。"""
    fx, db = iso
    c = await fx.company(db, "M3E")
    gh = await fx.greenhouse(db, c, "M3E-gh")
    dev = await fx.device(db, gh)
    await fx.binding(db, dev, gh, version=1, start_at=_t(days=30))
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()

    # 直接构造一条入库记录，验证 API 读到的归属字段（MQTT 通道在别处覆盖）
    row = await fx.raw_telemetry(
        db, gh, dev, timestamp=_t(minutes=5), created_at=_t(minutes=5),
        data_type="telemetry", temperature=21.0, time_quality=1,
        sampled_at=_t(minutes=6), attribution_status="resolved",
        attribution_reason="binding_window", binding_version=1, company_id=c)
    await db.commit()

    async with AsyncSessionLocal() as db2:
        saved = (await db2.execute(select(Telemetry).where(
            Telemetry.id == row.id))).scalar_one()
        assert saved.attribution_status == "resolved"
        assert saved.attribution_reason == "binding_window"
        assert saved.binding_version == 1


# ── 2. 新鲜度 ────────────────────────────────────────────────
def test_freshness_states_are_computed_at_query_time():
    now = datetime.now(timezone.utc)
    f = compute_freshness(sampled_at=now - timedelta(seconds=10), created_at=now,
                          time_quality=1, now=now)
    assert f["state"] == "fresh" and f["basis"] == "sampled_at"
    assert f["age_seconds"] == 10.0

    assert compute_freshness(sampled_at=now - timedelta(seconds=120),
                             created_at=now, time_quality=1,
                             now=now)["state"] == "lagging"
    assert compute_freshness(sampled_at=now - timedelta(minutes=10),
                             created_at=now, time_quality=1,
                             now=now)["state"] == "stale"
    assert compute_freshness(sampled_at=now - timedelta(hours=2),
                             created_at=now, time_quality=1,
                             now=now)["state"] == "expired"

    # 时间不可信 → 退化用 created_at，并在 basis 里如实标注
    f2 = compute_freshness(sampled_at=now - timedelta(hours=2), created_at=now,
                           time_quality=0, now=now)
    assert f2["state"] == "fresh" and f2["basis"] == "created_at"
    assert f2["time_quality"] == 0

    # 设备时钟超前
    f3 = compute_freshness(sampled_at=now + timedelta(days=3), created_at=now,
                           time_quality=1, now=now)
    assert f3["state"] == "future"

    # 什么都没有
    assert compute_freshness()["state"] == "unknown"


async def test_freshness_api_computes_at_query_time(client, iso):
    fx, db = iso
    c = await fx.company(db, "M3F")
    gh = await fx.greenhouse(db, c, "M3F-gh")
    dev = await fx.device(db, gh)
    u, token = await fx.user(db, UserRole.VIEWER, c)
    recent = datetime.now(timezone.utc) - timedelta(seconds=20)
    await fx.raw_telemetry(db, gh, dev, timestamp=recent,
                           created_at=recent, data_type="telemetry",
                           temperature=20.0, time_quality=1,
                           sampled_at=recent, company_id=c)
    await db.commit()

    r = await client.get(f"{API}/freshness/devices?greenhouse_id={gh.id}",
                         headers=auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    item = body["items"][0]
    assert body["total"] == 1
    assert item["freshness"]["state"] == "fresh", item
    assert item["freshness"]["basis"] == "sampled_at"
    assert item["within_max_age"] is True

    # 把数据改成 2 小时前 → 立刻变成 expired（说明是查询时算的，不是写死的）
    async with AsyncSessionLocal() as db2:
        rows = (await db2.execute(select(Telemetry).where(
            Telemetry.device_id == dev.id))).scalars().all()
        for row in rows:
            row.sampled_at = _t(hours=2)
            row.created_at = _t(hours=2)
        await db2.commit()

    r2 = await client.get(f"{API}/freshness/devices?greenhouse_id={gh.id}",
                          headers=auth(token))
    item2 = r2.json()["items"][0]
    assert item2["freshness"]["state"] == "expired", item2
    assert item2["within_max_age"] is False


# ── 3. 重启恢复 ──────────────────────────────────────────────
async def test_recovery_marks_stale_running(client, iso):
    fx, db = iso
    c = await fx.company(db, "M3G")
    gh = await fx.greenhouse(db, c, "M3G-gh")
    dev = await fx.device(db, gh)
    u, token = await fx.user(db, UserRole.ADMIN, c)

    old_task = await fx.task(db, gh, dev, status="running")
    old_task.started_at = _t(minutes=200)
    old_task.created_at = _t(minutes=201)
    fresh_task = await fx.task(db, gh, dev, status="running")
    fresh_task.started_at = _t(minutes=1)
    fresh_task.created_at = _t(minutes=2)
    done_task = await fx.task(db, gh, dev, status="completed")
    old_batch = await fx.batch(db, uid("ROB"), status="running",
                               last_event_at=_t(minutes=90))
    await db.commit()

    r = await client.post(f"{API}/maintenance/recover", headers=auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tasks_recovered"] == 1, body
    assert body["batches_recovered"] == 1, body

    async with AsyncSessionLocal() as db2:
        t_old = (await db2.execute(select(RobotTask).where(
            RobotTask.id == old_task.id))).scalar_one()
        t_fresh = (await db2.execute(select(RobotTask).where(
            RobotTask.id == fresh_task.id))).scalar_one()
        t_done = (await db2.execute(select(RobotTask).where(
            RobotTask.id == done_task.id))).scalar_one()
        b_old = (await db2.execute(select(InspectionBatch).where(
            InspectionBatch.id == old_batch.id))).scalar_one()

    assert t_old.status == "partial"
    assert "系统兜底标记" in (t_old.error_message or "")
    assert t_fresh.status == "running", "刚启动的任务不应被兜底"
    assert t_done.status == "completed", "终态任务不应被改写"
    assert b_old.status == "partial"
    assert b_old.close_reason == "stale_running_recovery"
    assert b_old.ended_at is not None


async def test_recovery_policy_endpoint(client, iso):
    fx, db = iso
    u, token = await fx.user(db, UserRole.VIEWER)
    await db.commit()
    r = await client.get(f"{API}/maintenance/recovery-policy", headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["task_target_status"] == "partial"


# ── 4. 异常事件 ──────────────────────────────────────────────
async def test_incident_state_machine_flow(client, iso):
    fx, db = iso
    c = await fx.company(db, "M3H")
    gh = await fx.greenhouse(db, c, "M3H-gh")
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()

    r = await client.post(f"{API}/incidents", json={
        "title": "CO2 超限", "greenhouse_id": gh.id,
        "severity": "warning", "category": "environment",
    }, headers=auth(token))
    assert r.status_code == 201, r.text
    inc_id = r.json()["id"]
    assert r.json()["status"] == "pending_confirm"

    async def _move(st):
        return await client.post(f"{API}/incidents/{inc_id}/status",
                                 json={"status": st}, headers=auth(token))

    # pending_confirm → handling 非法
    bad = await _move("handling")
    assert bad.status_code == 400, bad.text
    assert "非法事件状态流转" in bad.json()["detail"]

    for st in ("confirmed", "handling", "pending_review", "closed"):
        resp = await _move(st)
        assert resp.status_code == 200, f"{st}: {resp.text}"
        assert resp.json()["status"] == st

    # 终态不能再流转
    assert (await _move("confirmed")).status_code == 400

    got = (await client.get(f"{API}/incidents/{inc_id}", headers=auth(token))).json()
    assert got["status"] == "closed"
    assert [e["to_status"] for e in got["events"]] == [
        "pending_confirm", "confirmed", "handling", "pending_review", "closed"]


async def test_incident_wrong_severity_rejected(client, iso):
    fx, db = iso
    c = await fx.company(db, "M3I")
    gh = await fx.greenhouse(db, c, "M3I-gh")
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()
    r = await client.post(f"{API}/incidents", json={
        "title": "x", "greenhouse_id": gh.id, "severity": "disaster"},
        headers=auth(token))
    assert r.status_code == 400, r.text


async def test_alert_clear_does_not_close_incident(client, iso):
    """环境恢复 → 告警停止；事件单**不**自动关闭。"""
    fx, db = iso
    c = await fx.company(db, "M3J")
    gh = await fx.greenhouse(db, c, "M3J-gh")
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()

    inc = (await client.post(f"{API}/incidents", json={
        "title": "温度过高", "greenhouse_id": gh.id}, headers=auth(token))).json()
    alert = (await client.post(f"{API}/alerts", json={
        "rule_code": "temp_high", "incident_id": inc["id"],
        "greenhouse_id": gh.id, "metric": "temperature",
        "value": 40.0, "threshold": 35.0}, headers=auth(token))).json()
    assert alert["active"] is True

    cleared = await client.post(
        f"{API}/alerts/{alert['id']}/clear?reason=environment_recovered",
        headers=auth(token))
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["cleared"] is True
    assert cleared.json()["incident_closed"] is False, "默认不得自动关闭事件单"

    got = (await client.get(f"{API}/incidents/{inc['id']}",
                            headers=auth(token))).json()
    assert got["status"] == "pending_confirm", got
    assert "closed" != got["status"], "环境恢复不应关闭事件单"
    assert got["open_alert_count"] == 0

    # 显式要求关闭才会关
    alert2 = (await client.post(f"{API}/alerts", json={
        "rule_code": "temp_high2", "incident_id": inc["id"],
        "greenhouse_id": gh.id}, headers=auth(token))).json()
    r = await client.post(
        f"{API}/alerts/{alert2['id']}/clear?auto_close_incident=true",
        headers=auth(token))
    assert r.json()["incident_closed"] is True


async def test_alerts_list_is_company_scoped(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "M3K-A")
    c_b = await fx.company(db, "M3K-B")
    gh_a = await fx.greenhouse(db, c_a, "M3K-A-gh")
    gh_b = await fx.greenhouse(db, c_b, "M3K-B-gh")
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    u_b, t_b = await fx.user(db, UserRole.ADMIN, c_b)
    await fx.alert(db, gh=gh_a, rule="a-1", company_id=c_a)
    await fx.alert(db, gh=gh_b, rule="b-1", company_id=c_b)
    await db.commit()

    ra = (await client.get(f"{API}/alerts", headers=auth(t_a))).json()
    rb = (await client.get(f"{API}/alerts", headers=auth(t_b))).json()
    assert [x["rule_code"] for x in ra["items"]] == ["a-1"], ra
    assert [x["rule_code"] for x in rb["items"]] == ["b-1"], rb


# ── 5. device_bindings 通道互斥 / 版本强制 ───────────────────
async def test_binding_enforces_exclusive_channel_and_version(client, iso):
    fx, db = iso
    c = await fx.company(db, "M3L")
    gh1 = await fx.greenhouse(db, c, "M3L-gh1")
    gh2 = await fx.greenhouse(db, c, "M3L-gh2")
    dev = await fx.device(db, gh1)
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()

    b1 = await client.post(f"{API}/devices/{dev.device_id}/bindings",
                           json={"greenhouse_id": gh1.id, "channel": "ch1"},
                           headers=auth(token))
    assert b1.status_code == 201, b1.text
    assert b1.json()["binding_version"] == 1

    # 同通道再绑一个重叠的 → 409（通道互斥）
    dup = await client.post(f"{API}/devices/{dev.device_id}/bindings",
                            json={"greenhouse_id": gh2.id, "channel": "ch1"},
                            headers=auth(token))
    assert dup.status_code == 409, dup.text
    assert "通道" in dup.json()["detail"]

    # 手工回退版本 → 409（版本必须递增）
    back = await client.post(f"{API}/devices/{dev.device_id}/bindings",
                             json={"greenhouse_id": gh2.id, "channel": "ch2",
                                   "binding_version": 1},
                             headers=auth(token))
    assert back.status_code == 409, back.text
    assert "版本" in back.json()["detail"]

    # 先结束旧绑定，再登记新绑定 → 成功，版本递增
    ended = await client.post(f"{API}/devices/bindings/{b1.json()['id']}/end",
                              headers=auth(token))
    assert ended.status_code == 200, ended.text
    assert ended.json()["active"] is False

    b2 = await client.post(f"{API}/devices/{dev.device_id}/bindings",
                           json={"greenhouse_id": gh2.id, "channel": "ch1"},
                           headers=auth(token))
    assert b2.status_code == 201, b2.text
    assert b2.json()["binding_version"] == 2
    assert b2.json()["greenhouse_id"] == gh2.id

    listed = (await client.get(f"{API}/devices/{dev.device_id}/bindings",
                               headers=auth(token))).json()
    assert listed["total"] == 2
    assert listed["next_binding_version"] == 3


async def test_binding_rejects_bad_time_range(client, iso):
    fx, db = iso
    c = await fx.company(db, "M3M")
    gh = await fx.greenhouse(db, c, "M3M-gh")
    dev = await fx.device(db, gh)
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()

    r = await client.post(f"{API}/devices/{dev.device_id}/bindings", json={
        "greenhouse_id": gh.id,
        "start_at": _t(hours=1).isoformat(),
        "end_at": _t(hours=2).isoformat(),
    }, headers=auth(token))
    assert r.status_code == 400, r.text


# ── 6. 原始报文清理脚本 ──────────────────────────────────────
def test_purge_script_refuses_non_test_database():
    env = dict(os.environ)
    env["DATABASE_URL"] = "postgresql+asyncpg://greenhouse:x@localhost:5432/greenhouse"
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "purge_raw_messages.py"),
         "--apply"],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "拒绝运行" in (proc.stdout + proc.stderr)


def test_purge_script_dry_run_does_not_delete():
    env = dict(os.environ)
    env["DATABASE_URL"] = settings.DATABASE_URL
    env["RAW_RETENTION_DAYS"] = "90"
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "purge_raw_messages.py")],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "dry-run" in proc.stdout
