"""WS 专项发布补充测试（A 方案）：
  - 30s 权限复查独立运行（空闲连接也断开），查询失败 fail-closed；
  - token 吊销 / 用户停用 / 公司变更 → 失效；
  - config_ack 频道同样按公司隔离；
  - 缓存键行为不因 company_id 改变。
"""
import asyncio

import pytest
from fastapi import WebSocketDisconnect

import app.api.ws as wsmod
from app.api.ws import ConnectionManager, channel_company
from app.core.redis import CacheService, UNKNOWN_COMPANY, realtime_channel


class FakeUser:
    def __init__(self, company_id, username="u", is_active=True, uid="u-1"):
        self.id = uid
        self.company_id = company_id
        self.username = username
        self.is_active = is_active


class BlockingWS:
    """receive_text 一直阻塞，直到 close() 被调用；用于证明复查不依赖客户端消息。"""

    def __init__(self):
        self.accepted = False
        self.closed_code = None
        self.sent = []
        self._closed = asyncio.Event()

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self, code=1000):
        self.closed_code = code
        self._closed.set()

    async def receive_text(self):
        await self._closed.wait()
        raise WebSocketDisconnect()


class FakeRedis:
    def __init__(self, mapping=None):
        self.m = mapping or {}
        self.expires = []

    async def get(self, k):
        return self.m.get(k)

    async def expire(self, k, ttl):
        self.expires.append((k, ttl))
        return True


class FakeAsyncSession:
    def __init__(self, user):
        self._user = user

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, model, uid):
        return self._user


def _patch_store(monkeypatch, token_map, user):
    r = FakeRedis(token_map)

    async def _gr():
        return r

    monkeypatch.setattr(wsmod, "get_redis", _gr)
    monkeypatch.setattr(wsmod, "AsyncSessionLocal", lambda: FakeAsyncSession(user))
    return r


# ---------- 1. 独立定时复查 ----------

async def test_recheck_disconnects_idle_client(monkeypatch):
    """不带任何客户端消息，复查到失效也要在限定时间内断开。"""
    async def _user(_t):
        return FakeUser("company-a")

    async def _invalid(*a, **k):
        return False

    monkeypatch.setattr(wsmod, "_resolve_user", _user)
    monkeypatch.setattr(wsmod, "_session_still_valid", _invalid)
    monkeypatch.setattr(wsmod, "WS_AUTH_RECHECK_SECONDS", 0.01)

    fake = BlockingWS()
    await asyncio.wait_for(wsmod.websocket_endpoint(fake, token="ok"), timeout=1.0)
    assert fake.accepted is True
    assert fake.closed_code == 4401


async def test_recheck_fail_closed_on_query_error(monkeypatch):
    """复查查询抛异常时也必须断开，不能沿用旧权限。"""
    async def _user(_t):
        return FakeUser("company-a")

    async def _boom(*a, **k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(wsmod, "_resolve_user", _user)
    monkeypatch.setattr(wsmod, "_session_still_valid", _boom)
    monkeypatch.setattr(wsmod, "WS_AUTH_RECHECK_SECONDS", 0.01)

    fake = BlockingWS()
    await asyncio.wait_for(wsmod.websocket_endpoint(fake, token="ok"), timeout=1.0)
    assert fake.closed_code == 4401


# ---------- 2. 复查判定 ----------

async def test_session_ok(monkeypatch):
    _patch_store(monkeypatch, {"auth:token:t": "u-1"}, FakeUser("company-a"))
    assert await wsmod._session_still_valid("t", "u-1", "company-a") is True


async def test_session_invalid_when_token_revoked(monkeypatch):
    _patch_store(monkeypatch, {}, FakeUser("company-a"))
    assert await wsmod._session_still_valid("t", "u-1", "company-a") is False


async def test_session_invalid_when_token_points_to_other_user(monkeypatch):
    _patch_store(monkeypatch, {"auth:token:t": "u-2"}, FakeUser("company-a"))
    assert await wsmod._session_still_valid("t", "u-1", "company-a") is False


async def test_session_invalid_when_user_disabled(monkeypatch):
    _patch_store(monkeypatch, {"auth:token:t": "u-1"},
                 FakeUser("company-a", is_active=False))
    assert await wsmod._session_still_valid("t", "u-1", "company-a") is False


async def test_session_invalid_when_company_changed(monkeypatch):
    _patch_store(monkeypatch, {"auth:token:t": "u-1"}, FakeUser("company-b"))
    assert await wsmod._session_still_valid("t", "u-1", "company-a") is False


# ---------- 3. 配置 ACK 与遥测都按公司隔离 ----------

def test_config_ack_channel_isolation():
    assert realtime_channel("config_ack_updates", "a") == "config_ack_updates:a"
    assert channel_company(realtime_channel("config_ack_updates", "a")) == "a"
    assert channel_company(realtime_channel("config_ack_updates", None)) == ""


def test_telemetry_channel_isolation():
    assert realtime_channel("telemetry_updates", "a") == "telemetry_updates:a"
    assert channel_company(realtime_channel("telemetry_updates", "b")) == "b"


def test_companyless_publish_is_not_broadcastable():
    ch = realtime_channel("telemetry_updates", None)
    assert ch.endswith(UNKNOWN_COMPANY)
    assert channel_company(ch) == ""      # 监听侧据此丢弃，不会全局广播


async def test_broadcast_does_not_reach_unknown():
    m = ConnectionManager()
    unknown_ws = BlockingWS()
    await m.connect(unknown_ws, UNKNOWN_COMPANY)
    await m.broadcast("company-a", "x")
    assert unknown_ws.sent == []


# ---------- 4. 缓存键行为不变 ----------

async def test_cache_keys_unchanged_by_company():
    class R:
        def __init__(self):
            self.store = {}

        async def set(self, k, v, ex=None):
            self.store[k] = v

        async def get(self, k):
            return self.store.get(k)

    r = R()
    cs = CacheService(r, company_id="company-a")
    await cs.set("device:status:JY001", {"ok": True})
    assert "device:status:JY001" in r.store
    assert not any("company-a" in k for k in r.store)
