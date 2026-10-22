"""
api/__init__.py — 路由统一注册

安全基线（2026-10-17）：
  - 除 /auth 下的公开端点（login / logout / init-admin）外，所有业务 API 强制登录鉴权。
  - WebSocket 走 ?token= 查询参数鉴权（浏览器 WS 无法自定义 Header）；
    鉴权在 app/api/ws.py 端点内部完成（先 accept 再 close 4401/4403），
    不在路由级依赖里做——依赖报错发生在握手前，浏览器只会看到 1006。
  - 细粒度 RBAC 由各路由内 check_permission 负责；这里保证"必须已登录"。
  - 例外：`/emqx/authn` 是给 broker 调的钩子（T3 候选，未启用），拿不到业务 token，
    改用共享密钥 EMQX_AUTHN_TOKEN；未配置时端点直接 503。
"""
from fastapi import APIRouter, Depends

from app.api import (
    greenhouses, devices, telemetry, tasks, images,
    dashboard, ws, settings_api, waypoints, robot_patrol,
    deployments, auth, point_configs, inspection_batches,
    workbench, photo_catalog, incidents, freshness, bindings, maintenance,
    actuator_commands, ai_advice, planting_batches, reports,
    device_onboarding, emqx_authn,
)
from app.api.auth import get_current_user

api_router = APIRouter()


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
    (actuator_commands.router,  "",                   "ActuatorCommands 控制命令流转",    True),
    (ai_advice.router,          "",                   "AI 受控接口与指标定义",            True),
    (planting_batches.router,   "",                   "PlantingBatches 种植批次",         True),
    (reports.router,            "",                   "Reports 报表导出",                 True),
    (device_onboarding.router,  "",                   "DeviceOnboarding 设备入网与凭据",   True),
]

for _r, _prefix, _tag, _secure in _ROUTERS:
    _deps = [Depends(get_current_user)] if _secure else []
    api_router.include_router(_r, prefix=_prefix, tags=[_tag], dependencies=_deps)

# EMQX 认证钩子：单独挂载，**不强制业务登录**（broker 拿不到业务 token），
# 端点内部用共享密钥鉴权，未配置即 503。生产当前未启用，见 docs/emqx-authn-enable.md。
api_router.include_router(emqx_authn.router, tags=["EMQX authn hook（候选，未启用）"])

# WebSocket 单独挂载（鉴权方式与 HTTP 不同）
# 【2026-10-22 浏览器实测修正】不再挂 ws_current_user 依赖。
#   FastAPI 的依赖报错发生在握手之前，Starlette 会直接断开、不发 accept，
#   浏览器只能看到 1006（异常关闭），拿不到 4401/4403，前端无法区分"未登录"和"连接坏了"。
#   现在鉴权在 ws.py 端点内部完成：先 accept 再 close(4401/4403)，客户端能读到关闭码。
#   uvicorn 日志里的 "ASGI callable returned without sending handshake" 也随之消失。
api_router.include_router(ws.router, prefix="/ws", tags=["WebSocket"])

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
