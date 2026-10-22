#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备模拟器 + 自动联调工具（T5）。

一条命令跑完：
    cd /opt/greenhouse-nightly/worktree && ./scripts/sim_device_check.py

它自己做这些事：
  1. 在**测试库**里临时建一套公司/大棚/设备/凭据（跑完删干净）
  2. 拉起进程内测试 broker（tests/mqtt_test_broker.py），把它接到被测服务真正的
     上行入口 `mqtt_service._dispatch_payload` 上
  3. 用真实 MQTT 报文（paho 客户端，本机 socket）模拟设备各种行为
  4. 逐条给出：场景名 / 期望 / 实际观测到的服务端结果 / PASS 还是 FAIL，
     失败场景还会把当时发的原始报文原样打印出来
  5. 写 logs/sim-check-<时间戳>.json

**硬边界**：
  * 只用测试库 greenhouse_nightly_test + NIGHTLY_READONLY=1，
    启动前会自检，不满足直接退出，不给你连错库的机会。
  * 只用自己造的测试 Client ID（SIM- 前缀），**不连真实设备的 Client ID**。
  * 不连生产 EMQX、不起第二个 EMQX 实例、不改任何 EMQX 配置、
    **不向生产注入任何假遥测**。
  * 这里的"联调"是模拟器对着同一个上行入口跑，属于模拟联调，
    不是真实设备联调，报告里不得写成真实环境验证。
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("NIGHTLY_READONLY", "1")

from sqlalchemy import select, text  # noqa: E402

# ── 安全自检：必须指向测试库 ────────────────────────────────
from app.core.config import settings  # noqa: E402
if "greenhouse_nightly_test" not in settings.DATABASE_URL:
    sys.exit(f"拒绝运行：DATABASE_URL 未指向测试库（当前={settings.DATABASE_URL}）")
if os.getenv("NIGHTLY_READONLY") != "1":
    sys.exit("拒绝运行：需要 NIGHTLY_READONLY=1")

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.models.models import (  # noqa: E402
    Company, Device, DeviceStatus, DeviceType, Greenhouse, Image, MqttCredential,
    RawMessage, RobotTask, TaskStatus, TaskType, Telemetry,
)
from app.mqtt.mqtt_service import mqtt_service  # noqa: E402
from app.services import mqtt_credential_service as svc  # noqa: E402
from tests.mqtt_test_broker import TestBroker  # noqa: E402

try:
    import paho.mqtt.client as paho
except ImportError:
    sys.exit("缺少 paho-mqtt，无法跑真实 MQTT 报文链路")

SIM_PREFIX = "SIM"
WAIT_ACK = 5.0


# ── 服务端 ACK 的捕获/丢弃 ─────────────────────────────────
class AckCapture:
    """顶替 mqtt_service._client：既能记录服务端发了什么，也能模拟"ACK 丢了"。"""

    def __init__(self, broker):
        self.broker = broker
        self.sent = []          # [(topic, payload)]
        self.drop = False

    async def publish(self, topic, payload, qos=0, retain=False):
        body = payload.encode() if isinstance(payload, str) else payload
        if self.drop:
            return                       # 假装这条 ACK 在路上丢了
        self.sent.append((topic, body))
        await self.broker.deliver(topic, body)


async def _auth_fn(username, password, clientid):
    async with AsyncSessionLocal() as db:
        return await svc.authenticate(db, username=username,
                                      password=password, clientid=clientid)


class _Msg:
    @staticmethod
    def make(topic, payload_bytes):
        return SimpleNamespace(topic=topic, payload=payload_bytes)


def _publish_sync(host, port, topic, payload_obj, ack_topic, username=None,
                  password=None, clientid=None, wait=WAIT_ACK, raw=False):
    """在线程里用真实 MQTT 报文发一条上行，返回 (conn_rc, 收到的 ACK 列表)。"""
    import json as _json
    import time as _time

    acks, rc = [], [None]

    c = paho.Client(client_id=clientid or f"{SIM_PREFIX}-probe", clean_session=True)
    if username:
        c.username_pw_set(username, password)

    def on_connect(cli, u, f, r):
        rc[0] = r
        if r == 0 and ack_topic:
            cli.subscribe(ack_topic, qos=0)

    def on_message(cli, u, msg):
        try:
            acks.append(_json.loads(msg.payload))
        except Exception:
            acks.append({"_raw": msg.payload.decode("utf-8", "replace")})

    c.on_connect, c.on_message = on_connect, on_message
    # 不订阅 ACK 的场景没有可等的回执，没必要空等满 WAIT_ACK（14 个场景会慢到 70s+）
    if not ack_topic:
        wait = min(wait, 1.5)
    c.connect(host, port, 10)
    c.loop_start()
    deadline = _time.time() + wait
    published = False
    while _time.time() < deadline:
        if rc[0] is not None and rc[0] != 0:
            break
        if rc[0] == 0 and not published:
            body = payload_obj.encode() if raw else _json.dumps(payload_obj).encode()
            c.publish(topic, body, qos=0)
            published = True
        if acks:
            break
        _time.sleep(0.1)
    c.loop_stop()
    c.disconnect()
    return rc[0], acks


def telemetry_msg(device_id, seq, boot=1, temp=24.0, **kw):
    return {
        "type": "telemetry", "schema_version": 1, "device_id": device_id,
        "msg_id": f"{device_id}/{boot}/{seq}", "boot_id": boot, "seq": seq,
        "sampled_at": 0, "time_quality": 0,
        "data": {"temperature": temp, "humidity": 60.0,
                 "valid": [1, 1, 1, 1, 1, 1, 1, 1]},
        **kw,
    }


# ── 场景 ──────────────────────────────────────────────────
async def _count(db, model, **kw):
    q = select(model)
    for k, v in kw.items():
        q = q.where(getattr(model, k) == v)
    return len((await db.execute(q)).scalars().all())


async def scenario_normal(h, dev):
    """固定终端正常遥测 + ACK 回收。"""
    before = await _count(h.db, Telemetry, device_id=dev.id)
    msg = telemetry_msg(dev.device_id, 1)
    rc, acks = await h.send(f"agri/device/{dev.device_id}/up", msg,
                            ack_topic=f"agri/device/{dev.device_id}/ack")
    after = await _count(h.db, Telemetry, device_id=dev.id)
    ok = (rc == 0 and after == before + 1
          and acks and acks[0].get("status") == "stored"
          and acks[0].get("msg_id") == msg["msg_id"])
    return ok, {
        "期望": "入库 1 条 + 回 stored ACK",
        "实际": f"入库 {after - before} 条，ACK={acks[:1]}，连接 rc={rc}",
    }, msg


async def scenario_duplicate(h, dev):
    """同一条消息重复发送两次。"""
    before = await _count(h.db, Telemetry, device_id=dev.id)
    msg = telemetry_msg(dev.device_id, 2)
    await h.send(f"agri/device/{dev.device_id}/up", msg, ack_topic=None)
    async with AsyncSessionLocal() as _:
        pass
    rc, acks = await h.send(f"agri/device/{dev.device_id}/up", msg,
                            ack_topic=f"agri/device/{dev.device_id}/ack")
    after = await _count(h.db, Telemetry, device_id=dev.id)
    ok = after == before + 1 and acks and acks[0].get("status") == "stored"
    return ok, {
        "期望": "第二次重复不影响：仍只有 1 条，且仍回 stored ACK（契约 v1.2 §7）",
        "实际": f"入库 {after - before} 条，ACK={acks[:1]}",
    }, msg


async def scenario_ack_lost_resend(h, dev):
    """ACK 丢了之后设备重发。"""
    before = await _count(h.db, Telemetry, device_id=dev.id)
    msg = telemetry_msg(dev.device_id, 3)
    h.ack.drop = True                      # 服务端发出的 ACK 全部丢弃
    try:
        await h.send(f"agri/device/{dev.device_id}/up", msg, ack_topic=None)
        ack_sent_while_dropped = len(h.ack.sent)
    finally:
        h.ack.drop = False
    rc, acks = await h.send(f"agri/device/{dev.device_id}/up", msg,
                            ack_topic=f"agri/device/{dev.device_id}/ack")
    after = await _count(h.db, Telemetry, device_id=dev.id)
    ok = (after == before + 1 and acks and acks[0].get("status") == "stored"
          and ack_sent_while_dropped >= 1)
    return ok, {
        "期望": "第一次 ACK 被丢后服务端仍已入库，重发仍回 stored，数据不翻倍",
        "实际": f"第一次服务端发过 {ack_sent_while_dropped} 条 ACK（已丢弃）；"
                f"重发后入库 {after - before} 条，ACK={acks[:1]}",
    }, msg


async def scenario_reboot(h, dev):
    """重启换 boot：seq 从 1 重新开始。"""
    before = await _count(h.db, Telemetry, device_id=dev.id)
    msg = telemetry_msg(dev.device_id, 1, boot=99)
    rc, acks = await h.send(f"agri/device/{dev.device_id}/up", msg,
                            ack_topic=f"agri/device/{dev.device_id}/ack")
    after = await _count(h.db, Telemetry, device_id=dev.id)
    ok = rc == 0 and after == before + 1 and acks
    return ok, {
        "期望": "新 boot_id 下 seq 重新计数，仍然正常入库（不与旧 boot 冲突）",
        "实际": f"入库 {after - before} 条，ACK msg_id={acks[0].get('msg_id') if acks else None}",
    }, msg


async def scenario_old_boot_backfill(h, dev):
    """旧 boot 的补传：晚到但描述更早的时刻。"""
    msg = telemetry_msg(dev.device_id, 7, boot=98)
    rc, acks = await h.send(f"agri/device/{dev.device_id}/up", msg,
                            ack_topic=f"agri/device/{dev.device_id}/ack")
    rows = (await h.db.execute(
        select(Telemetry).where(Telemetry.msg_id == msg["msg_id"]))).scalars().all()
    ok = bool(acks) and len(rows) == 1
    return ok, {
        "期望": "照常入库并可回 ACK（要不要顶掉当前值属于展示层，见 T4 status 的 backfill 逻辑）",
        "实际": f"入库 {len(rows)} 条，ACK={acks[:1]}",
    }, msg


async def scenario_out_of_order(h, dev):
    """消息乱序：先发 seq 9，再发 seq 8。"""
    later = telemetry_msg(dev.device_id, 9, boot=97)
    earlier = telemetry_msg(dev.device_id, 8, boot=97)
    _, a1 = await h.send(f"agri/device/{dev.device_id}/up", later, ack_topic=None)
    _, a2 = await h.send(f"agri/device/{dev.device_id}/up", earlier, ack_topic=None)
    n = 0
    for m in (later, earlier):
        n += len((await h.db.execute(
            select(Telemetry).where(Telemetry.msg_id == m["msg_id"]))).scalars().all())
    ok = n == 2
    return ok, {
        "期望": "两条都入库（服务端按 msg_id 去重，不按 seq 排序丢弃）",
        "实际": f"入库 {n} 条（seq 9 与 seq 8）",
    }, {"先发": later, "后发": earlier}


async def scenario_no_trusted_time(h, dev):
    """无可信采集时间：time_quality=0 且 sampled_at=0。"""
    msg = telemetry_msg(dev.device_id, 11, boot=96)
    msg["time_quality"] = 0
    msg["sampled_at"] = 0
    await h.send(f"agri/device/{dev.device_id}/up", msg, ack_topic=None)
    row = (await h.db.execute(
        select(Telemetry).where(Telemetry.msg_id == msg["msg_id"]))).scalar_one_or_none()
    ok = row is not None and row.sampled_at is None and row.time_quality == 0
    return ok, {
        "期望": "sampled_at 必须存 NULL（不能是 1970-01-01），time_quality=0",
        "实际": f"sampled_at={row.sampled_at if row else None}, "
                f"time_quality={row.time_quality if row else None}",
    }, msg


async def scenario_robot_reporting(h, robot, gh, task):
    """机器人最小上报：任务遥测 + 点位到达 + 照片证据。"""
    before_img = await _count(h.db, Image, device_id=robot.id)
    tel = {"type": "telemetry", "device_id": robot.device_id,
           "msg_id": f"{robot.device_id}/T/1", "task_id": task.id,
           "position_x": 1.0, "position_y": 2.0, "battery_level": 88.0,
           "data": {"temperature": 25.0}}
    await h.send(f"agri/robot/{robot.device_id}/up", tel, ack_topic=None)

    arr = {"type": "point_arrival", "device_id": robot.device_id,
           "msg_id": f"{robot.device_id}/ARR/1", "nav_point_code": "SIM-A",
           "greenhouse_id": gh.id}
    await h.send(f"agri/robot/{robot.device_id}/up", arr, ack_topic=None)

    img = {"type": "image", "device_id": robot.device_id,
           "msg_id": f"{robot.device_id}/IMG/1", "task_id": task.id,
           "image_url": "http://sim.local/SIM-1.jpg",
           "captured_at": datetime.now(timezone.utc).isoformat(),
           "nav_point_code": "SIM-A"}
    await h.send(f"agri/robot/{robot.device_id}/up", img, ack_topic=None)

    after_img = await _count(h.db, Image, device_id=robot.id)
    ok = after_img == before_img + 1
    return ok, {
        "期望": "任务遥测/点位到达/照片通知都能被服务端接收，照片落到 images 表",
        "实际": f"images 新增 {after_img - before_img} 条（共 {after_img} 条）",
    }, {"telemetry": tel, "point_arrival": arr, "image": img}


async def scenario_image_task_missing(h, robot, gh):
    """照片证据带一个不存在的任务号。"""
    before = await _count(h.db, Image, device_id=robot.id)
    img = {"type": "image", "device_id": robot.device_id,
           "msg_id": f"{robot.device_id}/IMG/2", "task_id": "NO-SUCH-TASK",
           "image_url": "http://sim.local/SIM-2.jpg",
           "captured_at": datetime.now(timezone.utc).isoformat()}
    await h.send(f"agri/robot/{robot.device_id}/up", img, ack_topic=None)
    after = await _count(h.db, Image, device_id=robot.id)
    return (after == before), {
        "期望": "task_id 必须是已存在的巡检任务（images.task_id 有外键），"
                "不存在的任务号不入库，也不静默编造一个任务",
        "实际": f"入库 {after - before} 条（应为 0）",
    }, img


# ── 负向：故意构造错误，证明检测有效 ────────────────────────
async def scenario_wrong_identity(h, dev, other):
    """Topic 是 A，报文里自称 B。"""
    msg = telemetry_msg(other.device_id, 21, boot=95)
    await h.send(f"agri/device/{dev.device_id}/up", msg, ack_topic=None)
    rows = (await h.db.execute(
        select(Telemetry).where(Telemetry.msg_id == msg["msg_id"]))).scalars().all()
    return (len(rows) == 0), {
        "期望": "Topic 与报文身份不一致 → 拒绝、不入库",
        "实际": f"入库 {len(rows)} 条（应为 0）",
    }, msg


async def scenario_cross_device_topic(h, dev, other):
    """用自己的身份去发别人的上行主题。"""
    msg = telemetry_msg(dev.device_id, 22, boot=94)
    await h.send(f"agri/device/{other.device_id}/up", msg, ack_topic=None)
    rows = (await h.db.execute(
        select(Telemetry).where(Telemetry.msg_id == msg["msg_id"]))).scalars().all()
    return (len(rows) == 0), {
        "期望": "冒用他人上行主题 → 拒绝、不入库",
        "实际": f"入库 {len(rows)} 条（应为 0）",
    }, msg


async def scenario_garbage_payload(h, dev):
    """非 JSON 上行（生产上真实存在：USR DTU 的注册包 www.usr.cn）。"""
    rows_before = await _count(h.db, RawMessage, device_id=dev.device_id)
    await h.send(f"agri/device/{dev.device_id}/up", "www.usr.cn", ack_topic=None,
                 raw=True)
    rows_after = await _count(h.db, RawMessage, device_id=dev.device_id)
    return (rows_after == rows_before), {
        "期望": "非 JSON 报文被丢弃、不入库、服务不崩",
        "实际": f"raw_messages 新增 {rows_after - rows_before} 条（应为 0）",
    }, "www.usr.cn（10 字节 ASCII，非 JSON）"


async def scenario_unknown_device(h):
    """未注册设备上报。"""
    ghost = "SIM-NOT-REGISTERED"
    msg = telemetry_msg(ghost, 1, boot=1)
    await h.send(f"agri/device/{ghost}/up", msg, ack_topic=None)
    rows = (await h.db.execute(
        select(Telemetry).where(Telemetry.msg_id == msg["msg_id"]))).scalars().all()
    return (len(rows) == 0), {
        "期望": "未注册设备 → 拒绝、不入库",
        "实际": f"入库 {len(rows)} 条（应为 0）",
    }, msg


async def scenario_disabled_credential(h, dev, cred_user, cred_pass, cred_cid):
    """凭据停用后再次连接。"""
    async with AsyncSessionLocal() as db:
        cred = await svc.get_by_device(db, dev.device_id)
        await svc.set_enabled(db, cred=cred, enabled=False,
                              reason="sim tool 验证停用")
    rc, acks = await h.send(f"agri/device/{dev.device_id}/up",
                            telemetry_msg(dev.device_id, 99, boot=93),
                            ack_topic=None, username=cred_user,
                            password=cred_pass, clientid=cred_cid)
    ok = rc == 5 and not acks
    return ok, {
        "期望": "停用后 broker 拒绝连接（CONNACK rc=5），消息一条也发不出去",
        "实际": f"connect rc={rc}（5=被拒），收到 ACK {len(acks)} 条",
    }, {"username": cred_user, "clientid": cred_cid}


# ── 主流程 ────────────────────────────────────────────────
class Harness:
    def __init__(self, cred=None):
        self.broker = None
        self.ack = None
        self.db = None
        # 默认用已登记的模拟凭据连 broker（EMQX 认证口径下，空用户名一律拒绝），
        # 负向场景要专门证明"连上了但被服务端拒绝"时才走这里。
        self.cred_user = cred[0] if cred else None
        self.cred_pass = cred[1] if cred else None
        self.cred_cid = cred[2] if cred else None

    async def send(self, topic, payload, ack_topic=None, username=None,
                   password=None, clientid=None, raw=False):
        return await asyncio.to_thread(
            _publish_sync, "127.0.0.1", self.broker.port, topic, payload,
            ack_topic, username or self.cred_user,
            password or self.cred_pass, clientid or self.cred_cid,
            WAIT_ACK, raw)


async def _prepare():
    async with AsyncSessionLocal() as db:
        cid = "c-sim-0001"
        for stmt, prm in (
            ("DELETE FROM telemetry WHERE company_id='c-sim-0001'", {}),
            ("DELETE FROM images WHERE greenhouse_id IN (SELECT id FROM greenhouses WHERE company_id='c-sim-0001')", {}),
            ("DELETE FROM mqtt_credentials WHERE device_id LIKE 'SIM-%'", {}),
            ("DELETE FROM devices WHERE device_id LIKE 'SIM-%'", {}),
            ("DELETE FROM greenhouses WHERE company_id='c-sim-0001'", {}),
            ("DELETE FROM device_receipts WHERE device_id LIKE 'SIM-%'", {}),
            ("DELETE FROM raw_messages WHERE device_id LIKE 'SIM-%'", {}),
            ("DELETE FROM companies WHERE id='c-sim-0001'", {}),
        ):
            await db.execute(text(stmt), prm)
        await db.commit()

        await db.execute(text(
            "INSERT INTO companies(id,name,status) VALUES('c-sim-0001','Sim 公司','active')"))
        gh = Greenhouse(id=str(uuid.uuid4()), name="Sim 棚", company_id=cid,
                        is_active=True, coord_unit="m", schema_version=1)
        db.add(gh)
        await db.flush()
        dev = Device(id=str(uuid.uuid4()), device_id=f"{SIM_PREFIX}-DTU-0001",
                     name="模拟 DTU", device_type=DeviceType.SENSOR,
                     greenhouse_id=gh.id, status=DeviceStatus.ONLINE)
        other = Device(id=str(uuid.uuid4()), device_id=f"{SIM_PREFIX}-DTU-0002",
                       name="另一台模拟 DTU", device_type=DeviceType.SENSOR,
                       greenhouse_id=gh.id, status=DeviceStatus.OFFLINE)
        robot = Device(id=str(uuid.uuid4()), device_id=f"{SIM_PREFIX}-ROBOT-0001",
                       name="模拟机器人", device_type=DeviceType.ROBOT,
                       greenhouse_id=gh.id, status=DeviceStatus.ONLINE)
        db.add_all([dev, other, robot])
        await db.flush()
        task = RobotTask(id=str(uuid.uuid4()), task_name="SIM-TASK",
                         task_type=TaskType.INSPECTION, greenhouse_id=gh.id,
                         device_id=robot.id, status=TaskStatus.RUNNING)
        db.add(task)
        await db.flush()

        cred, plain = await svc.create_credential(
            db, device=dev, asset_no="SIM-DTU-0001",
            clientid="SIM00000000000001", username="dev-sim-0001",
            note="sim tool 自建，跑完即删")
        await db.commit()
        return {"cid": cid, "gh": gh, "dev": dev, "other": other,
                "robot": robot, "task": task, "cred": cred, "plain": plain}


async def _cleanup(ctx):
    async with AsyncSessionLocal() as db:
        for stmt, prm in (
            ("DELETE FROM images WHERE greenhouse_id=:g", {"g": ctx["gh"].id}),
            ("DELETE FROM robot_tasks WHERE greenhouse_id=:g", {"g": ctx["gh"].id}),
            ("DELETE FROM telemetry WHERE greenhouse_id=:g", {"g": ctx["gh"].id}),
            ("DELETE FROM device_receipts WHERE device_id LIKE 'SIM-%' "
             "OR device_id LIKE 'SIM-NOT-REGISTERED'", {}),
            ("DELETE FROM raw_messages WHERE device_id LIKE 'SIM-%'", {}),
            ("DELETE FROM audit_logs WHERE resource='mqtt_credential'", {}),
            ("DELETE FROM mqtt_credentials WHERE device_id LIKE 'SIM-%'", {}),
            ("DELETE FROM devices WHERE device_id LIKE 'SIM-%'", {}),
            ("DELETE FROM greenhouses WHERE id=:g", {"g": ctx["gh"].id}),
            ("DELETE FROM companies WHERE id='c-sim-0001'", {}),
        ):
            await db.execute(text(stmt), prm)
        await db.commit()


async def run_all(verbose=True):
    ctx = await _prepare()
    h = Harness(cred=("dev-sim-0001", ctx["plain"], "SIM00000000000001"))
    h.db = AsyncSessionLocal()

    async def on_publish(topic, payload):
        if topic.count("/") != 3 or not topic.startswith("agri/"):
            return
        await mqtt_service._dispatch_payload(_Msg.make(topic, payload))

    h.broker = TestBroker(authenticate=_auth_fn, on_publish=on_publish)
    await h.broker.start()
    h.ack = AckCapture(h.broker)
    saved_client = getattr(mqtt_service, "_client", None)
    mqtt_service._client = h.ack

    results = []
    scenarios = [
        ("正常遥测 + ACK 回收", "正向", lambda: scenario_normal(h, ctx["dev"])),
        ("同消息重复发送", "正向", lambda: scenario_duplicate(h, ctx["dev"])),
        ("ACK 丢失后重发", "正向", lambda: scenario_ack_lost_resend(h, ctx["dev"])),
        ("重启换 boot，seq 重新计数", "正向", lambda: scenario_reboot(h, ctx["dev"])),
        ("旧 boot 补传", "正向", lambda: scenario_old_boot_backfill(h, ctx["dev"])),
        ("消息乱序", "正向", lambda: scenario_out_of_order(h, ctx["dev"])),
        ("无可信采集时间", "正向", lambda: scenario_no_trusted_time(h, ctx["dev"])),
        ("机器人任务/点位/照片最小上报", "正向",
         lambda: scenario_robot_reporting(h, ctx["robot"], ctx["gh"], ctx["task"])),
        ("照片证据的任务号不存在", "负向",
         lambda: scenario_image_task_missing(h, ctx["robot"], ctx["gh"])),
        ("Topic 与报文身份不一致", "负向",
         lambda: scenario_wrong_identity(h, ctx["dev"], ctx["other"])),
        ("冒用他人上行主题", "负向",
         lambda: scenario_cross_device_topic(h, ctx["dev"], ctx["other"])),
        ("非 JSON 报文（www.usr.cn）", "负向",
         lambda: scenario_garbage_payload(h, ctx["dev"])),
        ("未注册设备上报", "负向", lambda: scenario_unknown_device(h)),
        ("凭据停用后再连接", "负向",
         lambda: scenario_disabled_credential(h, ctx["dev"], "dev-sim-0001",
                                              ctx["plain"], "SIM00000000000001")),
    ]
    try:
        for name, kind, fn in scenarios:
            try:
                ok, detail, payload = await fn()
            except Exception as e:                       # 服务炸了也要记下来
                ok, detail, payload = False, {"期望": "不抛异常",
                                              "实际": f"异常：{type(e).__name__}: {e}"}, None
            results.append({"name": name, "kind": kind, "ok": bool(ok),
                            "detail": detail, "payload": payload})
            if verbose:
                print(f"[{'PASS' if ok else 'FAIL'}] {kind}｜{name}\n"
                      f"        {detail.get('期望','')}\n        {detail.get('实际','')}")
                if not ok and payload is not None:
                    print("        失败报文：" + json.dumps(
                        payload, ensure_ascii=False, default=str)[:600])
    finally:
        mqtt_service._client = saved_client
        await h.broker.stop()
        await h.db.close()
        await _cleanup(ctx)

    passed = sum(1 for r in results if r["ok"])
    if verbose:
        print("=" * 66)
        print(f"合计 {len(results)} 项：通过 {passed} / 失败 {len(results) - passed}")
        print("说明：以上全部在测试库 + 进程内测试 broker 上完成，"
              "未连生产 EMQX、未碰真实设备。")
    return results


def main():
    results = asyncio.run(run_all())
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join("/opt/greenhouse-nightly/logs", f"sim-check-{stamp}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"明细已写入 {out}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
