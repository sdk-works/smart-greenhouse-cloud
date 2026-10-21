"""
mqtt_service.py — V9-Lite (aiomqtt v2.x 适配版)
核心改造：
  ① 统一单上行 Topic：agri/robot/{robot_id}/up
  ② 按 type 字段分发：telemetry / heartbeat / state / image / config_ack
  ③ 点位映射校验：navigation.point_mapping 字段完整性校验
  ④ msg_id + (chassis_point+timestamp) 双重去重
  ⑤ 从 point_mapping.greenhouse_id 精准分流数据
  ⑥ 适配 aiomqtt v2.x 最新 API 规范
  ⑦ 【新增】十大规范：设备消息自动入库 event_standard_log
"""
import asyncio, json, logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Set
import aiomqtt
import os
from types import SimpleNamespace
from app.mqtt.dtu_wire import LineFramer, unpack
from sqlalchemy import select, text
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis import CacheService, get_redis
from app.core.attribution import resolve_attribution
from app.core.photo_service import (
    greenhouse_company, latest_map_version, record_evidence, resolve_point,
)
from app.core.robot_map_service import check_robot_map_version
from app.models.models import (
    Device, Telemetry, DeviceStatus, MappingSyncStatus, ActuatorCommand,
    ActuatorProfile, CommandStatus, NavPointDiscovery, RobotPointConfigRelease,
    InspectionBatch, InspectionVisit, Greenhouse, RawMessage, Image,
)

logger = logging.getLogger(__name__)


def _parse_ts(ts) -> datetime:
    if not ts: return datetime.now(timezone.utc)
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _safe(v, t=dict): return v if isinstance(v, t) else (t() if t is dict else None)



def _normalize_realtime_data(data):
    """把原始报文 data 补上与数据库/前端一致的字段名，避免实时面板丢字段。

    设备上报用 co2/light/ph，而 DB 列与前端面板用 co2_level/light_intensity/ph_value。
    这里只补别名，不覆盖已有值，原始字段保留。
    """
    if not isinstance(data, dict):
        return {}
    out = dict(data)
    for raw, norm in (("co2", "co2_level"), ("light", "light_intensity"), ("ph", "ph_value")):
        if out.get(norm) is None and out.get(raw) is not None:
            out[norm] = out[raw]
    return out


class MQTTService:
    def __init__(self):
        self._client: Optional[aiomqtt.Client] = None
        self._running = False
        self._tasks: Set[asyncio.Task] = set()
        self._subscribed: Set[str] = set()
        self._seen_msg_ids: Set[str] = set()
        # Only explicitly designated DTU topics use byte-stream framing.
        self._dtu_robots = set(filter(None, os.getenv('DTU_ROBOT_IDS', 'ROB001').split(',')))
        self._dtu_framers = {r: LineFramer() for r in self._dtu_robots}
        self._dtu_lock = asyncio.Lock()

    async def connect(self):
        self._running = True
        logger.info(f"[MQTT-V9] 启动 → {settings.MQTT_BROKER_HOST}:{settings.MQTT_BROKER_PORT}")
        await self._run_loop()

    async def disconnect(self):
        self._running = False
        if self._client:
            await self._client.disconnect()
        self._client = None
        for t in list(self._tasks): t.cancel()

    async def publish_command(self, device_id: str, command: dict) -> bool:
        if not self._client or not self._client._connected: return False
        try:
            topic = f"agri/robot/{device_id}/command"
            await self._client.publish(topic, json.dumps(command, ensure_ascii=False), qos=1)
            logger.info(f"[MQTT-V9] ✅ 指令 device={device_id} cmd={command.get('command')}")
            return True
        except Exception as e:
            logger.error(f"[MQTT-V9] 指令失败: {e}"); return False

    async def publish_device_command(self, device_id: str, command: dict) -> bool:
        """风机/水阀命令使用独立命名空间，QoS 1。"""
        if not self._client or not self._client._connected: return False
        try:
            await self._client.publish(f"agri/device/{device_id}/command",
                                       json.dumps(command, ensure_ascii=False), qos=1)
            return True
        except Exception as e:
            logger.error(f"[CONTROL-V1] 指令发布失败: {e}"); return False

    async def publish_config(self, device_id: str, config: dict) -> bool:
        if not self._client or not self._client._connected: return False
        try:
            topic = f"agri/robot/{device_id}/config"
            # Newline is harmless to MQTT clients and required by serial DTU framing.
            await self._client.publish(topic, json.dumps(config, ensure_ascii=False) + "\n", qos=1, retain=True)
            logger.info(f"[MQTT-V9] ✅ 配置下发 device={device_id} version={config.get('config_version')}")
            return True
        except Exception as e:
            logger.error(f"[MQTT-V9] 配置下发失败: {e}"); return False

    async def subscribe_device(self, device_id: str, greenhouse_id: str = ""):
        if not self._client or not self._client._connected: return
        t = f"agri/robot/{device_id}/up"
        if t not in self._subscribed:
            try:
                await self._client.subscribe(t, qos=0)
                self._subscribed.add(t)
                logger.info(f"[MQTT-V9] 动态订阅: {t}")
            except Exception as e:
                logger.error(f"[MQTT-V9] 订阅失败 {t}: {e}")

    async def _run_loop(self):
        delay = 5
        while self._running:
            try:
                await self._connect_and_listen()
            except aiomqtt.MqttError as e:
                self._client = None; self._subscribed.clear()
                logger.warning(f"[MQTT-V9] 断开: {e}，{delay}s 重连...")
                await asyncio.sleep(delay)
            except asyncio.CancelledError: break
            except Exception as e:
                self._client = None; self._subscribed.clear()
                logger.error(f"[MQTT-V9] 异常: {e}", exc_info=True)
                await asyncio.sleep(delay)

    async def _connect_and_listen(self):
        kw = {
            "hostname": settings.MQTT_BROKER_HOST,
            "port":     settings.MQTT_BROKER_PORT,
            "client_id": settings.MQTT_CLIENT_ID,
            "keepalive": 60,
        }
        if settings.MQTT_USERNAME: kw["username"] = settings.MQTT_USERNAME
        if settings.MQTT_PASSWORD: kw["password"] = settings.MQTT_PASSWORD

        self._client = aiomqtt.Client(**kw)
        async with self._client:
            logger.info("[MQTT-V9] ✅ 连接成功")
            await self._client.subscribe("agri/robot/+/up", qos=0)
            await self._client.subscribe("agri/device/+/up", qos=1)
            self._subscribed.add("agri/robot/+/up")
            logger.info("[MQTT-V9] ✅ 订阅 agri/robot/+/up（统一单Topic）")

            async with self._client.messages() as messages:
                async for message in messages:
                    robot = str(message.topic).split('/')
                    if len(robot) == 4 and robot[:2] == ['agri', 'robot'] and robot[3] == 'up' and robot[2] in self._dtu_robots:
                        # Preserve fragment order; do not create concurrent tasks for a stream.
                        await self._dispatch(message)
                        continue
                    task = asyncio.create_task(self._dispatch(message))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

    async def _dispatch(self, message):
        topic = str(message.topic)
        parts = topic.split('/')
        if len(parts) == 4 and parts[:2] == ['agri', 'robot'] and parts[3] == 'up' and parts[2] in self._dtu_robots:
            async with self._dtu_lock:
                framer = self._dtu_framers[parts[2]]
                raw = bytes(message.payload)
                # Preserve legacy complete JSON MQTT messages when no partial line exists.
                if not framer.buffer and not framer.dropping and b'\n' not in raw:
                    try:
                        obj = json.loads(raw)
                        if isinstance(obj, dict) and not any(k in obj for k in ('wire', 'body', 'sha256')):
                            await self._dispatch_payload(message)
                            return
                    except (ValueError, UnicodeError):
                        pass
                before = framer.errors
                for line in framer.feed(raw):
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict) and any(k in obj for k in ('wire', 'body', 'sha256')):
                            await self._dispatch_dtu(parts[2], obj)
                        else:
                            await self._dispatch_payload(SimpleNamespace(topic=topic, payload=line))
                    except Exception as exc:
                        logger.warning('[DTU] rejected line robot=%s: %s', parts[2], exc)
                if framer.errors != before:
                    logger.warning('[DTU] discarded oversized/expired frame robot=%s', parts[2])
            return
        await self._dispatch_payload(message)

    async def _dispatch_dtu(self, robot_id, envelope):
        body, digest = unpack(envelope)
        if body.get('robot_id') != robot_id:
            raise ValueError('Robot identity does not match topic')
        kind = body.get('type')
        if kind == 'telemetry':
            accepted = await self._handle_telemetry(robot_id, body, digest=digest)
        elif kind == 'heartbeat':
            accepted = await self._handle_heartbeat(robot_id, body, digest=digest)
        elif kind == 'point_discovered':
            accepted = await self._handle_point_discovered(robot_id, body, digest)
        elif kind == 'point_arrival':
            accepted = await self._handle_point_arrival(robot_id, body, digest)
        elif kind == 'config_ack':
            async with AsyncSessionLocal() as db:
                duplicate = await self._receipt_check(db, robot_id, body['msg_id'], digest)
                if not duplicate:
                    await self._receipt_insert(db, robot_id, body['msg_id'], digest, kind)
                    await self._apply_config_ack(db, robot_id, body)
                await db.commit()
            accepted = True
        elif kind == 'link_test':
            # Diagnostic receipts are isolated from actual sensor records.
            async with AsyncSessionLocal() as db:
                duplicate = await self._receipt_check(db, robot_id, body['msg_id'], digest)
                if not duplicate:
                    await self._receipt_insert(db, robot_id, body['msg_id'], digest, kind)
                await db.commit()
            accepted = True
        else:
            raise ValueError('Unsupported acknowledged DTU message type')
        if accepted and self._client:
            ack = dict(type='upload_ack', wire='dtu-v1', robot_id=robot_id,
                       msg_id=body['msg_id'], sha256=digest, status='stored')
            await self._client.publish(f'agri/robot/{robot_id}/config',
                                      json.dumps(ack, separators=(',', ':')) + '\n', qos=1, retain=False)
            logger.info('[DTU] stored/ack robot=%s msg_id=%s', robot_id, body['msg_id'])

    async def _handle_point_discovered(self, robot_id, payload, digest):
        code = str(payload.get('nav_point_code', '')).strip()
        if not code or len(code) > 64:
            raise ValueError('Invalid nav_point_code')
        seen = _parse_ts(payload.get('timestamp'))
        async with AsyncSessionLocal() as db:
            if await self._receipt_check(db, robot_id, payload['msg_id'], digest):
                return True
            row = (await db.execute(select(NavPointDiscovery).where(
                NavPointDiscovery.robot_id == robot_id,
                NavPointDiscovery.nav_point_code == code))).scalar_one_or_none()
            if row:
                row.last_seen_at = seen
                row.seen_count = int(row.seen_count or 0) + int(payload.get('seen_count', 1))
                row.raw_data = payload
            else:
                row = NavPointDiscovery(robot_id=robot_id, nav_point_code=code,
                    first_seen_at=seen, last_seen_at=seen,
                    seen_count=int(payload.get('seen_count', 1)), raw_data=payload)
                db.add(row)
            await self._receipt_insert(db, robot_id, payload['msg_id'], digest, 'point_discovered')
            await db.commit()
        logger.info('[POINT] discovered robot=%s code=%s', robot_id, code)
        return True

    async def _active_batch(self, db, robot_id, event_time, config_version=None):
        """Return/create the inferred batch without pretending to know a route."""
        await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                         {'key': 'inspection-batch:' + robot_id})
        row = (await db.execute(select(InspectionBatch).where(
            InspectionBatch.robot_id == robot_id,
            InspectionBatch.status == 'running',
        ).order_by(InspectionBatch.started_at.desc()).limit(1))).scalar_one_or_none()
        cutoff = event_time - timedelta(minutes=15)
        if row and row.last_event_at < cutoff:
            row.status = 'partial'
            row.ended_at = row.last_event_at
            row.close_reason = 'arrival_event_timeout'
            row = None
        if not row:
            row = InspectionBatch(
                robot_id=robot_id, status='running', source='auto_event',
                route_identification='unknown', config_version=config_version,
                started_at=event_time, last_event_at=event_time,
            )
            db.add(row)
            await db.flush()
        else:
            row.last_event_at = max(row.last_event_at, event_time)
            if row.config_version is None:
                row.config_version = config_version
        return row

    async def _record_visit(self, db, robot_id, payload, *, status,
                            telemetry_id=None, digest=None):
        nav = _safe(payload.get('navigation'))
        pm = _safe(nav.get('point_mapping'))
        code = str(payload.get('nav_point_code') or pm.get('chassis_point') or '').strip()
        if not code:
            return None
        event_id = str(payload.get('arrival_event_id') or payload.get('task_id') or
                       payload.get('msg_id') or digest or '').strip()
        if not event_id:
            raise ValueError('Inspection point event has no stable identity')
        existing = (await db.execute(select(InspectionVisit).where(
            InspectionVisit.robot_id == robot_id,
            InspectionVisit.event_id == event_id))).scalar_one_or_none()
        event_time = _parse_ts(payload.get('arrived_at') or payload.get('timestamp'))
        if existing:
            if status == 'sampled':
                existing.status = status
                existing.sampled_at = _parse_ts(payload.get('timestamp'))
                existing.telemetry_id = telemetry_id or existing.telemetry_id
            return existing
        batch = await self._active_batch(db, robot_id, event_time,
                                         payload.get('config_version'))
        visit = InspectionVisit(
            batch_id=batch.id, robot_id=robot_id, event_id=event_id,
            task_id=payload.get('task_id'), nav_point_code=code,
            greenhouse_id=pm.get('greenhouse_id'),
            point_name=pm.get('greenhouse_point'), mapping_id=pm.get('mapping_id'),
            config_version=payload.get('config_version'), status=status,
            arrived_at=event_time,
            sampled_at=_parse_ts(payload.get('timestamp')) if status == 'sampled' else None,
            telemetry_id=telemetry_id, raw_event=payload,
        )
        db.add(visit)
        return visit

    async def _handle_point_arrival(self, robot_id, payload, digest=None):
        async with AsyncSessionLocal() as db:
            if digest and await self._receipt_check(db, robot_id, payload['msg_id'], digest):
                return True
            await self._record_visit(db, robot_id, payload, status='arrived', digest=digest)
            result = await db.execute(select(Device).where(Device.device_id == robot_id))
            device = result.scalar_one_or_none()
            if device:
                device.status = DeviceStatus.ONLINE
                device.last_seen = datetime.now(timezone.utc)
            if digest:
                await self._receipt_insert(db, robot_id, payload['msg_id'], digest, 'point_arrival')
            await db.commit()
        logger.info('[PATROL] arrival robot=%s point=%s route=unknown', robot_id,
                    payload.get('nav_point_code'))
        return True

    async def _receipt_check(self, db, robot, msg, digest):
        # Transaction lock protects replay checks across server workers/restarts.
        await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                         {'key': robot + ':' + msg})
        row = (await db.execute(text('SELECT digest FROM device_receipts WHERE device_id=:d AND msg_id=:m'),
                                {'d': robot, 'm': msg})).first()
        if row and row[0] != digest:
            raise ValueError('msg_id reused for different content')
        return bool(row)

    async def _receipt_insert(self, db, robot, msg, digest, kind):
        await db.execute(text('INSERT INTO device_receipts(device_id,msg_id,digest,kind) VALUES(:d,:m,:g,:k)'),
                         {'d': robot, 'm': msg, 'g': digest, 'k': kind})

    async def _dispatch_payload(self, message):
        topic = str(message.topic)
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception:
            logger.warning(f"[MQTT-V9] JSON解析失败 topic={topic}"); return

        if not isinstance(payload, dict): return

        parts = topic.split("/")
        robot_id = parts[2] if len(parts) >= 3 else payload.get("device_id", "")

        msg_id = payload.get("msg_id")
        msg_type = payload.get("type", "telemetry")
        # device 通道 telemetry 的重复/冲突检测交给 _handle_device_telemetry(事务锁+device_receipts+回ACK)
        # 不能走 _seen_msg_ids 内存拦截，否则重复上报不会回 stored ACK（契约 v1.2 §7）
        is_device_telemetry = topic.startswith("agri/device/") and msg_type == "telemetry"
        if msg_id and not is_device_telemetry:
            if msg_id in self._seen_msg_ids:
                logger.debug(f"[MQTT-V9] 重复消息 msg_id={msg_id}，已跳过")
                return
        logger.debug(f"[MQTT-V9] 收到 robot={robot_id} type={msg_type}")

        try:
            handled = True
            if topic.startswith("agri/device/") and msg_type in {"command_ack", "device_status"}:
                handled = await self._handle_actuator_message(robot_id, payload)
            elif msg_type == "telemetry":
                if topic.startswith("agri/device/"):
                    handled = await self._handle_device_telemetry(robot_id, payload)
                else:
                    handled = await self._handle_telemetry(robot_id, payload)
            elif msg_type == "heartbeat":  await self._handle_heartbeat(robot_id, payload)
            elif msg_type == "point_arrival": handled = await self._handle_point_arrival(robot_id, payload)
            elif msg_type == "state":      await self._handle_state(robot_id, payload)
            elif msg_type == "config_ack": await self._handle_config_ack(robot_id, payload)
            elif msg_type == "image":      await self._handle_image_notify(robot_id, payload)
            else: handled = False
            if not handled:
                return
        except Exception as e:
            logger.error(f"[MQTT-V9] 处理异常 type={msg_type}: {e}", exc_info=True)
            return

        if msg_id and not is_device_telemetry:
            self._seen_msg_ids.add(msg_id)
            if len(self._seen_msg_ids) > 10000:
                self._seen_msg_ids = set(list(self._seen_msg_ids)[-5000:])

        # ======================
        # 十大规范：直接写入数据库（最稳定）
        # ======================
        try:
            async with AsyncSessionLocal() as db:
                raw_json = json.dumps(payload, ensure_ascii=False)
                await db.execute(
                    text("""
                    INSERT INTO event_standard_log 
                    (event_no, event_type, event_source, raw_content, create_time)
                    VALUES 
                    (gen_random_uuid(), :type, :source, :raw, NOW())
                    """),
                    {
                        "type": f"MQTT_{msg_type.upper()}",
                        "source": topic,
                        "raw": raw_json
                    }
                )
                await db.commit()
        except Exception as e:
            logger.debug(f"[规范日志] 写入失败: {e}")

    async def _handle_actuator_message(self, hardware_id: str, payload: dict):
        """设备必须用相同 command_id 回执；云端只在回执后更新实际状态。"""
        async with AsyncSessionLocal() as db:
            device = (await db.execute(select(Device).where(Device.device_id == hardware_id))).scalar_one_or_none()
            if not device: return False
            device.status = DeviceStatus.ONLINE; device.last_seen = datetime.now(timezone.utc)
            profile = await db.get(ActuatorProfile, device.id)
            if not profile:
                profile = ActuatorProfile(device_id=device.id); db.add(profile)
            actual = _safe(payload.get("actual_state"))
            if actual:
                profile.actual_state = actual; profile.state_updated_at = datetime.now(timezone.utc)
            command_id = payload.get("command_id")
            if command_id:
                cmd = (await db.execute(select(ActuatorCommand).where(
                    ActuatorCommand.command_id == command_id))).scalar_one_or_none()
                if cmd:
                    status_value = payload.get("status", "accepted")
                    try: cmd.status = CommandStatus(status_value)
                    except ValueError: cmd.status = CommandStatus.FAILED
                    cmd.actual_state = actual
                    cmd.error_message = payload.get("error_message")
                    if cmd.status in {CommandStatus.ACCEPTED, CommandStatus.EXECUTING}:
                        cmd.accepted_at = datetime.now(timezone.utc)
                    if cmd.status in {CommandStatus.EXECUTED, CommandStatus.REJECTED, CommandStatus.FAILED}:
                        cmd.completed_at = datetime.now(timezone.utc)
            await db.commit()
        return True

    def _validate_point_mapping(self, payload: dict) -> Optional[dict]:
        nav = _safe(payload.get("navigation"))
        if not nav:
            return None

        pm = _safe(nav.get("point_mapping"))
        if not pm:
            logger.warning(f"[MQTT-V9] ⚠️ 缺少 navigation.point_mapping 字段，已忽略点位信息")
            return None

        required = {"chassis_point", "greenhouse_id", "greenhouse_point"}
        missing  = required - set(pm.keys())
        if missing:
            logger.warning(f"[MQTT-V9] ⚠️ point_mapping 缺少字段: {missing}，消息已丢弃")
            return None

        return pm

    async def _handle_device_telemetry(self, device_id: str, payload: dict):
        """固定终端(agri/device/{device_id}/up)遥测：业务ACK + (device_id,msg_id)唯一去重。
        契约 v1.2 §3/§5/§7。
        - Topic 的 device_id 必须与 body.device_id 一致；不一致拒绝。
        - 大棚归属由 Device 绑定决定，不信任 payload 自报 greenhouse_id。
        - 落库成功(事务提交)后才发布 stored ACK；重复只存一条仍回 stored；同号异内容拒绝。
        - time_quality=0 / sampled_at=0 时 sampled_at 存 NULL，不绘制成 1970。
        """
        topic_id = str(payload.get("device_id") or "").strip()
        if topic_id and topic_id != device_id:
            logger.warning(f"[DEV-ACK] Topic与body身份不一致 topic={device_id} body={topic_id}，拒绝")
            return False
        msg_id = payload.get("msg_id")
        if not msg_id:
            logger.warning(f"[DEV-ACK] 缺 msg_id device={device_id}，拒绝")
            return False

        # 内容摘要：同号异内容检测（device_receipts.digest）
        import hashlib
        digest = hashlib.sha256(
            json.dumps(payload.get("data", {}), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

        received_at = datetime.now(timezone.utc)
        data = _safe(payload.get("data"))
        time_quality = int(payload.get("time_quality", 0) or 0)
        sampled_at = None
        if time_quality and payload.get("sampled_at"):
            sampled_at = _parse_ts(payload.get("sampled_at"))

        async with AsyncSessionLocal() as db:
            await db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'),
                             {'key': 'device-receipt:' + device_id + ':' + msg_id})
            row = (await db.execute(
                text('SELECT digest FROM device_receipts WHERE device_id=:d AND msg_id=:m'),
                {'d': device_id, 'm': msg_id})).first()
            if row:
                if row[0] != digest:
                    logger.warning(f"[DEV-ACK] msg_id 复用且内容不同 device={device_id} msg={msg_id}，冲突拒绝")
                    await db.commit()
                    return False
                # 重复上报：不重复入库，仍回 stored
                await db.commit()
                await self._publish_device_ack(device_id, msg_id, "stored")
                logger.info(f"[DEV-ACK] 重复上报已存在 device={device_id} msg={msg_id}，回 stored")
                return True

            result = await db.execute(select(Device).where(Device.device_id == device_id))
            device = result.scalar_one_or_none()
            if not device:
                logger.warning(f"[DEV-ACK] 未注册设备 {device_id}，拒绝")
                await db.commit()
                return False

            device.status = DeviceStatus.ONLINE
            device.last_seen = datetime.now(timezone.utc)

            # M3：按绑定窗口判定该时刻的归属。
            # 补传场景用采样时间（时间可信时），否则用接收时间 —— 设备可能早已搬走。
            attribution = await resolve_attribution(
                db, device_id=device.id,
                at=sampled_at if (time_quality and sampled_at) else received_at)
            if attribution["status"] == "resolved":
                greenhouse_id = attribution["greenhouse_id"] or device.greenhouse_id
            else:
                # 判定不了：写 pending，greenhouse_id 用设备当前所在大棚作兜底，
                # 但状态明确标注为待确认，绝不冒充已确认
                greenhouse_id = attribution["greenhouse_id"] or device.greenhouse_id
            gh_row = await db.get(Greenhouse, greenhouse_id) if greenhouse_id else None
            company_id = gh_row.company_id if gh_row else None

            # 契约 §3.2 顺序与字段映射
            t = Telemetry(
                device_id        = device.id,
                greenhouse_id    = greenhouse_id,
                timestamp        = received_at,          # 服务器接收时间
                sampled_at       = sampled_at,           # 采样时间，未知为 NULL
                time_quality     = time_quality,
                msg_id           = msg_id,
                data_type        = "telemetry",
                temperature      = data.get("temperature"),
                humidity         = data.get("humidity"),
                soil_moisture    = data.get("soil_moisture"),
                soil_temperature = data.get("soil_temperature"),
                soil_ec          = data.get("soil_ec"),
                soil_nitrogen    = data.get("soil_nitrogen"),
                soil_phosphorus  = data.get("soil_phosphorus"),
                soil_potassium   = data.get("soil_potassium"),
                light_intensity  = data.get("light"),
                co2_level        = data.get("co2"),
                ph_value         = data.get("ph"),
                company_id       = company_id,
                attribution_status = attribution["status"],
                attribution_reason = attribution["reason"],
                binding_id       = attribution["binding_id"],
                binding_version  = attribution["binding_version"],
                raw_data         = payload,
            )
            db.add(t)
            await db.flush()
            await db.execute(text(
                'INSERT INTO device_receipts(device_id,msg_id,digest,kind) VALUES(:d,:m,:g,:k)'),
                {'d': device_id, 'm': msg_id, 'g': digest, 'k': 'telemetry'})
            await db.commit()

            try:
                async with AsyncSessionLocal() as rdb:
                    rdb.add(RawMessage(
                        msg_id=msg_id, source="mqtt", device_id=device_id,
                        received_at=received_at, payload=payload,
                        payload_bytes=len(json.dumps(payload, ensure_ascii=False)),
                        parse_status="stored", parser_version="v1"))
                    await rdb.commit()
            except Exception as e:
                logger.warning(f"[RAW] 原始报文记录失败: {e}")

        await self._publish_device_ack(device_id, msg_id, "stored")
        await self._push_realtime(device_id, greenhouse_id, received_at, data, {}, None,
                                  company_id=company_id)
        logger.info(f"[DEV-ACK] ✅ 遥测入库并回 stored device={device_id} msg={msg_id}")
        return True

    async def _publish_device_ack(self, device_id: str, msg_id: str, status: str):
        """向 agri/device/{device_id}/ack 发布业务确认；retain=false。"""
        if not self._client:
            logger.warning(f"[DEV-ACK] MQTT client 未就绪，ACK 未发布 device={device_id} msg={msg_id}")
            return
        ack = dict(type="upload_ack", msg_id=msg_id, status=status)
        try:
            await self._client.publish(
                f"agri/device/{device_id}/ack",
                json.dumps(ack, separators=(",", ":")) + "\n",
                qos=1, retain=False)
        except Exception as e:
            logger.error(f"[DEV-ACK] ACK 发布失败 device={device_id} msg={msg_id}: {e}")

    async def _handle_telemetry(self, robot_id: str, payload: dict, digest=None):
        async with AsyncSessionLocal() as db:
            msg_id = payload.get("msg_id")
            if digest and await self._receipt_check(db, robot_id, msg_id, digest):
                return True
            if msg_id and not digest:
                duplicate = await db.execute(
                    select(Telemetry.id).where(Telemetry.msg_id == msg_id).limit(1)
                )
                if duplicate.scalar_one_or_none():
                    logger.info(f"[MQTT-V9] 重复消息 msg_id={msg_id}，数据库已存在")
                    return False
            result = await db.execute(select(Device).where(Device.device_id == robot_id))
            device = result.scalar_one_or_none()
            if not device:
                logger.warning(f"[MQTT-V9] 未注册设备 {robot_id}"); return False

            device.status    = DeviceStatus.ONLINE
            device.last_seen = datetime.now(timezone.utc)

            gh_row      = await db.get(Greenhouse, device.greenhouse_id) if device.greenhouse_id else None
            company_id  = gh_row.company_id if gh_row else None

            ts           = _parse_ts(payload.get("timestamp"))
            data         = _safe(payload.get("data"))
            robot_status = _safe(payload.get("robot_status"))
            pm           = self._validate_point_mapping(payload)
            # M3：补传归属按绑定窗口判定（设备可能早已搬走/改绑）
            sample_ts = _parse_ts(payload.get("sampled_at")) \
                if payload.get("sampled_at") else None
            attribution = await resolve_attribution(
                db, device_id=device.id, at=sample_ts or ts)

            greenhouse_id = (
                pm["greenhouse_id"] if pm
                else payload.get("greenhouse_id") or device.greenhouse_id
            )

            t = Telemetry(
                device_id        = device.id,
                greenhouse_id    = greenhouse_id,
                timestamp        = ts,
                # 契约 v1 §1：采样时间未知存 NULL，绝不拿接收时间冒充
                sampled_at       = sample_ts,
                time_quality     = int(payload.get("time_quality", 0) or 0),
                msg_id           = payload.get("msg_id"),
                data_type        = "telemetry",
                temperature      = data.get("temperature"),
                humidity         = data.get("humidity"),
                soil_moisture    = data.get("soil_moisture"),
                soil_temperature = data.get("soil_temperature"),
                soil_ec          = data.get("soil_ec"),
                soil_nitrogen    = data.get("soil_nitrogen"),
                soil_phosphorus  = data.get("soil_phosphorus"),
                soil_potassium   = data.get("soil_potassium"),
                sample_count     = data.get("sample_count"),
                task_id          = payload.get("task_id"),
                light_intensity  = data.get("light") or data.get("light_intensity"),
                co2_level        = data.get("co2") or data.get("co2_level"),
                ph_value         = data.get("ph"),
                battery_level    = robot_status.get("battery"),
                position_x       = _safe(robot_status.get("position"), dict).get("x") if robot_status.get("position") else None,
                position_y       = _safe(robot_status.get("position"), dict).get("y") if robot_status.get("position") else None,
                robot_status     = robot_status,
                chassis_point    = pm["chassis_point"]    if pm else None,
                greenhouse_point = pm["greenhouse_point"] if pm else None,
                mapping_id       = pm.get("mapping_id")  if pm else None,
                raw_data         = payload,
                # M1：遥测必须带上公司归属，否则查询侧无法隔离
                company_id       = await greenhouse_company(db, greenhouse_id),
                attribution_status = attribution["status"],
                attribution_reason = attribution["reason"],
                binding_id       = attribution["binding_id"],
                binding_version  = attribution["binding_version"],
            )
            db.add(t)
            await db.flush()
            # Telemetry remains independently useful.  It also upgrades the
            # matching arrival visit, or creates a fallback visit for older IPCs.
            await self._record_visit(db, robot_id, payload, status='sampled',
                                     telemetry_id=t.id, digest=digest)
            if digest:
                await self._receipt_insert(db, robot_id, msg_id, digest, 'telemetry')
            await db.commit()

        await self._push_realtime(robot_id, greenhouse_id, ts, data, robot_status, pm,
                                  company_id=company_id)
        logger.info(
            f"[MQTT-V9] ✅ 遥测 robot={robot_id}"
            + (f" {pm['chassis_point']}→{pm['greenhouse_point']}" if pm else "")
            + f" T={data.get('temperature')}°C"
        )
        return True

    async def _handle_heartbeat(self, robot_id: str, payload: dict, digest=None):
        heartbeat_company_id = None
        async with AsyncSessionLocal() as db:
            msg_id = payload.get("msg_id")
            if digest and await self._receipt_check(db, robot_id, msg_id, digest):
                return True
            result = await db.execute(select(Device).where(
                Device.device_id.in_([robot_id, payload.get("robot_id", "")])
            ))
            device = result.scalar_one_or_none()
            if not device:
                return False
            device.status    = DeviceStatus.ONLINE
            device.last_seen = datetime.now(timezone.utc)
            gh_row = await db.get(Greenhouse, device.greenhouse_id) if device.greenhouse_id else None
            heartbeat_company_id = gh_row.company_id if gh_row else None
            if digest:
                await self._receipt_insert(db, robot_id, msg_id, digest, 'heartbeat')
            await db.commit()
        try:
            redis = await get_redis()
            cache = CacheService(redis, company_id=heartbeat_company_id)
            await cache.set_device_online(robot_id, True)
            await cache.publish("telemetry_updates", {
                "type": "heartbeat", "device_id": robot_id,
                "battery": payload.get("battery"),
                "status":  payload.get("status"),
            })
        except Exception as e:
            logger.error(f"[MQTT-V9] 心跳Redis: {e}")
        logger.info(f"[MQTT-V9] 💓 心跳 robot={robot_id} batt={payload.get('battery')}%")
        return True

    async def _handle_state(self, robot_id: str, payload: dict):
        pm = self._validate_point_mapping(payload)
        state = payload.get("state", "")
        logger.info(f"[MQTT-V9] 🔄 状态 robot={robot_id} state={state}"
                    + (f" @ {pm['chassis_point']}→{pm['greenhouse_point']}" if pm else ""))
        try:
            redis = await get_redis()
            cache = CacheService(redis)
            await cache.publish("telemetry_updates", {
                "type": "state", "device_id": robot_id,
                "state": state, "task_id": payload.get("task_id"),
                "point_mapping": pm,
            })
        except Exception: pass

    async def _handle_config_ack(self, robot_id: str, payload: dict):
        cv     = payload.get("config_version")
        pm_ok  = payload.get("point_mapping_sync_status", "synced")
        logger.info(f"[MQTT-V9] 📩 config_ack robot={robot_id} version={cv} mapping={pm_ok}")
        async with AsyncSessionLocal() as db:
            await self._apply_config_ack(db, robot_id, payload)
            await db.commit()
        try:
            redis = await get_redis()
            cache = CacheService(redis)
            await cache.publish("config_ack_updates", {
                "device_id": robot_id, "config_version": cv,
                "mapping_sync_status": pm_ok,
            })
        except Exception:
            pass

    async def _apply_config_ack(self, db, robot_id, payload):
        cv = payload.get("config_version")
        # M2：机器人回传的地图版本与平台登记不一致 → 只告警，不自动改
        reported_map = payload.get("robot_map_version") or payload.get("map_version")
        try:
            result = await check_robot_map_version(db, robot_id, reported_map,
                                                   source="config_ack")
            if result["status"] == "mismatch":
                logger.warning(
                    f"[MAPVER] ⚠️ robot={robot_id} 登记={result['expected']}"
                    f" 回传={result['reported']}（已记录告警，不自动修改）")
        except Exception as e:
            logger.error(f"[MAPVER] 核对失败 robot={robot_id}: {e}")
        pm_ok = payload.get("point_mapping_sync_status", "synced")
        result = await db.execute(select(Device).where(Device.device_id == robot_id))
        device = result.scalar_one_or_none()
        if device:
            if cv: device.config_version_synced = int(cv)
            device.mapping_sync_status = (
                MappingSyncStatus.SYNCED if pm_ok == "synced"
                else MappingSyncStatus.FAILED)
            device.mapping_synced_at = datetime.now(timezone.utc)
        if cv:
            release = (await db.execute(select(RobotPointConfigRelease).where(
                RobotPointConfigRelease.robot_id == robot_id,
                RobotPointConfigRelease.config_version == int(cv)))).scalar_one_or_none()
            if release:
                release.sync_status = "applied" if pm_ok == "synced" else "rejected"
                release.sync_detail = payload
                release.applied_at = datetime.now(timezone.utc)

    async def _handle_image_notify(self, robot_id: str, payload: dict):
        pm = self._validate_point_mapping(payload)
        logger.info(f"[MQTT-V9] 📷 图片通知 robot={robot_id} url={payload.get('image_url')}"
                    + (f" @ {pm['chassis_point']}" if pm else ""))
        await self._store_image_notify(robot_id, payload, pm)

    async def _store_image_notify(self, robot_id: str, payload: dict, pm):
        """M2：图片通知入库，并尽力解析点位归属（解析不出留 NULL，不猜）。"""
        image_url = payload.get("image_url") or payload.get("url")
        if not image_url:
            return None
        try:
            async with AsyncSessionLocal() as db:
                dev = (await db.execute(select(Device).where(
                    Device.device_id == robot_id))).scalar_one_or_none()
                if not dev:
                    logger.warning(f"[PHOTO] 未注册设备 {robot_id}，图片通知不入库")
                    return None

                greenhouse_id = (pm or {}).get("greenhouse_id") or dev.greenhouse_id
                nav_point_code = (pm or {}).get("nav_point_code") \
                    or payload.get("nav_point_code")
                point = await resolve_point(
                    db, robot_id=robot_id, nav_point_code=nav_point_code,
                    greenhouse_id=greenhouse_id,
                    point_name=payload.get("greenhouse_point") or (pm or {}).get("greenhouse_point"),
                )
                mv = await latest_map_version(db, greenhouse_id)
                captured_at = _parse_ts(payload.get("captured_at")) \
                    if payload.get("captured_at") else datetime.now(timezone.utc)

                img = Image(
                    device_id=dev.id,
                    task_id=payload.get("task_id"),
                    greenhouse_id=greenhouse_id,
                    storage_url=image_url,
                    thumbnail_url=payload.get("thumbnail_url"),
                    file_size=payload.get("file_size"),
                    chassis_point=(pm or {}).get("chassis_point"),
                    greenhouse_point=payload.get("greenhouse_point") or (pm or {}).get("greenhouse_point"),
                    captured_at=captured_at,
                    point_id=point.id if point else None,
                    map_version_id=mv.id if mv else None,
                    batch_id=payload.get("batch_id"),
                    company_id=await greenhouse_company(db, greenhouse_id),
                    tags=["mqtt_notify"],
                )
                db.add(img)
                await db.flush()

                await record_evidence(
                    db,
                    image_id=img.id,
                    image_url=image_url,
                    thumbnail_url=payload.get("thumbnail_url"),
                    task_id=payload.get("task_id"),
                    batch_id=payload.get("batch_id"),
                    point_id=img.point_id,
                    greenhouse_id=greenhouse_id,
                    robot_id=robot_id,
                    nav_point_code=nav_point_code,
                    map_version_id=img.map_version_id,
                    evidence_type="photo",
                    captured_at=captured_at,
                    file_size=payload.get("file_size"),
                )
                await db.commit()
                logger.info(
                    f"[PHOTO] 入库 id={img.id} point={img.point_id or 'NULL(待确认)'}"
                    f" map_version={img.map_version_id or 'NA'}")
                return img.id
        except Exception as e:
            logger.error(f"[PHOTO] 图片入库失败 robot={robot_id}: {e}")
            return None

    async def _push_realtime(self, device_id, greenhouse_id, ts, data, robot_status, pm,
                             company_id=None):
        data = _normalize_realtime_data(data)
        try:
            redis = await get_redis()
            # M1：缓存键带公司前缀，写侧与读侧必须用同一公司标识
            cache = CacheService(redis, company_id=company_id)
            await cache.set_device_online(device_id, True)
            await cache.set_device_status(device_id, {
                "online": True, "last_seen": datetime.now(timezone.utc).isoformat(),
                "data": data, "robot_status": robot_status, "point_mapping": pm,
            })
            await cache.publish("telemetry_updates", {
                "device_id": device_id, "greenhouse_id": greenhouse_id,
                "timestamp": ts.isoformat(), "data": data,
                "robot_status": robot_status, "point_mapping": pm,
            })
        except Exception as e:
            logger.error(f"[MQTT-V9] Redis推送失败: {e}")


mqtt_service = MQTTService()
