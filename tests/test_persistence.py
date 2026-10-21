"""
测试类别 3：持久化

覆盖：
  - POST /workbench/greenhouses 创建 → GET 能读回，字段一致
  - PUT  /workbench/greenhouses/{id}/profile 修改 → GET 能读回新值
  - 直接用 AsyncSession 断言数据库里真的有行（不是只存在响应里）
  - PUT  /point-config/maps/{gh} 写入点位布局 → GET 读回 + DB 中 map_data 已落盘
  - PUT  /point-config/maps/{gh}/points/{code}/binding → DB 中 robot_point_configs 真的新增一行
  - GET  /point-config/map-monitor/{robot} 能反映上面的绑定
"""
import uuid

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import Greenhouse, RobotPointConfig, UserRole
from tests.conftest import auth

API = settings.API_PREFIX
WB = f"{API}/workbench/greenhouses"
PC = f"{API}/point-config"

ROBOT = f"ROBNIGHT-{uuid.uuid4().hex[:8]}"


async def _purge(gh_id: str, robot_id: str):
    async with AsyncSessionLocal() as db:
        cfgs = (await db.execute(select(RobotPointConfig).where(
            RobotPointConfig.robot_id == robot_id))).scalars().all()
        for c in cfgs:
            await db.delete(c)
        row = (await db.execute(select(Greenhouse).where(
            Greenhouse.id == gh_id))).scalar_one_or_none()
        if row:
            await db.delete(row)
        await db.commit()


async def test_create_then_get_roundtrip(client, make_user):
    _, token = await make_user(UserRole.ADMIN)
    name = f"persist-{uuid.uuid4().hex[:8]}"

    r = await client.post(WB, json={
        "name": name, "location": "棚区-A", "area": 666.5, "crop_type": "番茄",
    }, headers=auth(token))
    assert r.status_code == 201, r.text
    gh_id = r.json()["id"]

    try:
        # 1) API 读回
        g = await client.get(f"{WB}/{gh_id}", headers=auth(token))
        assert g.status_code == 200, g.text
        body = g.json()
        assert body["name"] == name
        assert body["location"] == "棚区-A"
        assert abs(body["area"] - 666.5) < 1e-6
        assert body["crop_type"] == "番茄"
        assert body["points"] == []

        # 2) 数据库里真的有行
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(Greenhouse).where(
                Greenhouse.id == gh_id))).scalar_one_or_none()
            assert row is not None, "数据库 greenhouses 表中应存在该行"
            assert row.name == name
            assert row.is_active is True

        # 3) profile 修改后能读回
        new_name = name + "-v2"
        p = await client.put(f"{WB}/{gh_id}/profile",
                             json={"name": new_name, "description": "夜间验证"},
                             headers=auth(token))
        assert p.status_code == 200, p.text
        assert p.json()["name"] == new_name
        assert p.json()["description"] == "夜间验证"

        g2 = await client.get(f"{WB}/{gh_id}", headers=auth(token))
        assert g2.status_code == 200, g2.text
        assert g2.json()["name"] == new_name

        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(Greenhouse).where(
                Greenhouse.id == gh_id))).scalar_one_or_none()
            assert row.name == new_name, "数据库里也应已更新"
    finally:
        await _purge(gh_id, ROBOT)


async def test_map_layout_and_binding_persist(client, make_user):
    _, token = await make_user(UserRole.ADMIN)
    r = await client.post(WB, json={"name": f"persist-map-{uuid.uuid4().hex[:8]}"},
                          headers=auth(token))
    assert r.status_code == 201, r.text
    gh_id = r.json()["id"]

    try:
        layout = {"width": 100, "height": 60, "waypoints": [
            {"id": 1, "code": "P1", "label": "1号点", "x": 10, "y": 20, "actions": ["capture_image"]},
            {"id": 2, "code": "P2", "label": "2号点", "x": 30, "y": 40},
        ]}
        m = await client.put(f"{PC}/maps/{gh_id}", json=layout, headers=auth(token))
        assert m.status_code == 200, m.text
        assert m.json()["point_count"] == 2

        m2 = await client.get(f"{PC}/maps/{gh_id}", headers=auth(token))
        assert m2.status_code == 200, m2.text
        assert [w["code"] for w in m2.json()["waypoints"]] == ["P1", "P2"]

        async with AsyncSessionLocal() as db:
            row = await db.get(Greenhouse, gh_id)
            assert (row.map_data or {}).get("waypoints"), "map_data 必须真的落库"
            assert row.map_data["width"] == 100

        # 工作台 GET 也要能看到点位
        w = await client.get(f"{WB}/{gh_id}", headers=auth(token))
        assert w.status_code == 200, w.text
        assert [p["code"] for p in w.json()["points"]] == ["P1", "P2"]

        # 点位详情
        d = await client.get(f"{WB}/{gh_id}/points/P1", headers=auth(token))
        assert d.status_code == 200, d.text
        assert d.json()["code"] == "P1"
        assert d.json()["x"] == 10

        # 绑定：大棚点位 P1 ↔ 机器人导航点 A
        b = await client.put(f"{PC}/maps/{gh_id}/points/P1/binding",
                             json={"robot_id": ROBOT, "nav_point_code": "A点"},
                             headers=auth(token))
        assert b.status_code == 200, b.text

        async with AsyncSessionLocal() as db:
            cfg = (await db.execute(select(RobotPointConfig).where(
                RobotPointConfig.robot_id == ROBOT,
                RobotPointConfig.nav_point_code == "A点"))).scalar_one_or_none()
            assert cfg is not None, "robot_point_configs 应真的新增一行"
            assert cfg.greenhouse_id == gh_id
            assert cfg.point_name == "P1"

        # 绑定后工作台 /points/P1 应带出 binding
        d2 = await client.get(f"{WB}/{gh_id}/points/P1", headers=auth(token))
        assert d2.status_code == 200, d2.text
        binding = d2.json()["binding"]
        assert binding is not None and binding["robot_id"] == ROBOT
        assert binding["nav_point_code"] == "A点"

        # 地图监控应能看到该绑定
        mon = await client.get(f"{PC}/map-monitor/{ROBOT}", headers=auth(token))
        assert mon.status_code == 200, mon.text
        assert mon.json()["robot_id"] == ROBOT
        assert any(x["nav_point_code"] == "A点" for x in mon.json()["bindings"])
    finally:
        await _purge(gh_id, ROBOT)
