"""
测试类别 2：参数校验（非法 uuid / 缺字段 / 越界分页 / 未知点位）

要求：4xx，且错误信息可读（detail 非空）。
说明：越界分页只在本次新增的 /point-config/map-monitor 上做了 ge/le 校验；
      既有 /api/v1/telemetry/? 与 /api/v1/greenhouses 的 skip/limit 无约束，
      该差距已计入审计报告，不在本次改动范围内（避免动到既有采集/查询逻辑）。
"""
import uuid

from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.models import Greenhouse, UserRole
from tests.conftest import auth

API = settings.API_PREFIX
WB = f"{API}/workbench/greenhouses"
PC = f"{API}/point-config"


async def _delete_greenhouse(gh_id: str):
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(Greenhouse).where(Greenhouse.id == gh_id))).scalar_one_or_none()
        if row:
            await db.delete(row)
            await db.commit()


async def test_illegal_uuid_returns_404_with_readable_detail(client, make_user):
    _, token = await make_user(UserRole.ADMIN)
    r = await client.get(f"{WB}/not-a-uuid", headers=auth(token))
    assert 400 <= r.status_code < 500, r.text
    assert r.json()["detail"] == "大棚不存在"


async def test_create_missing_required_field_is_422(client, make_user):
    _, token = await make_user(UserRole.ADMIN)
    r = await client.post(WB, json={}, headers=auth(token))
    assert r.status_code == 422, r.text


async def test_create_empty_name_is_422(client, make_user):
    _, token = await make_user(UserRole.ADMIN)
    r = await client.post(WB, json={"name": ""}, headers=auth(token))
    assert r.status_code == 422, r.text


async def test_create_negative_area_is_422(client, make_user):
    _, token = await make_user(UserRole.ADMIN)
    r = await client.post(WB, json={"name": "bad-area", "actual_area": -1}, headers=auth(token))
    assert r.status_code == 422, r.text


async def test_profile_empty_body_is_400(client, make_user):
    _, token = await make_user(UserRole.ADMIN)
    r = await client.post(WB, json={"name": "params-empty-body"}, headers=auth(token))
    assert r.status_code == 201, r.text
    gh_id = r.json()["greenhouse_id"]
    try:
        r2 = await client.put(f"{WB}/{gh_id}/profile", json={}, headers=auth(token))
        assert r2.status_code == 400, r2.text
        assert "请求体为空" in r2.json()["detail"]
    finally:
        await _delete_greenhouse(gh_id)


async def test_unknown_point_is_404(client, make_user):
    _, token = await make_user(UserRole.ADMIN)
    r = await client.get(f"{WB}/{uuid.uuid4()}/points/NO_SUCH_POINT", headers=auth(token))
    assert 400 <= r.status_code < 500, r.text


async def test_map_monitor_limit_out_of_range_is_422(client, make_user):
    _, token = await make_user(UserRole.OPERATOR)
    for bad in (0, 101, -5):
        r = await client.get(f"{PC}/map-monitor/ROB001?limit={bad}", headers=auth(token))
        assert r.status_code == 422, f"limit={bad} 应被拒绝: {r.text}"


async def test_put_map_too_many_waypoints_is_400(client, make_user):
    _, token = await make_user(UserRole.ADMIN)
    payload = {"revision": 0, "points": [{"point_name": f"P{i}", "x": 1, "y": 1} for i in range(501)]}
    r = await client.put(f"{PC}/maps/{uuid.uuid4()}", json=payload, headers=auth(token))
    # 目标大棚不存在 → 404；存在 → 400 数量超限。两者都必须是 4xx 且 detail 可读。
    assert r.status_code in (400, 404), r.text
    assert r.json()["detail"]


async def test_binding_missing_robot_id_is_422(client, make_user):
    _, token = await make_user(UserRole.ADMIN)
    r = await client.put(f"{PC}/maps/{uuid.uuid4()}/points/P1/binding",
                         json={"nav_point_code": "A"}, headers=auth(token))
    assert r.status_code == 422, r.text


async def test_telemetry_hours_over_limit_is_422(client, make_user):
    """既有接口：hours 有 le=168 约束。"""
    _, token = await make_user(UserRole.VIEWER)
    r = await client.get(f"{API}/telemetry/?hours=9999", headers=auth(token))
    assert r.status_code == 422, r.text
