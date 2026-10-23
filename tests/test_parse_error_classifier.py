"""T5：上行报文解析错误分类（四类 + 正常）。

只测分类模块本身与聚合落库；**不改** `app/mqtt/mqtt_service.py` 的现有解析行为
（接线是独立的、需要人批准的 3 行 diff，见 docs/parse-error-classification-20261023.md）。
"""
import os

import psycopg2
import pytest
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.parse_error_classifier import (
    DEVICE_MISMATCH, DUPLICATE_REGISTRATION, MISSING_FIELD, NON_JSON, OK,
    build_rejected_raw_message, classify, record_parse_error, signature,
)

TOPIC = "agri/device/JY001/up"

MIGRATION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "migrations", "007_parse_error_kind_up.sql")


def sync_dsn() -> str:
    from app.core.config import settings
    return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="session", autouse=True)
def _ensure_007():
    """parse_error_stats 是迁移建的表（模型里没有），测试前幂等补一次。"""
    conn = psycopg2.connect(sync_dsn())
    try:
        with conn.cursor() as cur:
            with open(MIGRATION, encoding="utf-8") as f:
                cur.execute(f.read())
        conn.commit()
    finally:
        conn.close()


def test_non_json():
    kind, detail, parsed = classify(TOPIC, b'{"type": "telemetry", ')
    assert kind == NON_JSON and parsed is None
    assert "JSON 解析失败" in detail


def test_non_json_when_top_level_is_not_object():
    """能解析但不是对象（如裸字符串/数字），同样算 non_json，不算字段缺失。"""
    assert classify(TOPIC, b'"just-a-string"')[0] == NON_JSON
    assert classify(TOPIC, b'123')[0] == NON_JSON


def test_duplicate_registration_packet():
    """生产那条 www.usr.cn：命中厂家注册包签名，且与"乱码"区分开。"""
    kind, detail, parsed = classify(TOPIC, b"www.usr.cn")
    assert kind == DUPLICATE_REGISTRATION and parsed is None
    assert "www.usr.cn" in detail
    assert classify(TOPIC, b"www.usr.cn\r\n")[0] == DUPLICATE_REGISTRATION
    # 首次 vs 重复只在 detail 里体现，分类码不变
    assert "首次出现" in classify(TOPIC, b"www.usr.cn")[1]
    assert "重复出现" in classify(TOPIC, b"www.usr.cn", seen_before=True)[1]
    # 同样是乱码但不在签名表里 -> 归 non_json，不冒充注册包
    assert classify(TOPIC, b"\x00\x01\x02garbage")[0] == NON_JSON


def test_device_mismatch():
    payload = b'{"type":"telemetry","msg_id":"X/1/1","device_id":"JY002"}'
    kind, detail, parsed = classify(TOPIC, payload)
    assert kind == DEVICE_MISMATCH and parsed is not None
    assert "JY001" in detail and "JY002" in detail


def test_missing_field():
    payload = b'{"type":"telemetry"}'
    kind, detail, parsed = classify(TOPIC, payload)
    assert kind == MISSING_FIELD and parsed is not None
    assert "msg_id" in detail


def test_ok():
    payload = b'{"type":"telemetry","msg_id":"JY001/31/1","device_id":"JY001"}'
    kind, detail, parsed = classify(TOPIC, payload)
    assert kind == OK and parsed["msg_id"] == "JY001/31/1"
    # topic 取不到设备时不做 device_mismatch 判定
    assert classify("short/topic", payload)[0] == OK


def test_signature_dedups_identical_garbage():
    assert signature(b"www.usr.cn") == signature(b"www.usr.cn")
    assert signature(b"www.usr.cn") != signature(b"www.usr.cn-X")
    # 不可打印字节 -> 走 sha1，且稳定
    s = signature(b"\x00\x01\x02")
    assert s.startswith("sha1:") and s == signature(b"\x00\x01\x02")


@pytest.mark.asyncio
async def test_rejected_raw_message_shape():
    m = build_rejected_raw_message(TOPIC, b"www.usr.cn", DUPLICATE_REGISTRATION, "测试")
    assert m.parse_status == "rejected"
    assert m.device_id == "JY001"
    assert m.payload is None                 # 非 JSON 存不进 json/jsonb 列
    assert m.payload_bytes == 10  # "www.usr.cn" 是 10 字节
    assert m.error.startswith("[duplicate_registration] ")
    assert "www.usr.cn" in m.error


@pytest.mark.asyncio
async def test_record_parse_error_aggregates_and_persists():
    """同一条垃圾连发 3 次：聚合表只占一行且 cnt=3，raw_messages 有 3 条被拒记录。"""
    async with AsyncSessionLocal() as db:
        try:
            for i in range(3):
                cnt = await record_parse_error(
                    AsyncSessionLocal, TOPIC, b"www.usr.cn",
                    DUPLICATE_REGISTRATION, "厂家注册包")
                assert cnt == i + 1, f"第 {i+1} 次应累计到 {i+1}，实际 {cnt}"

            rows = (await db.execute(text(
                "SELECT device_id, kind, signature, cnt FROM parse_error_stats"))).all()
            assert len(rows) == 1, "同一条垃圾必须聚成一行"
            assert rows[0] == ("JY001", DUPLICATE_REGISTRATION, "www.usr.cn", 3)

            n = (await db.execute(text(
                "SELECT count(*) FROM raw_messages WHERE parse_status='rejected'"))).scalar()
            assert n == 3
        finally:
            await db.execute(text("DELETE FROM raw_messages WHERE parse_status='rejected'"))
            await db.execute(text("DELETE FROM parse_error_stats"))
            await db.commit()


@pytest.mark.asyncio
async def test_record_parse_error_never_breaks_pipeline():
    """观测链路不能把主链路搞挂：表不存在也必须吞掉异常并返回 None。"""
    from app.core import parse_error_classifier as mod

    def broken_factory():
        raise RuntimeError("db down")

    assert await mod.record_parse_error(broken_factory, TOPIC, b"x", NON_JSON, "x") is None
