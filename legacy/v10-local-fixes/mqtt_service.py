"""
mqtt_service.py — V9-Lite (aiomqtt v2.x 适配版)
核心改造：
  ① 统一单上行 Topic：agri/robot/{robot_id}/up
  ② 按 type 字段分发：telemetry / heartbeat / state / image / config_ack
  ③ 点位映射校验：navigation.point_mapping 字段完整性校验
  ④ msg_id + (chassis_point+timestamp) 双重去重
  ⑤ 从 point_mapping.greenhouse_id 精准分流数据
  ⑥ 适配 aiomqtt v2.x 最新 API 规范
"""
import asyncio, json, logging
from datetime import datetime, timezone
from typing import Optional, Set
import aiomqtt
from sqlalchemy import select
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis import CacheService, get_redis
from app.models.models import (
    Device, Telemetry, DeviceStatus, MappingSyncStatus,
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


class MQTTService:
    def __init__(self):
        self._client: Optional[aiomqtt.Client] = None
        self._running = False
        self._tasks: Set[asyncio.Task] = set()
        self._subscribed: Set[str] = set()
        # msg_id 去重缓存（内存，重启清空；生产用 Redis）
        self._seen_msg_ids: Set[str] = set()

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

    async def publish_config(self, device_id: str, config: dict) -> bool:
        """下发配置（retain=True），含 point_mapping_list"""
        if not self._client or not self._client._connected: return False
        try:
            topic = f"agri/robot/{device_id}/config"
            await self._client.publish(topic, json.dumps(config, ensure_ascii=False), qos=1, retain=True)
            logger.info(f"[MQTT-V9] ✅ 配置下发 device={device_id} version={config.get('config_version')}")
            return True
        except Exception as e:
            logger.error(f"[MQTT-V9] 配置下发失败: {e}"); return False

    async def subscribe_device(self, device_id: str, greenhouse_id: str = ""):
        if not self._client or not self._client._connected: return
        # V9: 只有一个上行 Topic
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
            "client_id": settings.MQTT_CLIENT_ID,  # ✅ v2.x 修正：identifier → client_id
            "keepalive": 60,
        }
        if settings.MQTT_USERNAME: kw["username"] = settings.MQTT_USERNAME
        if settings.MQTT_PASSWORD: kw["password"] = settings.MQTT_PASSWORD

        # ✅ v2.x 修正：先创建 Client 实例，再 async with 管理连接
        self._client = aiomqtt.Client(**kw)
        async with self._client:
            logger.info("[MQTT-V9] ✅ 连接成功")
            # V9 统一单上行 Topic 通配
            await self._client.subscribe("agri/robot/+/up", qos=0)
            self._subscribed.add("agri/robot/+/up")
            logger.info("[MQTT-V9] ✅ 订阅 agri/robot/+/up（统一单Topic）")

            # ✅ v2.x 修正：messages 改为异步上下文管理器，必须 async with 包裹
            async with self._client.messages() as messages:
                async for message in messages:
                    task = asyncio.create_task(self._dispatch(message))
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)

    # ── 消息分发 ─────────────────────────────────────────────

    async def _dispatch(self, message):
        topic = str(message.topic)
        try:
            payload = json.loads(message.payload.decode("utf-8"))
        except Exception:
            logger.warning(f"[MQTT-V9] JSON解析失败 topic={topic}"); return

        if not isinstance(payload, dict): return

        # 提取 robot_id（从 topic: agri/robot/{id}/up）
        parts = topic.split("/")
        robot_id = parts[2] if len(parts) >= 3 else payload.get("device_id", "")

        # msg_id 去重
        msg_id = payload.get("msg_id")
        if msg_id:
            if msg_id in self._seen_msg_ids:
                logger.debug(f"[MQTT-V9] 重复消息 msg_id={msg_id}，已跳过")
                return
            self._seen_msg_ids.add(msg_id)
            if len(self._seen_msg_ids) > 10000:
                self._seen_msg_ids = set(list(self._seen_msg_ids)[-5000:])

        msg_type = payload.get("type", "telemetry")
        logger.debug(f"[MQTT-V9] 收到 robot={robot_id} type={msg_type}")

        try:
            if   msg_type == "telemetry":  await self._handle_telemetry(robot_id, payload)
            elif msg_type == "heartbeat":  await self._handle_heartbeat(robot_id, payload)
            elif msg_type == "state":      await self._handle_state(robot_id, payload)
            elif msg_type == "config_ack": await self._handle_config_ack(robot_id, payload)
            elif msg_type == "image":      await self._handle_image_notify(robot_id, payload)
        except Exception as e:
            logger.error(f"[MQTT-V9] 处理异常 type={msg_type}: {e}", exc_info=True)

    # ── 点位映射校验 ─────────────────────────────────────────

    def _validate_point_mapping(self, payload: dict) -> Optional[dict]:
        """
        校验 navigation.point_mapping 字段完整性
        必须包含：chassis_point、greenhouse_id、greenhouse_point
        返回 mapping dict 或 None（校验失败）
        """
        nav = _safe(payload.get("navigation"))
        if not nav:
            return None  # 无导航字段，不是点位相关消息，允许通过

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

    # ── 各类型 Handler ────────────────────────────────────────

    async def _handle_telemetry(self, robot_id: str, payload: dict):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Device).where(Device.device_id == robot_id))
            device = result.scalar_one_or_none()
            if not device:
                logger.warning(f"[MQTT-V9] 未注册设备 {robot_id}"); return

            device.status    = DeviceStatus.ONLINE
            device.last_seen = datetime.now(timezone.utc)

            ts           = _parse_ts(payload.get("timestamp"))
            data         = _safe(payload.get("data"))
            robot_status = _safe(payload.get("robot_status"))
            pm           = self._validate_point_mapping(payload)

            # 从 point_mapping 精准获取 greenhouse_id
            greenhouse_id = (
                pm["greenhouse_id"] if pm
                else payload.get("greenhouse_id") or device.greenhouse_id
            )

            t = Telemetry(
                device_id        = device.id,
                greenhouse_id    = greenhouse_id,
                timestamp        = ts,
                msg_id           = payload.get("msg_id"),
                data_type        = "telemetry",
                temperature      = data.get("temperature"),
                humidity         = data.get("humidity"),
                soil_moisture    = data.get("soil_moisture"),
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
            )
            db.add(t); await db.commit()

        await self._push_realtime(robot_id, greenhouse_id, ts, data, robot_status, pm)
        logger.info(
            f"[MQTT-V9] ✅ 遥测 robot={robot_id}"
            + (f" {pm['chassis_point']}→{pm['greenhouse_point']}" if pm else "")
            + f" T={data.get('temperature')}°C"
        )

    async def _handle_heartbeat(self, robot_id: str, payload: dict):
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Device).where(
                Device.device_id.in_([robot_id, payload.get("robot_id", "")])
            ))
            device = result.scalar_one_or_none()
            if not device: return
            device.status    = DeviceStatus.ONLINE
            device.last_seen = datetime.now(timezone.utc)
            await db.commit()
        try:
            redis = await get_redis()
            await redis.set(f"device:online:{robot_id}", "1", ex=90)
            cache = CacheService(redis)
            await cache.publish("telemetry_updates", {
                "type": "heartbeat", "device_id": robot_id,
                "battery": payload.get("battery"),
                "status":  payload.get("status"),
            })
        except Exception as e:
            logger.error(f"[MQTT-V9] 心跳Redis: {e}")
        logger.info(f"[MQTT-V9] 💓 心跳 robot={robot_id} batt={payload.get('battery')}%")

    async def _handle_state(self, robot_id: str, payload: dict):
        """机器人状态变更（RUNNING/IDLE/CHARGING/ERROR）"""
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
        """工控机返回配置确认（config_ack）"""
        cv     = payload.get("config_version")
        pm_ok  = payload.get("point_mapping_sync_status", "synced")
        logger.info(f"[MQTT-V9] 📩 config_ack robot={robot_id} version={cv} mapping={pm_ok}")
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Device).where(Device.device_id == robot_id))
            device = result.scalar_one_or_none()
            if device:
                if cv: device.config_version_synced = int(cv)
                device.mapping_sync_status = (
                    MappingSyncStatus.SYNCED if pm_ok == "synced"
                    else MappingSyncStatus.FAILED
                )
                device.mapping_synced_at = datetime.now(timezone.utc)
                await db.commit()
        try:
            redis = await get_redis()
            cache = CacheService(redis)
            await cache.publish("config_ack_updates", {
                "device_id": robot_id, "config_version": cv,
                "mapping_sync_status": pm_ok,
            })
        except Exception: pass

    async def _handle_image_notify(self, robot_id: str, payload: dict):
        """图片通知（不走MQTT传图，仅通知有图片可下载）"""
        pm = self._validate_point_mapping(payload)
        logger.info(f"[MQTT-V9] 📷 图片通知 robot={robot_id} url={payload.get('image_url')}"
                    + (f" @ {pm['chassis_point']}" if pm else ""))

    async def _push_realtime(self, device_id, greenhouse_id, ts, data, robot_status, pm):
        try:
            redis = await get_redis()
            cache = CacheService(redis)
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