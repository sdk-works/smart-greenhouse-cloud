"""控制闭环 v1 夜间测试（2026-10-26）

用**假发布器**跑一遍完整闭环：权限检查 → 命令入库 → 发布 → 结果/状态回写 → 页面读取。
生产 MQTT 保持隔离：本文件从不连接真实 broker，发布器是可计数的假对象。

覆盖（任务书 §七 列出的七项）：
  1. 正常闭环：入库 → 发布（次数 1）→ ACK → 结果 → 列表/详情能读到
  2. 跨租户：拒绝且**发布次数为 0**
  3. 重复请求：同一条命令再请求下发 → 409 且发布次数仍为 1
  4. 结果先到：pending 状态就报结果 → 非法流转被拒（状态不被改写）
  5. 迟到结果：命令已过期 → 收敛为 timeout，迟到内容留痕但不改结论
  6. 重启代次变化：结果 boot_id 与命令不同 → 不改状态，标记 generation_mismatch
  7. 超时：过期扫描只改状态，**不自动重发**（发布次数不变，republished=0）

权限模型取自真实实现：app.core.company_scope（公司可见性）+
app.api.auth.check_permission("manage_robots")，不在测试里另造一套表。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.models import ActuatorCommand

import app.api.actuator_commands as ac_mod
from app.models.models import CommandStatus


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


async def _world(iso):
    """两个公司各一套（大棚+设备+用户），用来验跨租户。"""
    from app.models.models import UserRole

    fx, db = iso
    c1 = await fx.company(db, "控制闭环-公司A")
    c2 = await fx.company(db, "控制闭环-公司B")
    gh1 = await fx.greenhouse(db, c1, "A 棚")
    gh2 = await fx.greenhouse(db, c2, "B 棚")
    dev1 = await fx.device(db, gh1, name="A 棚卷帘机")
    dev2 = await fx.device(db, gh2, name="B 棚卷帘机")
    admin1, t1 = await fx.user(db, UserRole.ADMIN, c1)
    admin2, t2 = await fx.user(db, UserRole.ADMIN, c2)
    viewer1, tv = await fx.user(db, UserRole.VIEWER, c1)
    await db.commit()
    return {
        "db": db, "fx": fx,
        "c1": c1, "c2": c2, "gh1": gh1, "gh2": gh2,
        "dev1": dev1, "dev2": dev2,
        "admin1": admin1, "token1": t1,
        "admin2": admin2, "token2": t2,
        "viewer1": viewer1, "token_viewer": tv,
    }


async def create_cmd(client, token, device_id, **kw):
    body = {"device_id": device_id, "action": "move_up", **kw}
    r = await client.post("/api/v2/actuator-commands", json=body,
                          headers={"Authorization": f"Bearer {token}"})
    return r


async def test_closed_loop_publish_once_then_result(client, db, iso, publisher):
    w = await _world(iso)
    r = await create_cmd(client, w["token1"], w["dev1"].id,
                         boot_id="boot-1", session_id="s-1", command_seq=7)
    assert r.status_code == 201, r.text
    cmd = r.json()
    assert cmd["publish_count"] == 0, "入库阶段不得发布"
    assert cmd["boot_id"] == "boot-1" and cmd["command_seq"] == 7

    r = await client.post(f"/api/v2/actuator-commands/{cmd['id']}/send?publish=true",
                          headers={"Authorization": f"Bearer {w['token1']}"})
    assert r.status_code == 200, r.text
    assert r.json()["publish_count"] == 1
    assert publisher.count == 1, "必须只下发一次"
    assert publisher.calls[0]["command"]["boot_id"] == "boot-1"

    r = await client.post(f"/api/v2/actuator-commands/{cmd['id']}/ack",
                          headers={"Authorization": f"Bearer {w['token1']}"})
    assert r.status_code == 200, r.text
    assert r.json()["is_acked"] is True and r.json()["is_executed"] is False

    r = await client.post(f"/api/v2/actuator-commands/{cmd['id']}/result",
                          json={"status": "executed", "actual_state": {"pos": 120},
                                "boot_id": "boot-1"},
                          headers={"Authorization": f"Bearer {w['token1']}"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "executed" and out["is_executed"] is True

    # 页面读取：列表与详情都能读到同一结论
    lst = await client.get("/api/v2/actuator-commands",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    assert lst.status_code == 200
    row = [x for x in lst.json()["items"] if x["id"] == cmd["id"]][0]
    assert row["status"] == "executed" and row["publish_count"] == 1
    det = await client.get(f"/api/v2/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    assert det.status_code == 200 and det.json()["actual_state"] == {"pos": 120}


async def test_cross_tenant_publish_count_is_zero(client, db, iso, publisher):
    w = await _world(iso)
    # A 公司的管理员去开 B 公司设备的命令：设备不可见（404），且不得发布
    r = await create_cmd(client, w["token1"], w["dev2"].id, boot_id="boot-x")
    assert r.status_code == 404, r.text
    assert publisher.count == 0, "跨租户必须一次都不发布"

    # 就算手工造出一条属于 B 的命令，A 也不能下发
    cmd = await w["fx"].command(w["db"], w["dev2"], w["gh2"], action="move_up", status="pending")
    await db.commit()
    r = await client.post(f"/api/v2/actuator-commands/{cmd.id}/send?publish=true",
                          headers={"Authorization": f"Bearer {w['token1']}"})
    assert r.status_code in (403, 404), r.text
    assert publisher.count == 0, "越权下发不得产生任何报文"


async def test_duplicate_send_does_not_publish_twice(client, db, iso, publisher):
    w = await _world(iso)
    r = await create_cmd(client, w["token1"], w["dev1"].id, boot_id="boot-dup")
    cmd = r.json()
    for _ in range(3):
        await client.post(f"/api/v2/actuator-commands/{cmd['id']}/send?publish=true",
                          headers={"Authorization": f"Bearer {w['token1']}"})
    assert publisher.count == 1, "重复请求不得产生第二次下发"
    det = await client.get(f"/api/v2/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    assert det.json()["publish_count"] == 1


async def test_result_before_send_is_rejected(client, db, iso, publisher):
    w = await _world(iso)
    r = await create_cmd(client, w["token1"], w["dev1"].id, boot_id="boot-early")
    cmd = r.json()
    r = await client.post(f"/api/v2/actuator-commands/{cmd['id']}/result",
                          json={"status": "executed", "boot_id": "boot-early"},
                          headers={"Authorization": f"Bearer {w['token1']}"})
    assert r.status_code == 400, r.text  # 非法流转：pending → executed
    det = await client.get(f"/api/v2/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    assert det.json()["status"] == "pending", "结果先到不得改写状态"


async def test_late_result_becomes_timeout(client, db, iso, publisher):
    w = await _world(iso)
    r = await create_cmd(client, w["token1"], w["dev1"].id,
                         boot_id="boot-late", ttl_seconds=10)
    cmd = r.json()
    await client.post(f"/api/v2/actuator-commands/{cmd['id']}/send?publish=true",
                      headers={"Authorization": f"Bearer {w['token1']}"})
    # 把过期时间改到过去，模拟设备在命令过期之后才回结果
    c = await w["db"].get(ActuatorCommand, cmd["id"])
    c.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await w["db"].commit()

    r = await client.post(f"/api/v2/actuator-commands/{cmd['id']}/result",
                          json={"status": "executed", "boot_id": "boot-late"},
                          headers={"Authorization": f"Bearer {w['token1']}"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out.get("late_result") is True
    assert out["status"] == "timeout", "迟到结果不得把命令盖章为 executed"
    assert "迟到" in (out.get("error_message") or "")


async def test_restart_generation_change_blocks_old_result(client, db, iso, publisher):
    w = await _world(iso)
    r = await create_cmd(client, w["token1"], w["dev1"].id, boot_id="boot-gen-1")
    cmd = r.json()
    await client.post(f"/api/v2/actuator-commands/{cmd['id']}/send?publish=true",
                      headers={"Authorization": f"Bearer {w['token1']}"})
    await client.post(f"/api/v2/actuator-commands/{cmd['id']}/ack",
                      headers={"Authorization": f"Bearer {w['token1']}"})

    # 设备重启过：结果来自新代次 boot-gen-2，不能套到这一代的命令上
    r = await client.post(f"/api/v2/actuator-commands/{cmd['id']}/result",
                          json={"status": "executed", "boot_id": "boot-gen-2"},
                          headers={"Authorization": f"Bearer {w['token1']}"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out.get("generation_mismatch") is True
    assert out["status"] == "accepted", "旧代次结果不得改结论，命令仍停在已接收"
    assert "代次不匹配" in (out.get("error_message") or "")


async def test_expire_sweep_does_not_republish(client, db, iso, publisher):
    w = await _world(iso)
    r = await create_cmd(client, w["token1"], w["dev1"].id,
                         boot_id="boot-exp", ttl_seconds=10)
    cmd = r.json()
    await client.post(f"/api/v2/actuator-commands/{cmd['id']}/send?publish=true",
                      headers={"Authorization": f"Bearer {w['token1']}"})
    c = await w["db"].get(ActuatorCommand, cmd["id"])
    c.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await w["db"].commit()

    r = await client.post("/api/v2/actuator-commands/expire-sweep",
                          headers={"Authorization": f"Bearer {w['token1']}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["expired"] == 1
    assert body["republished"] == 0, "超时一律不自动重发启动（设备侧同样禁止）"
    assert publisher.count == 1, "扫描不得产生任何新报文"

    det = await client.get(f"/api/v2/actuator-commands/{cmd['id']}",
                           headers={"Authorization": f"Bearer {w['token1']}"})
    assert det.json()["status"] == "timeout"
    assert det.json()["publish_count"] == 1


async def test_viewer_cannot_issue_commands(client, db, iso, publisher):
    w = await _world(iso)
    r = await create_cmd(client, w["token_viewer"], w["dev1"].id, boot_id="boot-v")
    assert r.status_code == 403, r.text
    assert publisher.count == 0
