"""公司数据范围助手。

规则（v2 定稿）：
- 业务用户只能看自己公司；跨公司完全不可见。
- super_admin / is_platform_operator 可跨公司（运营方），但调用方需自行记审计。
- 迁移过渡期：company_id 为 NULL 的历史数据视为默认公司。
"""
from typing import Optional

from sqlalchemy import or_, select

from app.models.models import User, UserRole, Greenhouse

DEFAULT_COMPANY_ID = "c0000000-0000-0000-0000-000000000001"
DEFAULT_BASE_ID = "b0000000-0000-0000-0000-000000000001"


def is_platform(user: Optional[User]) -> bool:
    if user is None:
        return False
    if getattr(user, "is_platform_operator", False):
        return True
    return getattr(user, "role", None) == UserRole.SUPER_ADMIN


def resolve_company_id(user: Optional[User]) -> str:
    cid = getattr(user, "company_id", None) if user is not None else None
    return cid or DEFAULT_COMPANY_ID


def company_filter(user: Optional[User], column):
    """返回 SQLAlchemy 过滤条件；平台运营返回 None 表示不过滤。"""
    if is_platform(user):
        return None
    cid = resolve_company_id(user)
    return or_(column == cid, column.is_(None))


def can_access_company(user: Optional[User], company_id: Optional[str]) -> bool:
    if is_platform(user):
        return True
    return company_id is None or company_id == resolve_company_id(user)


def cache_company_for(user: Optional[User], target_company_id: Optional[str] = None) -> str:
    """缓存键使用的公司标识。

    业务用户恒为自己公司（跨公司缓存天然隔离）；平台运营跟随目标对象所属公司，
    对象不可判定（NULL）时回落到默认公司，避免平台侧把不同公司的数据混在同一个键上。
    """
    if is_platform(user):
        return target_company_id or DEFAULT_COMPANY_ID
    return resolve_company_id(user)


def scoped_greenhouse_ids(user: Optional[User], company_id: Optional[str] = None) -> Optional[object]:
    """返回"可见大棚 id"子查询；平台运营且未指定公司时返回 None（表示全平台，不过滤）。"""
    if is_platform(user):
        if company_id is None:
            return None
        return select(Greenhouse.id).where(Greenhouse.company_id == company_id)
    return select(Greenhouse.id).where(company_filter(user, Greenhouse.company_id))
