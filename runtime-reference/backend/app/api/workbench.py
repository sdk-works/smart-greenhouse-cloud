"""Greenhouse workspace; historical observations use immutable mapping associations."""
from datetime import datetime, timedelta, timezone
import re
from uuid import uuid4
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, String, Integer, JSON, ForeignKey, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import Base, get_db
from app.api.auth import get_current_user, check_permission
from app.models.models import Greenhouse, User, Telemetry, Image, Device, InspectionVisit
from app.api.point_maps import (PointMap, PointBindingArchive, PointMapRevision, Layout,
    Options, empty_map, lock_maps, archive_map, _greenhouse)

router = APIRouter()
METRICS = ('temperature','humidity','light_intensity','co2_level','soil_moisture',
    'soil_temperature','soil_ec','ph_value','soil_nitrogen','soil_phosphorus','soil_potassium')

class GreenhouseProfile(Base):
    __tablename__ = 'greenhouse_profiles'
    greenhouse_id = Column(String(36), ForeignKey('greenhouses.id'), primary_key=True)
    revision = Column(Integer, nullable=False, default=1)
    data = Column(JSON, nullable=False)

class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    location: str = Field(default='', max_length=200)
    crop_type: str = Field(default='', max_length=100)
    description: str = Field(default='', max_length=2000)
    structure: Literal['solar','single','multi','other'] = 'solar'
    covering: Literal['film','glass','polycarbonate','other'] = 'film'
    actual_area: Optional[float] = Field(default=None, gt=0, le=100000000, allow_inf_nan=False)

class CreateGreenhouse(Profile):
    layout: Layout
    defaults: Options = Field(default_factory=Options)

class UpdateProfile(Profile):
    revision: int = Field(ge=0)

def profile_value(gh, profile):
    return {'greenhouse_id': gh.id, 'name': gh.name, 'location': gh.location or '',
        'crop_type': gh.crop_type or '', 'description': gh.description or '',
        'structure': (profile.data if profile else {}).get('structure','other'),
        'covering': (profile.data if profile else {}).get('covering','other'),
        'actual_area': gh.area, 'revision': profile.revision if profile else 0}

@router.post('/greenhouses', status_code=201)
async def create(body: CreateGreenhouse, db: AsyncSession=Depends(get_db), user: User=Depends(get_current_user)):
    check_permission(user, 'edit_point_mapping')
    gh=Greenhouse(id=str(uuid4()), name=body.name, location=body.location, crop_type=body.crop_type,
        description=body.description, area=body.actual_area)
    db.add(gh)
    await db.flush()
    profile=GreenhouseProfile(greenhouse_id=gh.id,revision=1,
        data={'structure':body.structure,'covering':body.covering})
    db.add(profile)
    data=empty_map();data.update(layout=body.layout.model_dump(),defaults=body.defaults.model_dump())
    row=PointMap(greenhouse_id=gh.id,revision=1,data=data)
    db.add(row);await db.flush();await archive_map(db,row)
    await db.commit()
    return profile_value(gh,profile)

@router.get('/greenhouses/{gh_id}')
async def overview(gh_id: str, db: AsyncSession=Depends(get_db), user: User=Depends(get_current_user)):
    gh=await _greenhouse(db,gh_id)
    profile=await db.get(GreenhouseProfile,gh_id)
    row=await db.get(PointMap,gh_id)
    data=row.data if row else empty_map()
    # Imported associations are archived during migration; also include current bindings.
    aliases=(await db.execute(select(PointBindingArchive).where(PointBindingArchive.greenhouse_id==gh_id))).scalars().all()
    lookup={a.mapping_id:a.point_id for a in aliases}
    lookup.update({p['binding']['mapping_id']:p['id'] for p in data['points'] if p.get('binding')})
    latest={}
    if lookup:
        observations=(await db.execute(select(Telemetry).where(Telemetry.greenhouse_id==gh_id,
            Telemetry.mapping_id.in_(lookup)).distinct(Telemetry.mapping_id)
            .order_by(Telemetry.mapping_id,Telemetry.timestamp.desc(),Telemetry.id.desc()))).scalars().all()
        for t in observations:
            pid=lookup[t.mapping_id]
            if pid not in latest or t.timestamp>latest[pid].timestamp:latest[pid]=t
    return {'profile':profile_value(gh,profile),'map':{'greenhouse_id':gh_id,
        'revision':row.revision if row else 0,**data},
        'latest':{pid:observation(t) for pid,t in latest.items()}}

@router.put('/greenhouses/{gh_id}/profile')
async def update(gh_id: str, body: UpdateProfile, db: AsyncSession=Depends(get_db), user: User=Depends(get_current_user)):
    check_permission(user,'edit_point_mapping');await lock_maps(db)
    gh=await _greenhouse(db,gh_id);profile=await db.get(GreenhouseProfile,gh_id)
    if body.revision!=(profile.revision if profile else 0):raise HTTPException(409,'档案已更新，请重新加载')
    for key in ('name','location','crop_type','description'):setattr(gh,key,getattr(body,key))
    gh.area=body.actual_area
    if profile is None:
        profile=GreenhouseProfile(greenhouse_id=gh_id,revision=0,data={});db.add(profile)
    profile.data={'structure':body.structure,'covering':body.covering};profile.revision+=1
    await db.commit();return profile_value(gh,profile)

def observation(t):
    quality=(t.raw_data or {}).get('data_quality') or {}
    return {'id':t.id,'captured_at':t.timestamp,'received_at':t.created_at,'task_id':t.task_id,
        'point_name':t.greenhouse_point,'mapping_id':t.mapping_id,'sample_count':t.sample_count,
        'quality':quality,'values':{key:getattr(t,key) for key in METRICS}}

@router.get('/greenhouses/{gh_id}/points/{point_id}')
async def detail(gh_id: str, point_id: str, days: int=Query(default=7,ge=1,le=365),
    db: AsyncSession=Depends(get_db), user: User=Depends(get_current_user)):
    await _greenhouse(db,gh_id)
    row=await db.get(PointMap,gh_id)
    point=next((p for p in (row.data['points'] if row else []) if p['id']==point_id),None)
    if not point:raise HTTPException(404,'采集点不存在')
    aliases=(await db.execute(select(PointBindingArchive).where(
        PointBindingArchive.greenhouse_id==gh_id,PointBindingArchive.point_id==point_id))).scalars().all()
    ids={a.mapping_id for a in aliases}
    if point.get('binding'):ids.add(point['binding']['mapping_id'])
    since=datetime.now(timezone.utc)-timedelta(days=days)
    conditions=[Telemetry.greenhouse_id==gh_id,Telemetry.mapping_id.in_(ids),Telemetry.timestamp>=since]
    total=(await db.execute(select(func.count()).select_from(Telemetry).where(*conditions))).scalar()
    rows=(await db.execute(select(Telemetry).where(*conditions).order_by(Telemetry.timestamp.desc(),Telemetry.id.desc()).limit(500))).scalars().all()
    visits=(await db.execute(select(InspectionVisit).where(InspectionVisit.greenhouse_id==gh_id,
        InspectionVisit.mapping_id.in_(ids),InspectionVisit.arrived_at>=since)
        .order_by(InspectionVisit.arrived_at.desc()).limit(500))).scalars().all()
    from app.api.photo_catalog import catalogue
    catalog = await catalogue(greenhouse_id=gh_id, point_id=point_id, device_id=None,
        classification='all', start=since, end=None, keyword='', order='newest',
        page=1, page_size=24, db=db, user=user)
    photos=[{**item, 'url':item['storage_url']} for item in catalog['items']]
    revisions=(await db.execute(select(PointMapRevision).where(PointMapRevision.greenhouse_id==gh_id)
        .order_by(PointMapRevision.revision))).scalars().all()
    positions=[];previous=None
    for rev in revisions:
        p=next((p for p in rev.data.get('points',[]) if p['id']==point_id),None)
        if p:
            current=(p.get('x'),p.get('y'))
            if current!=previous:
                positions.append({'revision':rev.revision,'saved_at':rev.saved_at,'x':current[0],'y':current[1]});previous=current
    return {'point':point,'observations':[observation(t) for t in rows], 'total':total,
        'truncated':total>len(rows),'photos':photos,'photo_total':catalog['total'],'position_history':positions,
        'visits':[{key:getattr(v,key) for key in ('id','task_id','point_name','status','arrived_at','sampled_at','error_code','config_version')} for v in visits],
        'photo_note':'仅展示设备与完整任务编号确定对应的照片；未能确定归属的旧照片仍在图像管理中。'}
