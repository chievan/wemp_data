import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.core.config import settings
from api.core.database import Base, engine, SessionLocal
from api.models.task import IngestTask
from api.models.chat import ChatSession
from core.logger import api_logger

# Create tables
Base.metadata.create_all(bind=engine)

async def background_task_worker():
    from datetime import datetime
    api_logger.info("Background worker started...")
    while True:
        try:
            db = SessionLocal()
            task = db.query(IngestTask).filter(IngestTask.status == "pending").order_by(IngestTask.created_at.asc()).first()
            if task:
                task.status = "running"
                db.commit()
                try:
                    if task.task_type == "ingest":
                        from api.services.ingest_task_runner import execute_ingest_task
                        await execute_ingest_task(task.id, task.params)
                    elif task.task_type == "vectorize":
                        from api.services.vectorize_task_runner import execute_vectorize_task
                        await execute_vectorize_task(task.id)
                    task.status = "completed"
                    task.completed_at = datetime.utcnow()
                    db.commit()
                except Exception as task_err:
                    api_logger.error(f"Task {task.id} failed: {task_err}")
                    task.status = "failed"
                    task.logs = str(task_err)[:2000]
                    task.completed_at = datetime.utcnow()
                    db.commit()
            db.close()
        except Exception as e:
            api_logger.error(f"Worker error: {e}")
        await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(background_task_worker())
    yield
    task.cancel()

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 显式导入并注册所有路由
from api.routers import ingest, chat, articles, committee, logs, skills

app.include_router(ingest.router, prefix=settings.API_V1_STR)
app.include_router(chat.router, prefix=settings.API_V1_STR)
app.include_router(articles.router, prefix=settings.API_V1_STR)
app.include_router(committee.router, prefix=settings.API_V1_STR)
app.include_router(logs.router, prefix=settings.API_V1_STR)
app.include_router(skills.router, prefix=settings.API_V1_STR)

@app.get("/api/v1/health")
def health_check():
    return {"status": "ok"}
