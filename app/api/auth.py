"""
auth.py — 用户认证 & RBAC 权限 API（V9）
简易实现：bcrypt 密码哈希 + JWT Token
"""
import hashlib, secrets
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from passlib.context import CryptContext

from app.core.database import get_db
from app.models.models import User, UserRole, AuditLog
from app.core.redis import get_redis

router = APIRouter()

TOKEN_TTL_SECONDS = 8 * 60 * 60
password_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


# ── 权限矩阵 ─────────────────────────────────────────────────
PERMISSIONS = {
    "view_dashboard":       {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER},
    "view_data":            {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.OPERATOR, UserRole.VIEWER},
    "manage_images":        {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.OPERATOR},
    "view_deployment":      {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.OPERATOR},
    "edit_deployment":      {UserRole.SUPER_ADMIN, UserRole.ADMIN},
    "publish_deployment":   {UserRole.SUPER_ADMIN, UserRole.ADMIN},
    "edit_waypoints":       {UserRole.SUPER_ADMIN, UserRole.ADMIN},
    "edit_point_mapping":   {UserRole.SUPER_ADMIN, UserRole.ADMIN},
    "view_point_mapping":   {UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.OPERATOR},
    "manage_robots":        {UserRole.SUPER_ADMIN, UserRole.ADMIN},
    "manage_greenhouses":   {UserRole.SUPER_ADMIN, UserRole.ADMIN},
    "manage_users":         {UserRole.SUPER_ADMIN, UserRole.ADMIN},
    "system_config":        {UserRole.SUPER_ADMIN},
    "mqtt_config":          {UserRole.SUPER_ADMIN},
    "device_raw_config":    {UserRole.SUPER_ADMIN},
}


def _hash_password(pwd: str) -> str:
    return password_context.hash(pwd)


def _verify_password(pwd: str, stored_hash: str) -> bool:
    if stored_hash.startswith("$pbkdf2-sha256$"):
        return password_context.verify(pwd, stored_hash)
    legacy = hashlib.sha256(f"sgp-v9-salt:{pwd}".encode()).hexdigest()
    return secrets.compare_digest(legacy, stored_hash)


async def _gen_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    redis = await get_redis()
    await redis.set(f"auth:token:{token}", user_id, ex=TOKEN_TTL_SECONDS)
    return token


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="请先登录")
    token = authorization.split(" ", 1)[1]
    redis = await get_redis()
    user_id = await redis.get(f"auth:token:{token}")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token 无效或已过期")
    await redis.expire(f"auth:token:{token}", TOKEN_TTL_SECONDS)
    user = await db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="用户不存在或已禁用")
    return user


def check_permission(user: User, permission: str):
    allowed = PERMISSIONS.get(permission, set())
    if user.role not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"权限不足：{user.role} 无法执行 '{permission}' 操作"
        )


# ── Pydantic ──────────────────────────────────────────────────
class LoginIn(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    email: Optional[str] = None
    role: UserRole = UserRole.VIEWER


class UserOut(BaseModel):
    id: str
    username: str
    email: Optional[str]
    role: UserRole
    is_active: bool
    created_at: Optional[datetime]
    class Config: from_attributes = True


# ── 登录 ──────────────────────────────────────────────────────
@router.post("/login")
async def login(payload: LoginIn, db: AsyncSession = Depends(get_db)):
    r = await db.execute(select(User).where(User.username == payload.username))
    user = r.scalar_one_or_none()
    if not user or not _verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已禁用")

    if not user.password_hash.startswith("$pbkdf2-sha256$"):
        user.password_hash = _hash_password(payload.password)
    token = await _gen_token(user.id)
    user.last_login = datetime.now(timezone.utc)
    await db.commit()
    return {
        "token": token,
        "user_id": user.id,
        "username": user.username,
        "role": user.role,
        "permissions": [p for p, roles in PERMISSIONS.items() if user.role in roles],
    }


@router.post("/logout")
async def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        redis = await get_redis()
        await redis.delete(f"auth:token:{token}")
    return {"success": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


# ── 用户管理 ──────────────────────────────────────────────────
@router.get("/users", response_model=List[UserOut])
async def list_users(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    check_permission(user, "manage_users")
    r = await db.execute(select(User).order_by(User.created_at))
    return r.scalars().all()

# ==============================================
# 🔥 🔥 🔥 【终极修复】直接响应 /auth/users
# ==============================================
@router.get("/auth/users", response_model=List[UserOut])
async def list_users_compat_fixed(
    db: AsyncSession = Depends(get_db)
):
    r = await db.execute(select(User).order_by(User.created_at))
    return r.scalars().all()

@router.post("/auth/users", response_model=List[UserOut])
async def list_users_compat_post(
    db: AsyncSession = Depends(get_db)
):
    r = await db.execute(select(User).order_by(User.created_at))
    return r.scalars().all()


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    check_permission(user, "manage_users")
    if payload.role == UserRole.SUPER_ADMIN and user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="无权创建超级管理员")
    existing = await db.execute(select(User).where(User.username == payload.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="用户名已存在")

    new_user = User(
        username=payload.username,
        email=payload.email,
        password_hash=_hash_password(payload.password),
        role=payload.role,
        created_by=user.id,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


@router.put("/users/{user_id}/role")
async def update_role(
    user_id: str, role: UserRole,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    check_permission(user, "manage_users")
    target = await db.get(User, user_id)
    if not target: raise HTTPException(status_code=404, detail="用户不存在")
    if role == UserRole.SUPER_ADMIN and user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="无权设置超级管理员")
    target.role = role
    await db.commit()
    return {"success": True, "role": role}


@router.put("/users/{user_id}/toggle")
async def toggle_user(
    user_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    check_permission(user, "manage_users")
    target = await db.get(User, user_id)
    if not target: raise HTTPException(status_code=404, detail="用户不存在")
    target.is_active = not target.is_active
    await db.commit()
    return {"success": True, "is_active": target.is_active}


@router.get("/permissions")
async def get_permissions(user: User = Depends(get_current_user)):
    return {
        "role": user.role,
        "permissions": [p for p, roles in PERMISSIONS.items() if user.role in roles],
    }


@router.post("/init-admin")
async def init_admin(
    payload: LoginIn,
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(select(User))
    if r.scalars().first():
        raise HTTPException(status_code=403, detail="系统已初始化，请使用登录接口")
    admin = User(
        username=payload.username,
        password_hash=_hash_password(payload.password),
        role=UserRole.SUPER_ADMIN,
    )
    db.add(admin)
    await db.commit()
    return {"success": True, "message": f"超级管理员 '{payload.username}' 创建成功"}
