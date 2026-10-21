"""
images.py — v7 图片上传 API
策略：图片不走MQTT，改为HTTP POST上传
支持：本地缓存队列 / 断点续传 / 批量上传 / 4G/WiFi分流标识
"""
import os
import uuid
import imghdr
import hashlib
import json
from typing import List, Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Header
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.api.auth import check_permission, get_current_user
from app.core.config import settings
from app.core.photo_service import (
    greenhouse_company, latest_map_version, record_evidence, resolve_point,
)
from app.models.models import Image, Device
from app.models.models import User
from app.schemas.schemas import ImageResponse

router = APIRouter()

ALLOWED_TYPES = {"jpeg", "png", "gif", "webp", "bmp"}
MAX_FILE_SIZE  = 20 * 1024 * 1024  # 20MB


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    os.chmod(path, 0o755)
    return path


@router.post("/upload/{device_id}", response_model=ImageResponse)
async def upload_image(
    device_id:   str,
    file:        UploadFile = File(...),
    task_id:     Optional[str]   = Query(None),
    position_x:  Optional[float] = Query(None),
    position_y:  Optional[float] = Query(None),
    # 网络类型标识（机器人端传入：wifi / 4g / ethernet）
    network_type: Optional[str]  = Query(None, description="网络类型: wifi/ethernet/4g"),
    # 断点续传支持
    file_hash:   Optional[str]   = Query(None, description="文件MD5，用于去重"),
    # M2：点位归属（解析不出就留 NULL，不猜）
    nav_point_code:   Optional[str] = Query(None, description="机器人导航点位编号"),
    greenhouse_point: Optional[str] = Query(None, description="大棚点位名称"),
    db: AsyncSession = Depends(get_db),
):
    """
    图片 HTTP 上传接口（v7主要图片通道，替代MQTT图片传输）

    机器人端调用逻辑：
    - 4G网络：本地缓存，不上传
    - WiFi/有线：批量上传缓存中的图片
    """
    # 1. 验证设备
    result = await db.execute(select(Device).where(Device.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(
            status_code=404,
            detail=f"设备 '{device_id}' 未注册，请先注册设备"
        )

    # 2. 读取内容
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"文件过大（{len(content)//1024//1024}MB），限20MB")

    # 3. 断点续传：文件已存在则直接返回
    if file_hash:
        existing_url = await _find_by_hash(db, device.id, file_hash)
        if existing_url:
            # 已存在，返回已上传的记录
            img_result = await db.execute(
                select(Image).where(Image.device_id == device.id)
                .order_by(Image.created_at.desc()).limit(1)
            )
            existing_img = img_result.scalar_one_or_none()
            if existing_img:
                return existing_img

    # 4. 校验文件类型（用文件头魔数）
    img_type = imghdr.what(None, h=content)
    if img_type not in ALLOWED_TYPES:
        ct = (file.content_type or "").lower()
        if "image/" not in ct:
            raise HTTPException(
                status_code=415,
                detail=f"不支持的文件类型（{img_type or file.content_type}），请上传图片"
            )
        img_type = "jpeg"

    # 5. 保存文件
    ext       = {"jpeg":"jpg","png":"png","gif":"gif","webp":"webp","bmp":"bmp"}.get(img_type,"jpg")
    filename  = f"{uuid.uuid4()}.{ext}"
    dev_dir   = ensure_dir(os.path.join(settings.UPLOAD_DIR, device_id))
    filepath  = os.path.join(dev_dir, filename)

    try:
        with open(filepath, "wb") as f:
            f.write(content)
        os.chmod(filepath, 0o644)
    except PermissionError:
        raise HTTPException(status_code=500, detail=f"存储目录无写权限: {settings.UPLOAD_DIR}")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"文件保存失败: {e}")

    # 6. 计算文件 hash（用于去重/断点续传）
    actual_hash = hashlib.md5(content).hexdigest()
    storage_url = f"{settings.UPLOAD_URL_PREFIX}/{device_id}/{filename}"

    # 7. 写数据库（M2：点位归属解析，解析不出留 NULL）
    point = await resolve_point(
        db, robot_id=device_id, nav_point_code=nav_point_code,
        greenhouse_id=device.greenhouse_id, point_name=greenhouse_point,
    )
    mv = await latest_map_version(db, device.greenhouse_id)
    image = Image(
        device_id     = device.id,
        task_id       = task_id,
        greenhouse_id = device.greenhouse_id,
        filename      = filename,
        storage_url   = storage_url,
        file_size     = len(content),
        position_x    = position_x,
        position_y    = position_y,
        tags          = [f"network:{network_type}"] if network_type else [],
        captured_at   = datetime.now(timezone.utc),
        ai_analysis   = {"file_hash": actual_hash, "network_type": network_type},
        point_id      = point.id if point else None,
        map_version_id= mv.id if mv else None,
        company_id    = await greenhouse_company(db, device.greenhouse_id),
    )
    db.add(image)
    await db.flush()
    await record_evidence(
        db,
        image_id=image.id,
        image_url=storage_url,
        task_id=task_id,
        point_id=image.point_id,
        greenhouse_id=device.greenhouse_id,
        robot_id=device_id,
        nav_point_code=nav_point_code,
        map_version_id=image.map_version_id,
        captured_at=image.captured_at,
        file_size=len(content),
    )
    await db.commit()
    await db.refresh(image)
    return image


@router.post("/upload/batch/{device_id}")
async def upload_images_batch(
    device_id: str,
    files: List[UploadFile] = File(...),
    task_id: Optional[str]  = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    批量上传接口（WiFi下的缓存队列上传）
    机器人端：4G时本地缓存 → WiFi时调用此接口批量上传
    """
    result = await db.execute(select(Device).where(Device.device_id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备未注册")

    uploaded, failed = [], []
    for file in files[:20]:  # 单次最多20张
        try:
            content = await file.read()
            if not content or len(content) > MAX_FILE_SIZE:
                failed.append({"filename": file.filename, "error": "文件为空或过大"})
                continue

            img_type = imghdr.what(None, h=content) or "jpeg"
            if img_type not in ALLOWED_TYPES:
                failed.append({"filename": file.filename, "error": "格式不支持"})
                continue

            ext      = {"jpeg":"jpg","png":"png","gif":"gif","webp":"webp"}.get(img_type,"jpg")
            fname    = f"{uuid.uuid4()}.{ext}"
            dev_dir  = ensure_dir(os.path.join(settings.UPLOAD_DIR, device_id))
            with open(os.path.join(dev_dir, fname), "wb") as f:
                f.write(content)

            url = f"{settings.UPLOAD_URL_PREFIX}/{device_id}/{fname}"
            img = Image(
                device_id=device.id, task_id=task_id,
                greenhouse_id=device.greenhouse_id,
                filename=fname, storage_url=url, file_size=len(content),
                captured_at=datetime.now(timezone.utc),
                tags=["batch_upload"],
                company_id=await greenhouse_company(db, device.greenhouse_id),
            )
            db.add(img)
            await db.flush()
            await record_evidence(
                db,
                image_id=img.id,
                image_url=url,
                task_id=task_id,
                point_id=None,
                greenhouse_id=device.greenhouse_id,
                robot_id=device_id,
                map_version_id=None,
                captured_at=img.captured_at,
                file_size=len(content),
            )
            uploaded.append(url)
        except Exception as e:
            failed.append({"filename": file.filename, "error": str(e)})

    await db.commit()
    return {
        "success": len(uploaded),
        "failed":  len(failed),
        "urls":    uploaded,
        "errors":  failed,
    }


@router.get("/", response_model=List[ImageResponse])
async def list_images(
    greenhouse_id: Optional[str]  = Query(None),
    task_id:       Optional[str]  = Query(None),
    device_id_str: Optional[str]  = Query(None, alias="device_id"),
    skip: int = 0,
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    query = select(Image)
    if greenhouse_id:
        query = query.where(Image.greenhouse_id == greenhouse_id)
    if task_id:
        query = query.where(Image.task_id == task_id)
    if device_id_str:
        dev = await db.execute(select(Device).where(Device.device_id == device_id_str))
        d = dev.scalar_one_or_none()
        if d:
            query = query.where(Image.device_id == d.id)
    result = await db.execute(query.order_by(Image.created_at.desc()).offset(skip).limit(limit))
    return result.scalars().all()


@router.delete("/{image_id}", status_code=204)
async def delete_image(image_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    check_permission(user, "manage_images")
    result = await db.execute(select(Image).where(Image.id == image_id))
    image  = result.scalar_one_or_none()
    if not image:
        raise HTTPException(status_code=404, detail="图片不存在")
    if image.filename and image.storage_url:
        rel  = image.storage_url.replace(settings.UPLOAD_URL_PREFIX + "/", "")
        fp   = os.path.join(settings.UPLOAD_DIR, rel)
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError:
                pass
    await db.delete(image)
    await db.commit()


async def _find_by_hash(db, device_id: str, file_hash: str) -> Optional[str]:
    """通过文件 hash 查找已存在的图片（断点续传去重）"""
    try:
        result = await db.execute(
            select(Image).where(Image.device_id == device_id)
            .order_by(Image.created_at.desc()).limit(100)
        )
        for img in result.scalars().all():
            ai = img.ai_analysis or {}
            if ai.get("file_hash") == file_hash:
                return img.storage_url
    except Exception:
        pass
    return None
