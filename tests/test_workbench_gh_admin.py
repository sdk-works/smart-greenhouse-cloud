"""大棚工作台管理增强回归测试（2026-10-29）。

钉住四件事：
  1. 工作台列表按公司隔离，并带 设备摘要/点位数；
  2. 档案 PUT 支持局部 layout（长宽/已核实），其余布局键不被破坏；
  3. 工作台 DELETE 为软删：is_active=False，棚不消失；
  4. 单棚设备摘要 total/online 正确。
"""
from sqlalchemy import select

from app.core.config import settings
import uuid

from app.models.models import Device, DeviceStatus, DeviceType, Greenhouse, UserRole
from tests.conftest import auth

API = settings.API_PREFIX


async def _mk(fx, db, name, n_devices=0, n_offline=0):
    c = await fx.company(db, f"{name}-C")
    gh = await fx.greenhouse(db, c, f"{name}-gh")
    for i in range(n_devices):
        await fx.device(db, gh, name=f"{name}-dev{i}")
    if n_offline:
        rows = (await db.execute(
            select(Device).where(Device.greenhouse_id == gh.id))).scalars().all()
        for d in rows[-n_offline:]:
            d.status = DeviceStatus.OFFLINE
        await db.commit()
    u, token = await fx.user(db, UserRole.ADMIN, c)
    await db.commit()
    return c, gh, token


async def test_list_scoped_with_device_and_point_counts(client, iso):
    """列表：本公司可见自己棚+设备数；他司棚不出现。"""
    fx, db = iso
    c1, gh1, token1 = await _mk(fx, db, "wba", n_devices=2)
    c2 = await fx.company(db, "wbaB-C")
    await fx.greenhouse(db, c2, "wbaB-gh")
    await db.commit()

    r = await client.get(f"{API}/workbench/greenhouses", headers=auth(token1))
    assert r.status_code == 200, r.text
    items = r.json()
    mine = [i for i in items if i["id"] == gh1.id]
    assert len(mine) == 1
    assert mine[0]["devices"] == {"total": 2, "online": 2}
    assert mine[0]["point_count"] == 0
    assert all(i["id"] != "" for i in items)
    assert not any(i["name"] == "wbaB-gh" for i in items)


async def test_profile_layout_patch(client, iso):
    """PUT profile 带 layout 局部键：长宽更新，其余布局键保留。"""
    fx, db = iso
    c, gh, token = await _mk(fx, db, "wbb")
    g = await client.get(f"{API}/workbench/greenhouses/{gh.id}", headers=auth(token))
    assert g.status_code == 200, g.text
    rev = g.json()["map"]["revision"]
    layout_before = g.json()["map"]["layout"]

    r = await client.put(
        f"{API}/workbench/greenhouses/{gh.id}/profile",
        headers=auth(token),
        json={"layout": {"length_value": 88, "width_value": 12, "calibrated": True},
              "revision": rev},
    )
    assert r.status_code == 200, r.text
    g2 = await client.get(f"{API}/workbench/greenhouses/{gh.id}", headers=auth(token))
    layout_after = g2.json()["map"]["layout"]
    assert layout_after["length_value"] == 88
    assert layout_after["width_value"] == 12
    assert layout_after["calibrated"] is True
    # 其余键未被破坏（DEFAULT_LAYOUT 中必有的结构键仍在）
    for k in layout_before:
        if k not in ("length_value", "width_value", "calibrated"):
            assert k in layout_after, f"layout 键丢失: {k}"


async def test_delete_soft(client, iso):
    """DELETE：软删（is_active=False），GET 仍可访问（历史数据可追溯）。"""
    fx, db = iso
    c, gh, token = await _mk(fx, db, "wbc")
    r = await client.delete(f"{API}/workbench/greenhouses/{gh.id}", headers=auth(token))
    assert r.status_code == 204, r.text
    from app.core.database import AsyncSessionLocal as _S
    async with _S() as s2:
        active = await s2.scalar(select(Greenhouse.is_active).where(Greenhouse.id == gh.id))
    assert active is False
    g = await client.get(f"{API}/workbench/greenhouses/{gh.id}", headers=auth(token))
    assert g.status_code == 200
    # 2026-10-29 反馈：删除后刷新列表不应再出现
    lst = await client.get(f"{API}/workbench/greenhouses", headers=auth(token))
    assert all(i["id"] != gh.id for i in lst.json())


async def test_devices_unbound_pool(client, iso):
    """unbound=true 返回未绑定大棚的设备；绑定后再查不再出现。"""
    fx, db = iso
    c, gh, token = await _mk(fx, db, "wbu")
    # 造一台未绑定设备（无 greenhouse）
    from app.models.models import Device as D
    d = D(id=str(uuid.uuid4()), device_id=f"pool-{uuid.uuid4().hex[:8]}",
          name="待绑定", device_type=DeviceType.SENSOR, status=DeviceStatus.ONLINE)
    db.add(d)
    await db.commit()
    r = await client.get(f"{API}/devices/?unbound=true", headers=auth(token))
    assert r.status_code == 200, r.text
    ids = [x["device_id"] for x in r.json()]
    assert d.device_id in ids
    # 绑定到棚（PUT greenhouse_id）后不再是未绑定
    rb = await client.put(f"{API}/devices/{d.id}", headers=auth(token),
                          json={"greenhouse_id": gh.id})
    assert rb.status_code == 200, rb.text
    r2 = await client.get(f"{API}/devices/?unbound=true", headers=auth(token))
    assert d.device_id not in [x["device_id"] for x in r2.json()]
    # 棚下设备列表可见
    r3 = await client.get(f"{API}/devices/?greenhouse_id={gh.id}", headers=auth(token))
    assert d.device_id in [x["device_id"] for x in r3.json()]


async def test_devices_summary_mixed_status(client, iso):
    """设备摘要：在线/离线混合计数正确。"""
    fx, db = iso
    c, gh, token = await _mk(fx, db, "wbd", n_devices=3, n_offline=2)
    r = await client.get(f"{API}/workbench/greenhouses", headers=auth(token))
    assert r.status_code == 200, r.text
    mine = [i for i in r.json() if i["id"] == gh.id][0]
    assert mine["devices"] == {"total": 3, "online": 1}


async def test_cross_company_profile_404(client, iso):
    """他司用户改/看我司棚 → 404。"""
    fx, db = iso
    c1, gh1, token1 = await _mk(fx, db, "wbe")
    c2, gh2, token2 = await _mk(fx, db, "wbeX")
    r = await client.put(
        f"{API}/workbench/greenhouses/{gh1.id}/profile",
        headers=auth(token2), json={"name": "hehe", "revision": 0},
    )
    assert r.status_code == 404, r.text
