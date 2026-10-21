"""
api/__init__.py — 路由统一注册

安全基线（2026-10-17）：
  - 除 /auth 下的公开端点（login / logout / init-admin）外，所有业务 API 强制登录鉴权。
  - WebSocket 走 ?token= 查询参数鉴权（浏览器 WS 无法自定义 Header）。
  - 细粒度 RBAC 由各路由内 check_permission 负责；这里保证"必须已登录"。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from typing import Optional

from app.api import (
    greenhouses, devices, telemetry, tasks, images,
    dashboard, ws, settings_api, waypoints, robot_patrol,
    deployments, auth, point_configs, inspection_batches,
    workbench, photo_catalog, incidents, freshness, bindings, maintenance,
)
from app.api.auth import get_current_user
from app.core.redis import get_redis

api_router = APIRouter()


# ── WebSocket 鉴权：token 走查询参数 ───────────────────────────
async def ws_current_user(token: Optional[str] = Query(None)):
    """浏览器 WebSocket 不能设置 Header，用 ?token= 校验；无效即拒绝握手。"""
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="WS 缺少 token")
    redis = await get_redis()
    user_id = await redis.get(f"auth:token:{token}")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="WS token 无效")
    await redis.expire(f"auth:token:{token}", 8 * 60 * 60)
    return user_id


# ── 业务路由：统一强制登录（auth 前缀除外） ────────────────────
_ROUTERS = [
    (auth.router,               "/auth",               "Auth 认证&权限", False),
    (dashboard.router,          "/dashboard",          "Dashboard",                      True),
    (greenhouses.router,        "/greenhouses",        "Greenhouses",                    True),
    (devices.router,            "/devices",            "Devices",                        True),
    (telemetry.router,          "/telemetry",          "Telemetry",                      True),
    (tasks.router,              "/tasks",              "Tasks",                          True),
    (images.router,             "/images",             "Images",                         True),
    (settings_api.router,       "/settings",           "Settings",                       True),
    (waypoints.router,          "/greenhouses",        "Waypoints",                      True),
    (robot_patrol.router,       "/devices",            "Patrol",                         True),
    (deployments.router,        "/deployments",        "Deployments 巡检方案",            True),
    (point_configs.router,      "/point-config",       "Navigation point configuration", True),
    (inspection_batches.router, "/inspection-batches", "Automatic inspection batches",   True),
    (workbench.router,          "/workbench",          "Workbench 大棚工作台",            True),
    (photo_catalog.router,      "",                   "PhotoCatalog 照片归档",           True),
    (incidents.router,          "",                   "Incidents 异常事件与告警",        True),
    (freshness.router,          "",                   "Freshness 数据新鲜度",            True),
    (bindings.router,           "",                   "Bindings 设备绑定",               True),
    (maintenance.router,        "",                   "Maintenance 运维兜底",            True),
]

for _r, _prefix, _tag, _secure in _ROUTERS:
    _deps = [Depends(get_current_user)] if _secure else []
    api_router.include_router(_r, prefix=_prefix, tags=[_tag], dependencies=_deps)

# WebSocket 单独挂载（鉴权方式与 HTTP 不同）
api_router.include_router(
    ws.router, prefix="/ws", tags=["WebSocket"], dependencies=[Depends(ws_current_user)]
)

# ── 尾斜杠兼容：为带 "/" 的路由额外注册无尾斜杠副本，消除 307 重定向 ──
from fastapi.routing import APIRoute

def _add_no_slash_duplicates(router):
    extras = []
    for route in list(router.routes):
        if isinstance(route, APIRoute) and route.path.endswith("/") and len(route.path) > 1:
            no_slash = route.path[:-1]
            if any(getattr(r, "path", None) == no_slash for r in router.routes):
                continue
            extras.append(APIRoute(
                path=no_slash,
                endpoint=route.endpoint,
                response_model=route.response_model,
                status_code=route.status_code,
                tags=route.tags,
                dependencies=route.dependencies,
                summary=route.summary,
                description=route.description,
                response_description=route.response_description,
                responses=route.responses,
                deprecated=route.deprecated,
                methods=route.methods,
                operation_id=route.operation_id,
                response_model_by_alias=getattr(route, "response_model_by_alias", True),
                include_in_schema=False,
                response_class=route.response_class,
                name=route.name,
            ))
    router.routes.extend(extras)

_add_no_slash_duplicates(api_router)
