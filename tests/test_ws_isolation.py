"""WS 实时推送的公司隔离（M6a）

背景：2026-10-22 之前
  * /api/v1/ws/realtime 完全不校验身份 —— 谁都能连，连上就收到全平台遥测；
  * Redis 频道是裸名 `telemetry_updates` —— 所有公司共用一个频道，
    ConnectionManager 广播给**所有**连接。
  M1 只给缓存键加了公司前缀，消息通道没加，所以跨公司推送一直存在。

这批测试钉住三件事：
  1. 频道名必须带公司后缀，未归属必须能被识别成"不推"；
  2. ConnectionManager 只投给同一公司的连接；
  3. 发布侧（CacheService.publish）实际写的是带公司后缀的频道。
"""
import asyncio

import pytest

from app.core.redis import CacheService, UNKNOWN_COMPANY, realtime_channel
from app.api.ws import ConnectionManager, channel_company


class FakeWS:
    """够用的 WebSocket 替身：只记录收到的文本与关闭码。"""

    def __init__(self, name="ws"):
        self.name = name
        self.sent = []
        self.accepted = False
        self.closed_code = None

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self, code=1000):
        self.closed_code = code


class FakeRedis:
    """只实现 publish：记录 (channel, payload)。"""

    def __init__(self):
        self.published = []

    async def publish(self, channel, payload):
        self.published.append((channel, payload))


# ---------- 1. 频道命名 ----------

def test_channel_has_company_suffix():
    assert realtime_channel("telemetry_updates", "c-1") == "telemetry_updates:c-1"
    assert realtime_channel("config_ack_updates", "c-2") == "config_ack_updates:c-2"


def test_channel_falls_back_to_unknown():
    assert realtime_channel("telemetry_updates", None) == \
        f"telemetry_updates:{UNKNOWN_COMPANY}"


def test_channel_company_roundtrip():
    assert channel_company(realtime_channel("telemetry_updates", "acme")) == "acme"


def test_unknown_channel_maps_to_empty():
    assert channel_company(realtime_channel("telemetry_updates", None)) == ""
    assert channel_company("telemetry_updates") == ""      # 裸频道：老版本留下的
    assert channel_company("") == ""


# ---------- 2. 只投给同公司 ----------

async def test_broadcast_only_same_company():
    m = ConnectionManager()
    a1, a2, b1 = FakeWS("a1"), FakeWS("a2"), FakeWS("b1")
    await m.connect(a1, "company-a")
    await m.connect(a2, "company-a")
    await m.connect(b1, "company-b")

    await m.broadcast("company-a", '{"x":1}')

    assert a1.sent == ['{"x":1}']
    assert a2.sent == ['{"x":1}']
    assert b1.sent == []            # 关键：B 公司一条都不能收到
    assert m.count() == 3 and m.count("company-a") == 2


async def test_broadcast_to_unknown_company_hits_nobody():
    m = ConnectionManager()
    ws = FakeWS()
    await m.connect(ws, UNKNOWN_COMPANY)
    await m.broadcast("company-a", "payload")
    assert ws.sent == []


async def test_disconnect_stops_delivery():
    m = ConnectionManager()
    ws = FakeWS()
    await m.connect(ws, "company-a")
    await m.disconnect(ws)
    await m.broadcast("company-a", "later")
    assert ws.sent == []


async def test_broadcast_cleans_dead_connections():
    class BoomWS(FakeWS):
        async def send_text(self, text):
            raise RuntimeError("connection closed")

    m = ConnectionManager()
    bad, good = BoomWS("bad"), FakeWS("good")
    await m.connect(bad, "company-a")
    await m.connect(good, "company-a")

    await m.broadcast("company-a", "m")
    # 抛异常的连接被摘掉，正常连接照常收到
    assert good.sent == ["m"]
    assert m.count("company-a") == 1


async def test_concurrent_company_isolation():
    """两个公司并发广播，互不串台。"""
    m = ConnectionManager()
    clients = {"a": FakeWS("a"), "b": FakeWS("b")}
    for cid, ws in clients.items():
        await m.connect(ws, "company-" + cid)

    await asyncio.gather(
        m.broadcast("company-a", "A"),
        m.broadcast("company-b", "B"),
    )
    assert clients["a"].sent == ["A"]
    assert clients["b"].sent == ["B"]


# ---------- 3. 发布侧写的就是带后缀的频道 ----------

async def test_cache_publish_uses_company_channel():
    r = FakeRedis()
    await CacheService(r, company_id="company-a").publish("telemetry_updates", {"k": 1})
    assert r.published[0][0] == "telemetry_updates:company-a"
    assert '"k"' in r.published[0][1]


async def test_cache_publish_without_company_is_identifiable():
    """没有公司时必须落 unknown 后缀 —— 监听侧据此丢弃，不能落成裸频道。"""
    r = FakeRedis()
    await CacheService(r).publish("telemetry_updates", {"k": 1})
    assert r.published[0][0] == f"telemetry_updates:{UNKNOWN_COMPANY}"
    assert channel_company(r.published[0][0]) == ""


async def test_two_companies_do_not_share_channel():
    r = FakeRedis()
    await CacheService(r, company_id="a").publish("telemetry_updates", {})
    await CacheService(r, company_id="b").publish("telemetry_updates", {})
    assert r.published[0][0] != r.published[1][0]
