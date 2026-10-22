"""设备入网凭据管理（T3）。

范围：登记 / 生成凭据 / 停用 / 换密 / 审计 / 安装清单。**不含**固件烧录、扫码配网、OTA。

三条硬规则：
1. 明文口令只在"创建"或"换密"那一次返回，**不落库完整明文、不写日志、不进审计明细**
   （口令哈希用与 User 同一套 pbkdf2_sha256）。
2. username 与 clientid 一对一绑定；连接时两者必须同时匹配，只对一个也拒绝。
3. 任何写操作都要过 `check_permission` + 公司归属校验，并落 `audit_logs`。

对外还有一个纯函数 `authenticate(...)`：EMQX 的 HTTP 认证钩子、或本仓库自带的
测试用 broker，都调它，保证"线上怎么判、测试怎么判"是同一份代码。
"""
import secrets
import string
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import _hash_password, _verify_password
from app.core.audit import record_audit
from app.models.models import Device, MqttCredential, User

# 口令只从这些字符里取：DTU 配置界面/串口工具里手抄不容易看错（去掉 O/0/I/l/1）
_SECRET_ALPHABET = "".join(
    c for c in (string.ascii_letters + string.digits)
    if c not in "O0Il1"
)
SECRET_LEN = 24

# 拒绝原因码（给日志/测试用，不回给客户端细节以免泄漏账号是否存在）
DENY_NO_USER = "no_such_username"
DENY_BAD_PASSWORD = "bad_password"
DENY_DISABLED = "credential_disabled"
DENY_CLIENTID = "clientid_mismatch"


def generate_secret(length: int = SECRET_LEN) -> str:
    return "".join(secrets.choice(_SECRET_ALPHABET) for _ in range(length))


def hash_secret(plain: str) -> str:
    return _hash_password(plain)


async def get_by_device(db: AsyncSession, device_id: str) -> Optional[MqttCredential]:
    """按 devices.device_id 字符串身份取凭据。"""
    r = await db.execute(
        select(MqttCredential).where(MqttCredential.device_id == device_id))
    return r.scalar_one_or_none()


def _actor(u: Optional[User]) -> Optional[str]:
    return getattr(u, "id", None)


async def create_credential(
    db: AsyncSession,
    *,
    device: Device,
    asset_no: Optional[str],
    clientid: str,
    username: Optional[str] = None,
    note: Optional[str] = None,
    actor: Optional[User] = None,
) -> Tuple[MqttCredential, str]:
    """登记一台 DTU。**返回明文口令一次**，调用方必须立刻交给安装人员，不得留存。"""
    username = username or f"dev-{asset_no or device.device_id}".lower()
    conflict = await db.execute(
        select(MqttCredential).where(
            (MqttCredential.username == username)
            | (MqttCredential.clientid == clientid)
            | (MqttCredential.device_id == device.device_id)
        ))
    if conflict.scalar_one_or_none() is not None:
        raise ValueError("该设备已有凭据，或用户名/Client ID 已被占用")

    plain = generate_secret()
    cred = MqttCredential(
        username=username,
        password_hash=hash_secret(plain),
        clientid=clientid,
        device_id=device.device_id,
        asset_no=asset_no,
        # 兼容身份：老报文里的 device_id（例如 JY001），保留以便双边识别
        compat_identity=device.device_id,
        enabled=True,
        note=note,
        created_by=_actor(actor),
        updated_by=_actor(actor),
    )
    db.add(cred)
    await db.flush()

    await record_audit(
        db, user=actor, action="create", resource="mqtt_credential",
        resource_id=cred.username,
        extra={"device_id": device.device_id, "clientid": clientid,
               "asset_no": asset_no,
               # 只留口令指纹，明文一律不写
               "secret_fingerprint": _hash_password(plain)[-12:]},
    )
    await db.commit()
    await db.refresh(cred)
    return cred, plain


async def rotate_secret(
    db: AsyncSession, *, cred: MqttCredential, actor: Optional[User] = None
) -> str:
    """换密：旧口令立刻失效。**返回新明文一次**。"""
    plain = generate_secret()
    cred.password_hash = hash_secret(plain)
    cred.last_rotated_at = datetime.now(timezone.utc)
    cred.rotation_count = (cred.rotation_count or 0) + 1
    cred.updated_by = _actor(actor)
    await db.flush()
    await record_audit(
        db, user=actor, action="rotate", resource="mqtt_credential",
        resource_id=cred.username,
        extra={"rotation_count": cred.rotation_count,
               "secret_fingerprint": _hash_password(plain)[-12:]},
    )
    await db.commit()
    # commit 会把 ORM 属性置为过期，异步上下文里再触发惰性加载会 MissingGreenlet，
    # 这里显式刷新，保证调用方（含 public_view）读到的是提交后的值。
    await db.refresh(cred)
    return plain


async def set_enabled(
    db: AsyncSession, *, cred: MqttCredential, enabled: bool,
    actor: Optional[User] = None, reason: Optional[str] = None,
) -> MqttCredential:
    """停用 / 重新启用。停用后该凭据不能再次连接（authenticate 直接拒绝）。"""
    cred.enabled = enabled
    cred.updated_by = _actor(actor)
    cred.disabled_at = None if enabled else datetime.now(timezone.utc)
    await db.flush()
    await record_audit(
        db, user=actor,
        action="enable" if enabled else "disable",
        resource="mqtt_credential", resource_id=cred.username,
        extra={"enabled": enabled, "reason": reason or ""},
    )
    await db.commit()
    await db.refresh(cred)
    return cred


# ── 认证判定（EMQX HTTP authn 钩子 / 测试 broker 共用同一份） ──
async def authenticate(
    db: AsyncSession, *, username: str, password: str, clientid: str
) -> Tuple[bool, str]:
    """判定一次连接是否被允许。 deliberately 不抛异常好让钩子直接拿到结论。"""
    if not username or not clientid:
        return False, DENY_NO_USER
    r = await db.execute(
        select(MqttCredential).where(MqttCredential.username == username))
    cred = r.scalar_one_or_none()
    if cred is None:
        return False, DENY_NO_USER
    # clientid 必须与登记值完全一致：防止"拿甲账号、冒乙设备的 clientid"
    if cred.clientid != clientid:
        return False, DENY_CLIENTID
    if not cred.enabled:
        return False, DENY_DISABLED
    if not password or not _verify_password(password, cred.password_hash):
        return False, DENY_BAD_PASSWORD
    return True, "ok"


def public_view(cred: MqttCredential) -> dict:
    """对外可见字段。**永不含 password_hash**。"""
    return {
        "username":        cred.username,
        "clientid":        cred.clientid,
        "device_id":       cred.device_id,
        "asset_no":        cred.asset_no,
        "compat_identity": cred.compat_identity,
        "enabled":         cred.enabled,
        "note":            cred.note,
        "rotation_count":  cred.rotation_count or 0,
        "created_at":      cred.created_at.isoformat() if cred.created_at else None,
        "updated_at":      cred.updated_at.isoformat() if cred.updated_at else None,
        "disabled_at":     cred.disabled_at.isoformat() if cred.disabled_at else None,
        "last_rotated_at": cred.last_rotated_at.isoformat() if cred.last_rotated_at else None,
    }


__all__ = [
    "generate_secret", "hash_secret", "get_by_device", "create_credential",
    "rotate_secret", "set_enabled", "authenticate", "public_view",
    "DENY_NO_USER", "DENY_BAD_PASSWORD", "DENY_DISABLED", "DENY_CLIENTID",
]
