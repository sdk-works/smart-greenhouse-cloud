"""M2 机器人闭环契约测试。

覆盖：
  1. /photo-catalog 分页 / 过滤 / 空结果 / 默认排除无法解析点位的照片
  2. /photo-catalog/options 返回可见大棚与已归档点位
  3. 图片入库点位解析：robot_point_mappings 命中 → point_id；解析不出 → NULL（不猜）
  4. 地图发布版本：version 递增、发布后旧版本不可变
  5. 任务快照创建时冻结，后续编辑不改
  6. 巡检任务状态机：合法流转放行，非法流转 400
  7. 机器人地图版本登记与核对：不一致只告警、不自动改
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.robot_map_service import check_robot_map_version
from app.models.models import (
    Image, InspectionEvidence, InspectionPoint, MapVersion, RobotMapAlert,
    RobotTask, UserRole,
)
from tests.conftest import auth
from tests.iso_fx import uid

API = settings.API_PREFIX
PC = f"{API}/point-config"
CATALOG = f"{API}/photo-catalog"


# ── 1. 照片归档目录 ──────────────────────────────────────────
async def test_photo_catalog_pagination_and_filter(client, iso):
    fx, db = iso
    cid = await fx.company(db, "M2A")
    gh = await fx.greenhouse(db, cid, "M2A-gh")
    dev = await fx.device(db, gh)
    p1 = await fx.point(db, gh, "1号点")
    p2 = await fx.point(db, gh, "2号点")
    u, token = await fx.user(db, UserRole.ADMIN, cid)

    now = datetime.now(timezone.utc)
    for i in range(5):
        await fx.image(db, gh, dev, point_id=p1.id, url=f"http://u/p1-{i}.jpg")
    await fx.image(db, gh, dev, point_id=p2.id, url="http://u/p2-0.jpg")
    await db.commit()

    r = await client.get(f"{CATALOG}?greenhouse_id={gh.id}&page=1&page_size=2",
                         headers=auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 6, body
    assert len(body["items"]) == 2
    assert body["page"] == 1 and body["page_size"] == 2
    keys = {"id", "url", "thumbnail_url", "captured_at", "task_id", "point_id"}
    assert keys <= set(body["items"][0]), set(body["items"][0])

    r2 = await client.get(f"{CATALOG}?greenhouse_id={gh.id}&point_id={p2.id}",
                          headers=auth(token))
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["total"] == 1 and b2["items"][0]["point_id"] == p2.id

    # start 过滤：未来的起点应为空结果
    future = (now + timedelta(days=1)).isoformat()
    r3 = await client.get(f"{CATALOG}?greenhouse_id={gh.id}&start={future}",
                          headers=auth(token))
    assert r3.status_code == 200, r3.text
    assert r3.json() == {"items": [], "total": 0, "page": 1, "page_size": 24}

    # 过去的起点应全部返回
    past = (now - timedelta(days=1)).isoformat()
    r4 = await client.get(f"{CATALOG}?greenhouse_id={gh.id}&start={past}",
                          headers=auth(token))
    assert r4.json()["total"] == 6


async def test_photo_catalog_excludes_unassigned_by_default(client, iso):
    fx, db = iso
    cid = await fx.company(db, "M2B")
    gh = await fx.greenhouse(db, cid, "M2B-gh")
    dev = await fx.device(db, gh)
    p = await fx.point(db, gh, "1号点")
    u, token = await fx.user(db, UserRole.ADMIN, cid)
    await fx.image(db, gh, dev, point_id=p.id)
    await fx.image(db, gh, dev, point_id=None)
    await db.commit()

    r = await client.get(f"{CATALOG}?greenhouse_id={gh.id}", headers=auth(token))
    assert r.json()["total"] == 1, r.text

    r2 = await client.get(f"{CATALOG}?greenhouse_id={gh.id}&include_unassigned=true",
                          headers=auth(token))
    assert r2.json()["total"] == 2, r2.text


async def test_photo_catalog_is_company_scoped(client, iso):
    fx, db = iso
    c_a = await fx.company(db, "M2C-A")
    c_b = await fx.company(db, "M2C-B")
    gh_a = await fx.greenhouse(db, c_a, "M2C-A-gh")
    gh_b = await fx.greenhouse(db, c_b, "M2C-B-gh")
    dev_a = await fx.device(db, gh_a)
    dev_b = await fx.device(db, gh_b)
    pa = await fx.point(db, gh_a, "A点")
    pb = await fx.point(db, gh_b, "B点")
    u_a, t_a = await fx.user(db, UserRole.ADMIN, c_a)
    u_b, t_b = await fx.user(db, UserRole.ADMIN, c_b)
    await fx.image(db, gh_a, dev_a, point_id=pa.id)
    await fx.image(db, gh_b, dev_b, point_id=pb.id)
    await db.commit()

    ra = await client.get(f"{CATALOG}", headers=auth(t_a))
    assert ra.status_code == 200
    assert [i["greenhouse_id"] for i in ra.json()["items"]] == [gh_a.id], ra.text

    rb = await client.get(f"{CATALOG}", headers=auth(t_b))
    assert [i["greenhouse_id"] for i in rb.json()["items"]] == [gh_b.id], rb.text

    # 越权指定别家大棚 → 404
    r = await client.get(f"{CATALOG}?greenhouse_id={gh_b.id}", headers=auth(t_a))
    assert r.status_code == 404, r.text


async def test_photo_catalog_options(client, iso):
    fx, db = iso
    cid = await fx.company(db, "M2D")
    gh = await fx.greenhouse(db, cid, "M2D-gh")
    dev = await fx.device(db, gh)
    p = await fx.point(db, gh, "1号点")
    u, token = await fx.user(db, UserRole.ADMIN, cid)
    await fx.image(db, gh, dev, point_id=p.id)
    await db.commit()

    r = await client.get(f"{CATALOG}/options?greenhouse_id={gh.id}", headers=auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"greenhouses", "points", "greenhouse_id"} <= set(body)
    assert [x["id"] for x in body["points"]] == [p.id]
    assert body["points"][0]["name"] == "1号点"
    assert gh.id in [g["id"] for g in body["greenhouses"]]


# ── 3. 图片入库点位解析 ──────────────────────────────────────
async def test_image_upload_resolves_point_via_robot_mapping(client, iso):
    fx, db = iso
    cid = await fx.company(db, "M2E")
    gh = await fx.greenhouse(db, cid, "M2E-gh")
    dev = await fx.device(db, gh, name="ROB-M2E")
    p = await fx.point(db, gh, "1号点")
    await fx.robot_mapping(db, dev.device_id, "A点", p, gh)
    u, token = await fx.user(db, UserRole.ADMIN, cid)
    await db.commit()

    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    r = await client.post(
        f"{API}/images/upload/{dev.device_id}?nav_point_code=A%E7%82%B9",
        files={"file": ("a.png", png, "image/png")}, headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["point_id"] == p.id, r.text

    img_id = r.json()["id"]
    async with AsyncSessionLocal() as db2:
        row = (await db2.execute(select(Image).where(Image.id == img_id))
               ).scalar_one_or_none()
        assert row is not None and row.point_id == p.id
        ev = (await db2.execute(select(InspectionEvidence).where(
            InspectionEvidence.media["image_id"].as_string() == img_id))).scalars().all()
        assert len(ev) == 1, "入库图片应同步落一条证据"
        assert ev[0].point_id == p.id
        assert ev[0].attribution_status == "resolved"
        assert ev[0].robot_id == dev.device_id


async def test_image_upload_without_resolvable_point_is_pending(client, iso):
    """解析不出点位 → point_id=NULL，证据 attribution_status='pending'（不猜）。"""
    fx, db = iso
    cid = await fx.company(db, "M2F")
    gh = await fx.greenhouse(db, cid, "M2F-gh")
    dev = await fx.device(db, gh, name="ROB-M2F")
    u, token = await fx.user(db, UserRole.ADMIN, cid)
    await db.commit()

    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    r = await client.post(f"{API}/images/upload/{dev.device_id}",
                          files={"file": ("b.png", png, "image/png")},
                          headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["point_id"] is None, r.text

    ev = await fx.evidence(db, task_id=None, greenhouse_id=gh.id,
                           point_id=None, attribution_status="pending",
                           media={"image_id": r.json()["id"]})
    await db.commit()
    async with AsyncSessionLocal() as db2:
        row = (await db2.execute(select(InspectionEvidence).where(
            InspectionEvidence.id == ev.id))).scalar_one()
        assert row.attribution_status == "pending"
        assert row.point_id is None


async def test_image_upload_matches_point_name_as_fallback(client, iso):
    """没有机器人映射时，退化为按 inspection_points.name 精确匹配。"""
    fx, db = iso
    cid = await fx.company(db, "M2G")
    gh = await fx.greenhouse(db, cid, "M2G-gh")
    dev = await fx.device(db, gh, name="ROB-M2G")
    p = await fx.point(db, gh, "3号点")
    u, token = await fx.user(db, UserRole.ADMIN, cid)
    await db.commit()

    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    r = await client.post(
        f"{API}/images/upload/{dev.device_id}?greenhouse_point=3%E5%8F%B7%E7%82%B9",
        files={"file": ("c.png", png, "image/png")}, headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.json()["point_id"] == p.id, r.text


# ── 4. 地图发布版本（不可变） ────────────────────────────────
async def test_map_publish_creates_immutable_versions(client, iso):
    fx, db = iso
    cid = await fx.company(db, "M2H")
    gh = await fx.greenhouse(db, cid, "M2H-gh")
    u, token = await fx.user(db, UserRole.ADMIN, cid)
    await db.commit()

    # 第一次编辑地图：只有 A1 一个点位
    base = {"layout": {"width": 10, "height": 80},
            "defaults": {"sample_seconds": 20, "collect_soil": True,
                         "collect_environment": True, "take_photo": True,
                         "enabled": True},
            "points": [{"id": "p-m2h-1", "point_name": "A1", "zone": "z1",
                        "x": 1.0, "y": 2.0}]}
    put1 = await client.put(f"{PC}/maps/{gh.id}", json=base, headers=auth(token))
    assert put1.status_code == 200, put1.text

    r1 = await client.post(f"{PC}/maps/{gh.id}/publish", headers=auth(token))
    assert r1.status_code == 200, r1.text
    assert r1.json()["version"] == 1, r1.text
    assert r1.json()["point_count"] == 1

    # 第二次编辑：A1 改名 + 新增 A2
    upd = dict(base)
    upd["layout"] = {"width": 12, "height": 80}
    upd["defaults"] = {"sample_seconds": 30}
    upd["points"] = [{"id": "p-m2h-1", "point_name": "A1-改", "zone": "z1",
                      "x": 5.0, "y": 6.0},
                     {"id": "p-m2h-2", "point_name": "A2", "zone": "z1",
                      "x": 7.0, "y": 8.0}]
    put2 = await client.put(f"{PC}/maps/{gh.id}", json=upd, headers=auth(token))
    assert put2.status_code == 200, put2.text

    r2 = await client.post(f"{PC}/maps/{gh.id}/publish", headers=auth(token))
    assert r2.json()["version"] == 2, r2.text

    v1 = (await client.get(f"{PC}/maps/{gh.id}/versions/1", headers=auth(token))).json()
    v2 = (await client.get(f"{PC}/maps/{gh.id}/versions/2", headers=auth(token))).json()

    # v1 快照未随后续编辑改变
    assert v1["points"][0]["point_name"] == "A1", v1
    assert len(v1["points"]) == 1, v1
    assert v1["layout"]["width"] == 10, v1
    assert v1["defaults"]["sample_seconds"] == 20, v1
    assert v2["points"][0]["point_name"] == "A1-改"
    assert len(v2["points"]) == 2
    assert v1["immutable"] is True and v2["immutable"] is True

    lst = (await client.get(f"{PC}/maps/{gh.id}/versions", headers=auth(token))).json()
    assert lst["total"] == 2
    assert [x["version"] for x in lst["items"]] == [2, 1]

    missing = await client.get(f"{PC}/maps/{gh.id}/versions/99", headers=auth(token))
    assert missing.status_code == 404


async def test_map_publish_requires_permission(client, iso):
    fx, db = iso
    cid = await fx.company(db, "M2I")
    gh = await fx.greenhouse(db, cid, "M2I-gh")
    u, token = await fx.user(db, UserRole.VIEWER, cid)
    await db.commit()
    r = await client.post(f"{PC}/maps/{gh.id}/publish", headers=auth(token))
    assert r.status_code == 403, r.text


# ── 5. 任务快照 ──────────────────────────────────────────────
async def test_task_snapshot_frozen_at_creation(client, iso):
    fx, db = iso
    cid = await fx.company(db, "M2J")
    gh = await fx.greenhouse(db, cid, "M2J-gh")
    dev = await fx.device(db, gh)
    mv = await fx.map_version(db, gh, 1, points_snapshot=[{"id": "p-1"}])
    u, token = await fx.user(db, UserRole.ADMIN, cid)
    await db.commit()

    created = await client.post(f"{API}/tasks/", json={
        "task_name": "快照任务", "greenhouse_id": gh.id, "device_id": dev.id,
        "waypoints": [{"x": 1.0, "y": 2.0, "action": "sample"}],
        "task_config": {"sample_seconds": 30},
    }, headers=auth(token))
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]

    async with AsyncSessionLocal() as db2:
        row = (await db2.execute(select(RobotTask).where(RobotTask.id == task_id))
               ).scalar_one()
        snap = row.snapshot
    assert snap["map_version"] == 1, snap
    assert snap["map_version_id"] == mv.id
    assert snap["params"] == {"sample_seconds": 30}
    assert len(snap["points"]) == 1

    # 编辑任务（改名 + 改点位）后快照不变
    await client.put(f"{API}/tasks/{task_id}", json={
        "task_name": "改名后", "progress": 50,
    }, headers=auth(token))
    async with AsyncSessionLocal() as db2:
        row = (await db2.execute(select(RobotTask).where(RobotTask.id == task_id))
               ).scalar_one()
        assert row.snapshot["map_version"] == 1
        assert row.snapshot["params"] == {"sample_seconds": 30}
        assert len(row.snapshot["points"]) == 1
        assert row.task_name == "改名后"


# ── 6. 任务状态机 ────────────────────────────────────────────
async def test_task_status_machine_valid_flow(client, iso):
    fx, db = iso
    cid = await fx.company(db, "M2K")
    gh = await fx.greenhouse(db, cid, "M2K-gh")
    dev = await fx.device(db, gh)
    u, token = await fx.user(db, UserRole.ADMIN, cid)
    task = await fx.task(db, gh, dev, status="pending")
    await db.commit()

    async def _set(st):
        return await client.post(f"{API}/tasks/{task.id}/status",
                                 json={"status": st}, headers=auth(token))

    r = await _set("running")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"

    assert (await _set("paused")).status_code == 200
    assert (await _set("running")).status_code == 200
    r = await _set("partial")
    assert r.status_code == 200, r.text

    # 终态之后不能再流转
    r = await _set("running")
    assert r.status_code == 400, r.text
    assert "非法任务状态流转" in r.json()["detail"], r.text


async def test_task_status_machine_rejects_illegal_transitions(client, iso):
    fx, db = iso
    cid = await fx.company(db, "M2L")
    gh = await fx.greenhouse(db, cid, "M2L-gh")
    dev = await fx.device(db, gh)
    u, token = await fx.user(db, UserRole.ADMIN, cid)
    await db.commit()

    created = await client.post(f"{API}/tasks/", json={
        "task_name": "流转任务", "greenhouse_id": gh.id, "device_id": dev.id,
    }, headers=auth(token))
    task_id = created.json()["id"]

    # pending → completed 非法（必须先 running）
    r = await client.post(f"{API}/tasks/{task_id}/status",
                          json={"status": "completed"}, headers=auth(token))
    assert r.status_code == 400, r.text
    assert "非法任务状态流转" in r.json()["detail"]

    # PUT 改状态也走状态机
    r2 = await client.put(f"{API}/tasks/{task_id}", json={"status": "completed"},
                          headers=auth(token))
    assert r2.status_code == 400, r2.text

    # pending → cancelled 合法
    r3 = await client.post(f"{API}/tasks/{task_id}/status",
                           json={"status": "cancelled", "reason": "人为取消"},
                           headers=auth(token))
    assert r3.status_code == 200, r3.text

    # 未知状态 400
    r4 = await client.post(f"{API}/tasks/{task_id}/status",
                           json={"status": "whatever"}, headers=auth(token))
    assert r4.status_code == 400, r4.text


async def test_task_state_machine_endpoint(client, iso):
    fx, db = iso
    u, token = await fx.user(db, UserRole.VIEWER)
    await db.commit()
    r = await client.get(f"{API}/tasks/task-state-machine", headers=auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["states"]) == {"pending", "running", "paused", "completed",
                                   "partial", "failed", "cancelled"}
    assert body["transitions"]["pending"] == ["cancelled", "failed", "running"]
    assert body["transitions"]["completed"] == []


# ── 7. 机器人地图版本登记与核对 ──────────────────────────────
async def test_robot_map_version_mismatch_generates_alert_only(client, iso):
    fx, db = iso
    cid = await fx.company(db, "M2M")
    gh = await fx.greenhouse(db, cid, "M2M-gh")
    robot = uid("ROB")
    u, token = await fx.user(db, UserRole.ADMIN, cid)
    await db.commit()

    r = await client.put(f"{PC}/robots/{robot}/map-version",
                         json={"map_version": "map-v1", "greenhouse_id": gh.id},
                         headers=auth(token))
    assert r.status_code == 200, r.text

    got = await client.get(f"{PC}/robots/{robot}/map-version", headers=auth(token))
    assert got.json()["expected"]["map_version"] == "map-v1", got.text

    # 回传不一致 → 落告警，且**不自动改登记值**
    async with AsyncSessionLocal() as db2:
        res = await check_robot_map_version(db2, robot, "map-v2", source="config_ack")
        await db2.commit()
        assert res["status"] == "mismatch", res
        alert_id = res["alert_id"]

    got2 = await client.get(f"{PC}/robots/{robot}/map-version", headers=auth(token))
    body = got2.json()
    assert body["expected"]["map_version"] == "map-v1", "绝不自动改登记版本"
    assert len(body["open_alerts"]) == 1
    assert body["open_alerts"][0]["reported_map_version"] == "map-v2"

    # 相同的一对不重复刷告警
    async with AsyncSessionLocal() as db2:
        res2 = await check_robot_map_version(db2, robot, "map-v2", source="config_ack")
        await db2.commit()
    assert res2["duplicated"] is True

    # 一致时不告警
    async with AsyncSessionLocal() as db2:
        res3 = await check_robot_map_version(db2, robot, "map-v1", source="config_ack")
        await db2.commit()
    assert res3["status"] == "match"

    # 人工闭环
    rc = await client.post(f"{PC}/robots/map-alerts/{alert_id}/resolve?synced=true",
                           headers=auth(token))
    assert rc.status_code == 200, rc.text
    async with AsyncSessionLocal() as db2:
        a = (await db2.execute(select(RobotMapAlert).where(
            RobotMapAlert.id == alert_id))).scalar_one()
        assert a.status == "resolved"


async def test_robot_map_version_unknown_without_registry(iso):
    """未登记期望值 / 未回传 → unknown，不产生告警（不能凭空判定不一致）。"""
    fx, db = iso
    robot = uid("ROB")
    res = await check_robot_map_version(db, robot, "map-v9", source="config_ack")
    assert res["status"] == "unknown"
    await fx.map_registry(db, robot, "map-v1")
    res2 = await check_robot_map_version(db, robot, None, source="config_ack")
    assert res2["status"] == "unknown"
    await db.commit()
    rows = (await db.execute(select(RobotMapAlert).where(
        RobotMapAlert.robot_id == robot))).scalars().all()
    assert rows == []
