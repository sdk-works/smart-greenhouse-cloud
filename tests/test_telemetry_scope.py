"""telemetry 查询范围修复的回归测试（2026-10-29，release 自包含版）。

钉住三件事：
  1. /latest/{device_id} 必须登录（原来完全没有鉴权）；
  2. 业务用户查他司设备 → 404；平台运营放行；
  3. list?device_id=不存在 → 空列表，不得退化为全量查询串出他司数据。
"""
import secrets
import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.redis import get_redis
from app.models.models import (
    Company, Device, DeviceStatus, DeviceType, Greenhouse, Telemetry, User, UserRole,
)
from tests.conftest import auth

API = settings.API_PREFIX


async def _scene(name, with_telemetry=True):
    """自包含建数据：公司+棚+设备+（可选）遥测+业务用户；返回 (dev, token, user_id)。"""
    async with AsyncSessionLocal() as db:
        c = Company(id=str(uuid.uuid4()), name=f"{name}-C")
        db.add(c); await db.flush()
        gh = Greenhouse(id=str(uuid.uuid4()), name=f"{name}-gh", company_id=c.id, is_active=True)
        db.add(gh); await db.flush()
        dev = Device(id=str(uuid.uuid4()), device_id=f"{name}-{uuid.uuid4().hex[:10]}",
                     name=f"{name}-dev", device_type=DeviceType.SENSOR,
                     greenhouse_id=gh.id, status=DeviceStatus.ONLINE)
        db.add(dev); await db.flush()
        if with_telemetry:
            db.add(Telemetry(id=str(uuid.uuid4()), device_id=dev.id, greenhouse_id=gh.id,
                             timestamp=datetime.now(timezone.utc), data_type="telemetry",
                             temperature=25.0, company_id=c.id))
        u = User(id=str(uuid.uuid4()), username=f"{name}-{uuid.uuid4().hex[:10]}",
                 password_hash="x", role=UserRole.ADMIN, is_active=True, company_id=c.id)
        db.add(u); await db.commit()
        uid = u.id
    token = secrets.token_urlsafe(24)
    redis = await get_redis()
    await redis.set(f"auth:token:{token}", uid, ex=3600)
    return dev, token


async def test_latest_requires_auth(client, make_user):
    """未登录 → 401（修复前此接口完全裸奔）。"""
    dev, token = await _scene("tsA")
    r = await client.get(f"{API}/telemetry/latest/{dev.device_id}")
    assert r.status_code == 401, r.text


async def test_latest_cross_company_404(client, make_user):
    """他司业务用户 → 404；平台运营 → 200。"""
    dev, token = await _scene("tsB")
    userB, tokenB = await make_user()   # make_user 默认无公司 → 业务用户视角
    r = await client.get(f"{API}/telemetry/latest/{dev.device_id}", headers=auth(tokenB))
    assert r.status_code == 404, r.text


async def test_latest_own_company_200(client, make_user):
    """本公司用户 → 200（有数据返回数据）。"""
    dev, token = await _scene("tsC")
    r = await client.get(f"{API}/telemetry/latest/{dev.device_id}", headers=auth(token))
    assert r.status_code == 200, r.text


async def test_latest_no_data_returns_empty_obj(client, make_user):
    """本公司设备但从未上报 → 200 + {}。"""
    dev, token = await _scene("tsD", with_telemetry=False)
    r = await client.get(f"{API}/telemetry/latest/{dev.device_id}", headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.json() == {}


async def test_list_unknown_device_returns_empty(client, make_user):
    """list 指定不存在的设备 → []，不串出其他设备数据。"""
    dev, token = await _scene("tsE")
    r = await client.get(f"{API}/telemetry/?device_id=no-such-device", headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.json() == []
    r2 = await client.get(f"{API}/telemetry/", headers=auth(token))
    assert r2.status_code == 200
    assert len(r2.json()) >= 1
