import asyncio
from datetime import datetime, timedelta
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

# Ensure app_settings table exists
from sqlalchemy import text as sa_text
with engine.connect() as conn:
    conn.execute(sa_text("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """))
    conn.commit()

async def background_task_worker():
    api_logger.info("Background worker started...")
    ZOMBIE_TIMEOUT = 1800  # 30 分钟超时，自动标记僵尸任务为 failed

    while True:
        db = None
        try:
            db = SessionLocal()
            # ─── 清理僵尸任务：running 超过 30 分钟的（用 SQL 避免 detached 对象问题）───
            cutoff = (datetime.utcnow() - timedelta(seconds=ZOMBIE_TIMEOUT)).isoformat()
            result = db.execute(sa_text(
                "UPDATE task_queue SET status = 'failed', "
                "logs = COALESCE(logs, '') || :msg, "
                "completed_at = :now "
                "WHERE status = 'running' AND created_at < :cutoff"
            ), {"msg": f"\n[SYSTEM] Marked as zombie (running > {ZOMBIE_TIMEOUT}s)",
                "now": datetime.utcnow().isoformat(), "cutoff": cutoff})
            db.commit()
            if result.rowcount > 0:
                api_logger.warning(f"Cleaned up {result.rowcount} zombie task(s)")

            # ─── 执行 pending 任务 ───
            task = db.query(IngestTask).filter(IngestTask.status == "pending").order_by(IngestTask.created_at.asc()).first()
            if task:
                task_id = task.id  # 先保存 ID
                task_type = task.task_type  # 先保存 type
                task_params = task.params  # 先保存 params
                task.status = "running"
                db.commit()
                db.close()  # 任务开始前先释放连接
                db = None
                try:
                    if task_type == "ingest":
                        from api.services.ingest_task_runner import execute_ingest_task
                        await execute_ingest_task(task_id, task_params)
                    elif task_type == "vectorize":
                        from api.services.vectorize_task_runner import execute_vectorize_task
                        await execute_vectorize_task(task_id)
                    db = SessionLocal()
                    task = db.query(IngestTask).filter(IngestTask.id == task_id).first()
                    if task:
                        task.status = "completed"
                        task.completed_at = datetime.utcnow()
                        db.commit()
                    else:
                        db.close()
                except Exception as task_err:
                    api_logger.error(f"Task {task_id} failed: {task_err}")
                    if db:
                        db.close()
                    db = SessionLocal()
                    task = db.query(IngestTask).filter(IngestTask.id == task_id).first()
                    if task:
                        task.status = "failed"
                        task.logs = (str(task_err) + "\n")[:2000]
                        task.completed_at = datetime.utcnow()
                        db.commit()
                    else:
                        db.close()
        except Exception as e:
            api_logger.error(f"Worker error: {e}")
        finally:
            if db:
                db.close()
        await asyncio.sleep(5)

async def scheduled_ingest_worker():
    """定时投递 ingest 任务，配置从 app_settings 表读取"""
    import json
    api_logger.info("Scheduled ingest worker started")

    while True:
        await asyncio.sleep(60)  # 每分钟检查一次配置
        db = None
        try:
            db = SessionLocal()
            row = db.execute(sa_text(
                "SELECT value FROM app_settings WHERE key = 'ingest_schedule'"
            )).fetchone()
            if not row:
                continue
            cfg = json.loads(row[0])
            if not cfg.get("enabled"):
                continue

            # 检查是否到了执行时间
            last_row = db.execute(sa_text(
                "SELECT value FROM app_settings WHERE key = 'ingest_last_run'"
            )).fetchone()
            now = datetime.utcnow().timestamp()
            interval = cfg.get("interval_seconds", 3600)
            if last_row:
                last_run = json.loads(last_row[0]).get("ts", 0)
            else:
                last_run = 0
            if now - last_run < interval:
                continue
            api_logger.info(f"Scheduled check: interval={interval}s, elapsed={now - last_run:.0f}s, ready={'yes' if now - last_run >= interval else 'no'}")

            # 检查是否有正在运行的任务
            running = db.query(IngestTask).filter(
                IngestTask.status.in_(["pending", "running"])
            ).first()
            if running:
                api_logger.info(f"Scheduled check: task #{running.id} ({running.status}) is blocking, skip")
                continue

            # 投递新任务（增量模式 + 自动向量化）
            new_task = IngestTask(
                task_type="ingest",
                status="pending",
                params=json.dumps({"limit": 0, "force": False, "skip_ddb": False, "incremental": True})
            )
            db.add(new_task)
            # 记录执行时间
            db.execute(sa_text(
                "INSERT INTO app_settings (key, value) VALUES ('ingest_last_run', :v) ON CONFLICT(key) DO UPDATE SET value = :v"
            ), {"v": json.dumps({"ts": now})})
            db.commit()
            api_logger.info(f"Scheduled ingest task #{new_task.id} queued (incremental mode)")
        except Exception as e:
            api_logger.error(f"Scheduled ingest error: {e}")
        finally:
            if db:
                db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    t1 = asyncio.create_task(background_task_worker())
    t2 = asyncio.create_task(scheduled_ingest_worker())
    yield
    t1.cancel()
    t2.cancel()

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
