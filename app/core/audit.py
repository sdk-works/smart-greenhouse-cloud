"""访问审计（M1 隔离收尾）。

规则：
- 只有平台运营（is_platform_operator / super_admin）可能跨公司访问。
- 平台运营访问**不属于自己公司**的对象时，必须落 audit_logs（谁 / 何时 / 哪家公司 / 什么资源）。
- 业务用户跨公司访问一律 403，不留审计（那是拒绝，不是访问）。
"""
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.company_scope import can_access_company, is_platform, resolve_company_id
from app.models.models import AuditLog, User


async def record_audit(
    db: AsyncSession,
    *,
    user: Optional[User],
    action: str,
    resource: str,
    resource_id: Optional[str] = None,
    target_company_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    extra: Optional[dict] = None,
) -> AuditLog:
    detail = dict(extra or {})
    if target_company_id:
        detail["target_company_id"] = target_company_id
    detail["actor_company_id"] = resolve_company_id(user)
    log = AuditLog(
        user_id=user.id if user is not None else None,
        action=action,
        resource=resource,
        resource_id=resource_id,
        detail=detail,
        ip_address=ip_address,
    )
    db.add(log)
    await db.flush()
    return log


async def record_cross_company_access(
    db: AsyncSession,
    user: Optional[User],
    target_company_id: Optional[str],
    resource: str,
    resource_id: Optional[str] = None,
    action: str = "read",
    ip_address: Optional[str] = None,
) -> Optional[AuditLog]:
    """平台运营访问其它公司数据时落审计；同公司/业务用户不落。"""
    if not is_platform(user):
        return None
    if not target_company_id:
        return None
    if target_company_id == resolve_company_id(user):
        return None
    return await record_audit(
        db,
        user=user,
        action=f"cross_company_{action}",
        resource=resource,
        resource_id=resource_id,
        target_company_id=target_company_id,
        ip_address=ip_address,
    )


async def ensure_company_access(
    db: AsyncSession,
    user: Optional[User],
    target_company_id: Optional[str],
    resource: str,
    resource_id: Optional[str] = None,
    action: str = "read",
    ip_address: Optional[str] = None,
) -> None:
    """先鉴权后审计：无权直接 403，有权且属跨公司则写审计日志。"""
    if not can_access_company(user, target_company_id):
        raise HTTPException(status_code=403, detail="无权访问其它公司的数据")
    await record_cross_company_access(
        db,
        user,
        target_company_id,
        resource=resource,
        resource_id=resource_id,
        action=action,
        ip_address=ip_address,
    )
