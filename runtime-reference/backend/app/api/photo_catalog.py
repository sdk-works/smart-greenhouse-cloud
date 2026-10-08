import asyncio
import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
from typing import Optional, Literal
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func, case, true, or_
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image as PILImage, ImageOps
from app.core.database import get_db
from app.core.config import settings
from app.api.auth import get_current_user
from app.models.models import Image, Device, Telemetry, Greenhouse, User
from app.api.point_maps import PointBindingArchive, PointMap

router=APIRouter()
logger=logging.getLogger(__name__)
thumb_slots=asyncio.Semaphore(4)

def thumbnail(image_id, storage_url):
    root=Path(settings.UPLOAD_DIR).resolve()
    prefix=urlsplit(settings.UPLOAD_URL_PREFIX).path.rstrip('/')+'/'
    storage_path=urlsplit(storage_url or '').path
    if not storage_path.startswith(prefix):return None
    source=(root/storage_path[len(prefix):]).resolve()
    if not source.is_relative_to(root) or not source.is_file():return None
    folder=root/'thumbnails';target=folder/(image_id+'.jpg')
    if target.is_file():return prefix+'thumbnails/'+target.name
    temp=None
    try:
        folder.mkdir(exist_ok=True)
        with PILImage.open(source) as im:
            im=ImageOps.exif_transpose(im)
            im.thumbnail((480,360))
            if im.mode!='RGB':im=im.convert('RGB')
            temp=folder/(image_id+'.'+uuid4().hex+'.tmp')
            im.save(temp,format='JPEG',quality=75,optimize=True)
        temp.replace(target)
        return prefix+'thumbnails/'+target.name
    except Exception as exc:
        logger.warning('Thumbnail unavailable image=%s: %s',image_id,type(exc).__name__)
        return None
    finally:
        if temp and temp.exists():temp.unlink()

async def thumb(id,url):
    async with thumb_slots:return await asyncio.to_thread(thumbnail,id,url)

@router.get('/options')
async def options(db:AsyncSession=Depends(get_db), user:User=Depends(get_current_user)):
    maps=(await db.execute(select(PointMap))).scalars().all()
    return {'points':[{'id':p['id'],'greenhouse_id':m.greenhouse_id,'name':p['point_name'],'enabled':p.get('enabled',True)} for m in maps for p in m.data.get('points',[])]}

@router.get('')
async def catalogue(greenhouse_id:Optional[str]=None,point_id:Optional[str]=None,
    device_id:Optional[str]=None,classification:Literal['all','linked','unconfirmed']='all',
    start:Optional[datetime]=None,end:Optional[datetime]=None,
    keyword:str=Query('',max_length=100),order:Literal['newest','oldest']='newest',
    page:int=Query(1,ge=1),page_size:int=Query(24,ge=1,le=48),
    db:AsyncSession=Depends(get_db),user:User=Depends(get_current_user)):
    for value in (start,end):
        if value is not None and value.tzinfo is None:raise HTTPException(422,'时间需包含时区')
    if start and end and start>=end:raise HTTPException(422,'结束时间须晚于开始时间')
    # Match exact device + complete task ID. Do not infer from navigation code or time proximity.
    name=case((func.substr(Image.filename,1,func.length(Device.device_id)+1)==Device.device_id+'_',
        func.substr(Image.filename,func.length(Device.device_id)+2)),else_=Image.filename)
    pattern=r'_[0-9]{10}(\.[0-9]+)?\.(jpg|jpeg|png)$'
    task=case((name.op('~*')(pattern),func.regexp_replace(name,pattern,'','i')),else_=None)
    event=(select(Telemetry.mapping_id.label('mapping_id'),Telemetry.greenhouse_id.label('greenhouse_id'),
        Telemetry.greenhouse_point.label('old_name'),Telemetry.task_id.label('task_id'))
        .where(Telemetry.device_id==Image.device_id,Telemetry.task_id==task)
        .order_by(Telemetry.timestamp.desc(),Telemetry.id.desc()).limit(1).correlate(Image,Device).lateral('observation'))
    base=(select(Image.id).select_from(Image).join(Device,Device.id==Image.device_id)
        .outerjoin(event,true()).outerjoin(PointBindingArchive,
            (PointBindingArchive.mapping_id==event.c.mapping_id)&(PointBindingArchive.greenhouse_id==event.c.greenhouse_id))
        .outerjoin(Greenhouse,Greenhouse.id==event.c.greenhouse_id))
    captured=func.coalesce(Image.captured_at,Image.created_at)
    filters=[]
    if greenhouse_id:filters.append(event.c.greenhouse_id==greenhouse_id)
    if point_id:filters.append(PointBindingArchive.point_id==point_id)
    if device_id:filters.append(Image.device_id==device_id)
    if classification=='linked':filters.append(PointBindingArchive.point_id.is_not(None))
    if classification=='unconfirmed':filters.append(PointBindingArchive.point_id.is_(None))
    if start:filters.append(captured>=start)
    if end:filters.append(captured<end)
    if keyword.strip():
        q='%'+keyword.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        filters.append(or_(Image.filename.ilike(q),event.c.task_id.ilike(q),event.c.old_name.ilike(q)))
    base=base.where(*filters)
    total=(await db.execute(select(func.count()).select_from(base.subquery()))).scalar()
    query=base.with_only_columns(Image,Device.name.label('device_name'),Greenhouse.name.label('greenhouse_name'),
        event.c.greenhouse_id,event.c.old_name,event.c.task_id,PointBindingArchive.point_id)
    direction=captured.desc() if order=='newest' else captured.asc()
    rows=(await db.execute(query.order_by(direction,Image.id).offset((page-1)*page_size).limit(page_size))).all()
    maps=(await db.execute(select(PointMap).where(PointMap.greenhouse_id.in_({r.greenhouse_id for r in rows if r.greenhouse_id})))).scalars().all()
    names={(m.greenhouse_id,p['id']):p['point_name'] for m in maps for p in m.data.get('points',[])}
    thumbnails=await asyncio.gather(*(thumb(r.Image.id,r.Image.storage_url) for r in rows))
    items=[]
    for r,t in zip(rows,thumbnails):
        im=r.Image
        items.append({'id':im.id,'filename':im.filename,'storage_url':urlsplit(im.storage_url).path,'thumbnail_url':t,
            'file_size':im.file_size,'captured_at':im.captured_at,'created_at':im.created_at,
            'device_id':im.device_id,'device_name':r.device_name,'greenhouse_id':r.greenhouse_id,
            'greenhouse_name':r.greenhouse_name,'point_id':r.point_id,
            'point_name':names.get((r.greenhouse_id,r.point_id),r.old_name),
            'task_id':r.task_id,'classification':'linked' if r.point_id else 'unconfirmed',
            'capture_time_source':(im.ai_analysis or {}).get('capture_time_source')})
    return {'items':items,'total':total,'page':page,'page_size':page_size}
