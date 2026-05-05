from sqlalchemy import Column, Integer, String, DateTime, Text
from datetime import datetime
from api.core.database import Base

class IngestTask(Base):
    __tablename__ = "task_queue"

    id = Column(Integer, primary_key=True, index=True)
    task_type = Column(String, index=True, default="ingest") # ingest, briefing, etc.
    status = Column(String, index=True, default="pending") # pending, running, completed, failed
    params = Column(Text, nullable=True) # JSON string of parameters
    logs = Column(Text, nullable=True) # Execution logs/errors
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
