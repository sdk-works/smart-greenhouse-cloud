from fastapi import APIRouter
from app.api import (
    greenhouses, devices, telemetry, tasks, images,
    dashboard, ws, settings_api, waypoints, robot_patrol,
    deployments, auth, point_configs, inspection_batches,
)

api_router = APIRouter()
api_router.include_router(auth.router,        prefix="/auth",        tags=["Auth 认证&权限"])
api_router.include_router(dashboard.router,   prefix="/dashboard",   tags=["Dashboard"])
api_router.include_router(greenhouses.router, prefix="/greenhouses", tags=["Greenhouses"])
api_router.include_router(devices.router,     prefix="/devices",     tags=["Devices"])
api_router.include_router(telemetry.router,   prefix="/telemetry",   tags=["Telemetry"])
api_router.include_router(tasks.router,       prefix="/tasks",       tags=["Tasks"])
api_router.include_router(images.router,      prefix="/images",      tags=["Images"])
api_router.include_router(ws.router,          prefix="/ws",          tags=["WebSocket"])
api_router.include_router(settings_api.router,prefix="/settings",    tags=["Settings"])
api_router.include_router(waypoints.router,   prefix="/greenhouses", tags=["Waypoints"])
api_router.include_router(robot_patrol.router,prefix="/devices",     tags=["Patrol"])
api_router.include_router(deployments.router, prefix="/deployments", tags=["Deployments 巡检方案"])
api_router.include_router(point_configs.router, prefix="/point-config", tags=["Navigation point configuration"])
api_router.include_router(inspection_batches.router, prefix="/inspection-batches", tags=["Automatic inspection batches"])
