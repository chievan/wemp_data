from sqlalchemy import Column, Integer, String, DateTime, Text
from datetime import datetime
from api.core.database import Base

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, index=True, unique=True)
    title = Column(String, nullable=True)
    article_id = Column(String, nullable=True) # 深度绑定的文章ID
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
