import asyncio
import json
import logging
import sqlite3
import traceback
from pathlib import Path
from datetime import datetime

import core.ingest_service as old_ingest
from api.core.config import settings, _yaml_cfg
from api.core.database import SessionLocal
from api.models.task import IngestTask
from core.logger import vectorize_logger as logger

def run_vectorize_sync(task_id: int):
    """
    Synchronous function that finds un-embedded articles and vectorizes them into DolphinDB.
    """
    db_session = SessionLocal()
    task = db_session.query(IngestTask).filter(IngestTask.id == task_id).first()
    if not task:
        db_session.close()
        return

    def log_to_db(msg: str):
        logger.info(msg)
        try:
            current_logs = task.logs or ""
            task.logs = current_logs + msg + "\n"
            db_session.commit()
        except Exception:
            pass

    log_to_db(f"Starting vectorize task {task_id}.")

    try:
        cfg = _yaml_cfg
        
        # Connect to DBs
        db_path = settings.DATABASE_URL.replace("sqlite:///", "")
        dest_conn = sqlite3.connect(db_path)
        dest_conn.row_factory = sqlite3.Row
        
        ddb_sess = old_ingest.connect_ddb(cfg)
        log_to_db("DolphinDB connected successfully.")

        # Find un-embedded articles
        cursor = dest_conn.cursor()
        cursor.execute("SELECT * FROM wemp_articles WHERE embedded = 0 OR embedded IS NULL")
        rows = cursor.fetchall()
        
        log_to_db(f"Found {len(rows)} articles to vectorize.")
        
        counts = {"ok": 0, "error": 0}
        
        for row in rows:
            aid = row["article_id"]
            md_content = row["content_md"]
            if not md_content:
                counts["error"] += 1
                continue
                
            try:
                # 1. Chunk text
                chunks = old_ingest.chunk_text(md_content)
                if not chunks:
                    continue
                    
                # 2. Embed
                embeddings = old_ingest.embed_texts([c["text"] for c in chunks], cfg)
                
                # 3. Insert to DDB
                chunk_data = []
                for idx, c in enumerate(chunks):
                    chunk_data.append({
                        "chunk_id": f"{aid}__{idx:04d}",
                        "article_id": aid,
                        "content_hash": "hash_ph",
                        "mp_id": row["mp_id"],
                        "mp_name": row["mp_name"],
                        "title": row["title"],
                        "pub_time": datetime.fromtimestamp(int(row["published_at"])),
                        "source_url": row["source_url"],
                        "topic_tags": "",
                        "chunk_no": idx,
                        "chunk_text": c["text"],
                        "chunk_len": len(c["text"]),
                        "embedding": embeddings[idx]
                    })
                
                old_ingest._insert_chunks_to_ddb(ddb_sess, chunk_data, cfg)
                
                # Update SQLite status
                cursor.execute("UPDATE wemp_articles SET embedded = 1 WHERE article_id = ?", (aid,))
                dest_conn.commit()
                counts["ok"] += 1
                
                if counts["ok"] % 5 == 0:
                    log_to_db(f"Vectorized {counts['ok']} articles...")
                    
            except Exception as e:
                log_to_db(f"Error vectorizing {aid}: {e}")
                counts["error"] += 1

        dest_conn.close()
        log_to_db(f"Vectorize Task Complete! ok={counts['ok']}, err={counts['error']}")

    except Exception as e:
        error_trace = traceback.format_exc()
        log_to_db(f"CRITICAL ERROR:\n{error_trace}")
        raise e
    finally:
        db_session.close()


async def execute_vectorize_task(task_id: int):
    await asyncio.to_thread(run_vectorize_sync, task_id)
