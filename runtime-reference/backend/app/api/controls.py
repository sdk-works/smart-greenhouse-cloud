"""风机/水阀控制 V1：持久化命令、安全校验和规则配置。"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.auth import check_permission, get_current_user
from app.core.database import get_db
from app.models.models import (ActuatorCommand, ActuatorProfile, AuditLog, CommandStatus,
    ControlMode, ControlRule, Device, DeviceType, User)
from app.mqtt.mqtt_service import mqtt_service

router = APIRouter()
ACTUATOR_TYPES = {DeviceType.FAN, DeviceType.IRRIGATION}

class CommandIn(BaseModel):
    action: str
    params: Dict[str, Any] = Field(default_factory=dict)
    reason: Optional[str] = None
    ttl_seconds: int = Field(default=30, ge=5, le=300)

class ProfileUpdate(BaseModel):
    control_mode: Optional[ControlMode] = None
    zone_id: Optional[str] = None
    safety_limits: Optional[Dict[str, Any]] = None
    maintenance_lock: Optional[bool] = None

class RuleIn(BaseModel):
    name: str; greenhouse_id: str; device_id: str
    enabled: bool = False; metric: str; start_operator: str
    start_threshold: float; stop_threshold: float
    duration_seconds: int = Field(default=60, ge=1, le=3600)
    cooldown_seconds: int = Field(default=300, ge=0, le=86400)
    priority: int = Field(default=5, ge=1, le=10)
    action: str
    action_params: Dict[str, Any] = Field(default_factory=dict)

def _out(device, profile):
    return {"device_id": device.id, "hardware_id": device.device_id, "name": device.name,
        "device_type": device.device_type, "greenhouse_id": device.greenhouse_id,
        "status": device.status, "last_seen": device.last_seen, "zone_id": profile.zone_id,
        "control_mode": profile.control_mode, "desired_state": profile.desired_state or {},
        "actual_state": profile.actual_state or {}, "safety_limits": profile.safety_limits or {},
        "emergency_stop": profile.emergency_stop, "maintenance_lock": profile.maintenance_lock,
        "last_command_id": profile.last_command_id, "state_updated_at": profile.state_updated_at}

async def _get(db, device_id):
    device = await db.get(Device, device_id)
    if not device or device.device_type not in ACTUATOR_TYPES:
        raise HTTPException(404, "风机或水阀设备不存在")
    profile = await db.get(ActuatorProfile, device.id)
    if not profile:
        profile = ActuatorProfile(device_id=device.id, safety_limits={
            "max_duration_seconds": 1800 if device.device_type == DeviceType.FAN else 300})
        db.add(profile); await db.flush()
    return device, profile

def _validate(device, profile, item):
    allowed = ({"turn_on", "turn_off", "set_level"} if device.device_type == DeviceType.FAN
               else {"open", "close", "turn_on", "turn_off"})
    if item.action not in allowed: raise HTTPException(422, f"不支持动作 {item.action}")
    stopping = item.action in {"turn_off", "close"}
    if (profile.emergency_stop or profile.maintenance_lock or
            profile.control_mode == ControlMode.MAINTENANCE) and not stopping:
        raise HTTPException(409, "设备处于急停/维护锁定状态，只允许关闭")
    duration = int(item.params.get("duration_seconds", 0) or 0)
    maximum = int((profile.safety_limits or {}).get("max_duration_seconds",
        1800 if device.device_type == DeviceType.FAN else 300))
    if duration < 0 or duration > maximum: raise HTTPException(422, f"运行时长必须在 0~{maximum} 秒")
    if item.action == "set_level" and not (isinstance(item.params.get("level"), (int, float))
                                            and 0 <= item.params["level"] <= 100):
        raise HTTPException(422, "风机 level 必须为 0~100")

async def _issue(db, device, profile, item, user, source="manual"):
    _validate(device, profile, item); now = datetime.now(timezone.utc)
    cid = f"cmd-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    desired = {"running": item.action not in {"turn_off", "close"}, "action": item.action, **item.params}
    cmd = ActuatorCommand(command_id=cid, device_id=device.id, greenhouse_id=device.greenhouse_id,
        action=item.action, params=item.params, source=source, reason=item.reason,
        desired_state=desired, issued_by=user.id, expires_at=now + timedelta(seconds=item.ttl_seconds))
    db.add(cmd); await db.flush()
    payload = {"version": 1, "type": "command", "command_id": cid, "device_id": device.device_id,
        "action": item.action, "params": item.params, "issued_at": now.isoformat(),
        "expires_at": cmd.expires_at.isoformat()}
    sent = await mqtt_service.publish_device_command(device.device_id, payload)
    cmd.status = CommandStatus.SENT if sent else CommandStatus.FAILED
    cmd.error_message = None if sent else "MQTT 未连接或发布失败"
    profile.desired_state = desired; profile.last_command_id = cid
    db.add(AuditLog(user_id=user.id, action="control_command", resource="actuator",
                    resource_id=device.id, detail={**payload, "sent": sent}))
    await db.commit(); await db.refresh(cmd); return cmd

@router.get("/actuators")
async def list_actuators(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    check_permission(user, "view_controls")
    devices = (await db.execute(select(Device).where(Device.device_type.in_(list(ACTUATOR_TYPES))))).scalars().all()
    result = []
    for device in devices:
        _, profile = await _get(db, device.id); result.append(_out(device, profile))
    await db.commit(); return result

@router.put("/actuators/{device_id}/profile")
async def update_profile(device_id: str, item: ProfileUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    check_permission(user, "manage_controls"); device, profile = await _get(db, device_id)
    for key, value in item.model_dump(exclude_unset=True).items(): setattr(profile, key, value)
    await db.commit(); await db.refresh(profile); return _out(device, profile)

@router.post("/actuators/{device_id}/commands")
async def issue_command(device_id: str, item: CommandIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    check_permission(user, "control_devices"); device, profile = await _get(db, device_id)
    cmd = await _issue(db, device, profile, item, user)
    return {"command_id": cmd.command_id, "status": cmd.status,
        "message": "命令已发送，等待设备确认" if cmd.status == CommandStatus.SENT else cmd.error_message}

@router.post("/emergency/close-all")
async def emergency_close_all(greenhouse_id: Optional[str] = None, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    check_permission(user, "emergency_control")
    q = select(Device).where(Device.device_type == DeviceType.IRRIGATION)
    if greenhouse_id: q = q.where(Device.greenhouse_id == greenhouse_id)
    results = []
    for device in (await db.execute(q)).scalars().all():
        _, profile = await _get(db, device.id); profile.emergency_stop = True
        cmd = await _issue(db, device, profile, CommandIn(action="close", reason="emergency_close_all"), user, "emergency")
        results.append({"device_id": device.device_id, "command_id": cmd.command_id, "status": cmd.status})
    return {"count": len(results), "commands": results}

@router.get("/commands")
async def list_commands(device_id: Optional[str] = None, limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    check_permission(user, "view_controls"); q = select(ActuatorCommand).order_by(ActuatorCommand.created_at.desc()).limit(limit)
    if device_id: q = q.where(ActuatorCommand.device_id == device_id)
    return (await db.execute(q)).scalars().all()

@router.get("/rules")
async def list_rules(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    check_permission(user, "view_controls")
    return (await db.execute(select(ControlRule).order_by(ControlRule.created_at.desc()))).scalars().all()

@router.post("/rules")
async def create_rule(item: RuleIn, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    check_permission(user, "manage_control_rules"); device, _ = await _get(db, item.device_id)
    if device.greenhouse_id != item.greenhouse_id: raise HTTPException(422, "设备不属于指定大棚")
    if item.start_operator not in {">", ">=", "<", "<="}: raise HTTPException(422, "不支持的比较符")
    if item.enabled: raise HTTPException(409, "V1 尚未启用自动执行器，规则只能以禁用状态保存")
    rule = ControlRule(id=str(uuid.uuid4()), created_by=user.id, **item.model_dump())
    db.add(rule); await db.commit(); await db.refresh(rule); return rule
