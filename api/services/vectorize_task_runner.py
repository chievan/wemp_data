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
                ingest_cfg = cfg.get("ingest", {})
                chunks = old_ingest.chunk_text(
                    md_content,
                    int(ingest_cfg.get("max_chunk_chars", 800)),
                    int(ingest_cfg.get("min_chunk_chars", 100)),
                    int(ingest_cfg.get("chunk_overlap_chars", 100))
                )
                if not chunks:
                    continue
                    
                # 2. Embed
                embed_cfg = cfg["embedding"]
                api_key = cfg.get("api_keys", {}).get("dashscope", "").strip()
                from openai import OpenAI
                client = OpenAI(base_url=embed_cfg["base_url"], api_key=api_key)
                vectors = old_ingest.embed_texts(client, embed_cfg, chunks)
                
                # 3. Insert to DDB
                pub_val = row["published_at"]
                # 处理可能是时间戳字符串或数字的情况
                try:
                    pub_ts = datetime.fromtimestamp(float(pub_val))
                except:
                    pub_ts = datetime.now()
                
                import pandas as pd
                pub_month = pd.Timestamp(year=pub_ts.year, month=pub_ts.month, day=1)
                
                art_row = {
                    "pub_month": pub_month, "article_id": aid,
                    "content_hash": "legacy", "mp_id": row["mp_id"], "mp_name": row["mp_name"],
                    "title": row["title"], "pub_time": pub_ts, "source_url": row["source_url"],
                    "file_path": "", "assets_dir": "",
                    "cover_image": row["cover_cos"] or "", "topic_tags": "未分类",
                    "content_clean": row["content_clean"] or "", "content_len": len(row["content_clean"] or ""),
                    "ingested_at": pd.Timestamp.now(),
                }
                
                chunk_rows = [
                    {
                        "pub_month": pub_month,
                        "chunk_id": f"{aid}__{idx:04d}",
                        "article_id": aid, "content_hash": "legacy",
                        "mp_id": row["mp_id"], "mp_name": row["mp_name"], "title": row["title"],
                        "pub_time": pub_ts, "source_url": row["source_url"],
                        "topic_tags": "未分类", "chunk_no": idx,
                        "chunk_text": chunk, "chunk_len": len(chunk),
                        "embedding": vec, "ingested_at": pd.Timestamp.now(),
                    }
                    for idx, (chunk, vec) in enumerate(zip(chunks, vectors), 1)
                ]
                
                old_ingest.write_to_ddb(ddb_sess, cfg, art_row, chunk_rows)
                
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
