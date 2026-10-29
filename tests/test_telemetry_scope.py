"""telemetry 查询范围修复的回归测试（2026-10-29）。

钉住三件事：
  1. /latest/{device_id} 必须登录（原来完全没有鉴权）；
  2. 业务用户查他司/未挂棚室设备 → 404；平台运营放行；
  3. list?device_id=不存在 → 空列表，不得退化为全量查询串出他司数据。
"""
import uuid
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.models.models import Telemetry, UserRole
from tests.conftest import auth

API = settings.API_PREFIX


async def _scene(iso, db, name, with_telemetry=True):
    """公司C + 棚 + 设备 + （可选）一条遥测 + 该公司业务用户。"""
    c = await iso.company(db, f"{name}-C")
    gh = await iso.greenhouse(db, c, f"{name}-gh")
    dev = await iso.device(db, gh, name=f"{name}-dev")
    u, token = await iso.user(db, UserRole.ADMIN, c)
    if with_telemetry:
        db.add(Telemetry(id=str(uuid.uuid4()), device_id=dev.id, greenhouse_id=gh.id,
                         timestamp=datetime.now(timezone.utc), data_type="telemetry",
                         temperature=25.0, company_id=c))
    await db.commit()
    return c, gh, dev, token


async def test_latest_requires_auth(client, iso):
    """未登录 → 401（修复前此接口完全裸奔）。"""
    fx, db = iso
    c, gh, dev, token = await _scene(fx, db, "tsA")
    r = await client.get(f"{API}/telemetry/latest/{dev.device_id}")
    assert r.status_code == 401, r.text


async def test_latest_cross_company_404(client, iso):
    """他司业务用户 → 404；平台运营 → 200。"""
    fx, db = iso
    c1, gh1, dev1, token1 = await _scene(fx, db, "tsB")
    c2 = await fx.company(db, "tsB2-C")
    adminB, tokenB = await fx.user(db, UserRole.ADMIN, c2)
    platform, ptoken = await fx.user(db, UserRole.ADMIN, c2, platform=True)
    await db.commit()

    r = await client.get(f"{API}/telemetry/latest/{dev1.device_id}", headers=auth(tokenB))
    assert r.status_code == 404, r.text

    r = await client.get(f"{API}/telemetry/latest/{dev1.device_id}", headers=auth(ptoken))
    assert r.status_code == 200, r.text


async def test_latest_no_data_returns_empty_obj(client, iso):
    """本公司设备但从未上报 → 200 + {}（设备存在与有数据分开表达）。"""
    fx, db = iso
    c, gh, dev, token = await _scene(fx, db, "tsC", with_telemetry=False)
    r = await client.get(f"{API}/telemetry/latest/{dev.device_id}", headers=auth(token))
    assert r.status_code == 200, r.text
    assert r.json() == {}


async def test_list_unknown_device_returns_empty(client, iso):
    """list 指定不存在的设备 → []，不串出其他设备数据。"""
    fx, db = iso
    c1, gh1, dev1, token1 = await _scene(fx, db, "tsD")
    r = await client.get(f"{API}/telemetry/?device_id=no-such-device",
                         headers=auth(token1))
    assert r.status_code == 200, r.text
    assert r.json() == []

    # 对照：不指定设备时本公司能看到自己的数据
    r2 = await client.get(f"{API}/telemetry/", headers=auth(token1))
    assert r2.status_code == 200
    assert len(r2.json()) >= 1
