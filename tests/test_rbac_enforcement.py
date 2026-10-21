"""
测试类别 2：既有业务路由的 RBAC 强制校验（关闭 B1 缺口）

背景：此前全站只在 app/api/__init__.py 做了"必须登录"，
但业务路由内部从未调用 check_permission —— 任意已登录的 VIEWER
都能删大棚、下发设备指令、改 MQTT/.env 配置。

本测试验证：
  * VIEWER 调用任意受保护的写接口 -> 403（权限不足）
  * 对无破坏性副作用的子集，ADMIN 能越过权限门（返回 404/201 而非 401/403）

说明：update_mqtt_config / update_system_config / delete_image 会写文件或删文件，
其 ADMIN 正向用例刻意不测，避免测试过程改动环境；仅验证 VIEWER 被 403 拦截。
"""
import uuid

import pytest

from app.core.config import settings
from app.models.models import UserRole
from tests.conftest import auth

API = settings.API_PREFIX

# (method, path, json_body_or_None, safe_for_admin_positive)
CASES = [
    ("post",   f"{API}/greenhouses/", {"name": "x"}, True),
    ("delete", f"{API}/greenhouses/{uuid.uuid4()}", None, True),
    ("post",   f"{API}/devices/",
     {"device_id": f"dev-{uuid.uuid4().hex[:10]}", "name": "x", "device_type": "sensor"}, True),
    ("post",   f"{API}/devices/{uuid.uuid4()}/command", {"command": "start"}, False),
    ("post",   f"{API}/greenhouses/{uuid.uuid4()}/waypoints", {"x": 1.0, "y": 2.0}, True),
    ("post",   f"{API}/deployments/", {"name": "x"}, True),
    ("post",   f"{API}/deployments/{uuid.uuid4()}/publish", None, True),
    ("post",   f"{API}/deployments/{uuid.uuid4()}/point-mapping",
     {"chassis_point": "a", "greenhouse_id": "x", "greenhouse_point": "b"}, True),
    ("put",    f"{API}/settings/greenhouse/{uuid.uuid4()}/map",
     {"width": 100, "height": 60, "waypoints": []}, True),
    ("post",   f"{API}/settings/greenhouse/{uuid.uuid4()}/map/waypoints",
     {"id": 1, "x": 1.0, "y": 2.0}, True),
    ("put",    f"{API}/settings/robot/{uuid.uuid4()}/config",
     {"patrol_speed": 0.5, "capture_interval": 5, "return_battery_threshold": 20,
      "auto_patrol": False, "patrol_waypoints": []}, True),
    ("post",   f"{API}/settings/robot/{uuid.uuid4()}/assign-greenhouse/{uuid.uuid4()}", None, True),
    ("put",    f"{API}/settings/mqtt", {"broker_host": "h"}, False),
    ("put",    f"{API}/settings/system", {}, False),
    ("post",   f"{API}/tasks/{uuid.uuid4()}/dispatch", None, True),
    ("delete", f"{API}/images/{uuid.uuid4()}", None, False),
    ("post",   f"{API}/devices/{uuid.uuid4()}/patrol-greenhouses", {"greenhouse_id": "x"}, True),
    ("put",    f"{API}/inspection-batches/{uuid.uuid4()}/route-label", {"label": "x"}, True),
]


@pytest.mark.parametrize("method,path,body,_safe", CASES)
async def test_viewer_blocked_on_protected_mutations(
    client, make_user, method, path, body, _safe
):
    _, token = await make_user(UserRole.VIEWER)
    req = getattr(client, method)
    kwargs = {"headers": auth(token)}
    if body is not None:
        kwargs["json"] = body
    r = await req(path, **kwargs)
    assert r.status_code == 403, f"{method.upper()} {path} -> {r.status_code}: {r.text}"
    assert "权限不足" in r.json()["detail"]


SAFE_CASES = [c for c in CASES if c[3]]


@pytest.mark.parametrize("method,path,body,_safe", SAFE_CASES)
async def test_admin_passes_permission_gate(
    client, make_user, method, path, body, _safe
):
    _, token = await make_user(UserRole.ADMIN)
    req = getattr(client, method)
    kwargs = {"headers": auth(token)}
    if body is not None:
        kwargs["json"] = body
    r = await req(path, **kwargs)
    assert r.status_code not in (401, 403), (
        f"admin wrongly blocked: {method.upper()} {path} -> {r.status_code}: {r.text}"
    )
