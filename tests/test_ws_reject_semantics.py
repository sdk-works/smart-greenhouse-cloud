"""验证 WS 拒绝语义：必须 accept 之后再用 4401/4403 关闭。

背景（2026-10-22 浏览器实测发现）：候选分支虽然把鉴权写进了 ws.py 端点，
但 app/api/__init__.py 仍挂着路由级 dependencies=[Depends(ws_current_user)]。
FastAPI 的依赖异常发生在**握手之前**，Starlette 直接断开，浏览器只看到 1006，
拿不到 4401/4403 —— 前端无法区分"未登录"和"连接坏了"，uvicorn 还会打
"ASGI callable returned without sending handshake"。

所以这里钉两件事：
  1. WS 路由**不能**有路由级依赖（结构约束，防止有人再加回来）；
  2. 端点自身对无效 token / 无公司账号，必须 accept→close(4401/4403)。
"""
import asyncio

import pytest

from app.api.ws import websocket_endpoint


class FakeWS:
    def __init__(self):
        self.accepted = False
        self.closed_code = None
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(text)

    async def close(self, code=1000):
        self.closed_code = code

    async def receive_text(self):
        await asyncio.sleep(3600)


class FakeUser:
    def __init__(self, company_id, username="u", is_active=True):
        self.company_id = company_id
        self.username = username
        self.is_active = is_active


def test_ws_route_has_no_router_level_dependency():
    from main import app

    routes = [r for r in app.routes if getattr(r, "path", "") == "/api/v1/ws/realtime"]
    assert routes, "找不到 /api/v1/ws/realtime 路由"
    deps = getattr(routes[0], "dependencies", [])
    assert deps == [], (
        "WS 路由不能再挂 dependencies：依赖报错发生在握手前，浏览器只会看到 1006；"
        "拒绝逻辑必须在端点内 accept 之后执行"
    )


def test_invalid_token_accepts_then_closes_4401(monkeypatch):
    import app.api.ws as wsmod

    async def _none(_token):
        return None

    monkeypatch.setattr(wsmod, "_resolve_user", _none)
    fake = FakeWS()
    asyncio.run(websocket_endpoint(fake, token="bogus"))
    assert fake.accepted is True, "必须先 accept，客户端才能读到关闭码"
    assert fake.closed_code == 4401


def test_missing_token_accepts_then_closes_4401(monkeypatch):
    import app.api.ws as wsmod

    async def _none(_token):
        return None

    monkeypatch.setattr(wsmod, "_resolve_user", _none)
    fake = FakeWS()
    asyncio.run(websocket_endpoint(fake, token=None))
    assert fake.accepted is True
    assert fake.closed_code == 4401


def test_user_without_company_closes_4403(monkeypatch):
    import app.api.ws as wsmod

    async def _user(_token):
        return FakeUser(company_id=None, username="no-company")

    monkeypatch.setattr(wsmod, "_resolve_user", _user)
    fake = FakeWS()
    asyncio.run(websocket_endpoint(fake, token="ok"))
    assert fake.accepted is True
    assert fake.closed_code == 4403
