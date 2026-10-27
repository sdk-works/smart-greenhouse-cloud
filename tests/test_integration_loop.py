"""全链路集成联调（I 分支）——**模拟数据 + 模拟设备，非真实硬件联调**。

链路：公司/基地 → 大棚与地图 → 点位与机器人映射 → 设备绑定 → 地图发布 →
      巡检任务（快照）→ 模拟设备上报遥测/图片 → 照片归档 → 证据 →
      仪表盘聚合 → AI 建议 → 报表导出 → 异常事件 → 控制命令 → 重启恢复

明确声明：
  * **没有连接过真实 MQTT broker，也没有向任何真实设备下发过指令。**
  * 设备上行是直接调用 mqtt_service 的处理函数（同一段代码路径，但没有网络），
    属于"模拟联调通过"，**不是"真实环境验证通过"**。
  * 数据库用的是测试库 greenhouse_nightly_test。
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.robot_map_service import check_robot_map_version
from app.models.models import (
    Alert, AiAdviceLog, Image, Incident, InspectionEvidence, InspectionVisit,
    MapVersion, RobotMapAlert, RobotTask, Telemetry, UserRole,
)
from app.mqtt.mqtt_service import mqtt_service
from tests.conftest import auth
from tests.iso_fx import uid

API = settings.API_PREFIX


def _utc(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


async def _base_scenario(fx, db, name_prefix="INT"):
    """搭一套完整场景，返回上下文。"""
    c = await fx.company(db, f"{name_prefix}-C")
    gh = await fx.greenhouse(db, c, f"{name_prefix}-gh")
    dev = await fx.device(db, gh, name=f"{name_prefix}-ROB")
    p1 = await fx.point(db, gh, "1号点")
    p2 = await fx.point(db, gh, "2号点")
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()
    return {"c": c, "gh": gh, "dev": dev, "p1": p1, "p2": p2, "user": u,
            "token": token}


async def test_full_loop_simulation(client, iso):
    """一条链路从登记走到归档、分析、处置。"""
    fx, db = iso
    ctx = await _base_scenario(fx, db, "INT1")
    gh, dev, token = ctx["gh"], ctx["dev"], ctx["token"]
    p1, p2 = ctx["p1"], ctx["p2"]
    robot_id = dev.device_id

    # ── 1) 设备绑定（M3：绑定窗口 + 版本） ──────────────────
    # 绑定生效时间设在 1 天前（模拟设备早已安装），否则补传数据会落在窗口外
    bind = await client.post(f"{API}/devices/{robot_id}/bindings",
                             json={"greenhouse_id": gh.id, "channel": "default",
                                   "start_at": _utc(days=1).isoformat()},
                             headers=auth(token))
    assert bind.status_code == 201, bind.text

    # ── 2) 机器人导航映射（M2） ────────────────────────────
    await fx.robot_mapping(db, robot_id, "A点", p1, gh)
    await fx.robot_mapping(db, robot_id, "B点", p2, gh)
    await db.commit()

    # ── 3) 编辑并发布地图 v1（M2） ─────────────────────────
    doc = {"layout": {"width": 10, "height": 80},
           "defaults": {"sample_seconds": 20, "collect_soil": True,
                        "collect_environment": True, "take_photo": True,
                        "enabled": True},
           "points": [{"id": p1.id, "point_name": "1号点", "x": 1.0, "y": 2.0},
                      {"id": p2.id, "point_name": "2号点", "x": 3.0, "y": 4.0}]}
    put = await client.put(f"{API}/point-config/maps/{gh.id}", json=doc,
                           headers=auth(token))
    assert put.status_code == 200, put.text
    pub = await client.post(f"{API}/point-config/maps/{gh.id}/publish",
                            headers=auth(token))
    assert pub.status_code == 200 and pub.json()["version"] == 1, pub.text

    # ── 4) 登记机器人地图版本（M2） ────────────────────────
    mvreg = await client.put(f"{API}/point-config/robots/{robot_id}/map-version",
                             json={"map_version": "map-v1",
                                   "greenhouse_id": gh.id},
                             headers=auth(token))
    assert mvreg.status_code == 200, mvreg.text

    # ── 5) 创建巡检任务（快照冻结 v1） ─────────────────────
    created = await client.post(f"{API}/tasks/", json={
        "task_name": "联调巡检", "greenhouse_id": gh.id, "device_id": dev.id,
        "waypoints": [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}],
        "task_config": {"sample_seconds": 20},
    }, headers=auth(token))
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]

    # ── 6) 模拟设备上报遥测（直接调用入库函数，不走网络） ────
    ts = datetime.now(timezone.utc)
    payloads = [{
        "msg_id": f"int-{uuid.uuid4().hex[:8]}",
        "event_id": f"ev-{uuid.uuid4().hex[:8]}",
        "device_id": robot_id, "timestamp": ts.isoformat(),
        "time_quality": 1, "sampled_at": (ts - timedelta(seconds=2)).isoformat(),
        "data": {"temperature": 24.0, "humidity": 60.0, "co2": 900, "light": 15000},
        "navigation": {"point_mapping": {"chassis_point": "A点",
                                         "greenhouse_id": gh.id,
                                         "greenhouse_point": "1号点"}},
    }, {
        "msg_id": f"int-{uuid.uuid4().hex[:8]}",
        "event_id": f"ev-{uuid.uuid4().hex[:8]}",
        "device_id": robot_id, "timestamp": ts.isoformat(),
        "time_quality": 1, "sampled_at": (ts - timedelta(seconds=1)).isoformat(),
        "data": {"temperature": 25.5, "humidity": 62.0, "co2": 950, "light": 16000},
        "navigation": {"point_mapping": {"chassis_point": "B点",
                                         "greenhouse_id": gh.id,
                                         "greenhouse_point": "2号点"}},
    }]
    for p in payloads:
        ok = await mqtt_service._handle_telemetry(robot_id, p)
        assert ok, f"模拟上行失败：{p['msg_id']}"

    async with AsyncSessionLocal() as db2:
        rows = (await db2.execute(select(Telemetry).where(
            Telemetry.device_id == dev.id))).scalars().all()
        assert len(rows) == 2, len(rows)
        for r in rows:
            # 绑定窗口内 → 归属已确认
            assert r.attribution_status == "resolved", r.attribution_reason
            assert r.attribution_reason == "binding_window"
            assert r.greenhouse_id == gh.id
            assert r.company_id == ctx["c"]
        visits = (await db2.execute(select(InspectionVisit).where(
            InspectionVisit.robot_id == robot_id))).scalars().all()
        assert len(visits) >= 1, "上行应落到点到记录"

    # ── 7) 模拟图片通知入库（M2：解析点位） ─────────────────
    await mqtt_service._handle_image_notify(robot_id, {
        "image_url": f"http://sim/{uuid.uuid4().hex}.jpg",
        "task_id": task_id, "captured_at": ts.isoformat(),
        "navigation": {"point_mapping": {"chassis_point": "A点",
                                         "greenhouse_id": gh.id,
                                         "greenhouse_point": "1号点"}},
    })
    await mqtt_service._handle_image_notify(robot_id, {
        "image_url": f"http://sim/{uuid.uuid4().hex}.jpg",
        "task_id": task_id, "captured_at": ts.isoformat(),
    })
    async with AsyncSessionLocal() as db2:
        imgs = (await db2.execute(select(Image).where(
            Image.device_id == dev.id))).scalars().all()
        assert len(imgs) == 2
        with_point = [i for i in imgs if i.point_id == p1.id]
        assert len(with_point) == 1, "A点 应解析到 1号点"
        assert [i for i in imgs if i.point_id is None][0].id, "解析不出应留 NULL"

    # ── 8) 照片归档目录（M2） ─────────────────────────────
    cat = (await client.get(f"{API}/photo-catalog?greenhouse_id={gh.id}",
                            headers=auth(token))).json()
    assert cat["total"] == 1, cat
    assert cat["items"][0]["point_id"] == p1.id
    assert cat["items"][0]["task_id"] == task_id

    opts = (await client.get(
        f"{API}/photo-catalog/options?greenhouse_id={gh.id}",
        headers=auth(token))).json()
    assert [x["id"] for x in opts["points"]] == [p1.id], opts

    ev = (await client.get(f"{API}/photo-catalog/tasks/{task_id}/evidence",
                           headers=auth(token))).json()
    assert ev["total"] == 2, ev
    assert sorted(x["attribution_status"] for x in ev["items"]) == \
        ["pending", "resolved"], ev

    # ── 9) 任务推进到执行中（模拟，不真下发 MQTT） ─────────
    r = await client.post(f"{API}/tasks/{task_id}/status",
                          json={"status": "running"}, headers=auth(token))
    assert r.status_code == 200, r.text

    # ── 10) 仪表盘聚合（M1） ──────────────────────────────
    stats = (await client.get(f"{API}/dashboard/stats",
                              headers=auth(token))).json()
    assert stats["total_greenhouses"] >= 1
    assert stats["total_devices"] >= 1
    assert stats["active_tasks"] >= 1
    assert stats["today_telemetry_count"] >= 2
    assert stats["today_images_count"] >= 1

    # ── 11) 新鲜度（M3，查询时计算） ─────────────────────
    fresh = (await client.get(
        f"{API}/freshness/devices?greenhouse_id={gh.id}",
        headers=auth(token))).json()
    assert fresh["items"][0]["freshness"]["state"] in ("fresh", "lagging")
    assert fresh["items"][0]["freshness"]["basis"] == "sampled_at"

    # ── 12) AI 受控建议（M4） ─────────────────────────────
    ai = (await client.post(f"{API}/ai/advice",
                            json={"greenhouse_id": gh.id, "hours": 1},
                            headers=auth(token))).json()
    assert ai["limits"]["db_access"] is False
    assert ai["limits"]["mqtt_control"] is False
    assert all(a["kind"] != "control" for a in ai["advice"])

    # ── 13) 报表导出（M4） ───────────────────────────────
    rep = await client.get(f"{API}/reports/telemetry?fmt=csv",
                           headers=auth(token))
    assert rep.status_code == 200, rep.text
    assert "temperature" in rep.text
    assert rep.text.count("\n") >= 2

    # ── 14) 机器人回执地图版本不一致 → 只告警（M2） ────────
    await mqtt_service._handle_config_ack(robot_id, {
        "config_version": 1,
        "point_mapping_sync_status": "synced",
        "robot_map_version": "map-v2",
    })
    async with AsyncSessionLocal() as db2:
        alerts = (await db2.execute(select(RobotMapAlert).where(
            RobotMapAlert.robot_id == robot_id))).scalars().all()
        assert len(alerts) == 1
        assert alerts[0].expected_map_version == "map-v1"
        assert alerts[0].reported_map_version == "map-v2"
        assert alerts[0].status == "open"
    reg = (await client.get(f"{API}/point-config/robots/{robot_id}/map-version",
                            headers=auth(token))).json()
    assert reg["expected"]["map_version"] == "map-v1", "不得自动改登记值"

    # ── 15) 控制命令：ACK ≠ 执行结果（M4，控制契约 v1 适配）──
    # 契约要求：命令必须有可信 device_state；动作限契约 v1 集合
    from app.models.models import ActuatorProfile as _AP
    from datetime import datetime as _dt2, timezone as _tz2
    db.add(_AP(device_id=dev.id, boot_id=17, session_id="b17-7f3a9c",
               state_seq=12, last_command_seq=5, mode="remote",
               motion="stopped", uptime_ms=123456,
               state_received_at=_dt2.now(_tz2.utc)))
    await db.commit()
    cmd = (await client.post(f"{API}/actuator-commands", json={
        "device_id": dev.id, "action": "move_up"}, headers=auth(token))).json()
    assert cmd["status"] == "pending"
    await client.post(f"{API}/actuator-commands/{cmd['id']}/send",
                      headers=auth(token))
    ack = (await client.post(f"{API}/actuator-commands/{cmd['id']}/ack",
                             headers=auth(token))).json()
    assert ack["is_acked"] is True and ack["is_executed"] is False
    await client.post(f"{API}/actuator-commands/{cmd['id']}/result",
                      json={"status": "executing"}, headers=auth(token))
    done = (await client.post(f"{API}/actuator-commands/{cmd['id']}/result",
                              json={"status": "executed",
                                    "actual_state": {"valve": "open"}},
                              headers=auth(token))).json()
    assert done["is_executed"] is True

    # ── 16) 异常事件：环境恢复只停告警（M3） ──────────────
    inc = (await client.post(f"{API}/incidents", json={
        "title": "联调温度偏高", "greenhouse_id": gh.id},
        headers=auth(token))).json()
    al = (await client.post(f"{API}/alerts", json={
        "rule_code": "temp_high", "incident_id": inc["id"],
        "greenhouse_id": gh.id, "metric": "temperature",
        "value": 25.5, "threshold": 30}, headers=auth(token))).json()
    clr = (await client.post(
        f"{API}/alerts/{al['id']}/clear?reason=environment_recovered",
        headers=auth(token))).json()
    assert clr["cleared"] is True and clr["incident_closed"] is False

    # ── 17) 任务收尾 + 重启恢复兜底（M3） ─────────────────
    async with AsyncSessionLocal() as db2:
        t = (await db2.execute(select(RobotTask).where(
            RobotTask.id == task_id))).scalar_one()
        t.started_at = _utc(minutes=200)
        await db2.commit()
    rec = (await client.post(f"{API}/maintenance/recover",
                             headers=auth(token))).json()
    assert rec["tasks_recovered"] == 1, rec
    async with AsyncSessionLocal() as db2:
        t = (await db2.execute(select(RobotTask).where(
            RobotTask.id == task_id))).scalar_one()
        assert t.status == "partial"
        # 快照不受后续流转影响
        assert t.snapshot["map_version"] == 1
        assert len(t.snapshot["points"]) == 2


async def test_full_loop_is_isolated_between_companies(client, iso):
    """同一套链路在两家公司各跑一遍，彼此完全不可见。"""
    fx, db = iso
    a = await _base_scenario(fx, db, "INT2A")
    b = await _base_scenario(fx, db, "INT2B")
    await db.commit()

    for ctx, other in ((a, b), (b, a)):
        token, gh, dev = ctx["token"], ctx["gh"], ctx["dev"]
        r = await client.get(f"{API}/dashboard/stats", headers=auth(token))
        assert r.status_code == 200, r.text

        # 别家大棚在所有接口上都应该不可见
        for path in (f"{API}/photo-catalog?greenhouse_id={other['gh'].id}",
                     f"{API}/reports/telemetry?greenhouse_id={other['gh'].id}",
                     f"{API}/freshness/greenhouses/{other['gh'].id}",
                     f"{API}/point-config/maps/{other['gh'].id}"):
            resp = await client.get(path, headers=auth(token))
            assert resp.status_code in (403, 404), f"{path} -> {resp.status_code}"

        # 控制别家的设备 → 403（设备归属校验）
        cmd = await client.post(f"{API}/actuator-commands", json={
            "device_id": other["dev"].id, "action": "open_valve"},
            headers=auth(token))
        assert cmd.status_code == 404, cmd.text

    # 模拟上行只会落到各自的库记录里
    ts = datetime.now(timezone.utc)
    for ctx in (a, b):
        ok = await mqtt_service._handle_telemetry(ctx["dev"].device_id, {
            "msg_id": f"int2-{uuid.uuid4().hex[:8]}",
            "device_id": ctx["dev"].device_id,
            "timestamp": ts.isoformat(), "time_quality": 1,
            "sampled_at": ts.isoformat(),
            "data": {"temperature": 20.0},
        })
        assert ok

    ra = (await client.get(f"{API}/reports/telemetry?fmt=json",
                           headers=auth(a["token"]))).json()
    rb = (await client.get(f"{API}/reports/telemetry?fmt=json",
                           headers=auth(b["token"]))).json()
    assert ra["meta"]["count"] == 1 and rb["meta"]["count"] == 1, (ra["meta"], rb["meta"])
    assert ra["items"][0]["id"] != rb["items"][0]["id"]
