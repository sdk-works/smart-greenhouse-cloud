#!/usr/bin/env python3
"""
seed_data.py - 种子数据脚本
修复 [#13]: DeviceType 枚举引用正确
修复 [#19]: DEVICES 的 greenhouse_id 在创建时动态绑定（非 None）
修复 [#20]: --reset 实际删除旧数据后重建
"""
import asyncio
import sys
import os
import random
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 加载 .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://greenhouse:SET_LOCALLY_DO_NOT_COMMIT@localhost:5432/greenhouse"
)

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select, text

from app.core.database import Base
from app.models.models import (
    Greenhouse, Device, Telemetry, RobotTask, Image,
    DeviceType, DeviceStatus, TaskStatus, TaskType
)


async def reset_data(db: AsyncSession):
    """清空所有业务数据（保留表结构）"""
    print("🗑️  清空旧数据...")
    # 按外键依赖顺序删除
    for table in ["images", "robot_tasks", "telemetry", "device_logs", "devices", "greenhouses"]:
        await db.execute(text(f"DELETE FROM {table}"))
    await db.commit()
    print("✅ 旧数据已清空")


async def seed():
    engine = create_async_engine(DATABASE_URL, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 建表
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        # 检查是否已有数据
        existing = (await db.execute(select(Greenhouse))).scalars().first()
        if existing and "--reset" not in sys.argv:
            print("⚠️  数据库已有数据，跳过（用 --reset 参数强制重建）")
            return
        if existing and "--reset" in sys.argv:
            await reset_data(db)

        print("🌱 开始创建种子数据...")

        # ── 创建大棚 ──────────────────────────────────
        gh_configs = [
            {
                "name": "1号大棚·番茄区", "location": "东区A-1",
                "area": 2000.0, "crop_type": "樱桃番茄",
                "description": "主要种植区，配备全套自动化设备",
                "map_data": {
                    "width": 100, "height": 60,
                    "waypoints": [
                        {"id": i, "x": x, "y": y}
                        for i, (x, y) in enumerate([
                            (10,10),(30,10),(50,10),(70,10),(90,10),
                            (90,30),(70,30),(50,30),(30,30),(10,30),
                            (10,50),(30,50),(50,50),(70,50),(90,50),
                        ])
                    ]
                }
            },
            {
                "name": "2号大棚·黄瓜区", "location": "东区A-2",
                "area": 1800.0, "crop_type": "水果黄瓜",
                "description": "试验种植区",
                "map_data": {"width": 100, "height": 60}
            },
            {
                "name": "3号大棚·草莓区", "location": "西区B-1",
                "area": 2500.0, "crop_type": "红颜草莓",
                "description": "高价值作物区，精细化管理",
                "map_data": {"width": 100, "height": 60}
            },
        ]
        greenhouses = []
        for cfg in gh_configs:
            gh = Greenhouse(**cfg)
            db.add(gh)
            greenhouses.append(gh)
        await db.flush()
        print(f"  ✅ 大棚: {len(greenhouses)} 个")

        # ── 创建设备（greenhouse_id 动态绑定）────────
        device_configs = [
            # 1号大棚
            {"device_id": "robot-001",      "name": "1号巡检机器人",  "device_type": DeviceType.ROBOT,       "gh_idx": 0, "status": DeviceStatus.ONLINE},
            {"device_id": "sensor-001",     "name": "环境传感器A1",   "device_type": DeviceType.SENSOR,      "gh_idx": 0, "status": DeviceStatus.ONLINE},
            {"device_id": "sensor-002",     "name": "环境传感器A2",   "device_type": DeviceType.SENSOR,      "gh_idx": 0, "status": DeviceStatus.ONLINE},
            {"device_id": "irrigation-001", "name": "灌溉控制器1",   "device_type": DeviceType.IRRIGATION,  "gh_idx": 0, "status": DeviceStatus.ONLINE},
            {"device_id": "fan-001",        "name": "通风风机1",      "device_type": DeviceType.FAN,         "gh_idx": 0, "status": DeviceStatus.OFFLINE},
            {"device_id": "light-001",      "name": "补光灯组1",      "device_type": DeviceType.GROW_LIGHT,  "gh_idx": 0, "status": DeviceStatus.ONLINE},
            # 2号大棚
            {"device_id": "robot-002",      "name": "2号巡检机器人",  "device_type": DeviceType.ROBOT,       "gh_idx": 1, "status": DeviceStatus.ONLINE},
            {"device_id": "sensor-003",     "name": "环境传感器B1",   "device_type": DeviceType.SENSOR,      "gh_idx": 1, "status": DeviceStatus.ONLINE},
            {"device_id": "curtain-001",    "name": "卷帘机1",        "device_type": DeviceType.CURTAIN,     "gh_idx": 1, "status": DeviceStatus.ONLINE},
            # 3号大棚
            {"device_id": "sensor-004",     "name": "环境传感器C1",   "device_type": DeviceType.SENSOR,      "gh_idx": 2, "status": DeviceStatus.OFFLINE},
            {"device_id": "fertilizer-001", "name": "施肥机1",        "device_type": DeviceType.FERTILIZER,  "gh_idx": 2, "status": DeviceStatus.ONLINE},
        ]
        all_devices = []
        for cfg in device_configs:
            gh_idx = cfg.pop("gh_idx")
            device = Device(
                **cfg,
                firmware_version="v2.1.0",
                greenhouse_id=greenhouses[gh_idx].id,
                last_seen=datetime.now(timezone.utc) - timedelta(minutes=random.randint(1, 30))
            )
            db.add(device)
            all_devices.append((device, gh_idx))
        await db.flush()
        print(f"  ✅ 设备: {len(all_devices)} 个")

        # ── 历史遥测数据（过去24小时，每5分钟一条）──
        tele_count = 0
        sensors = [(d, gi) for d, gi in all_devices
                   if d.device_type in (DeviceType.SENSOR, DeviceType.ROBOT)]

        for device, gh_idx in sensors:
            base_temp = [24.5, 22.0, 20.5][gh_idx]
            base_hum  = [72.0, 68.0, 75.0][gh_idx]
            for i in range(288):  # 24h × 12条/h
                ts   = datetime.now(timezone.utc) - timedelta(minutes=5 * (287 - i))
                hour = ts.hour
                temp_offset = 4 * (1 - abs(hour - 14) / 14.0)
                light = max(0.0, 15000 * (1 - abs(hour - 12) / 12.0)) if 6 <= hour <= 20 else 0.0
                is_robot = device.device_type == DeviceType.ROBOT

                t = Telemetry(
                    device_id       = device.id,
                    greenhouse_id   = device.greenhouse_id,  # 正确绑定
                    timestamp       = ts,
                    data_type       = "robot_telemetry" if is_robot else "sensor",
                    temperature     = round(base_temp + temp_offset + random.gauss(0, 0.5), 2),
                    humidity        = round(base_hum + random.gauss(0, 2), 2),
                    soil_moisture   = round(45 + random.gauss(0, 5), 2),
                    light_intensity = round(light + random.gauss(0, 300), 1),
                    co2_level       = round(420 + random.gauss(0, 30), 1),
                    ph_value        = round(6.5 + random.gauss(0, 0.2), 2),
                    battery_level   = round(max(5, 85 - i * 0.05 + random.gauss(0, 1)), 1) if is_robot else None,
                    position_x      = round(random.uniform(10, 90), 2) if is_robot else None,
                    position_y      = round(random.uniform(10, 50), 2) if is_robot else None,
                    robot_status    = {"battery": 85, "speed": 0.5, "status": "patrolling"} if is_robot else None,
                    raw_data        = {"device_id": device.device_id, "timestamp": ts.isoformat()},
                )
                db.add(t)
                tele_count += 1

        await db.flush()
        print(f"  ✅ 遥测数据: {tele_count} 条（过去24小时）")

        # ── 任务 ──────────────────────────────────────
        robots = [d for d, _ in all_devices if d.device_type == DeviceType.ROBOT]
        task_configs = [
            {"task_name": "1号棚早间巡检",    "task_type": TaskType.INSPECTION,    "status": TaskStatus.COMPLETED, "progress": 100, "gh": greenhouses[0], "dev": robots[0]},
            {"task_name": "1号棚下午定期巡检", "task_type": TaskType.INSPECTION,    "status": TaskStatus.RUNNING,   "progress": 45,  "gh": greenhouses[0], "dev": robots[0]},
            {"task_name": "2号棚病虫害检测",   "task_type": TaskType.PEST_DETECTION,"status": TaskStatus.PENDING,   "progress": 0,   "gh": greenhouses[1], "dev": robots[1]},
            {"task_name": "1号棚自动灌溉",     "task_type": TaskType.WATERING,      "status": TaskStatus.COMPLETED, "progress": 100, "gh": greenhouses[0], "dev": None},
        ]
        for cfg in task_configs:
            gh = cfg.pop("gh"); dev = cfg.pop("dev")
            task = RobotTask(
                **cfg,
                greenhouse_id = gh.id,
                device_id     = dev.id if dev else None,
                priority      = random.randint(4, 8),
                waypoints     = [{"x": 10, "y": 10}, {"x": 30, "y": 10}, {"x": 50, "y": 10}],
                actions       = [{"type": "capture_image"}, {"type": "measure_env"}],
                started_at    = datetime.now(timezone.utc) - timedelta(hours=2),
                completed_at  = datetime.now(timezone.utc) - timedelta(hours=1) if cfg["status"] == TaskStatus.COMPLETED else None,
            )
            db.add(task)

        await db.commit()
        print(f"  ✅ 任务: {len(task_configs)} 个")

    print("\n🎉 种子数据创建完成！访问平台即可看到演示数据")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
