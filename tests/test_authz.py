"""
测试类别 1：权限（鉴权 / RBAC）

覆盖：
  - 未带 token                → 401
  - 伪造 / 失效 token         → 401
  - viewer 调写接口           → 403
  - operator 可读地图、viewer 不可读（view_point_mapping 不含 viewer）
  - admin 写接口放行
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


async def test_missing_token_is_401(client, schema_ready):
    r = await client.get(f"{WB}/{uuid.uuid4()}")
    assert r.status_code == 401, r.text
    assert r.json()["detail"]


async def test_malformed_token_is_401(client, schema_ready):
    r = await client.get(f"{WB}/{uuid.uuid4()}",
                         headers={"Authorization": "not-a-bearer-token"})
    assert r.status_code == 401, r.text


async def test_unknown_token_is_401(client, schema_ready):
    r = await client.get(f"{WB}/{uuid.uuid4()}", headers=auth("bogus.token.value"))
    assert r.status_code == 401, r.text


async def test_viewer_cannot_create_greenhouse(client, make_user):
    _, token = await make_user(UserRole.VIEWER)
    r = await client.post(WB, json={"name": "viewer-试图新建"}, headers=auth(token))
    assert r.status_code == 403, r.text
    assert "权限不足" in r.json()["detail"]


async def test_viewer_cannot_update_profile(client, make_user):
    _, token = await make_user(UserRole.VIEWER)
    r = await client.put(f"{WB}/{uuid.uuid4()}/profile",
                         json={"name": "x"}, headers=auth(token))
    assert r.status_code == 403, r.text
    assert "权限不足" in r.json()["detail"]


async def test_viewer_cannot_write_map(client, make_user):
    _, token = await make_user(UserRole.VIEWER)
    r = await client.put(f"{PC}/maps/{uuid.uuid4()}",
                         json={"waypoints": []}, headers=auth(token))
    assert r.status_code == 403, r.text


async def test_viewer_cannot_bind_point(client, make_user):
    _, token = await make_user(UserRole.VIEWER)
    r = await client.put(f"{PC}/maps/{uuid.uuid4()}/points/P1/binding",
                         json={"robot_id": "ROB001", "nav_point_code": "A"},
                         headers=auth(token))
    assert r.status_code == 403, r.text


async def test_viewer_cannot_reset_discovery(client, make_user):
    _, token = await make_user(UserRole.VIEWER)
    r = await client.post(f"{PC}/robots/ROB001/reset-discovery", headers=auth(token))
    assert r.status_code == 403, r.text


async def test_viewer_cannot_read_map_but_operator_reaches_404(client, make_user):
    """view_point_mapping 只给 admin/operator：viewer 403，operator 鉴权放行后 404。"""
    gh_id = str(uuid.uuid4())
    _, viewer_token = await make_user(UserRole.VIEWER)
    _, operator_token = await make_user(UserRole.OPERATOR)

    r_viewer = await client.get(f"{PC}/maps/{gh_id}", headers=auth(viewer_token))
    assert r_viewer.status_code == 403, r_viewer.text

    r_operator = await client.get(f"{PC}/maps/{gh_id}", headers=auth(operator_token))
    assert r_operator.status_code == 404, r_operator.text


async def test_admin_can_create_greenhouse(client, make_user):
    _, token = await make_user(UserRole.ADMIN)
    r = await client.post(WB, json={"name": "authz-ok"}, headers=auth(token))
    assert r.status_code == 201, r.text
    gh_id = r.json()["id"]
    try:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                select(Greenhouse).where(Greenhouse.id == gh_id))).scalar_one_or_none()
            assert row is not None, "admin 创建后数据库应能查到该行"
    finally:
        await _delete_greenhouse(gh_id)
