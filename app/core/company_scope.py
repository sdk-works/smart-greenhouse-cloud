"""公司数据范围助手。

规则（v2 定稿）：
- 业务用户只能看自己公司；跨公司完全不可见。
- super_admin / is_platform_operator 可跨公司（运营方），但调用方需自行记审计。
- 迁移过渡期：company_id 为 NULL 的历史数据视为默认公司。
"""
from typing import Optional

from sqlalchemy import or_

from app.models.models import User, UserRole

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
