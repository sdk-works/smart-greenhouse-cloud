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
    """M2 巡检任务状态机：流转规则见 app/core/task_state.py。"""
    PENDING = "pending"; RUNNING = "running"; PAUSED = "paused"
    COMPLETED = "completed"; PARTIAL = "partial"
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

class ControlMode(str, enum.Enum):
    MANUAL = "manual"; AUTO = "auto"; MAINTENANCE = "maintenance"

class CommandStatus(str, enum.Enum):
    PENDING = "pending"; SENT = "sent"; ACCEPTED = "accepted"
    EXECUTING = "executing"; EXECUTED = "executed"; REJECTED = "rejected"
    TIMEOUT = "timeout"; FAILED = "failed"; CANCELLED = "cancelled"


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
    company_id = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    is_platform_operator = Column(Boolean, default=False, nullable=False)
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
    company_id  = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    base_id     = Column(String(36), ForeignKey("bases.id"), nullable=True, index=True)
    coord_unit  = Column(String(20), default="schematic", nullable=False)
    schema_version = Column(Integer, default=2, nullable=False)
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
    control_profile = relationship("ActuatorProfile", back_populates="device", uselist=False)
    actuator_commands = relationship("ActuatorCommand", back_populates="device")


class ActuatorProfile(Base):
    """风机/水阀运行状态与安全边界。"""
    __tablename__ = "actuator_profiles"
    device_id       = Column(String(36), ForeignKey("devices.id"), primary_key=True)
    zone_id         = Column(String(100), nullable=True, index=True)
    control_mode    = Column(SAEnum(ControlMode), default=ControlMode.MANUAL, nullable=False)
    desired_state   = Column(JSON, default={})
    actual_state    = Column(JSON, default={})
    safety_limits   = Column(JSON, default={})
    emergency_stop  = Column(Boolean, default=False, nullable=False)
    maintenance_lock= Column(Boolean, default=False, nullable=False)
    last_command_id = Column(String(100), nullable=True)
    state_updated_at= Column(DateTime(timezone=True))
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())
    device          = relationship("Device", back_populates="control_profile")


class ActuatorCommand(Base):
    """先落库、后发送、等待设备回执的可审计命令。"""
    __tablename__ = "actuator_commands"
    id             = Column(String(36), primary_key=True, default=gen_uuid)
    command_id     = Column(String(100), unique=True, nullable=False, index=True)
    device_id      = Column(String(36), ForeignKey("devices.id"), nullable=False, index=True)
    greenhouse_id  = Column(String(36), ForeignKey("greenhouses.id"), nullable=True, index=True)
    action          = Column(String(50), nullable=False)
    params          = Column(JSON, default={})
    source          = Column(String(30), default="manual", nullable=False)
    reason          = Column(Text)
    status          = Column(SAEnum(CommandStatus), default=CommandStatus.PENDING, nullable=False, index=True)
    desired_state   = Column(JSON, default={})
    actual_state    = Column(JSON, default={})
    issued_by       = Column(String(36), ForeignKey("users.id"), nullable=True)
    issued_at       = Column(DateTime(timezone=True), server_default=func.now())
    expires_at      = Column(DateTime(timezone=True), nullable=False)
    accepted_at     = Column(DateTime(timezone=True))
    completed_at    = Column(DateTime(timezone=True))
    error_message   = Column(Text)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())
    device          = relationship("Device", back_populates="actuator_commands")


class ControlRule(Base):
    """自动控制规则配置；V1 只保存与校验，不自动执行。"""
    __tablename__ = "control_rules"
    id             = Column(String(36), primary_key=True, default=gen_uuid)
    name           = Column(String(200), nullable=False)
    greenhouse_id  = Column(String(36), ForeignKey("greenhouses.id"), nullable=False, index=True)
    device_id      = Column(String(36), ForeignKey("devices.id"), nullable=False, index=True)
    enabled        = Column(Boolean, default=False, nullable=False)
    metric         = Column(String(50), nullable=False)
    start_operator = Column(String(5), nullable=False)
    start_threshold= Column(Float, nullable=False)
    stop_threshold = Column(Float, nullable=False)
    duration_seconds=Column(Integer, default=60, nullable=False)
    cooldown_seconds=Column(Integer, default=300, nullable=False)
    priority       = Column(Integer, default=5, nullable=False)
    action         = Column(String(50), nullable=False)
    action_params  = Column(JSON, default={})
    created_by     = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), onupdate=func.now())


# ── 遥测 ──────────────────────────────────────────────────────
class Telemetry(Base):
    __tablename__ = "telemetry"
    id              = Column(String(36), primary_key=True, default=gen_uuid)
    device_id       = Column(String(36), ForeignKey("devices.id"), nullable=False, index=True)
    greenhouse_id   = Column(String(36), ForeignKey("greenhouses.id"), nullable=True, index=True)
    timestamp       = Column(DateTime(timezone=True), nullable=False, index=True)
    sampled_at      = Column(DateTime(timezone=True), nullable=True)  # 契约v1.2 §5: 采样时间，未知为NULL
    time_quality    = Column(Integer, default=1)                      # 0=无可信时间，1=有
    msg_id          = Column(String(100), nullable=True, index=True)  # V9: 去重字段
    data_type       = Column(String(50))   # telemetry/heartbeat/state/image
    # 环境数据
    temperature     = Column(Float); humidity      = Column(Float)
    soil_moisture   = Column(Float); soil_temperature = Column(Float)
    soil_ec         = Column(Float); ph_value      = Column(Float)
    soil_nitrogen   = Column(Float); soil_phosphorus = Column(Float)
    soil_potassium  = Column(Float); sample_count  = Column(Integer)
    light_intensity = Column(Float); co2_level     = Column(Float)
    task_id         = Column(String(100), nullable=True, index=True)
    # 机器人状态
    battery_level   = Column(Float); position_x   = Column(Float); position_y = Column(Float)
    robot_status    = Column(JSON)
    # V9: 点位映射信息（来自 payload.navigation.point_mapping）
    chassis_point       = Column(String(50))   # 底盘点位，如 "A点"
    greenhouse_point    = Column(String(50))   # 大棚点位，如 "1号点"
    mapping_id          = Column(String(36))   # 映射关系ID
    raw_data            = Column(JSON)
    company_id          = Column(String(36), nullable=True, index=True)
    position_quality    = Column(String(20))
    attribution_status  = Column(String(20))
    # M3：归属判定依据（binding_window / no_binding_history / outside_window ...）
    attribution_reason  = Column(String(40))
    binding_id          = Column(String(36))
    binding_version     = Column(Integer)
    map_version_id      = Column(String(36))
    raw_message_id      = Column(String(36))
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
    # M2：状态改为普通字符串列（流转规则由 app/core/task_state.py 在代码层强制），
    # 避免每次新增状态都要 ALTER TYPE（原生枚举不能在事务内加值）。
    status        = Column(String(20), default=TaskStatus.PENDING.value,
                           nullable=False, index=True)
    priority      = Column(Integer, default=5)
    scheduled_at  = Column(DateTime(timezone=True))
    started_at    = Column(DateTime(timezone=True))
    completed_at  = Column(DateTime(timezone=True))
    waypoints     = Column(JSON, default=[])
    actions       = Column(JSON, default=[])
    task_config   = Column(JSON, default={})
    result        = Column(JSON); error_message = Column(Text); progress = Column(Integer, default=0)
    # M2：创建时快照（地图版本 / 点位列表 / 采集参数），后续编辑不改写
    snapshot      = Column(JSON)
    map_version_id = Column(String(36), ForeignKey("map_versions.id"), nullable=True)
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
    # M2：照片归档（能解析才写，解析不出留 NULL，不猜）
    point_id        = Column(String(36), ForeignKey("inspection_points.id"), nullable=True, index=True)
    map_version_id  = Column(String(36), ForeignKey("map_versions.id"), nullable=True)
    batch_id        = Column(String(36), ForeignKey("inspection_batches.id"), nullable=True)
    company_id      = Column(String(36), nullable=True, index=True)
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


class NavPointDiscovery(Base):
    __tablename__ = "nav_point_discoveries"
    __table_args__ = (UniqueConstraint("robot_id", "nav_point_code", name="uq_nav_point_discovery"),)
    id = Column(String(36), primary_key=True, default=gen_uuid)
    robot_id = Column(String(100), nullable=False, index=True)
    nav_point_code = Column(String(64), nullable=False)
    first_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)
    seen_count = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="unassigned")
    raw_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class RobotPointConfig(Base):
    __tablename__ = "robot_point_configs"
    __table_args__ = (UniqueConstraint("robot_id", "nav_point_code", name="uq_robot_point_config"),)
    id = Column(String(36), primary_key=True, default=gen_uuid)
    robot_id = Column(String(100), nullable=False, index=True)
    nav_point_code = Column(String(64), nullable=False)
    greenhouse_id = Column(String(36), ForeignKey("greenhouses.id"), nullable=False)
    point_name = Column(String(100), nullable=False)
    point_id = Column(String(36), ForeignKey("inspection_points.id"), nullable=True, index=True)
    robot_map_version = Column(String(64), nullable=True)
    sample_seconds = Column(Integer, nullable=False, default=10)
    collect_soil = Column(Boolean, nullable=False, default=True)
    collect_environment = Column(Boolean, nullable=False, default=True)
    take_photo = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)
    note = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    greenhouse = relationship("Greenhouse")


class RobotPointConfigRelease(Base):
    __tablename__ = "robot_point_config_releases"
    __table_args__ = (UniqueConstraint("robot_id", "config_version", name="uq_robot_point_release"),)
    id = Column(String(36), primary_key=True, default=gen_uuid)
    robot_id = Column(String(100), nullable=False, index=True)
    config_version = Column(Integer, nullable=False)
    mode = Column(String(20), nullable=False)
    config_sha256 = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    sync_status = Column(String(20), nullable=False, default="pending")
    sync_detail = Column(JSON)
    published_at = Column(DateTime(timezone=True), server_default=func.now())
    applied_at = Column(DateTime(timezone=True))


class InspectionBatch(Base):
    """A cloud-side grouping inferred from arrival/telemetry events.

    It deliberately does not claim control of, or knowledge about, the vendor
    navigation route.  A route label can be added later by an operator.
    """
    __tablename__ = "inspection_batches"
    id = Column(String(36), primary_key=True, default=gen_uuid)
    robot_id = Column(String(100), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="running", index=True)
    source = Column(String(30), nullable=False, default="auto_event")
    navigation_route_label = Column(String(200), nullable=True)
    route_identification = Column(String(30), nullable=False, default="unknown")
    config_version = Column(Integer, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    last_event_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    close_reason = Column(String(100), nullable=True)
    summary = Column(JSON, default={})
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    visits = relationship("InspectionVisit", back_populates="batch",
                          cascade="all, delete-orphan")


class InspectionVisit(Base):
    """One independently useful physical-point visit within a batch."""
    __tablename__ = "inspection_visits"
    __table_args__ = (
        UniqueConstraint("robot_id", "event_id", name="uq_inspection_visit_event"),
    )
    id = Column(String(36), primary_key=True, default=gen_uuid)
    batch_id = Column(String(36), ForeignKey("inspection_batches.id"),
                      nullable=False, index=True)
    robot_id = Column(String(100), nullable=False, index=True)
    event_id = Column(String(100), nullable=False)
    task_id = Column(String(100), nullable=True, index=True)
    nav_point_code = Column(String(64), nullable=False, index=True)
    greenhouse_id = Column(String(36), ForeignKey("greenhouses.id"), nullable=True)
    point_name = Column(String(100), nullable=True)
    mapping_id = Column(String(36), nullable=True)
    config_version = Column(Integer, nullable=True)
    status = Column(String(30), nullable=False, default="arrived")
    arrived_at = Column(DateTime(timezone=True), nullable=False)
    sampled_at = Column(DateTime(timezone=True), nullable=True)
    telemetry_id = Column(String(36), ForeignKey("telemetry.id"), nullable=True)
    error_code = Column(String(100), nullable=True)
    raw_event = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    batch = relationship("InspectionBatch", back_populates="visits")


# ── 第一批：公司 / 基地 / 稳定点位 / 机器人映射 / 绑定历史 / 地图版本 / 原始消息 ──
class Company(Base):
    __tablename__ = "companies"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    name       = Column(String(100), nullable=False)
    status     = Column(String(20), default="active", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SiteBase(Base):
    """基地（避免与 SQLAlchemy Base 同名，类名用 SiteBase）。"""
    __tablename__ = "bases"
    id         = Column(String(36), primary_key=True, default=gen_uuid)
    company_id = Column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    name       = Column(String(100), nullable=False)
    location   = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InspectionPoint(Base):
    """稳定巡检点位，独立于机器人。"""
    __tablename__ = "inspection_points"
    __table_args__ = (UniqueConstraint("greenhouse_id", "code", name="uq_inspection_point_code"),)
    id                = Column(String(36), primary_key=True, default=gen_uuid)
    greenhouse_id     = Column(String(36), ForeignKey("greenhouses.id"), nullable=False, index=True)
    code              = Column(String(64), nullable=False)
    name              = Column(String(100), nullable=False)
    zone              = Column(String(100))
    x                 = Column(Float)
    y                 = Column(Float)
    coord_unit        = Column(String(20), default="schematic", nullable=False)
    map_edit_revision = Column(Integer, default=0, nullable=False)
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    retired_at        = Column(DateTime(timezone=True))


class RobotPointMapping(Base):
    """机器人导航映射：point_id ↔ (robot_id, nav_point_code)，支持一点多机器人。"""
    __tablename__ = "robot_point_mappings"
    id                  = Column(String(36), primary_key=True, default=gen_uuid)
    robot_id            = Column(String(100), nullable=False, index=True)
    robot_map_version   = Column(String(64))
    nav_point_code      = Column(String(64), nullable=False, index=True)
    point_id            = Column(String(36), ForeignKey("inspection_points.id"), nullable=False, index=True)
    greenhouse_id       = Column(String(36), ForeignKey("greenhouses.id"), nullable=False, index=True)
    sample_seconds      = Column(Integer, default=10, nullable=False)
    collect_soil        = Column(Boolean, default=True, nullable=False)
    collect_environment = Column(Boolean, default=True, nullable=False)
    take_photo          = Column(Boolean, default=True, nullable=False)
    enabled             = Column(Boolean, default=True, nullable=False)
    note                = Column(Text)
    effective_from      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    effective_to        = Column(DateTime(timezone=True))
    status              = Column(String(20), default="active", nullable=False)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())


class DeviceBinding(Base):
    """设备安装/绑定历史（固定设备）。"""
    __tablename__ = "device_bindings"
    id              = Column(String(36), primary_key=True, default=gen_uuid)
    device_id       = Column(String(36), ForeignKey("devices.id"), nullable=False, index=True)
    greenhouse_id   = Column(String(36), ForeignKey("greenhouses.id"), nullable=False)
    point_id        = Column(String(36), ForeignKey("inspection_points.id"), nullable=True)
    channel         = Column(String(50), default="default", nullable=False)
    binding_version = Column(Integer, default=1, nullable=False)
    start_at        = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    end_at          = Column(DateTime(timezone=True))
    created_at      = Column(DateTime(timezone=True), server_default=func.now())


class MapVersion(Base):
    """已发布地图版本（不可变）。"""
    __tablename__ = "map_versions"
    __table_args__ = (UniqueConstraint("greenhouse_id", "version", name="uq_map_version"),)
    id              = Column(String(36), primary_key=True, default=gen_uuid)
    greenhouse_id   = Column(String(36), ForeignKey("greenhouses.id"), nullable=False, index=True)
    version         = Column(Integer, nullable=False)
    layout          = Column(JSON, nullable=False)
    defaults        = Column(JSON, nullable=False)
    points_snapshot = Column(JSON, nullable=False)
    schema_version  = Column(Integer, default=2, nullable=False)
    published_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    published_by    = Column(String(36))
    immutable       = Column(Boolean, default=True, nullable=False)


# ── 第二批：巡检证据 / 机器人地图版本登记与核对 ─────────────────
class InspectionEvidence(Base):
    """巡检证据：一次点位采集留存的媒体与上下文，独立于事后编辑。"""
    __tablename__ = "inspection_evidence"
    id                = Column(String(36), primary_key=True, default=gen_uuid)
    task_id           = Column(String(36), ForeignKey("robot_tasks.id"), nullable=True, index=True)
    batch_id          = Column(String(36), ForeignKey("inspection_batches.id"), nullable=True)
    point_id          = Column(String(36), ForeignKey("inspection_points.id"), nullable=True, index=True)
    greenhouse_id     = Column(String(36), ForeignKey("greenhouses.id"), nullable=True, index=True)
    robot_id          = Column(String(100), nullable=True, index=True)
    nav_point_code    = Column(String(64), nullable=True)
    map_version_id    = Column(String(36), ForeignKey("map_versions.id"), nullable=True)
    evidence_type     = Column(String(30), nullable=False, default="photo")
    # 归属解析结果：resolved / pending（不确定时不猜，直接留 pending）
    attribution_status = Column(String(20), nullable=False, default="pending")
    media             = Column(JSON, nullable=False, default={})
    captured_at       = Column(DateTime(timezone=True))
    company_id        = Column(String(36), nullable=True, index=True)
    created_at        = Column(DateTime(timezone=True), server_default=func.now())


class RobotMapRegistry(Base):
    """平台侧登记的机器人地图版本（期望值）。"""
    __tablename__ = "robot_map_registry"
    id             = Column(String(36), primary_key=True, default=gen_uuid)
    robot_id       = Column(String(100), nullable=False, index=True)
    greenhouse_id  = Column(String(36), ForeignKey("greenhouses.id"), nullable=True)
    map_version    = Column(String(64), nullable=False)
    source         = Column(String(30), nullable=False, default="platform")
    registered_by  = Column(String(36), ForeignKey("users.id"), nullable=True)
    note           = Column(Text)
    active         = Column(Boolean, default=True, nullable=False)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())


class RobotMapAlert(Base):
    """机器人回传地图版本与平台登记不一致时产生的告警记录（只记录，不自动改）。"""
    __tablename__ = "robot_map_alerts"
    id                    = Column(String(36), primary_key=True, default=gen_uuid)
    robot_id              = Column(String(100), nullable=False, index=True)
    expected_map_version  = Column(String(64), nullable=True)
    reported_map_version  = Column(String(64), nullable=True)
    source                = Column(String(30), nullable=False, default="config_ack")
    status                = Column(String(20), nullable=False, default="open", index=True)
    detail                = Column(JSON)
    created_at            = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at           = Column(DateTime(timezone=True))


# ── 第三批：异常事件（告警 / 事件单） ─────────────────────────
class Incident(Base):
    """异常事件单。告警"停了"≠ 事件"关了"，两者刻意分开。"""
    __tablename__ = "incidents"
    id            = Column(String(36), primary_key=True, default=gen_uuid)
    code          = Column(String(40), nullable=False, index=True)
    title         = Column(String(200), nullable=False)
    category      = Column(String(40), nullable=False, default="environment")
    severity      = Column(String(20), nullable=False, default="warning")
    status        = Column(String(20), nullable=False, default="pending_confirm", index=True)
    source        = Column(String(30), nullable=False, default="auto")
    greenhouse_id = Column(String(36), ForeignKey("greenhouses.id"), nullable=True, index=True)
    device_id     = Column(String(36), ForeignKey("devices.id"), nullable=True, index=True)
    company_id    = Column(String(36), nullable=True, index=True)
    detected_at   = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    payload       = Column(JSON, default={})
    note          = Column(Text)
    created_by    = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())


class IncidentEvent(Base):
    """事件单状态流转历史（每次流转留痕）。"""
    __tablename__ = "incident_events"
    id          = Column(String(36), primary_key=True, default=gen_uuid)
    incident_id = Column(String(36), ForeignKey("incidents.id"), nullable=False, index=True)
    from_status = Column(String(20))
    to_status   = Column(String(20), nullable=False)
    actor_id    = Column(String(36), ForeignKey("users.id"), nullable=True)
    note        = Column(Text)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


class Alert(Base):
    """告警（可因环境恢复而停止，但对应事件单仍需人工关闭）。"""
    __tablename__ = "alerts"
    id            = Column(String(36), primary_key=True, default=gen_uuid)
    incident_id   = Column(String(36), ForeignKey("incidents.id"), nullable=True, index=True)
    rule_code     = Column(String(60), nullable=False)
    metric        = Column(String(50))
    value         = Column(Float)
    threshold     = Column(Float)
    greenhouse_id = Column(String(36), ForeignKey("greenhouses.id"), nullable=True, index=True)
    device_id     = Column(String(36), ForeignKey("devices.id"), nullable=True)
    company_id    = Column(String(36), nullable=True, index=True)
    raised_at     = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    cleared_at    = Column(DateTime(timezone=True))
    clear_reason  = Column(String(40))
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


# ── 第四批：种植批次 / 指标定义 / AI 建议留痕 ─────────────────
class PlantingBatch(Base):
    """种植批次：一块地（大棚/分区）一茬作物的起止。"""
    __tablename__ = "planting_batches"
    id           = Column(String(36), primary_key=True, default=gen_uuid)
    code         = Column(String(60), nullable=False, index=True)
    greenhouse_id = Column(String(36), ForeignKey("greenhouses.id"), nullable=False, index=True)
    zone         = Column(String(100))
    crop_type    = Column(String(100), nullable=False)
    variety      = Column(String(100))
    area         = Column(Float)
    status       = Column(String(20), nullable=False, default="planned", index=True)
    planted_at   = Column(DateTime(timezone=True))
    expected_harvest_at = Column(DateTime(timezone=True))
    harvested_at = Column(DateTime(timezone=True))
    note         = Column(Text)
    company_id   = Column(String(36), nullable=True, index=True)
    created_by   = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())


class MetricDefinition(Base):
    """指标定义最小集：编码 / 单位 / 类型 / 合理区间 / 质量规则。"""
    __tablename__ = "metric_definitions"
    __table_args__ = (UniqueConstraint("code", name="uq_metric_definition_code"),)
    id           = Column(String(36), primary_key=True, default=gen_uuid)
    code         = Column(String(60), nullable=False)
    name         = Column(String(100), nullable=False)
    unit         = Column(String(20))
    value_type   = Column(String(20), nullable=False, default="float")
    min_value    = Column(Float)
    max_value    = Column(Float)
    precision    = Column(Integer, default=2)
    source       = Column(String(30), default="sensor", nullable=False)
    quality_rules = Column(JSON, default={})
    enabled      = Column(Boolean, default=True, nullable=False)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())


class AiAdviceLog(Base):
    """AI 建议留痕：AI 只产出建议，不直接操作设备。"""
    __tablename__ = "ai_advice_logs"
    id            = Column(String(36), primary_key=True, default=gen_uuid)
    greenhouse_id = Column(String(36), ForeignKey("greenhouses.id"), nullable=True, index=True)
    kind          = Column(String(40), nullable=False, default="advice")
    request       = Column(JSON, default={})
    advice        = Column(JSON, default=[])
    basis         = Column(JSON, default={})
    # 受控标记：本次生成是否碰过数据库/是否发过控制指令
    db_access_used     = Column(Boolean, default=False, nullable=False)
    control_issued     = Column(Boolean, default=False, nullable=False)
    requested_by  = Column(String(36), ForeignKey("users.id"), nullable=True)
    company_id    = Column(String(36), nullable=True, index=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


class RawMessage(Base):
    """原始报文（可追溯/可重放）。"""
    __tablename__ = "raw_messages"
    id             = Column(String(36), primary_key=True, default=gen_uuid)
    msg_id         = Column(String(100), index=True)
    source         = Column(String(30), nullable=False)
    device_id      = Column(String(100), index=True)
    received_at    = Column(DateTime(timezone=True), nullable=False)
    payload        = Column(JSON)
    payload_bytes  = Column(Integer)
    parse_status   = Column(String(20), default="stored", nullable=False)
    parser_version = Column(String(20))
    error          = Column(Text)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())


class MqttCredential(Base):
    """MQTT 设备凭据（设备入网闭环的核心表）。

    表 `mqtt_credentials` 在生产已存在（2026-10-22 手工建），006 迁移补齐入网所需字段。
    对应关系：
        username  ← EMQX 认证用户名（设备侧 MQTT user）
        clientid  ← 设备侧唯一允许使用的 Client ID（DTU 模块序列号）
        device_id ← 关联 `devices.device_id`（字符串形态的身份，如 JY001）
        asset_no  ← DTU 资产编号（贴在设备壳上的那个号）
        compat_identity ← 兼容身份：老的上行报文里用的 device_id，便于并轨期识别
    username 与 clientid 都唯一 ⇒ 二者天然一对一绑定。
    """
    __tablename__ = "mqtt_credentials"
    username        = Column(String(100), primary_key=True)
    password_hash   = Column(Text, nullable=False)
    clientid        = Column(String(100), nullable=False, unique=True)
    device_id       = Column(String(100), nullable=False, unique=True, index=True)
    enabled         = Column(Boolean, nullable=False, default=True, index=True)
    note            = Column(Text)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    asset_no        = Column(String(64))
    compat_identity = Column(String(100))
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())
    disabled_at     = Column(DateTime(timezone=True))
    last_rotated_at = Column(DateTime(timezone=True))
    rotation_count  = Column(Integer, nullable=False, default=0)
    created_by      = Column(String(36))
    updated_by      = Column(String(36))
