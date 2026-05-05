import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.core.config import settings
from api.core.database import Base, engine, SessionLocal
from api.models.task import IngestTask
from core.logger import api_logger

# Create tables
Base.metadata.create_all(bind=engine)

async def background_task_worker():
    """
    Background worker that polls the task_queue table for 'pending' tasks.
    """
    api_logger.info("Background worker started...")
    while True:
        try:
            db = SessionLocal()
            # Find the oldest pending task
            task = db.query(IngestTask).filter(IngestTask.status == "pending").order_by(IngestTask.created_at.asc()).first()
            if task:
                api_logger.info(f"Worker picked up task ID: {task.id}")
                # Mark as running
                task.status = "running"
                db.commit()
                
                if task.task_type == "ingest":
                    from api.services.ingest_task_runner import execute_ingest_task
                    await execute_ingest_task(task.id, task.params)
                elif task.task_type == "vectorize":
                    from api.services.vectorize_task_runner import execute_vectorize_task
                    await execute_vectorize_task(task.id)
                
                # Mark as completed
                task.status = "completed"
                from datetime import datetime
                task.completed_at = datetime.utcnow()
                db.commit()
                api_logger.info(f"Worker completed task ID: {task.id}")
            db.close()
        except Exception as e:
            api_logger.error(f"Worker error: {e}")
            if 'db' in locals() and 'task' in locals() and task:
                try:
                    task.status = "failed"
                    task.logs = (task.logs or "") + f"\nWorker Exception: {str(e)}"
                    db.commit()
                except:
                    pass
        
        # Poll every 5 seconds
        await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start the background worker
    task = asyncio.create_task(background_task_worker())
    yield
    # Shutdown
    task.cancel()

app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.routers import ingest, chat, articles, committee, logs

app.include_router(ingest.router, prefix=settings.API_V1_STR)
app.include_router(chat.router, prefix=settings.API_V1_STR)
app.include_router(articles.router, prefix=settings.API_V1_STR)
app.include_router(committee.router, prefix=settings.API_V1_STR)
app.include_router(logs.router, prefix=settings.API_V1_STR)

@app.get("/api/v1/health")
def health_check():
    return {"status": "ok"}
