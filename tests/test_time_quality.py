"""
未校时（time_quality）数据验证 —— 只验证到 API 层，不含前端渲染。

场景：现场设备时钟未校时，telemetry.time_quality != 0 且时间戳明显偏离真实时间。
验证点：
  1. GET /api/v1/telemetry/? 能返回这种记录、不报错；
  2. 时间字段（timestamp）未被静默改写；
  3. 按时间范围查询时，这种记录落在哪一侧。

结论（由本文件断言固化）：
  list_telemetry 的窗口条件是 Telemetry.created_at >= now - hours，**不是** timestamp。
  - 刚入库的未校时记录（timestamp 2 小时前 / 3 天后）都在窗口「内」，会被返回；
  - 只有 created_at（入库时间）超出窗口的记录才被排除；
  - 排序按 timestamp DESC，未来时间戳的记录排在最前面。

已知差距（本文件不修复，仅记录）：
  TelemetryResponse 未导出 msg_id / sampled_at / time_quality，
  因此前端无法从 /api/v1/telemetry/? 判断某条记录时间是否可信。
  本测试改用 temperature 作为区分标记，并在 DB 侧断言 time_quality。
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import Device, DeviceType, Greenhouse, Telemetry, UserRole
from tests.conftest import auth

API = settings.API_PREFIX

T_PAST, T_FUTURE, T_OLD = 11.0, 22.0, 33.0


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


async def test_time_quality_records_returned_and_not_rewritten(client, make_user,
                                                              schema_ready):
    _, token = await make_user(UserRole.VIEWER)

    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=2)      # 设备时钟慢 2 小时
    future = now + timedelta(days=3)     # 设备时钟快 3 天

    async with AsyncSessionLocal() as db:
        gh = Greenhouse(name=f"tq-{uuid.uuid4().hex[:8]}")
        db.add(gh)
        await db.flush()
        dev = Device(device_id=f"TQ-{uuid.uuid4().hex[:8]}", name="tq-dev",
                     device_type=DeviceType.SENSOR, greenhouse_id=gh.id)
        db.add(dev)
        await db.flush()

        rows = [
            # A: 未校时（慢 2h），刚入库
            Telemetry(device_id=dev.id, greenhouse_id=gh.id, timestamp=past,
                      sampled_at=past, time_quality=1, msg_id="tq-past-1",
                      data_type="telemetry", temperature=T_PAST),
            # B: 未校时（快 3 天），刚入库
            Telemetry(device_id=dev.id, greenhouse_id=gh.id, timestamp=future,
                      sampled_at=future, time_quality=1, msg_id="tq-future-1",
                      data_type="telemetry", temperature=T_FUTURE),
            # C: 正常时间，但 created_at 被显式设成 10 天前（模拟“早就入库”）
            Telemetry(device_id=dev.id, greenhouse_id=gh.id, timestamp=now,
                      sampled_at=now, time_quality=0, msg_id="tq-old-created-1",
                      data_type="telemetry", temperature=T_OLD),
        ]
        db.add_all(rows)
        await db.flush()
        rows[2].created_at = now - timedelta(days=10)
        gh_id, dev_id = gh.id, dev.id
        await db.commit()

    try:
        r = await client.get(f"{API}/telemetry/?greenhouse_id={gh_id}&hours=24",
                             headers=auth(token))
        assert r.status_code == 200, r.text
        items = r.json()
        by_temp = {i["temperature"]: i for i in items}

        # 1) 未校时记录能被返回、不报错
        assert T_PAST in by_temp, "2 小时前的未校时记录应被返回"
        assert T_FUTURE in by_temp, "3 天后的未校时记录应被返回"

        # 2) 时间字段未被静默改写
        assert abs(_parse(by_temp[T_PAST]["timestamp"]) - past) < timedelta(seconds=1)
        assert abs(_parse(by_temp[T_FUTURE]["timestamp"]) - future) < timedelta(seconds=1)

        # 3) 窗口判定依据是 created_at，不是 timestamp
        assert T_OLD not in by_temp, \
            "created_at 在窗口之外的记录应被排除（证明窗口按入库时间过滤）"

        # 4) 排序按 timestamp DESC → 未来时间戳排最前
        assert items[0]["temperature"] == T_FUTURE, \
            f"未来时间戳应排在最前，实际首条温度={items[0].get('temperature')}"

        # 窄窗口（hours=1）结论不变：只要 created_at 是现在，未校时记录仍返回
        r2 = await client.get(f"{API}/telemetry/?greenhouse_id={gh_id}&hours=1",
                              headers=auth(token))
        assert r2.status_code == 200, r2.text
        temps2 = {i["temperature"] for i in r2.json()}
        assert {T_PAST, T_FUTURE} <= temps2, "窄窗口下未校时记录仍应返回"

        # 数据库侧再确认一次：值就是写入的值，且 time_quality 保持 1
        async with AsyncSessionLocal() as db:
            for msg, expected_ts, expected_tq in (
                ("tq-past-1", past, 1),
                ("tq-future-1", future, 1),
                ("tq-old-created-1", now, 0),
            ):
                row = (await db.execute(select(Telemetry).where(
                    Telemetry.msg_id == msg))).scalar_one()
                assert abs((row.timestamp - expected_ts).total_seconds()) < 1, msg
                assert row.time_quality == expected_tq, msg
    finally:
        async with AsyncSessionLocal() as db:
            for m in ("tq-past-1", "tq-future-1", "tq-old-created-1"):
                row = (await db.execute(select(Telemetry).where(
                    Telemetry.msg_id == m))).scalar_one_or_none()
                if row:
                    await db.delete(row)
            dev = await db.get(Device, dev_id)
            if dev:
                await db.delete(dev)
            gh = await db.get(Greenhouse, gh_id)
            if gh:
                await db.delete(gh)
            await db.commit()
