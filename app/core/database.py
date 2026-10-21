from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # 确保默认公司/基地存在（第一批迁移后的新库与测试库都需要）
    try:
        from sqlalchemy import select
        from app.core.company_scope import DEFAULT_BASE_ID, DEFAULT_COMPANY_ID
        from app.models.models import Company, SiteBase
        async with AsyncSessionLocal() as _db:
            exists = (await _db.execute(
                select(Company).where(Company.id == DEFAULT_COMPANY_ID)
            )).scalars().first()
            if not exists:
                _db.add(Company(id=DEFAULT_COMPANY_ID, name="默认公司"))
                _db.add(SiteBase(id=DEFAULT_BASE_ID, company_id=DEFAULT_COMPANY_ID,
                                 name="默认基地"))
                await _db.commit()
    except Exception as _e:  # 不阻断启动；迁移已 seed 时也走不到这里
        import logging
        logging.getLogger(__name__).warning(f"默认公司/基地初始化跳过: {_e}")
