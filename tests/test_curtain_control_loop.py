"""卷帘机控制闭环契约测试（2026-10-27，控制契约 v1 rev.2）。

四个重点（任务书原文）逐项覆盖：
  1. 独立 control_device 权限：越权时数据库不产生可执行任务、MQTT 不下发
  2. 完整命令信封：boot_id/session_id/ref_uptime_ms 只来自设备可信新状态，
     客户端自报被忽略，缺可信状态拒绝（不填零、不用服务器时间）
  3. 结果归属：主题设备 == payload 设备 == 数据库设备；命令大棚一致；
     boot/session 与命令代次一致；不信任 payload 自报身份
  4. 命令生命周期：pending→sent→accepted→executing→executed 全链、
     发布失败停 pending、recover→unknown、超时收敛不重发、新 boot 收敛旧命令

生产 MQTT 保持隔离：发布器是可计数的假对象；MQTT 上行处理直接调函数。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import app.api.actuator_commands as ac_mod
from app.core.config import settings as _settings
API_PREFIX = _settings.API_PREFIX
from app.models.models import (
    ActuatorCommand, ActuatorProfile, CommandStatus,
)
from app.core import curtain_control as cc


class FakePublisher:
    """假发布器：只计数 + 记录报文，不碰任何真实 broker。"""

    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    async def publish(self, device_id, command):
        self.calls.append({"device_id": device_id, "command": command})
        return self.ok

    @property
    def count(self):
        return len(self.calls)


@pytest.fixture
def publisher(monkeypatch):
    pub = FakePublisher()
    monkeypatch.setattr(ac_mod.mqtt_service, "publish_device_command", pub.publish)
    return pub


def _state_payload(hw, *, boot_id=17, state_seq=12, session_id="b17-7f3a9c",
                   uptime_ms=123456, last_command_seq=5, mode="remote"):
    return {"type": "device_state", "schema_version": 1, "device_id": hw,
            "channel_id": "curtain_1", "state_seq": state_seq,
            "boot_id": boot_id, "session_id": session_id, "mode": mode,
            "motion": "stopped", "motion_confirmed": True,
            "limit_up": False, "limit_down": False, "estop": False,
            "fault": None, "position_raw": None, "position_valid": False,
            "last_command_seq": last_command_seq, "uptime_ms": uptime_ms,
            "time_quality": 0}


async def _set_profile(db, dev, *, stale_seconds=0, **kw):
    """直接落一份 device_state 摄取后的 profile（等价于设备上报过）。"""
    p = await db.get(ActuatorProfile, dev.id)
    if p is None:
        p = ActuatorProfile(device_id=dev.id)
        db.add(p)
    p.boot_id = kw.get("boot_id", 17)
    p.session_id = kw.get("session_id", "b17-7f3a9c")
    p.state_seq = kw.get("state_seq", 12)
    p.last_command_seq = kw.get("last_command_seq", 5)
    p.mode = kw.get("mode", "remote")
    p.motion = "stopped"
    p.uptime_ms = kw.get("uptime_ms", 123456)
    p.state_received_at = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
    await db.commit()
    return p


async def _world(iso):
    """两个公司各一套（大棚+设备+用户），设备硬件号固定便于 MQTT 主题测试。"""
    from app.models.models import DeviceStatus, DeviceType, UserRole

    fx, db = iso
    # 硬件号每次运行随机，避免与测试库残留行撞唯一约束
    hw1 = f"JY-CTRL-A-{uuid.uuid4().hex[:8]}"
    hw2 = f"JY-CTRL-B-{uuid.uuid4().hex[:8]}"
    c1 = await fx.company(db, "契约闭环-公司A")
    c2 = await fx.company(db, "契约闭环-公司B")
    gh1 = await fx.greenhouse(db, c1, "A 棚")
    gh2 = await fx.greenhouse(db, c2, "B 棚")

    def mkdev(gh, hw, name):
        from app.models.models import Device
        d = Device(id=str(uuid.uuid4()), device_id=hw, name=name,
                   device_type=DeviceType.SENSOR, greenhouse_id=gh.id,
                   status=DeviceStatus.ONLINE)
        db.add(d)
        fx.devices.append(d.id)
        return d

    dev1 = mkdev(gh1, hw1, "A 棚卷帘机")
    dev2 = mkdev(gh2, hw2, "B 棚卷帘机")
    await db.flush()
    admin1, t1 = await fx.user(db, UserRole.ADMIN, c1)
    admin2, t2 = await fx.user(db, UserRole.ADMIN, c2)
    viewer1, tv = await fx.user(db, UserRole.VIEWER, c1)
    await db.commit()
    return {"db": db, "fx": fx, "c1": c1, "c2": c2, "gh1": gh1, "gh2": gh2,
            "dev1": dev1, "dev2": dev2,
            "admin1": admin1, "token1": t1,
            "admin2": admin2, "token2": t2,
            "viewer1": viewer1, "token_viewer": tv}


async def create_cmd(client, token, device_id, **kw):
    body = {"device_id": device_id, "action": kw.pop("action", "move_up"), **kw}
    return await client.post(API_PREFIX + "/actuator-commands", json=body,
                             headers={"Authorization": f"Bearer {token}"})


async def send_cmd(client, token, cmd_id):
    return await client.post(f"{API_PREFIX}/actuator-commands/{cmd_id}/send?publish=true",
                             headers={"Authorization": f"Bearer {token}"})


async def _cmd_count(db, dev):
    rows = (await db.execute(select(ActuatorCommand)
                             .where(ActuatorCommand.device_id == dev.id))).scalars().all()
    await db.commit()
    return len(rows)


# ──────────────── 重点 1：独立 control_device 权限 ────────────────

async def test_viewer_forbidden_no_row_no_publish(client, db, iso, publisher):
    """VIEWER 无 control_device → 403；不产生命令行、不发布。"""
    w = await _world(iso)
    await _set_profile(db, w["dev1"])
    r = await create_cmd(client, w["token_viewer"], w["dev1"].id)
    assert r.status_code == 403, r.text
    assert await _cmd_count(db, w["dev1"]) == 0, "越权不得产生命令行"
    assert publisher.count == 0, "越权不得发布"
    # send/ack/result/sweep/recover 全部端点同样被拒（精确路由，非 404 碰运气）
    for url, body in [
        (API_PREFIX + "/actuator-commands/x/send?publish=true", None),
        (API_PREFIX + "/actuator-commands/x/ack", None),
        (API_PREFIX + "/actuator-commands/x/result", {"status": "executed"}),
        (API_PREFIX + "/actuator-commands/expire-sweep", None),
        (API_PREFIX + "/actuator-commands/recover", None)]:
        rr = await client.post(url, json=body,
                               headers={"Authorization": f"Bearer {w['token_viewer']}"})
        assert rr.status_code == 403, (url, rr.status_code)


async def test_cross_company_404_no_row_no_publish(client, db, iso, publisher):
    """跨公司：设备不可见（404）；数据库无任务、MQTT 零报文。"""
    w = await _world(iso)
    await _set_profile(db, w["dev1"])
    r = await create_cmd(client, w["token2"], w["dev1"].id)
    assert r.status_code == 404, r.text
    assert await _cmd_count(db, w["dev1"]) == 0
    assert publisher.count == 0
    # 就算命令行已存在（别的路径造出来），跨公司也不能下发
    cmd = ActuatorCommand(command_id=str(uuid.uuid4()), device_id=w["dev1"].id,
                          greenhouse_id=w["gh1"].id, action="move_up",
                          status=CommandStatus.PENDING, boot_id="17",
                          session_id="b17-7f3a9c", command_seq=6,
                          expires_at=datetime.now(timezone.utc) + timedelta(seconds=60))
    w["db"].add(cmd)
    await w["db"].commit()
    r = await send_cmd(client, w["token2"], cmd.id)
    assert r.status_code in (403, 404), r.text
    assert publisher.count == 0


# ──────────────── 重点 2：完整命令信封 ────────────────

async def test_start_requires_trusted_state(client, db, iso, publisher):
    """无可信状态（无 profile）→ 409 device_offline；无命令行、无报文。"""
    w = await _world(iso)
    r = await create_cmd(client, w["token1"], w["dev1"].id)
    assert r.status_code == 409, r.text
    assert "device_offline" in r.text
    assert await _cmd_count(db, w["dev1"]) == 0
    assert publisher.count == 0


async def test_start_requires_fresh_state_but_stop_allowed(client, db, iso, publisher):
    """状态过期：move_up 409；stop（安全动作）仍可创建。"""
    w = await _world(iso)
    await _set_profile(db, w["dev1"], stale_seconds=300)
    r = await create_cmd(client, w["token1"], w["dev1"].id, action="move_up")
    assert r.status_code == 409 and "device_offline" in r.text
    assert publisher.count == 0
    r = await create_cmd(client, w["token1"], w["dev1"].id, action="stop")
    assert r.status_code == 201, r.text


async def test_envelope_from_device_state_not_client(client, db, iso, publisher):
    """信封字段全部来自设备状态；客户端自报的 boot/session/seq 被忽略。"""
    w = await _world(iso)
    await _set_profile(db, w["dev1"], last_command_seq=5, uptime_ms=123456)
    r = await create_cmd(client, w["token1"], w["dev1"].id,
                         boot_id="999", session_id="CLIENT-FAKE", command_seq=999)
    assert r.status_code == 201, r.text
    cmd = r.json()
    assert cmd["publish_count"] == 0, "入库阶段不得发布"
    assert cmd["boot_id"] == "17" and cmd["command_seq"] == 6
    assert cmd["ref_uptime_ms"] == 123456 and cmd["session_id"] == "b17-7f3a9c"
    assert cmd["channel_id"] == "curtain_1"

    r = await send_cmd(client, w["token1"], cmd["id"])
    assert r.status_code == 200, r.text
    assert publisher.count == 1
    env = publisher.calls[0]["command"]
    assert env["type"] == "command" and env["schema_version"] == 1
    assert env["device_id"] == w["dev1"].device_id and env["channel_id"] == "curtain_1"
    assert env["action"] == "move_up"
    assert env["boot_id"] == 17 and env["session_id"] == "b17-7f3a9c"
    assert env["command_seq"] == 6 and env["ref_uptime_ms"] == 123456
    assert 1000 <= env["ttl_ms"] <= 60000
    assert "params" not in env and "desired_state" not in env
    wire = cc.envelope_wire(env)
    assert len(wire.encode("utf-8")) <= 320, f"信封超 320B：{len(wire)}"
    # 服务器侧记录与信封一致
    det = await client.get(f"{API_PREFIX}/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    d = det.json()
    assert d["boot_id"] == "17" and d["ref_uptime_ms"] == 123456
    assert d["status"] == "sent" and d["publish_count"] == 1


async def test_send_rejects_boot_change_between_create_and_send(client, db, iso, publisher):
    """create 与 send 之间设备重启：409 BOOT_MISMATCH，不发布。"""
    w = await _world(iso)
    await _set_profile(db, w["dev1"])
    cmd = (await create_cmd(client, w["token1"], w["dev1"].id)).json()
    await _set_profile(db, w["dev1"], boot_id=18, state_seq=13)
    r = await send_cmd(client, w["token1"], cmd["id"])
    assert r.status_code == 409 and "BOOT_MISMATCH" in r.text
    assert publisher.count == 0
    det = await client.get(f"{API_PREFIX}/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    assert det.json()["status"] == "pending"


async def test_seq_follows_device_high_water(client, db, iso, publisher):
    """设备 last_command_seq=100 → 云端分配 101（契约 §7.2 规则 6）。"""
    w = await _world(iso)
    await _set_profile(db, w["dev1"], last_command_seq=100)
    r = await create_cmd(client, w["token1"], w["dev1"].id)
    assert r.json()["command_seq"] == 101


async def test_publish_failure_stays_pending(client, db, iso):
    """MQTT 未连接 → 503，状态停在 pending，发布计数 0。"""
    w = await _world(iso)
    await _set_profile(db, w["dev1"])
    pub = FakePublisher(ok=False)
    import app.api.actuator_commands as m
    orig = m.mqtt_service.publish_device_command
    m.mqtt_service.publish_device_command = pub.publish
    try:
        cmd = (await create_cmd(client, w["token1"], w["dev1"].id)).json()
        r = await send_cmd(client, w["token1"], cmd["id"])
        assert r.status_code == 503, r.text
    finally:
        m.mqtt_service.publish_device_command = orig
    det = await client.get(f"{API_PREFIX}/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    assert det.json()["status"] == "pending"
    assert det.json()["publish_count"] == 0 and pub.count == 1


# ──────────────── 重点 3：结果归属 ────────────────

def _result_payload(hw, command_id, *, boot_id=17, session_id="b17-7f3a9c",
                    status="executed", state_seq=13):
    return {"type": "command_result", "schema_version": 1, "command_id": command_id,
            "device_id": hw, "channel_id": "curtain_1", "status": status,
            "error_code": None, "state_seq": state_seq, "boot_id": boot_id,
            "session_id": session_id, "uptime_ms": 130000, "motion": "stopped",
            "motion_confirmed": True, "limit_up": False, "limit_down": False,
            "estop": False, "command_seq": 6}


async def _create_and_send(w, client, publisher, action="move_up"):
    await _set_profile(w["db"], w["dev1"])
    cmd = (await create_cmd(client, w["token1"], w["dev1"].id, action=action)).json()
    r = await send_cmd(client, w["token1"], cmd["id"])
    assert r.status_code == 200, r.text
    return cmd


async def test_mqtt_result_full_lifecycle(client, db, iso, publisher):
    """正常归属链：accepted → executing → executed，actual_state 落库。"""
    w = await _world(iso)
    cmd = await _create_and_send(w, client, publisher)
    hw = w["dev1"].device_id
    ok = await cc.handle_up_message(hw, "command_result",
                                    _result_payload(hw, cmd["command_id"], status="accepted"))
    assert ok
    ok = await cc.handle_up_message(hw, "command_result",
                                    _result_payload(hw, cmd["command_id"], status="executing",
                                                    state_seq=14))
    assert ok
    ok = await cc.handle_up_message(hw, "command_result",
                                    _result_payload(hw, cmd["command_id"], status="executed"))
    assert ok
    det = await client.get(f"{API_PREFIX}/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    d = det.json()
    assert d["status"] == "executed" and d["is_executed"] is True
    assert d["actual_state"]["motion"] == "stopped"
    assert d["completed_at"] is not None


async def test_mqtt_result_topic_payload_mismatch_rejected(client, db, iso, publisher):
    """归属第一层：payload 自报设备 != 主题设备 → 整条拒绝，命令不动。"""
    w = await _world(iso)
    cmd = await _create_and_send(w, client, publisher)
    hw = w["dev1"].device_id
    bad = _result_payload(hw, cmd["command_id"])
    bad["device_id"] = "someone-else"
    ok = await cc.handle_up_message(hw, "command_result", bad)
    assert ok is False, "归属不匹配必须整体拒绝"
    det = await client.get(f"{API_PREFIX}/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    assert det.json()["status"] == "sent", "归属不匹配不得改状态"


async def test_mqtt_result_cross_device_never_matches(client, db, iso, publisher):
    """归属第二层：dev2 主题上重放 dev1 的 command_id → 查询按设备过滤，命中不了。"""
    w = await _world(iso)
    cmd = await _create_and_send(w, client, publisher)
    ok = await cc.handle_up_message(w["dev2"].device_id, "command_result",
                                    _result_payload(w["dev2"].device_id, cmd["command_id"]))
    assert ok  # 设备存在，消息本身合法，但命令不归它
    det = await client.get(f"{API_PREFIX}/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    assert det.json()["status"] == "sent"


async def test_mqtt_result_boot_mismatch_keeps_state(client, db, iso, publisher):
    """代次校验：结果 boot != 命令 boot → 不改结论，留痕。"""
    w = await _world(iso)
    cmd = await _create_and_send(w, client, publisher)
    hw = w["dev1"].device_id
    ok = await cc.handle_up_message(hw, "command_result",
                                    _result_payload(hw, cmd["command_id"], boot_id=99))
    assert ok
    det = await client.get(f"{API_PREFIX}/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    d = det.json()
    assert d["status"] == "sent", "旧代次结果不得改结论"
    assert "代次不匹配" in (d["error_message"] or "")
    assert d["result_boot_id"] == "99"


async def test_mqtt_result_session_mismatch_keeps_state(client, db, iso, publisher):
    """会话校验：结果 session != 命令 session → 拒收留痕。"""
    w = await _world(iso)
    cmd = await _create_and_send(w, client, publisher)
    hw = w["dev1"].device_id
    ok = await cc.handle_up_message(hw, "command_result",
                                    _result_payload(hw, cmd["command_id"],
                                                    session_id="old-session"))
    assert ok
    det = await client.get(f"{API_PREFIX}/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    d = det.json()
    assert d["status"] == "sent"
    assert "会话不匹配" in (d["error_message"] or "")


async def test_device_state_ingestion_and_old_boot_discard(client, db, iso):
    """device_state 摄取：新鲜状态入库；旧 boot 状态丢弃不回退。"""
    w = await _world(iso)
    hw = w["dev1"].device_id
    ok = await cc.handle_up_message(hw, "device_state", _state_payload(hw))
    assert ok
    p = await w["db"].get(ActuatorProfile, w["dev1"].id)
    assert p.boot_id == 17 and p.session_id == "b17-7f3a9c"
    assert p.last_command_seq == 5 and p.uptime_ms == 123456
    assert p.state_received_at is not None
    old_seq = p.state_seq
    # 旧 boot：丢弃
    ok = await cc.handle_up_message(hw, "device_state",
                                    _state_payload(hw, boot_id=9, state_seq=999))
    assert ok
    await w["db"].refresh(p)
    assert p.boot_id == 17 and p.state_seq == old_seq


# ──────────────── 重点 4：命令生命周期 ────────────────

async def test_higher_boot_expires_inflight_commands(client, db, iso, publisher):
    """新 boot 的 device_state → 旧未终态命令 timeout/BOOT_CHANGED。"""
    w = await _world(iso)
    cmd = await _create_and_send(w, client, publisher)
    hw = w["dev1"].device_id
    ok = await cc.handle_up_message(hw, "device_state",
                                    _state_payload(hw, boot_id=18, state_seq=1))
    assert ok
    det = await client.get(f"{API_PREFIX}/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    d = det.json()
    assert d["status"] == "timeout" and "BOOT_CHANGED" in (d["error_message"] or "")


async def test_recover_unknown_then_timeout_sweep(client, db, iso, publisher):
    """发布后无回执 → recover 置 unknown(PUBLISH_UNKNOWN)；过期扫描 → timeout；全程不重发。"""
    w = await _world(iso)
    cmd = await _create_and_send(w, client, publisher)
    # 把发布时间拨回恢复窗口之前
    c = await w["db"].get(ActuatorCommand, cmd["id"])
    c.last_publish_at = datetime.now(timezone.utc) - timedelta(seconds=300)
    await w["db"].commit()
    r = await client.post(API_PREFIX + "/actuator-commands/recover",
                          headers={"Authorization": f"Bearer {w['token1']}"})
    assert r.status_code == 200 and r.json()["recovered"] == 1, r.text
    assert r.json()["republished"] == 0
    det = await client.get(f"{API_PREFIX}/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    d = det.json()
    assert d["status"] == "unknown" and d["is_unknown"] is True
    assert "PUBLISH_UNKNOWN" in (d["error_message"] or "")
    assert d["is_finished"] is False, "unknown 是非终态：结果未知 ≠ 失败"
    # 结果迟到（命令过期后）→ sweep 收敛 timeout，不重发
    c = await w["db"].get(ActuatorCommand, cmd["id"])
    c.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await w["db"].commit()
    r = await client.post(API_PREFIX + "/actuator-commands/expire-sweep",
                          headers={"Authorization": f"Bearer {w['token1']}"})
    assert r.json()["expired"] == 1 and r.json()["republished"] == 0
    assert publisher.count == 1, "扫描/恢复不得产生任何新报文"
    det = await client.get(f"{API_PREFIX}/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    assert det.json()["status"] == "timeout"


async def test_duplicate_send_409(client, db, iso, publisher):
    """重复 send：409，只发布一次。"""
    w = await _world(iso)
    cmd = await _create_and_send(w, client, publisher)
    r = await send_cmd(client, w["token1"], cmd["id"])
    assert r.status_code == 409
    assert publisher.count == 1


async def test_unknown_accepts_late_result(client, db, iso, publisher):
    """unknown 状态收到设备回执：可推进（不回退、不卡死）。"""
    w = await _world(iso)
    cmd = await _create_and_send(w, client, publisher)
    c = await w["db"].get(ActuatorCommand, cmd["id"])
    c.status = CommandStatus.UNKNOWN
    c.error_message = "PUBLISH_UNKNOWN: 已发布但无设备回执"
    await w["db"].commit()
    hw = w["dev1"].device_id
    ok = await cc.handle_up_message(hw, "command_result",
                                    _result_payload(hw, cmd["command_id"], status="executed"))
    assert ok
    await w["db"].refresh(c)
    assert c.status == CommandStatus.EXECUTED
