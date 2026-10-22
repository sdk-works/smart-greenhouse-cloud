"""
conftest.py — 夜间测试公共夹具（修正版 v4）

硬性安全约束：
  * 只允许连测试库 greenhouse_nightly_test，DATABASE_URL 不含该串则整体拒绝运行。
  * 强制 NIGHTLY_READONLY=1。
  * httpx 的 ASGITransport 不触发 lifespan，测试进程内无后台协程。

修正点（相对初版）：
  * 配合 pytest.ini 的 asyncio_default_{fixture,test}_loop_scope = session，
    整轮测试共用同一个事件循环，避免全局 QueuePool engine（被 mqtt_service
    在导入时捕获）在“另一个 loop”上被拆除导致跨 loop 报错。
  * schema_ready 只在 session 开头建一次表；其余夹具函数级但都跑在同一 loop 上。
"""
import os
import secrets
import sys
import uuid
from typing import Optional

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("NIGHTLY_READONLY", "1")

from app.core.config import settings                  # noqa: E402
from app.core.database import AsyncSessionLocal, init_db  # noqa: E402
from app.core.redis import get_redis                 # noqa: E402
from app.models.models import User, UserRole          # noqa: E402

TEST_DB_MARKER = "greenhouse_nightly_test"

DEVICE_RECEIPTS_DDL = """
CREATE TABLE IF NOT EXISTS device_receipts (
  device_id varchar(100) NOT NULL,
  msg_id    varchar(200) NOT NULL,
  digest    varchar(64)  NOT NULL DEFAULT '',
  kind      varchar(30)  NOT NULL DEFAULT 'telemetry',
  created_at timestamptz DEFAULT now(),
  PRIMARY KEY (device_id, msg_id)
)
"""


def _guard_test_database():
    url = settings.DATABASE_URL
    if TEST_DB_MARKER not in url:
        pytest.exit(
            f"拒绝运行测试：DATABASE_URL 未指向 {TEST_DB_MARKER}（当前={url}）",
            returncode=2,
        )
    return url


@pytest.fixture(scope="session", autouse=True)
def guard_database():
    return _guard_test_database()


SKIP_STMT_PREFIXES = ("begin", "commit", "rollback", "grant", "revoke", "\\")


def split_sql_file(sql: str):
    """把 .sql 文件切分为单条语句（正确处理 $$...$$ 与字符串字面量内的分号）。"""
    stmts, buf, i, n = [], [], 0, len(sql)
    while i < n:
        if sql.startswith("$$", i):
            j = sql.find("$$", i + 2)
            if j == -1:
                buf.append(sql[i:]); break
            buf.append(sql[i:j + 2]); i = j + 2; continue
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2; continue
                    break
                j += 1
            buf.append(sql[i:j + 1]); i = j + 1; continue
        if ch == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            if j == -1:
                break
            i = j + 1; continue
        if ch == ";":
            stmts.append("".join(buf).strip()); buf = []; i += 1; continue
        buf.append(ch); i += 1
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    out = []
    for s in stmts:
        if s and not s.lstrip().lower().startswith(SKIP_STMT_PREFIXES):
            out.append(s)
    return out


async def _apply_migration(db, name: str):
    """在测试库执行 migrations/<name>（只跳过事务控制/授权语句）。"""
    path = os.path.join(ROOT, "migrations", name)
    if not os.path.exists(path):
        pytest.exit(f"缺少迁移文件：{path}", returncode=3)
    with open(path, "r", encoding="utf-8") as fh:
        sql = fh.read()
    for stmt in split_sql_file(sql):
        await db.execute(text(stmt))
    await db.commit()


@pytest_asyncio.fixture(scope="session")
async def schema_ready():
    await init_db()
    async with AsyncSessionLocal() as db:
        await db.execute(text(DEVICE_RECEIPTS_DDL))
        await db.commit()
        # 迁移文件是测试库 schema 的唯一来源，测试跑前必须已应用
        for mig in ("003_robot_loop_up.sql", "004_data_reliability_up.sql",
                    "005_business_up.sql", "006_device_onboarding_up.sql"):
            if os.path.exists(os.path.join(ROOT, "migrations", mig)):
                await _apply_migration(db, mig)
    return True


@pytest_asyncio.fixture
async def db(schema_ready):
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client(schema_ready):
    import main
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://nightly.test") as ac:
        yield ac


@pytest_asyncio.fixture
async def make_user(schema_ready):
    created = []

    async def _make(role: UserRole = UserRole.ADMIN, username: Optional[str] = None):
        async with AsyncSessionLocal() as session:
            user = User(
                username=username or f"night-{uuid.uuid4().hex[:12]}",
                password_hash="nightly-not-used",
                role=role,
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        token = secrets.token_urlsafe(24)
        redis = await get_redis()
        await redis.set(f"auth:token:{token}", user.id, ex=3600)
        created.append((user.id, token))
        return user, token

    yield _make

    redis = await get_redis()
    async with AsyncSessionLocal() as session:
        for user_id, token in created:
            await redis.delete(f"auth:token:{token}")
            row = await session.get(User, user_id)
            if row:
                await session.delete(row)
        await session.commit()


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# 供所有测试模块共用的隔离数据夹具（tests/iso_fx.py）
from tests.iso_fx import IsoFixture, iso  # noqa: E402,F401
