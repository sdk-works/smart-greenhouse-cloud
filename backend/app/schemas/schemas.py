from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class DeviceTypeEnum(str, Enum):
    ROBOT      = "robot"
    SENSOR     = "sensor"
    IRRIGATION = "irrigation"
    FAN        = "fan"
    GROW_LIGHT = "grow_light"
    CURTAIN    = "curtain"
    FERTILIZER = "fertilizer"
    CAMERA     = "camera"


class DeviceStatusEnum(str, Enum):
    ONLINE      = "online"
    OFFLINE     = "offline"
    ERROR       = "error"
    MAINTENANCE = "maintenance"


class TaskStatusEnum(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"


class TaskTypeEnum(str, Enum):
    INSPECTION     = "inspection"
    WATERING       = "watering"
    FERTILIZING    = "fertilizing"
    PEST_DETECTION = "pest_detection"


# ── Greenhouse ────────────────────────────────────────
class GreenhouseCreate(BaseModel):
    name:        str
    location:    Optional[str]   = None
    area:        Optional[float] = None
    crop_type:   Optional[str]   = None
    description: Optional[str]   = None
    map_data:    Optional[Dict]  = None


class GreenhouseUpdate(BaseModel):
    name:        Optional[str]   = None
    location:    Optional[str]   = None
    area:        Optional[float] = None
    crop_type:   Optional[str]   = None
    description: Optional[str]   = None
    map_data:    Optional[Dict]  = None
    is_active:   Optional[bool]  = None


class GreenhouseResponse(BaseModel):
    id:          str
    name:        str
    location:    Optional[str]   = None
    area:        Optional[float] = None
    crop_type:   Optional[str]   = None
    description: Optional[str]   = None
    map_data:    Optional[Dict]  = None
    is_active:   bool
    created_at:  Optional[datetime] = None
    model_config = {"from_attributes": True}


# ── Device ────────────────────────────────────────────
class DeviceCreate(BaseModel):
    device_id:        str
    name:             str
    device_type:      DeviceTypeEnum
    greenhouse_id:    Optional[str]  = None
    firmware_version: Optional[str]  = None
    config:           Optional[Dict] = None


class DeviceUpdate(BaseModel):
    name:             Optional[str]            = None
    greenhouse_id:    Optional[str]            = None
    firmware_version: Optional[str]            = None
    config:           Optional[Dict]           = None
    status:           Optional[DeviceStatusEnum] = None


class DeviceResponse(BaseModel):
    id:               str
    device_id:        str
    name:             str
    device_type:      DeviceTypeEnum
    greenhouse_id:    Optional[str]  = None
    firmware_version: Optional[str]  = None
    status:           DeviceStatusEnum
    last_seen:        Optional[datetime] = None
    config:           Optional[Dict]     = None
    created_at:       Optional[datetime] = None
    is_online:        bool                = False
    mqtt_topics:      Optional[Dict]     = None  # v6 新增
    model_config = {"from_attributes": True}


# ── Telemetry ─────────────────────────────────────────
class TelemetryResponse(BaseModel):
    id:              str
    device_id:       str
    greenhouse_id:   Optional[str]   = None
    timestamp:       datetime                   # 始终带 tzinfo (UTC)
    data_type:       Optional[str]   = None
    temperature:     Optional[float] = None
    humidity:        Optional[float] = None
    soil_moisture:   Optional[float] = None
    light_intensity: Optional[float] = None
    co2_level:       Optional[float] = None
    battery_level:   Optional[float] = None
    position_x:      Optional[float] = None
    position_y:      Optional[float] = None
    robot_status:    Optional[Dict]  = None
    raw_data:        Optional[Dict]  = None
    created_at:      Optional[datetime] = None
    model_config = {"from_attributes": True}


# ── Task ──────────────────────────────────────────────
class Waypoint(BaseModel):
    x:       float
    y:       float
    z:       float = 0.0
    label:   Optional[str]   = None
    actions: List[str]       = []


class TaskAction(BaseModel):
    type:   str
    params: Optional[Dict] = None


class RobotTaskCreate(BaseModel):
    task_name:    str
    task_type:    TaskTypeEnum = TaskTypeEnum.INSPECTION
    greenhouse_id:str
    device_id:    Optional[str] = None
    priority:     int           = Field(default=5, ge=1, le=10)
    scheduled_at: Optional[datetime] = None
    waypoints:    List[Waypoint]    = []
    actions:      List[TaskAction]  = []
    task_config:  Optional[Dict]    = {}


class RobotTaskUpdate(BaseModel):
    task_name:     Optional[str]            = None
    status:        Optional[TaskStatusEnum] = None
    device_id:     Optional[str]            = None
    priority:      Optional[int]            = None
    progress:      Optional[int]            = None
    result:        Optional[Dict]           = None
    error_message: Optional[str]            = None


class RobotTaskResponse(BaseModel):
    id:            str
    task_name:     str
    task_type:     TaskTypeEnum
    greenhouse_id: str
    device_id:     Optional[str]         = None
    status:        TaskStatusEnum
    priority:      int
    scheduled_at:  Optional[datetime]    = None
    started_at:    Optional[datetime]    = None
    completed_at:  Optional[datetime]    = None
    waypoints:     Optional[List]        = None
    actions:       Optional[List]        = None
    progress:      int
    result:        Optional[Dict]        = None
    error_message: Optional[str]         = None
    created_at:    Optional[datetime]    = None
    model_config = {"from_attributes": True}


# ── Command ───────────────────────────────────────────
class DeviceCommand(BaseModel):
    command:  str
    task_id:  Optional[str] = None
    params:   Optional[Dict] = None
    task:     Optional[Dict] = None


# ── Device Log ────────────────────────────────────────
class DeviceLogResponse(BaseModel):
    id:        str
    device_id: str
    level:     str
    event:     Optional[str] = None
    message:   Optional[str] = None
    meta_data: Optional[Dict] = None
    created_at:Optional[datetime] = None
    model_config = {"from_attributes": True}


# ── Image ─────────────────────────────────────────────
class ImageResponse(BaseModel):
    id:            str
    device_id:     str
    task_id:       Optional[str]   = None
    greenhouse_id: Optional[str]   = None
    filename:      Optional[str]   = None
    storage_url:   str
    thumbnail_url: Optional[str]   = None
    file_size:     Optional[int]   = None
    position_x:    Optional[float] = None
    position_y:    Optional[float] = None
    tags:          Optional[List]  = None
    captured_at:   Optional[datetime] = None
    created_at:    Optional[datetime] = None
    model_config = {"from_attributes": True}
