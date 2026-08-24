from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from app.core.config import settings

# 同步引擎，彻底关闭异步，永不报错！
engine = create_engine(
    "postgresql://postgres:SET_LOCALLY_DO_NOT_COMMIT@127.0.0.1:5432/greenhouse",
    echo=False,
    future=True
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
