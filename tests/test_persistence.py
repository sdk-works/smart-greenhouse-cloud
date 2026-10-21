"""
测试类别 3：持久化（按前端真实契约）

覆盖：
  - POST /workbench/greenhouses 创建 → 返回 greenhouse_id → GET 返回 {profile,map,latest}
  - PUT  /workbench/greenhouses/{id}/profile 修改 → GET 能读回新值
  - PUT  /point-config/maps/{gh} 写入文档（layout/points/defaults）→ GET 读回 + DB map_data 落盘
  - PUT  /point-config/maps/{gh}/points/{point_id}/binding → robot_point_configs 新增一行
  - GET  /point-config/map-monitor/{robot} 反映绑定 + 含 latest_arrival
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

LAYOUT = {
    "template": "solar", "width": 100, "height": 60, "calibrated": False,
    "aisle_side": "north", "aisle_width": 1.5, "bays": 4,
    "length_axis": "ew", "planting_direction": "ns",
    "collection_lane_count": 0, "collection_direction": "ns",
}
DEFAULTS = {"sample_seconds": 20, "collect_soil": True,
            "collect_environment": True, "take_photo": True, "enabled": True}


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
        "name": name, "location": "棚区-A", "actual_area": 666.5, "crop_type": "番茄",
        "structure": "solar", "covering": "film",
    }, headers=auth(token))
    assert r.status_code == 201, r.text
    gh_id = r.json()["greenhouse_id"]

    try:
        # 1) API 读回 {profile, map, latest}
        g = await client.get(f"{WB}/{gh_id}", headers=auth(token))
        assert g.status_code == 200, g.text
        body = g.json()
        assert body["profile"]["name"] == name
        assert body["profile"]["location"] == "棚区-A"
        assert abs(body["profile"]["actual_area"] - 666.5) < 1e-6
        assert body["profile"]["crop_type"] == "番茄"
        assert body["map"]["points"] == []
        assert "layout" in body["map"] and "defaults" in body["map"]
        assert body["latest"] == {}

        # 2) 数据库里真的有行
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(Greenhouse).where(
                Greenhouse.id == gh_id))).scalar_one_or_none()
            assert row is not None, "数据库 greenhouses 表中应存在该行"
            assert row.name == name
            assert row.is_active is True

        # 3) profile 修改后能读回（revision 递增）
        new_name = name + "-v2"
        p = await client.put(f"{WB}/{gh_id}/profile",
                             json={"name": new_name, "description": "夜间验证",
                                   "revision": body["profile"]["revision"]},
                             headers=auth(token))
        assert p.status_code == 200, p.text
        assert p.json()["success"] is True

        g2 = await client.get(f"{WB}/{gh_id}", headers=auth(token))
        assert g2.json()["profile"]["name"] == new_name
        assert g2.json()["profile"]["description"] == "夜间验证"

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
    gh_id = r.json()["greenhouse_id"]

    try:
        doc = {
            "revision": 1,
            "layout": LAYOUT,
            "defaults": DEFAULTS,
            "next_name": "A3",
            "points": [
                {"id": "p-1", "point_name": "A1", "zone": "中区", "x": 10, "y": 20,
                 "note": "", **DEFAULTS},
                {"id": "p-2", "point_name": "A2", "zone": "北区", "x": 30, "y": 40,
                 "note": "", **DEFAULTS},
            ],
        }
        m = await client.put(f"{PC}/maps/{gh_id}", json=doc, headers=auth(token))
        assert m.status_code == 200, m.text
        assert len(m.json()["points"]) == 2
        assert m.json()["revision"] == 2

        m2 = await client.get(f"{PC}/maps/{gh_id}", headers=auth(token))
        assert m2.status_code == 200, m2.text
        assert [p["point_name"] for p in m2.json()["points"]] == ["A1", "A2"]

        async with AsyncSessionLocal() as db:
            row = await db.get(Greenhouse, gh_id)
            assert (row.map_data or {}).get("points"), "map_data 必须真的落库"
            assert row.map_data["layout"]["width"] == 100

        # 工作台 GET 也要能看到点位
        w = await client.get(f"{WB}/{gh_id}", headers=auth(token))
        assert w.status_code == 200, w.text
        assert [p["point_name"] for p in w.json()["map"]["points"]] == ["A1", "A2"]

        # 点位详情（按 id）
        d = await client.get(f"{WB}/{gh_id}/points/p-1", headers=auth(token))
        assert d.status_code == 200, d.text
        assert d.json()["point"]["point_name"] == "A1"
        assert d.json()["point"]["x"] == 10
        assert d.json()["observations"] == []

        # 绑定：点位 p-1 ↔ 机器人导航点 A点
        b = await client.put(f"{PC}/maps/{gh_id}/points/p-1/binding",
                             json={"revision": 2, "robot_id": ROBOT, "nav_point_code": "A点"},
                             headers=auth(token))
        assert b.status_code == 200, b.text
        bound = next(p for p in b.json()["points"] if p["id"] == "p-1")
        assert bound["binding"]["nav_point_code"] == "A点"

        async with AsyncSessionLocal() as db:
            cfg = (await db.execute(select(RobotPointConfig).where(
                RobotPointConfig.robot_id == ROBOT,
                RobotPointConfig.nav_point_code == "A点"))).scalar_one_or_none()
            assert cfg is not None, "robot_point_configs 应真的新增一行"
            assert cfg.greenhouse_id == gh_id
            assert cfg.point_name == "A1"

        # 绑定后点位详情应带出 binding
        d2 = await client.get(f"{WB}/{gh_id}/points/p-1", headers=auth(token))
        assert d2.status_code == 200, d2.text
        binding = d2.json()["point"]["binding"]
        assert binding is not None and binding["robot_id"] == ROBOT
        assert binding["nav_point_code"] == "A点"

        # 地图监控应能看到该绑定，并含 latest_arrival 字段
        mon = await client.get(f"{PC}/map-monitor/{ROBOT}", headers=auth(token))
        assert mon.status_code == 200, mon.text
        assert mon.json()["robot_id"] == ROBOT
        assert any(x["nav_point_code"] == "A点" for x in mon.json()["bindings"])
        assert "latest_arrival" in mon.json()
    finally:
        await _purge(gh_id, ROBOT)
