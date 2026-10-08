"""Greenhouse diagrams and preplanned sample points. No motion commands."""
from copy import deepcopy
from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict, model_validator
from sqlalchemy import Column, String, Integer, JSON, ForeignKey, DateTime, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base, get_db
from app.api.auth import get_current_user, check_permission
from app.models.models import (Greenhouse, User, RobotPointConfig, NavPointDiscovery,
                               RobotPointConfigRelease, InspectionVisit)

router = APIRouter()
FIELDS = ('point_name', 'sample_seconds', 'collect_soil', 'collect_environment',
          'take_photo', 'enabled', 'note')


class PointMap(Base):
    __tablename__ = 'greenhouse_point_maps'
    greenhouse_id = Column(String(36), ForeignKey('greenhouses.id'), primary_key=True)
    revision = Column(Integer, nullable=False, default=1)
    data = Column(JSON, nullable=False)


class PointBindingArchive(Base):
    __tablename__ = 'point_binding_archive'
    mapping_id = Column(String(36), primary_key=True)
    greenhouse_id = Column(String(36), ForeignKey('greenhouses.id'), nullable=False, index=True)
    point_id = Column(String(36), nullable=False, index=True)
    robot_id = Column(String(100), nullable=False)
    snapshot = Column(JSON, nullable=False)


class PointMapRevision(Base):
    __tablename__ = 'point_map_revisions'
    greenhouse_id = Column(String(36), ForeignKey('greenhouses.id'), primary_key=True)
    revision = Column(Integer, primary_key=True)
    data = Column(JSON, nullable=False)
    saved_at = Column(DateTime(timezone=True), nullable=False)


async def archive_map(db, row):
    if not row:
        return
    if not await db.get(PointMapRevision, (row.greenhouse_id, row.revision)):
        db.add(PointMapRevision(greenhouse_id=row.greenhouse_id, revision=row.revision,
            data=deepcopy(row.data), saved_at=datetime.now(timezone.utc)))
    for point in row.data.get('points', []):
        b = point.get('binding') or {}
        if b.get('mapping_id') and not await db.get(PointBindingArchive, b['mapping_id']):
            db.add(PointBindingArchive(mapping_id=b['mapping_id'], greenhouse_id=row.greenhouse_id,
                point_id=point['id'], robot_id=b['robot_id'], snapshot=deepcopy(point)))
    await db.flush()


class Options(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    sample_seconds: int = Field(default=20, ge=1, le=120)
    collect_soil: bool = True
    collect_environment: bool = True
    take_photo: bool = True
    enabled: bool = True


class PlannedPoint(Options):
    id: UUID
    point_name: str = Field(min_length=1, max_length=100)
    zone: str = Field(default='', max_length=100)
    x: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    y: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    note: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode='after')
    def coordinates(self):
        if (self.x is None) != (self.y is None):
            raise ValueError('请同时设置两个坐标，或都留空')
        return self


class Layout(BaseModel):
    model_config = ConfigDict(extra='forbid')
    template: Literal['solar', 'glass', 'polycarbonate', 'blank'] = 'solar'
    width: float = Field(default=80, ge=1, le=2000, allow_inf_nan=False)
    height: float = Field(default=10, ge=1, le=2000, allow_inf_nan=False)
    calibrated: bool = False
    aisle_side: Literal['north', 'south', 'east', 'west', 'center'] = 'north'
    aisle_width: float = Field(default=1.5, ge=0.3, le=20, allow_inf_nan=False)
    bays: int = Field(default=4, ge=1, le=30)
    length_axis: Literal['ew', 'ns'] = 'ew'
    planting_direction: Literal['ew', 'ns'] = 'ns'
    collection_lane_count: int = Field(default=0, ge=0, le=50)
    collection_direction: Literal['ew', 'ns'] = 'ns'

    @model_validator(mode='after')
    def aisle(self):
        if self.aisle_width >= (self.width if self.aisle_side in ('east', 'west') else self.height):
            raise ValueError('主通道宽度必须小于所在方向的大棚尺寸')
        return self


class SaveMap(BaseModel):
    revision: int = Field(ge=0)
    layout: Layout
    defaults: Options
    next_name: str = Field(default='A1', min_length=1, max_length=100)
    points: list[PlannedPoint] = Field(max_length=500)

    @model_validator(mode='after')
    def unique_points(self):
        if len({p.id for p in self.points}) != len(self.points):
            raise ValueError('点位ID重复')
        if len({p.point_name for p in self.points}) != len(self.points):
            raise ValueError('同一大棚的点位名称不能重复')
        if any(p.x is not None and (p.x > self.layout.width or p.y > self.layout.height)
               for p in self.points):
            raise ValueError('点位超出大棚边界，请先调整位置')
        return self


class Binding(BaseModel):
    revision: int = Field(ge=1)
    robot_id: str = Field(min_length=1, max_length=100)
    nav_point_code: Optional[str] = Field(default=None, min_length=1, max_length=64)


def empty_map():
    return {'layout': Layout().model_dump(), 'defaults': Options().model_dump(),
            'next_name': 'A1', 'points': []}


async def lock_maps(db):
    # Serialize small editor transactions, including bindings across greenhouses.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('greenhouse-point-map-v1'))"))


async def guard_legacy(db, robot_id, code=None):
    await lock_maps(db)
    maps = (await db.execute(select(PointMap))).scalars().all()
    for row in maps:
        for p in row.data.get('points', []):
            b = p.get('binding') or {}
            if b.get('robot_id') == robot_id and (code is None or b.get('nav_point_code') == code):
                raise HTTPException(409, '该编号已关联图上采集点，请在图上管理中修改或先解除关联')


async def _greenhouse(db, gh_id):
    gh = await db.get(Greenhouse, gh_id)
    if not gh or not gh.is_active:
        raise HTTPException(404, '大棚不存在或已停用')
    return gh


@router.get('/maps/{gh_id}')
async def get_map(gh_id: str, db: AsyncSession = Depends(get_db),
                  user: User = Depends(get_current_user)):
    await _greenhouse(db, gh_id)
    row = await db.get(PointMap, gh_id)
    return {'greenhouse_id': gh_id, 'revision': row.revision if row else 0,
            **(row.data if row else empty_map())}


@router.put('/maps/{gh_id}')
async def save_map(gh_id: str, body: SaveMap, db: AsyncSession = Depends(get_db),
                   user: User = Depends(get_current_user)):
    check_permission(user, 'edit_point_mapping')
    await lock_maps(db)
    await _greenhouse(db, gh_id)
    row = await db.get(PointMap, gh_id)
    if body.revision != (row.revision if row else 0):
        raise HTTPException(409, '点位图已被其他操作更新，请重新加载后再保存')
    await archive_map(db, row)
    old = {p['id']: p for p in row.data['points']} if row else {}
    ids = {str(p.id) for p in body.points}
    if any(p.get('binding') and key not in ids for key, p in old.items()):
        raise HTTPException(409, '请先解除导航关联，再删除点位')
    archived = (await db.execute(select(PointBindingArchive.point_id).where(
        PointBindingArchive.greenhouse_id == gh_id))).scalars().all()
    if any(pid in old and pid not in ids for pid in archived):
        raise HTTPException(409, '该点位有导航关联历史，请停用保留，避免历史入口丢失')
    data = body.model_dump(mode='json', exclude={'revision'})
    now = datetime.now(timezone.utc).isoformat()
    for p in data['points']:
        previous = old.get(p['id'], {})
        p['binding'] = previous.get('binding')
        moved = (p['x'], p['y']) != (previous.get('x'), previous.get('y'))
        p['position_updated_at'] = now if moved else previous.get('position_updated_at')
        if p['binding']:
            cfg = await db.get(RobotPointConfig, p['binding']['mapping_id'])
            if not cfg:
                raise HTTPException(409, '导航关联已改变，请重新加载检查')
            for field in FIELDS:
                setattr(cfg, field, p[field])
    if row is None:
        row = PointMap(greenhouse_id=gh_id, revision=0, data={})
        db.add(row)
    row.data = data
    row.revision += 1
    await archive_map(db, row)
    await db.commit()
    return {'greenhouse_id': gh_id, 'revision': row.revision, **data}


@router.put('/maps/{gh_id}/points/{point_id}/binding')
async def bind(gh_id: str, point_id: str, body: Binding,
               db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    check_permission(user, 'edit_point_mapping')
    await lock_maps(db)
    await _greenhouse(db, gh_id)
    row = await db.get(PointMap, gh_id)
    if not row or row.revision != body.revision:
        raise HTTPException(409, '点位图版本已改变，请重新加载')
    await archive_map(db, row)
    data = deepcopy(row.data)
    point = next((p for p in data['points'] if p['id'] == point_id), None)
    if not point:
        raise HTTPException(404, '采集点不存在')
    binding = point.get('binding')
    if body.nav_point_code is None:
        if binding and binding['robot_id'] != body.robot_id:
            raise HTTPException(409, '机器人与原关联不一致')
        if binding:
            cfg = await db.get(RobotPointConfig, binding['mapping_id'])
            if cfg:
                await db.delete(cfg)
            disc = (await db.execute(select(NavPointDiscovery).where(
                NavPointDiscovery.robot_id == body.robot_id,
                NavPointDiscovery.nav_point_code == binding['nav_point_code']))).scalar_one_or_none()
            if disc:
                disc.status = 'unassigned'
        point['binding'] = None
    else:
        if binding:
            raise HTTPException(409, '此采集点已有导航关联，请先解除原关联')
        disc = (await db.execute(select(NavPointDiscovery).where(
            NavPointDiscovery.robot_id == body.robot_id,
            NavPointDiscovery.nav_point_code == body.nav_point_code))).scalar_one_or_none()
        if not disc:
            raise HTTPException(400, '请先监听收到该导航编号，再关联')
        cfg = (await db.execute(select(RobotPointConfig).where(
            RobotPointConfig.robot_id == body.robot_id,
            RobotPointConfig.nav_point_code == body.nav_point_code))).scalar_one_or_none()
        if cfg:
            raise HTTPException(409, f'导航编号已分配给 {cfg.point_name}，请先解除原关联')
        cfg = RobotPointConfig(robot_id=body.robot_id, nav_point_code=body.nav_point_code,
                               greenhouse_id=gh_id, **{f: point[f] for f in FIELDS})
        db.add(cfg)
        await db.flush()
        disc.status = 'assigned'
        point['binding'] = {'robot_id': body.robot_id, 'nav_point_code': body.nav_point_code,
                            'mapping_id': cfg.id,
                            'bound_at': datetime.now(timezone.utc).isoformat()}
    row.data = data
    row.revision += 1
    await archive_map(db, row)
    await db.commit()
    return {'greenhouse_id': gh_id, 'revision': row.revision, **data}


@router.get('/map-monitor/{robot_id}')
async def monitor(robot_id: str, db: AsyncSession = Depends(get_db),
                  user: User = Depends(get_current_user)):
    latest = (await db.execute(select(InspectionVisit).where(
        InspectionVisit.robot_id == robot_id).order_by(
        InspectionVisit.arrived_at.desc()).limit(1))).scalar_one_or_none()
    visits = (await db.execute(select(InspectionVisit).where(
        InspectionVisit.robot_id == robot_id).distinct(InspectionVisit.nav_point_code)
        .order_by(InspectionVisit.nav_point_code, InspectionVisit.arrived_at.desc()))).scalars().all()
    releases = (await db.execute(select(RobotPointConfigRelease).where(
        RobotPointConfigRelease.robot_id == robot_id).order_by(
        RobotPointConfigRelease.config_version.desc()).limit(1))).scalar_one_or_none()
    configs = (await db.execute(select(RobotPointConfig).where(
        RobotPointConfig.robot_id == robot_id))).scalars().all()
    fields = ('nav_point_code', 'greenhouse_id', *FIELDS)
    published = {p['nav_point_code']: p for p in (releases.payload.get('points', []) if releases else [])}
    dirty = not releases or len(published) != len(configs) or any(
        c.nav_point_code not in published or c.id != published[c.nav_point_code].get('mapping_id') or any(getattr(c, f) != published[c.nav_point_code].get(f)
                                               for f in fields) for c in configs)
    def entry(v):
        if not v:
            return None
        return {f: getattr(v, f) for f in ('id', 'nav_point_code', 'mapping_id', 'greenhouse_id',
                'point_name', 'config_version', 'status', 'arrived_at', 'sampled_at', 'error_code')}
    return {'server_time': datetime.now(timezone.utc), 'latest_arrival': entry(latest),
            'point_results': [entry(v) for v in visits], 'draft_differs': dirty,
            'published_points': list(published.values())}


async def seed_maps(db):
    """One-time import preserves config IDs, names, parameters and unknown coordinates."""
    await lock_maps(db)
    configs = (await db.execute(select(RobotPointConfig).order_by(
        RobotPointConfig.greenhouse_id, RobotPointConfig.point_name))).scalars().all()
    groups = {}
    for c in configs:
        groups.setdefault(c.greenhouse_id, []).append(c)
    count = 0
    for gh_id, rows in groups.items():
        if await db.get(PointMap, gh_id):
            continue
        data = empty_map()
        names = set()
        for c in rows:
            name = c.point_name
            while name in names:
                name += '副本'
            names.add(name)
            p = PlannedPoint(id=uuid4(), **{f: getattr(c, f) for f in FIELDS}).model_dump(mode='json')
            p['point_name'] = name
            p['binding'] = {'robot_id': c.robot_id, 'nav_point_code': c.nav_point_code,
                            'mapping_id': c.id, 'bound_at': None}
            p['position_updated_at'] = None
            data['points'].append(p)
            count += 1
        data['defaults'] = {k: getattr(rows[-1], k) for k in Options.model_fields}
        db.add(PointMap(greenhouse_id=gh_id, revision=1, data=data))
    await db.commit()
    return count
