from sqlalchemy import Column, BigInteger, String, Text, DateTime, func
from app.core.database import Base

class EventStandardLog(Base):
    __tablename__ = "event_standard_log"

    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    event_no = Column(String(64), unique=True, index=True, nullable=False, comment="事件唯一编号")
    event_type = Column(String(32), default="", comment="事件类型")
    event_source = Column(String(64), default="", comment="事件来源/设备")
    raw_content = Column(Text, nullable=True, comment="原始事件内容")
    chain_id = Column(String(64), default="", comment="因果链ID")
    parent_event_no = Column(String(64), default="", comment="父事件编号")
    snapshot_id = Column(String(64), default="", comment="状态快照ID")
    create_time = Column(DateTime, default=func.now(), comment="创建时间")
