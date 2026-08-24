"""
models.py — Smart Greenhouse Platform V9-Lite
新增：Deployment（巡检方案）、PointMapping（点位映射）、User/Role（RBAC权限）
"""
import uuid, enum
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, DateTime, Float, Integer,
    ForeignKey, Text, JSON, Enum as SAEnum, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


def gen_uuid(): return str(uuid.uuid4())


# ── 枚举 ──────────────────────────────────────────────────────
class DeviceType(str, enum.Enum):
    ROBOT = "robot"; SENSOR = "sensor"; IRRIGATION = "irrigation"
    FAN = "fan"; GROW_LIGHT = "grow_light"; CURTAIN = "curtain"
    FERTILIZER = "fertilizer"; CAMERA = "camera"

class DeviceStatus(str, enum.Enum):
    ONLINE = "online"; OFFLINE = "offline"; ERROR = "error"; MAINTENANCE = "maintenance"

class TaskStatus(str, enum.Enum):
    PENDING = "pending"; RUNNING = "running"; COMPLETED = "completed"
    FAILED = "failed"; CANCELLED = "cancelled"

class TaskType(str, enum.Enum):
    INSPECTION = "inspection"; WATERING = "watering"
    FERTILIZING = "fertilizing"; PEST_DETECTION = "pest_detection"

class DeploymentStatus(str, enum.Enum):
    DRAFT = "draft"; ACTIVE = "active"; ARCHIVED = "archived"

class UserRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"    # 我方超级管理员
    ADMIN       = "admin"          # 客户管理员
    OPERATOR    = "operator"       # 运维人员
    VIEWER      = "viewer"         # 观察者

class MappingSyncStatus(str, enum.Enum):
    PENDING = "pending"; SYNCED = "synced"; FAILED = "failed"


# ── 用户 & 权限 ───────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    username   = Column(String(100), unique=True, nullable=False, index=True)
    email      = Column(String(200), unique=True, nullable=True)
    password_hash = Column(String(200), nullable=False)
    role       = Column(SAEnum(UserRole), default=UserRole.VIEWER, nullable=False)
    is_active  = Column(Boolean, default=True)
    last_login = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    # 操作日志
    audit_logs = relationship("AuditLog", back_populates="user",
                              foreign_keys="AuditLog.user_id")


class AuditLog(Base):
    """操作日志（所有关键操作记录）"""
    __tablename__ = "audit_logs"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    user_id    = Column(String(36), ForeignKey("users.id"), nullable=True)
    action     = Column(String(200), nullable=False)   # create/update/delete/publish
    resource   = Column(String(100), nullable=False)   # deployment/waypoint/mapping...
    resource_id= Column(String(36),  nullable=True)
    detail     = Column(JSON)                          # 修改前后的值
    ip_address = Column(String(50))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user       = relationship("User", back_populates="audit_logs",
                              foreign_keys=[user_id])


# ── 大棚 ──────────────────────────────────────────────────────
class Greenhouse(Base):
    __tablename__ = "greenhouses"
    id          = Column(String(36), primary_key=True, default=gen_uuid)
    name        = Column(String(100), nullable=False)
    location    = Column(String(200))
    area        = Column(Float)
    crop_type   = Column(String(100))
    description = Column(Text)
    map_data    = Column(JSON)   # {width, height, waypoints:[{id,x,y,label,actions}]}
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), onupdate=func.now())

    devices     = relationship("Device", back_populates="greenhouse")
    telemetry   = relationship("Telemetry", back_populates="greenhouse")
    tasks       = relationship("RobotTask", back_populates="greenhouse")


# ── 设备 ──────────────────────────────────────────────────────
class Device(Base):
    __tablename__ = "devices"
    id                = Column(String(36), primary_key=True, default=gen_uuid)
    device_id         = Column(String(100), unique=True, nullable=False, index=True)
    name              = Column(String(100), nullable=False)
    device_type       = Column(SAEnum(DeviceType), nullable=False)
    greenhouse_id     = Column(String(36), ForeignKey("greenhouses.id"), nullable=True)
    firmware_version  = Column(String(50))
    ip_address        = Column(String(50))
    status            = Column(SAEnum(DeviceStatus), default=DeviceStatus.OFFLINE)
    last_seen         = Column(DateTime(timezone=True))
    # V9: 配置版本同步状态
    config_version_synced = Column(Integer, default=0)
    mapping_sync_status   = Column(SAEnum(MappingSyncStatus), default=MappingSyncStatus.PENDING)
    mapping_synced_at     = Column(DateTime(timezone=True))
    config            = Column(JSON, default={})
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), onupdate=func.now())

    greenhouse  = relationship("Greenhouse", back_populates="devices")
    telemetry   = relationship("Telemetry", back_populates="device")
    logs        = relationship("DeviceLog", back_populates="device")
    tasks       = relationship("RobotTask", back_populates="device")
    deployments = relationship("Deployment", back_populates="device")


# ── 遥测 ──────────────────────────────────────────────────────
class Telemetry(Base):
    __tablename__ = "telemetry"
    id              = Column(String(36), primary_key=True, default=gen_uuid)
    device_id       = Column(String(36), ForeignKey("devices.id"), nullable=False, index=True)
    greenhouse_id   = Column(String(36), ForeignKey("greenhouses.id"), nullable=True, index=True)
    timestamp       = Column(DateTime(timezone=True), nullable=False, index=True)
    msg_id          = Column(String(100), nullable=True, index=True)  # V9: 去重字段
    data_type       = Column(String(50))   # telemetry/heartbeat/state/image
    # 环境数据
    temperature     = Column(Float); humidity      = Column(Float)
    soil_moisture   = Column(Float); light_intensity = Column(Float)
    co2_level       = Column(Float); ph_value      = Column(Float)
    # 机器人状态
    battery_level   = Column(Float); position_x   = Column(Float); position_y = Column(Float)
    robot_status    = Column(JSON)
    # V9: 点位映射信息（来自 payload.navigation.point_mapping）
    chassis_point       = Column(String(50))   # 底盘点位，如 "A点"
    greenhouse_point    = Column(String(50))   # 大棚点位，如 "1号点"
    mapping_id          = Column(String(36))   # 映射关系ID
    raw_data            = Column(JSON)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    device      = relationship("Device",     back_populates="telemetry")
    greenhouse  = relationship("Greenhouse", back_populates="telemetry")


# ── 设备日志 ──────────────────────────────────────────────────
class DeviceLog(Base):
    __tablename__ = "device_logs"
    id        = Column(String(36), primary_key=True, default=gen_uuid)
    device_id = Column(String(36), ForeignKey("devices.id"), nullable=False, index=True)
    level     = Column(String(20), default="info")
    event     = Column(String(200)); message = Column(Text); meta_data = Column(JSON)
    created_at= Column(DateTime(timezone=True), server_default=func.now())
    device    = relationship("Device", back_populates="logs")


# ── 任务 ──────────────────────────────────────────────────────
class RobotTask(Base):
    __tablename__ = "robot_tasks"
    id            = Column(String(36), primary_key=True, default=gen_uuid)
    task_name     = Column(String(200), nullable=False)
    task_type     = Column(SAEnum(TaskType), default=TaskType.INSPECTION)
    greenhouse_id = Column(String(36), ForeignKey("greenhouses.id"), nullable=False)
    device_id     = Column(String(36), ForeignKey("devices.id"), nullable=True)
    status        = Column(SAEnum(TaskStatus), default=TaskStatus.PENDING)
    priority      = Column(Integer, default=5)
    scheduled_at  = Column(DateTime(timezone=True))
    started_at    = Column(DateTime(timezone=True))
    completed_at  = Column(DateTime(timezone=True))
    waypoints     = Column(JSON, default=[])
    actions       = Column(JSON, default=[])
    task_config   = Column(JSON, default={})
    result        = Column(JSON); error_message = Column(Text); progress = Column(Integer, default=0)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())
    greenhouse    = relationship("Greenhouse", back_populates="tasks")
    device        = relationship("Device",     back_populates="tasks")
    images        = relationship("Image",      back_populates="task")


# ── 图片 ──────────────────────────────────────────────────────
class Image(Base):
    __tablename__ = "images"
    id              = Column(String(36), primary_key=True, default=gen_uuid)
    device_id       = Column(String(36), ForeignKey("devices.id"), nullable=False)
    task_id         = Column(String(36), ForeignKey("robot_tasks.id"), nullable=True)
    greenhouse_id   = Column(String(36), ForeignKey("greenhouses.id"), nullable=True)
    filename        = Column(String(500)); storage_url = Column(String(1000), nullable=False)
    thumbnail_url   = Column(String(1000)); file_size = Column(Integer)
    position_x      = Column(Float); position_y = Column(Float)
    # V9: 点位信息
    chassis_point   = Column(String(50)); greenhouse_point = Column(String(50))
    ai_analysis     = Column(JSON); tags = Column(JSON, default=[])
    captured_at     = Column(DateTime(timezone=True))
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    task            = relationship("RobotTask", back_populates="images")


# ── V9 NEW: 巡检方案（Deployment）────────────────────────────
class Deployment(Base):
    """
    巡检方案：将机器人、大棚、点位、映射关系绑定为一个完整可下发的配置对象
    """
    __tablename__ = "deployments"
    id             = Column(String(36), primary_key=True, default=gen_uuid)
    name           = Column(String(200), nullable=False)
    device_id      = Column(String(36), ForeignKey("devices.id"), nullable=True)
    # 方案配置（JSON存储，含greenhouses/tasks/patrol参数）
    config         = Column(JSON, default={})
    status         = Column(SAEnum(DeploymentStatus), default=DeploymentStatus.DRAFT)
    version        = Column(Integer, default=1)           # 方案版本（修改递增）
    config_version = Column(Integer, default=1)           # 配置版本（发布递增）
    published_at   = Column(DateTime(timezone=True))
    created_by     = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), onupdate=func.now())

    device         = relationship("Device",      back_populates="deployments")
    point_mappings = relationship("PointMapping", back_populates="deployment",
                                  cascade="all, delete-orphan")


# ── V9 NEW: 点位映射（PointMapping）─────────────────────────
class PointMapping(Base):
    """
    底盘点位 ↔ 大棚点位 映射关系
    约束：同一底盘点位在同一Deployment中只能关联一个大棚点位
    """
    __tablename__ = "point_mappings"
    __table_args__ = (
        UniqueConstraint("deployment_id", "chassis_point",
                         name="uq_deployment_chassis_point"),
    )
    id                = Column(String(36), primary_key=True, default=gen_uuid)
    deployment_id     = Column(String(36), ForeignKey("deployments.id"), nullable=False, index=True)
    chassis_point     = Column(String(50), nullable=False)    # "A点"、"B点"
    greenhouse_id     = Column(String(36), ForeignKey("greenhouses.id"), nullable=False)
    greenhouse_point  = Column(String(50), nullable=False)    # "1号点"、"2号点"
    description       = Column(Text)
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), onupdate=func.now())

    deployment  = relationship("Deployment",  back_populates="point_mappings")
    greenhouse  = relationship("Greenhouse")
